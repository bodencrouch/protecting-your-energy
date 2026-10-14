#!/usr/bin/env python3
"""Observe selected original Discord edit/reply metadata without returning bodies."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

sys.dont_write_bytecode = True
HELPER_SHA = '201e8aa11906978fee04b3cb482091d65fc4025e8130ecf4fbf1995b72d37914'
SOURCE_LIMIT = 128 * 1024 * 1024
PARENT_LIMIT = 64
MESSAGE_LIMIT = 500000
CONFIG_LIMIT = 256 * 1024
OUTPUT_LIMIT = 256 * 1024
SHA = re.compile(r'[0-9a-f]{64}\Z')
ID = re.compile(r'[1-9][0-9]{0,39}\Z')
EDIT_KEYS = ('timestampEdited', 'timestamp_edited', 'edited_at')


class ObservationError(ValueError):
    pass


def require(condition, diagnostic):
    if not condition:
        raise ObservationError(diagnostic)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def serialize(result):
    raw = (json.dumps(result, indent=2, ensure_ascii=True) + '\n').encode('utf-8')
    require(len(raw) <= OUTPUT_LIMIT, 'observation-output-over-limit')
    return raw


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'observation-json-duplicate-key')
            result[key] = value
        return result
    def constant(_):
        raise ObservationError('observation-json-nonfinite')
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        if isinstance(error, ObservationError):
            raise
        raise ObservationError('observation-json-invalid') from None
    require(isinstance(value, dict), 'observation-json-object-required')
    return value


def canonical_id(value):
    if type(value) is int:
        value = str(value)
    require(isinstance(value, str) and bool(ID.fullmatch(value)), 'observation-id-invalid')
    return value


def instant(value):
    require(isinstance(value, str) and len(value) <= 96, 'observation-timestamp-invalid')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    except ValueError:
        raise ObservationError('observation-timestamp-invalid') from None
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            'observation-timestamp-not-aware')
    return parsed.astimezone(timezone.utc)


def checkpoint(deadline):
    require(time.monotonic() <= deadline, 'observation-deadline-exceeded')


def helper_module():
    path = Path(__file__).resolve().with_name('archive_access.py')
    require(not path.is_symlink(), 'observation-helper-location-invalid')
    raw = path.read_bytes()
    require(sha(raw) == HELPER_SHA, 'observation-helper-hash-mismatch')
    module = types.ModuleType('pye_observed_archive_access')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def read_regular(path, helper, deadline, limit, offset=0, count=None):
    """Read bounded source bytes, refusing symlinks and changed source identity."""
    path = Path(path).expanduser()
    require(path.is_absolute() and '..' not in path.parts and not helper.guarded(path),
            'observation-path-guarded-or-invalid')
    # All permitted apparent roots are SSD/NVMe locations. Component traversal
    # also refuses a home alias redirecting the read to a removable HDD.
    require(path.is_relative_to(Path.home()) or
            path.is_relative_to(Path('/run/media/brunner56/WorkspaceSSD')),
            'observation-source-outside-ssd-roots')
    checkpoint(deadline)
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_fd
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode), 'observation-source-not-regular')
            if count is None:
                count = before.st_size
            require(type(offset) is int and type(count) is int and offset >= 0
                    and 0 < count <= limit and offset + count <= before.st_size,
                    'observation-source-range-invalid-or-over-limit')
            chunks, cursor = [], 0
            while cursor < count:
                checkpoint(deadline)
                chunk = os.pread(fd, min(1024 * 1024, count - cursor), offset + cursor)
                require(bool(chunk), 'observation-source-read-incomplete')
                chunks.append(chunk)
                cursor += len(chunk)
            after = os.fstat(fd)
            # Resolve every component again, not through the original parent FD:
            # replacing an ancestor must not make an old FD prove a fresh path.
            fresh_directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in path.parts[1:-1]:
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=fresh_directory)
                    os.close(fresh_directory)
                    fresh_directory = next_fd
                reopened = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                   dir_fd=fresh_directory)
            finally:
                os.close(fresh_directory)
            try:
                current = os.fstat(reopened)
            finally:
                os.close(reopened)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(identity(before) == identity(after) == identity(current),
                    'observation-source-changed-during-read')
        finally:
            os.close(fd)
    finally:
        os.close(directory)
    checkpoint(deadline)
    raw = b''.join(chunks)
    return raw, {'path': str(path), 'bytes': count, 'byte_range': [offset, offset + count],
                 'sha256': sha(raw), 'stable_read_verified': True,
                 'no_symlink_traversal': True}


def edit_observation(message):
    entries, values, nulls, invalid = {}, [], 0, False
    for key in EDIT_KEYS:
        if key not in message:
            entries[key] = {'state': 'absent'}
        elif message[key] is None:
            entries[key] = {'state': 'null'}
            nulls += 1
        else:
            try:
                parsed = instant(message[key])
                entries[key] = {'state': 'value', 'value': message[key],
                                'instant_utc': parsed.isoformat()}
                values.append(parsed)
            except ObservationError:
                entries[key] = {'state': 'invalid'}
                invalid = True
    unique = set(values)
    state = ('invalid' if invalid else 'conflict' if len(unique) > 1 or (values and nulls)
             else 'value' if values else 'null' if nulls else 'absent')
    return {'state': state, 'instant_utc': next(iter(unique)).isoformat() if state == 'value' else None,
            'aliases': entries}


def reply_observation(message):
    aliases, values, nulls, invalid = {}, [], 0, False
    reference = message.get('reference')
    if 'reference' not in message:
        aliases['reference.messageId'] = {'state': 'absent'}
    elif reference is None:
        aliases['reference.messageId'] = {'state': 'null'}
        nulls += 1
    elif not isinstance(reference, dict):
        aliases['reference.messageId'] = {'state': 'invalid'}
        invalid = True
    elif 'messageId' not in reference:
        aliases['reference.messageId'] = {'state': 'absent'}
    else:
        aliases['reference.messageId'] = {'state': 'pending', 'raw': reference['messageId']}
    if 'reply_to' in message:
        aliases['reply_to'] = {'state': 'pending', 'raw': message['reply_to']}
    else:
        aliases['reply_to'] = {'state': 'absent'}
    for alias in aliases.values():
        if alias['state'] != 'pending':
            continue
        value = alias.pop('raw')
        if value is None:
            alias['state'] = 'null'
            nulls += 1
        else:
            try:
                value = canonical_id(value)
                alias.update(state='value', value=value)
                values.append(value)
            except ObservationError:
                alias['state'] = 'invalid'
                invalid = True
    unique = set(values)
    state = ('invalid' if invalid else 'conflict' if len(unique) > 1 or (values and nulls)
             else 'value' if values else 'null' if nulls else 'absent')
    clean_reference = None
    if isinstance(reference, dict):
        clean_reference = {}
        for key in ('messageId', 'channelId', 'guildId'):
            if key not in reference:
                continue
            try:
                clean_reference[key] = canonical_id(reference[key]) if reference[key] is not None else None
            except ObservationError:
                clean_reference[key] = {'state': 'invalid'}
        if 'type' in reference:
            clean_reference['type'] = reference['type'] if reference['type'] in ('Default', 'Forward', None) else {'state': 'unsupported'}
        clean_reference['unsupported_field_count'] = len(set(reference) - {'type', 'messageId', 'channelId', 'guildId'})
    return {'state': state, 'message_id': next(iter(unique)) if state == 'value' else None,
            'aliases': aliases, 'reference': clean_reference}


def combined_edit(original, normalized):
    if original['state'] in ('conflict', 'invalid') or normalized['state'] in ('conflict', 'invalid'):
        return {'state': 'unknown-conflict-or-invalid', 'instant_utc': None}
    if original['state'] == 'absent':
        return {'state': 'unknown-original-absent', 'instant_utc': None}
    if normalized['state'] != 'absent' and (original['state'], original['instant_utc']) != (
            normalized['state'], normalized['instant_utc']):
        return {'state': 'unknown-layer-conflict', 'instant_utc': None}
    return {'state': 'observed-edit' if original['state'] == 'value' else 'no-reported-edit-at-capture',
            'instant_utc': original['instant_utc']}


def cutoff_decision(created, edit, cutoff):
    if cutoff is None:
        return {'state': 'not-requested', 'historical_content_availability_proven': False}
    if created > cutoff:
        decision = 'exclude-created-after-cutoff'
    elif edit['state'] == 'observed-edit' and instant(edit['instant_utc']) > cutoff:
        decision = 'exclude-current-content-edited-after-cutoff'
    elif edit['state'].startswith('unknown'):
        decision = 'unknown-edit-history'
    else:
        decision = 'not-excluded-by-observed-timestamps'
    return {'state': decision, 'cutoff_utc': cutoff.isoformat(),
            'historical_content_availability_proven': False}


def observe(unit_id, generation, selected_ids=None, cutoff=None, config=None, budget=60):
    require(isinstance(unit_id, str) and SHA.fullmatch(unit_id), 'observation-unit-id-invalid')
    require(isinstance(generation, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', generation),
            'observation-generation-invalid')
    require(type(budget) in (int, float) and 1 <= budget <= 120, 'observation-budget-invalid')
    cutoff_time = instant(cutoff) if cutoff is not None else None
    deadline = time.monotonic() + budget
    helper = helper_module()
    config_path = Path(config or helper.DEFAULT_CONFIG).expanduser()
    config_raw, config_pin = read_regular(config_path, helper, deadline, CONFIG_LIMIT)
    strict_json(config_raw)
    settings = helper.settings(str(config_path))
    after_config, _ = read_regular(config_path, helper, deadline, CONFIG_LIMIT)
    require(after_config == config_raw, 'observation-configuration-changed')
    args = types.SimpleNamespace(unit_id=unit_id, generation=generation,
                                 budget=max(0.01, deadline - time.monotonic()))
    hydrated = helper.generic_hydrate(settings, args, metadata_only=True)
    require(hydrated.get('status') == 'normalized-parent-locators', 'observation-parent-resolution-unavailable')
    checkpoint(deadline)
    provenance = helper.generic_provenance(settings, hydrated['provenance'])
    require(provenance.get('platform') == 'discord' and provenance.get('kind') == 'conversation'
            and provenance.get('message_pointer') == '/messages', 'observation-source-format-unsupported')
    parents = provenance['parents']
    require(0 < len(parents) <= PARENT_LIMIT, 'observation-parent-count-over-limit')
    wanted = set(canonical_id(i) for i in selected_ids) if selected_ids else None
    require(wanted is None or len(wanted) <= PARENT_LIMIT, 'observation-selected-parent-count-over-limit')
    approved = {}
    for parent in parents:
        message_id = canonical_id(parent.get('message_id'))
        if wanted is not None and message_id not in wanted:
            continue
        require(message_id not in approved, 'observation-published-parent-duplicate')
        approved[message_id] = parent
    require(bool(approved) and (wanted is None or set(approved) == wanted),
            'observation-selected-id-not-published-parent')
    normalized_path = Path(provenance['normalized_path'])
    root = Path(settings['dense']['root']).expanduser()
    apparent_part = Path(provenance['part'])
    if not apparent_part.is_absolute():
        apparent_part = root / apparent_part
    require(apparent_part == normalized_path and normalized_path.is_relative_to(root / 'archive/corpus/records'),
            'observation-normalized-path-invalid-or-aliased')
    normalized_raw, record_pin = read_regular(normalized_path, helper, deadline, SOURCE_LIMIT,
        offset=provenance.get('record_offset'), count=provenance.get('record_bytes'))
    require(record_pin['sha256'] == provenance.get('record_line_sha256'), 'observation-normalized-record-hash-mismatch')
    record = strict_json(normalized_raw)
    require(record.get('id') == provenance.get('record_id') and not helper.restricted(record),
            'observation-normalized-record-identity-or-access-mismatch')
    normalized_messages = helper.normalized_message_view(record, '/messages')
    source = provenance['source']
    require(source.get('export_format') == 'discordchatexporter_json' and
            isinstance(source.get('source_sha256'), str) and SHA.fullmatch(source['source_sha256']),
            'observation-original-format-or-pin-unavailable')
    source_value = source.get('archive_path')
    require(isinstance(source_value, str), 'observation-original-locator-unavailable')
    apparent_original = Path(source_value).expanduser()
    archive_root = Path(settings['archive_root'])
    if not apparent_original.is_absolute():
        apparent_original = archive_root / apparent_original
    else:
        for prior in settings.get('source_roots', []):
            prior = Path(prior).expanduser()
            if apparent_original.is_relative_to(prior):
                apparent_original = archive_root / apparent_original.relative_to(prior)
                break
    original_path = helper.source_path(settings, source_value)
    require(apparent_original == original_path, 'observation-original-path-aliased')
    for prefix in settings.get('dense', {}).get('excluded_source_prefixes', []):
        require(not source_value.startswith(prefix) and not str(original_path).startswith(prefix),
                'observation-original-excluded')
    original_raw, original_pin = read_regular(original_path, helper, deadline, SOURCE_LIMIT)
    require(original_pin['sha256'] == source['source_sha256'], 'observation-original-hash-mismatch')
    original_export = strict_json(original_raw)
    originals = original_export.get('messages')
    require(isinstance(originals, list) and len(originals) <= MESSAGE_LIMIT,
            'observation-original-message-container-invalid-or-over-limit')
    matched = {}
    for original in originals:
        checkpoint(deadline)
        if not isinstance(original, dict):
            raise ObservationError('observation-original-message-invalid')
        raw_id = original.get('id')
        if type(raw_id) is int:
            raw_id = str(raw_id)
        if raw_id in approved:
            require(raw_id not in matched, 'observation-original-selected-id-duplicate')
            matched[raw_id] = original
    require(set(matched) == set(approved), 'observation-original-selected-parents-missing')
    observations = []
    for message_id, parent in approved.items():
        checkpoint(deadline)
        index = parent.get('message_index')
        require(type(index) is int and 0 <= index < len(normalized_messages), 'observation-parent-index-invalid')
        normalized = normalized_messages[index]
        original = matched[message_id]
        require(isinstance(normalized, dict) and canonical_id(normalized.get('id')) == message_id
                and not helper.restricted(normalized) and not helper.restricted(original),
                'observation-parent-identity-or-access-mismatch')
        original_author, normalized_author, published_author = (original.get('author'), normalized.get('author'), parent.get('author'))
        require(all(isinstance(a, dict) and not helper.restricted(a) for a in
                    (original_author, normalized_author, published_author)), 'observation-parent-actor-unavailable')
        actor = canonical_id(original_author.get('id'))
        require(actor == canonical_id(normalized_author.get('id')) == canonical_id(published_author.get('id')),
                'observation-parent-actor-mismatch')
        original_text, normalized_text = original.get('content'), normalized.get('content')
        require(isinstance(original_text, str) and isinstance(normalized_text, str),
                'observation-parent-text-format-unsupported')
        text_sha = sha(original_text.encode('utf-8'))
        require(text_sha == parent.get('text_sha256') == sha(normalized_text.encode('utf-8')),
                'observation-parent-text-hash-mismatch')
        require(type(parent.get('char_start')) is int and type(parent.get('char_end')) is int
                and 0 <= parent['char_start'] < parent['char_end'] <= len(original_text),
                'observation-parent-character-span-invalid')
        created = instant(original.get('timestamp'))
        require(created == instant(parent.get('timestamp')) == instant(normalized.get('sent_at')),
                'observation-parent-timestamp-mismatch')
        extra = normalized.get('extra', {})
        require(isinstance(extra, dict), 'observation-normalized-extra-invalid')
        raw_extra = extra.get('raw', {})
        require(isinstance(raw_extra, dict), 'observation-normalized-raw-extra-invalid')
        # Keep direct and preserved raw aliases together without silently overwriting them.
        normalized_edit = edit_observation(normalized)
        preserved_edit = edit_observation(raw_extra)
        if normalized_edit['state'] == 'absent':
            normalized_edit = preserved_edit
        elif preserved_edit['state'] != 'absent' and (normalized_edit['state'], normalized_edit['instant_utc']) != (
                preserved_edit['state'], preserved_edit['instant_utc']):
            normalized_edit = {'state': 'conflict', 'instant_utc': None,
                               'direct': normalized_edit, 'preserved_raw': preserved_edit}
        original_edit = edit_observation(original)
        edit = combined_edit(original_edit, normalized_edit)
        observations.append({'message_id': message_id, 'message_index': index,
            'actor_id': actor, 'original_text_sha256': text_sha, 'parent_text_actor_timestamp_verified': True,
            'published_parent': {'timestamp': parent['timestamp'],
                'timestamp_edited': {'state': 'absent'} if 'timestamp_edited' not in parent else
                    {'state': 'null'} if parent['timestamp_edited'] is None else edit_observation(parent),
                'reply_to': reply_observation(parent)},
            'normalized_observed': {'edit': normalized_edit, 'reply': reply_observation(normalized),
                                    'preserved_raw_reply': reply_observation(raw_extra)},
            'original_observed': {'created_timestamp': original['timestamp'], 'edit': original_edit,
                                  'reply': reply_observation(original)},
            'edit_resolution': edit, 'cutoff': cutoff_decision(created, edit, cutoff_time)})
    result = {'status': 'observed-selected-original-metadata', 'unit_id': unit_id, 'generation': generation,
        'observations': observations, 'source_bodies_returned': False,
        'receipt': {'helper_sha256': HELPER_SHA, 'module_sha256': sha(Path(__file__).read_bytes()),
            'configuration': config_pin, 'normalized_record': {**record_pin, 'record_id': provenance['record_id']},
            'original': original_pin, 'selected_parent_count': len(observations),
            'published_parent_count': len(parents), 'unselected_context_approved': False,
            'scope': 'selected published Discord parents only; exact normalized and freshly pinned original reads',
            'limits': {'source_bytes': SOURCE_LIMIT, 'parents': PARENT_LIMIT,
                'original_messages': MESSAGE_LIMIT, 'output_bytes': OUTPUT_LIMIT, 'budget_seconds': budget,
                'deadline_kind': 'cooperative checks between bounded OS reads and decode operations'},
            'qualifiers': ['Published fields remain distinct from observed metadata.',
                'Original exporter null reports no edit at that capture; it does not prove an unedited history.',
                'Absent or conflicting edit metadata does not establish historical content availability.',
                'Reply target identity is observed; its content, availability and adoption are not verified.']}}
    checkpoint(deadline)
    serialize(result)
    return result


def main():
    parser = argparse.ArgumentParser(description='Observe selected original message metadata.')
    parser.add_argument('--unit-id', required=True)
    parser.add_argument('--generation', required=True)
    parser.add_argument('--message-id', action='append', help='Select a published parent message ID.')
    parser.add_argument('--cutoff', help='Apply an aware ISO8601 content cutoff.')
    parser.add_argument('--config', help='Read the existing private archive configuration.')
    parser.add_argument('--budget', type=float, default=60, help='Bound observation to 1–120 seconds.')
    args = parser.parse_args()
    try:
        result = observe(args.unit_id, args.generation, args.message_id, args.cutoff, args.config, args.budget)
        sys.stdout.buffer.write(serialize(result))
        return 0
    except (ObservationError, OSError, ValueError, TypeError, KeyError, OverflowError, ImportError):
        # Never echo backend diagnostics or arbitrary source field values.
        error = sys.exc_info()[1]
        diagnostic = str(error) if isinstance(error, ObservationError) else 'observation-source-unavailable'
        print(json.dumps({'status': 'unavailable', 'diagnostic': diagnostic,
                          'source_bodies_returned': False, 'original_verified': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

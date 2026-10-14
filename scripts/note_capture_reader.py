"""Verify a selected normalized Note against a pinned restored export.

The caller has already qualified the normalized document and selected parents.
This component adds source coordinates; it does not classify authorship, search,
change a lane, or return another copy of the document body.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import time
import math

MANIFEST_LIMIT = 2 * 1024 * 1024
PART_LIMIT = 64 * 1024 * 1024
SOURCE_LIMIT = 32 * 1024 * 1024
NOTE_LIMIT = 10000
PARENT_LIMIT = 64
UUID = re.compile(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')


class NoteError(ValueError):
    pass


def require(condition, diagnostic):
    if not condition:
        raise NoteError(diagnostic)


def pinned_sha(value):
    require(isinstance(value, str) and SHA.fullmatch(value), 'note-source-pin-invalid')
    return value


def object_field(record, name):
    value = record.get(name)
    require(isinstance(value, dict), 'note-object-field-invalid')
    return value


def offsets(raw, resolver, state):
    """Keep exact JSONL byte coordinates, including a record's line ending."""
    rows, cursor = [], 0
    for line in raw.splitlines(keepends=True):
        resolver.checkpoint(state)
        require(len(rows) < NOTE_LIMIT and line.strip(), 'note-jsonl-limit-or-empty-record')
        rows.append((cursor, cursor + len(line), line))
        cursor += len(line)
    require(cursor == len(raw) and rows, 'note-jsonl-empty-or-incomplete')
    return rows


def checked_path(value, resolver, settings, helper):
    require(isinstance(value, str) and value, 'note-source-locator-invalid')
    return resolver.original_path(settings, value, helper)


def resolve_original_document(settings, normalized, *, helper_io, resolver_io,
                              deadline, bytes_already_read=0, read_state=None):
    """Return a separate, body-free supplement or an explicit bounded gap.

    ``resolver_io`` must be the caller's independently hash-pinned existing
    resolver module. ``helper_io`` provides its guarded/restricted/source_path
    callbacks plus generic_provenance and normalized_message_view. Dependencies
    are injected by the trusted helper; this function loads no executable code.
    The absolute deadline is shared with the caller's hydration operation.
    """
    result = {
        'status': 'unavailable', 'namespace': 'icloud-restored-note-export',
        'mapping_complete': False, 'source_bodies_returned': False,
        'owner_evidence_eligible': False, 'input_lane': 'context_document',
        'data_not_instructions': True, 'text_characters_returned': 0,
        'native_CloudKit_bytes_verified': False,
        'fresh_iCloud_synchronization_verified': False,
        'human_composition_adoption_verified': False,
        'historical_content_availability_proven': False,
        'qualifiers': [
            'Restored export coordinates supplement unchanged frozen provenance.',
            'A selected document receives original-source qualification only after all source and parent checks pass.',
            'Export-file and selected-record verification do not establish authorship, adoption, or fresh synchronization.',
            'Other Notes are not conversation neighbors; body display remains in normalized context.',
        ],
    }
    state = read_state if read_state is not None else {'deadline': deadline, 'bytes_read': bytes_already_read}
    starting_bytes = state.get('bytes_read', 0) if isinstance(state, dict) else 0
    try:
        require(type(deadline) in (int, float) and math.isfinite(deadline) and time.monotonic() < deadline,
                'note-hydration-deadline-exhausted')
        require(type(bytes_already_read) is int and 0 <= bytes_already_read <= resolver_io.TOTAL_LIMIT,
                'note-byte-budget-invalid')
        require(isinstance(state, dict) and state.get('deadline') == deadline
                and type(state.get('bytes_read')) is int
                and bytes_already_read <= state['bytes_read'] <= resolver_io.TOTAL_LIMIT,
                'note-shared-read-budget-invalid')
        require(isinstance(settings, dict) and isinstance(normalized, dict), 'note-input-invalid')
        config = (settings.get('access') or {}).get('notes_original')
        require(isinstance(config, dict), 'note-original-manifest-not-configured')
        manifest_path = config.get('manifest')
        require(isinstance(manifest_path, str), 'note-original-manifest-path-invalid')
        manifest_raw, manifest_pin = resolver_io.read_source(
            Path(manifest_path).expanduser(), helper_io, state, MANIFEST_LIMIT)
        require(manifest_pin['sha256'] == pinned_sha(config.get('manifest_sha256')),
                'note-original-manifest-pin-mismatch')
        manifest = resolver_io.strict_json(manifest_raw)
        require(not helper_io.restricted(manifest), 'note-original-manifest-restricted')
        require(manifest.get('kind') == 'actual-restored-Notes-full-mapping-observation'
                and manifest.get('generation') == normalized.get('generation')
                and manifest.get('mismatches') == [], 'note-original-manifest-scope-mismatch')
        matches = manifest.get('matches')
        require(isinstance(matches, list) and 0 < len(matches) <= NOTE_LIMIT
                and manifest.get('full_mapping_match_count') == len(matches)
                and manifest.get('normalized_note_count') == len(matches),
                'note-original-manifest-mappings-invalid')
        unrepresented = manifest.get('original_unrepresented_notes')
        require(isinstance(unrepresented, list) and len(unrepresented) <= NOTE_LIMIT,
                'note-original-unrepresented-inventory-invalid')
        result['coverage_scope'] = {
            'normalized_notes_in_manifest': len(matches),
            'raw_unrepresented_notes_in_manifest': len(unrepresented),
            'raw_unrepresented_reasons_established': False,
            'all_raw_notes_semantically_indexed': False,
        }
        p = helper_io.generic_provenance(settings, normalized.get('provenance'))
        require(p.get('kind') == 'document' and p.get('platform') == 'icloud'
                and p.get('message_pointer') == '/extra/wrapped_message',
                'note-normalized-format-unsupported')
        require(normalized.get('input_lane') == 'context_document', 'note-normalized-lane-mismatch')
        parents = p.get('parents')
        require(isinstance(parents, list) and 0 < len(parents) <= PARENT_LIMIT,
                'note-parent-count-invalid')
        selected = [entry for entry in matches if isinstance(entry, dict)
                    and entry.get('normalized_id') == p.get('record_id')]
        require(len(selected) == 1, 'note-manifest-record-missing-or-duplicate')
        mapping = selected[0]
        uid = mapping.get('export_uuid')
        require(isinstance(uid, str) and UUID.fullmatch(uid), 'note-export-uuid-invalid')
        require(sum(entry.get('export_uuid') == uid for entry in matches if isinstance(entry, dict)) == 1,
                'note-manifest-uuid-duplicate')
        original = object_field(manifest, 'original')
        frozen = object_field(manifest, 'frozen_normalized_part')
        source = object_field(p, 'source')
        require(source.get('family') == 'icloud_notes', 'note-source-family-unsupported')
        original_path = checked_path(original.get('path'), resolver_io, settings, helper_io)
        require(checked_path(source.get('archive_path'), resolver_io, settings, helper_io) == original_path
                and source.get('source_sha256') == pinned_sha(original.get('sha256')),
                'note-published-source-binding-mismatch')
        part_path = Path(p['normalized_path'])
        require(part_path == Path(frozen.get('path', '')) and part_path.is_absolute(),
                'note-frozen-part-binding-mismatch')
        part_raw, part_pin = resolver_io.read_source(part_path, helper_io, state, PART_LIMIT)
        require(part_pin['sha256'] == pinned_sha(frozen.get('sha256')), 'note-frozen-part-pin-mismatch')
        part_lines = offsets(part_raw, resolver_io, state)
        line_number = mapping.get('normalized_part_line')
        require(type(line_number) is int and 1 <= line_number <= len(part_lines),
                'note-normalized-line-invalid')
        left, right, record_raw = part_lines[line_number - 1]
        require(left == p.get('record_offset') and right - left == p.get('record_bytes')
                and resolver_io.digest(record_raw) == pinned_sha(p.get('record_line_sha256')),
                'note-normalized-byte-record-mismatch')
        record = resolver_io.strict_json(record_raw)
        require(record.get('id') == p.get('record_id') and record.get('kind') == 'document'
                and record.get('type') == 'doc.note', 'note-normalized-record-identity-mismatch')
        extra = object_field(record, 'extra')
        meta = object_field(extra, 'ir_v2_meta')
        wrapped = object_field(extra, 'wrapped_message')
        for obj in (record, extra, meta, wrapped, wrapped.get('extra') or {}):
            require(not helper_io.restricted(obj), 'note-normalized-record-restricted')
        require(meta.get('note_id') == uid and wrapped.get('id') == 'icloud-note:' + uid,
                'note-normalized-uuid-mismatch')
        messages = helper_io.normalized_message_view(record, p['message_pointer'])
        require(len(messages) == 1 and messages[0] == wrapped, 'note-normalized-message-container-mismatch')
        body = object_field(record, 'body').get('text')
        require(isinstance(body, str) and wrapped.get('content') == body,
                'note-normalized-body-mismatch')
        body_hash = resolver_io.digest(body.encode('utf-8'))
        require(body_hash == pinned_sha(mapping.get('body_sha256'))
                and len(body) == mapping.get('body_characters'), 'note-manifest-body-mismatch')
        displayed = normalized.get('messages')
        require(isinstance(displayed, list) and len(displayed) == 1,
                'note-normalized-display-missing-or-invalid')
        display = displayed[0]
        require(isinstance(display, dict) and not helper_io.restricted(display)
                and display.get('message_index') == 0 and display.get('message_id') == wrapped['id']
                and display.get('source_pointer') == '/extra/wrapped_message'
                and display.get('text_sha256') == body_hash and display.get('text_chars') == len(body),
                'note-normalized-display-binding-mismatch')
        shown_range = display.get('text_range')
        require(isinstance(shown_range, dict), 'note-normalized-display-range-missing')
        shown_start, shown_end = shown_range.get('start'), shown_range.get('end')
        require(type(shown_start) is int and type(shown_end) is int
                and 0 <= shown_start <= shown_end <= len(body)
                and display.get('text') == body[shown_start:shown_end],
                'note-normalized-display-text-mismatch')
        parent_matches = []
        for parent in parents:
            resolver_io.checkpoint(state)
            start, end = parent.get('char_start'), parent.get('char_end')
            require(parent.get('message_index') == 0 and parent.get('message_id') == wrapped['id']
                    and parent.get('text_sha256') == body_hash
                    and type(start) is int and type(end) is int and 0 <= start < end <= len(body),
                    'note-selected-parent-mismatch')
            require(parent.get('timestamp') == wrapped.get('sent_at'), 'note-parent-time-mismatch')
            parent_matches.append({'message_id': parent['message_id'], 'message_index': 0,
                                   'text_sha256': body_hash, 'char_start': start, 'char_end': end,
                                   'full_parent_text_match_verified': True,
                                   'matched_span_displayed': shown_start <= start and shown_end >= end})
        raw, original_pin = resolver_io.read_source(original_path, helper_io, state, SOURCE_LIMIT)
        require(original_pin['sha256'] == original['sha256'] and len(raw) == original.get('bytes'),
                'note-original-file-pin-or-size-mismatch')
        raw_lines = offsets(raw, resolver_io, state)
        require(len(raw_lines) == original.get('records'), 'note-original-record-count-mismatch')
        found = []
        for number, (begin, finish, line) in enumerate(raw_lines, 1):
            resolver_io.checkpoint(state)
            candidate = resolver_io.strict_json(line)
            if candidate.get('id') == uid:
                found.append((number, begin, finish, line, candidate))
        require(len(found) == 1, 'note-original-uuid-missing-or-duplicate')
        raw_line_number, begin, finish, raw_record, note = found[0]
        require(not helper_io.restricted(note), 'note-original-record-restricted')
        require(raw_line_number == mapping.get('original_line')
                and [begin, finish] == mapping.get('original_byte_range')
                and resolver_io.digest(raw_record) == pinned_sha(mapping.get('original_record_sha256')),
                'note-original-record-coordinate-or-hash-mismatch')
        title_value = note.get('title') or 'Untitled Note'
        require(isinstance(title_value, str), 'note-original-title-invalid')
        title = title_value.strip() or 'Untitled Note'
        transformed = str(note.get('body') or '').strip() or title
        require(record.get('title') == title and transformed == body, 'note-original-title-or-text-mismatch')
        require(note.get('modificationDate') == meta.get('modificationDate')
                and note.get('creationDate') == meta.get('creationDate'), 'note-original-date-fields-mismatch')
        modified = note.get('modificationDate')
        require(type(modified) is int, 'note-original-modification-date-unsupported')
        stamp = datetime.fromtimestamp(modified / 1000, timezone.utc)
        require(resolver_io.instant(wrapped.get('sent_at')) == stamp
                and resolver_io.instant(object_field(record, 'time').get('started_at')) == stamp,
                'note-normalized-modification-time-mismatch')
        raw_hint = (wrapped.get('extra') or {}).get('raw')
        if isinstance(raw_hint, dict):
            require(not helper_io.restricted(raw_hint), 'note-wrapped-source-hint-restricted')
            for key in ('id', 'note_id'):
                if key in raw_hint:
                    require(raw_hint[key] == uid, 'note-wrapped-source-uuid-mismatch')
        resolver_io.checkpoint(state)
        result.update(status='verified-original-document', mapping_complete=True,
                      manifest_pin=manifest_pin, normalized_part_pin=part_pin,
                      normalized_record={'record_id': record['id'], 'line': line_number,
                                         'byte_range': [left, right], 'sha256': p['record_line_sha256']},
                      original_source_pin=original_pin,
                      original_record={'export_uuid': uid, 'line': raw_line_number,
                                       'byte_range': [begin, finish],
                                       'sha256': resolver_io.digest(raw_record)},
                      body_match={'text_sha256': body_hash, 'characters': len(body),
                                  'transform': 'str(body or empty).strip() or stripped title fallback',
                                  'selected_parent_matches': parent_matches},
                      bytes_read=state['bytes_read'] - starting_bytes,
                      cumulative_source_bytes_read=state['bytes_read'])
        result['qualifiers'].append('Selected normalized body matches this export after the recorded title/body transformation.')
        result['normalized_display'] = {
            'characters': shown_end - shown_start, 'text_range': [shown_start, shown_end],
            'display_matches_original_transformed_body': True,
            'all_matched_spans_displayed': all(match['matched_span_displayed'] for match in parent_matches),
            'duplicate_body_omitted_reason': 'Normalized context already displays this verified source text within the shared text budget.',
        }
        if shown_end == shown_start:
            result['status'] = 'verified-original-document-with-display-gap'
            result['normalized_display']['duplicate_body_omitted_reason'] = 'Shared text budget exhausted; source proof is verified but no document content is displayed.'
            result['display_gap'] = 'no-normalized-document-text-displayed'
    except (NoteError, OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
        result['diagnostic'] = str(error) if isinstance(error, (NoteError, resolver_io.ResolveError)) else 'note-original-supplement-unavailable'
        result['bytes_read'] = state.get('bytes_read', starting_bytes) - starting_bytes if isinstance(state, dict) else 0
    return result

#!/usr/bin/env python3
"""Capture bounded native input metadata without storing source bodies."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
import time
import types
import json

sys.dont_write_bytecode = True
NATIVE_SHA = '224064965aed00cb6dec46aa3ba177a0f9a2b07986894075c9236a3b509576b8'
COMPILER_SHA = 'e01fc29bed81249357ab3f54d70ab7521319274e1a8b6c7c09c3b824b4b8da89'
TOTAL_PREFIX_LIMIT = 256 * 1024 * 1024
THREAD_PREFIX_LIMIT = 128 * 1024 * 1024
SCAN_ROW_LIMIT = 8 * 1024 * 1024
SCAN_RECORD_LIMIT = 200000
CHUNK = 1024 * 1024
# These are native transport categories, not personality or semantic categories.
NONHUMAN_KINDS = frozenset((
    'agents_md.instructions', 'environments.environment_context',
    'additional_content.codex_apps_open_page', 'goal.internal_context', 'user.heartbeat',
))


class RefreshError(ValueError):
    def __init__(self, diagnostic, coverage=None):
        super().__init__(diagnostic)
        self.coverage = coverage or {}


def require(condition, diagnostic, coverage=None):
    if not condition:
        raise RefreshError(diagnostic, coverage)


def pinned_module(name, filename, expected):
    path = Path(__file__).resolve().with_name(filename)
    require(not path.is_symlink(), 'refresh-module-location-invalid')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected, 'refresh-module-hash-mismatch')
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def check_deadline(deadline):
    require(time.monotonic() <= deadline, 'refresh-deadline-exceeded')


def iter_prefix(fd, size, deadline):
    """Read only the captured prefix, preserving complete physical JSONL lines."""
    cursor = 0
    pending = bytearray()
    start = 0
    while cursor < size:
        check_deadline(deadline)
        raw = os.pread(fd, min(CHUNK, size - cursor), cursor)
        require(bool(raw), 'refresh-prefix-shortened')
        cursor += len(raw)
        pending.extend(raw)
        offset = 0
        while True:
            end = pending.find(b'\n', offset)
            if end < 0:
                break
            line = bytes(pending[offset:end + 1])
            require(len(line) <= SCAN_ROW_LIMIT, 'refresh-scan-row-over-limit')
            yield start, start + len(line), line
            start += len(line)
            offset = end + 1
        if offset:
            del pending[:offset]
        require(len(pending) <= SCAN_ROW_LIMIT, 'refresh-scan-row-over-limit')
    require(not pending, 'refresh-captured-eof-not-complete-line',
            {'captured_size_bytes': size, 'complete_line_boundary_bytes': start,
             'partial_suffix_bytes': len(pending), 'coverage_complete': False})


def digest_prefix(fd, size, deadline):
    digest = hashlib.sha256()
    cursor = 0
    while cursor < size:
        check_deadline(deadline)
        raw = os.pread(fd, min(CHUNK, size - cursor), cursor)
        require(bool(raw), 'refresh-prefix-shortened')
        digest.update(raw)
        cursor += len(raw)
    return digest.hexdigest()


def classify(raw, row, start, end, native, inventory):
    payload = row.get('payload')
    if not isinstance(payload, dict):
        return None
    if payload.get('role') != 'user':
        return None
    require(row.get('type') == 'response_item' and payload.get('type') == 'message',
            'refresh-unclassified-user-record-type',
            {'unclassified_user_rows': 1, 'byte_range': [start, end],
             'record_sha256': native._sha(raw), 'coverage_complete': False})
    meta = payload.get('internal_chat_message_metadata_passthrough')
    if isinstance(meta, str):
        meta = native._json(meta)
    kinds = meta.get('content_item_kinds') if isinstance(meta, dict) else None
    if (isinstance(kinds, list) and bool(kinds)
            and all(isinstance(kind, str) and kind in NONHUMAN_KINDS for kind in kinds)):
        content = payload.get('content')
        require(isinstance(content, list) and len(content) == len(kinds)
                and all(isinstance(item, dict) and item.get('type') == 'input_text'
                        and isinstance(item.get('text'), str) for item in content),
                'refresh-control-content-scope-invalid')
        for kind in kinds:
            inventory[kind] = inventory.get(kind, 0) + 1
        return None
    if kinds != ['user.text']:
        raise RefreshError('refresh-unclassified-user-content-kind',
                           {'unclassified_user_rows': 1, 'byte_range': [start, end],
                            'record_sha256': native._sha(raw), 'coverage_complete': False})
    inventory['user.text'] = inventory.get('user.text', 0) + 1
    content = payload.get('content')
    # The shared classifier performs all authoritative actor, block and control checks.
    chars = sum(len(item.get('text', '')) for item in content
                if isinstance(item, dict) and isinstance(item.get('text'), str)) if isinstance(content, list) else 0
    metadata = {'message_id': payload.get('id'), 'timestamp': row.get('timestamp'),
                'byte_start': start, 'byte_end': end, 'record_sha256': native._sha(raw),
                'content_item_kind': 'user.text', 'text_characters': chars,
                'text_pointer': '/payload/content', 'full_record_checked': True,
                'body_stored': False}
    native._metadata(metadata)
    text, _ = native._user_row(raw, metadata)
    return metadata, len(text.encode('utf-8'))


def scan_thread(entry, native, compiler, deadline, budget):
    require(isinstance(entry.get('thread_id'), str) and native._THREAD.fullmatch(entry['thread_id']),
            'refresh-thread-id-invalid')
    require(isinstance(entry.get('header_sha256'), str) and native._SHA.fullmatch(entry['header_sha256'])
            and native._integer(entry.get('observed_size_bytes')),
            'refresh-old-thread-proof-invalid')
    old = entry.get('all_current_user_text_record_metadata')
    require(isinstance(old, list) and bool(old), 'refresh-old-records-missing')
    old_by_start = {}
    last_end = 0
    for item in old:
        native._metadata(item)
        require(last_end <= item['byte_start'] < item['byte_end'] <= entry['observed_size_bytes']
                and item['byte_start'] not in old_by_start, 'refresh-old-record-range-invalid')
        old_by_start[item['byte_start']] = item
        last_end = item['byte_end']
    fd, path = native._open_native(entry['original_native_file'], compiler.guarded)
    try:
        before = os.fstat(fd)
        captured = before.st_size
        require(entry['observed_size_bytes'] <= captured <= THREAD_PREFIX_LIMIT,
                'refresh-prefix-shorter-than-old-or-over-limit')
        budget['prefix_bytes'] += captured
        require(budget['prefix_bytes'] <= TOTAL_PREFIX_LIMIT, 'refresh-total-prefix-over-limit')
        digest = hashlib.sha256()
        rows, inventory, old_seen = [], {}, set()
        count, max_row, header_size = 0, 0, 0
        for start, end, raw in iter_prefix(fd, captured, deadline):
            count += 1
            budget['scan_records'] += 1
            require(budget['scan_records'] <= SCAN_RECORD_LIMIT, 'refresh-scan-record-count-over-limit')
            max_row = max(max_row, len(raw))
            digest.update(raw)
            if start == 0:
                native._header(raw, entry['thread_id'], entry['header_sha256'])
                header_size = end
                continue
            row = native._json(raw)
            found = classify(raw, row, start, end, native, inventory)
            if start in old_by_start:
                pinned = old_by_start[start]
                require(end == pinned['byte_end'] and native._sha(raw) == pinned['record_sha256'],
                        'refresh-old-row-pin-mismatch')
                native._user_row(raw, pinned)
                require(found is not None and found[0] == pinned, 'refresh-old-row-classification-mismatch')
                old_seen.add(start)
            if found:
                metadata, text_bytes = found
                require(metadata['message_id'] not in budget['identities'], 'refresh-user-message-id-duplicate')
                budget['identities'].add(metadata['message_id'])
                budget['records'] += 1
                budget['text_bytes'] += text_bytes
                require(budget['records'] <= native.RECORD_LIMIT, 'refresh-user-record-count-over-limit')
                require(budget['text_bytes'] <= native.TEXT_LIMIT, 'refresh-user-text-over-limit')
                rows.append(metadata)
        require(count > 0 and old_seen == set(old_by_start), 'refresh-old-pinned-rows-not-all-seen')
        first_sha = digest.hexdigest()
        # Reopen through the same guarded path for an independent second observation.
        second_fd, _ = native._open_native(path, compiler.guarded)
        try:
            second = os.fstat(second_fd)
            require((before.st_dev, before.st_ino) == (second.st_dev, second.st_ino)
                    and second.st_size >= captured, 'refresh-source-replaced-or-shortened')
            require(digest_prefix(second_fd, captured, deadline) == first_sha,
                    'refresh-captured-prefix-rewrite')
            prior = entry.get('prefix_snapshot')
            if prior is not None:
                require(isinstance(prior, dict) and prior.get('whole_prefix_authenticated') is True
                        and prior.get('complete_line_boundary_bytes') == entry['observed_size_bytes']
                        and isinstance(prior.get('prefix_sha256'), str)
                        and native._SHA.fullmatch(prior['prefix_sha256']), 'refresh-old-prefix-proof-invalid')
                require(digest_prefix(second_fd, entry['observed_size_bytes'], deadline) == prior['prefix_sha256'],
                        'refresh-old-prefix-pin-mismatch')
            after = os.fstat(second_fd)
            require(after.st_size >= captured, 'refresh-prefix-shortened')
        finally:
            os.close(second_fd)
        check_deadline(deadline)
        return {'thread_id': entry['thread_id'], 'original_native_file': path,
                'header_sha256': entry['header_sha256'], 'observed_size_bytes': captured,
                'observed_mtime_ns': before.st_mtime_ns,
                'all_current_user_text_record_metadata': rows, 'native_kind_inventory': inventory,
                'source_mutability': 'live log; complete captured prefix authenticated twice',
                'source_route_observed': True, 'newer_human_inputs_may_exist_after_observation': True,
                'prefix_snapshot': {'captured_size_bytes': captured,
                    'complete_line_boundary_bytes': captured, 'prefix_sha256': first_sha,
                    'whole_prefix_authenticated': True, 'independent_reread_equal': True,
                    'previous_full_prefix_pin_available': entry.get('prefix_snapshot') is not None,
                    'previous_row_pins_verified': len(old_seen), 'header_byte_range': [0, header_size],
                    'captured_eof_complete': True, 'live_tail_scanned': False,
                    'appended_bytes_observed_after_capture': after.st_size - captured,
                    'scan_row_count': count, 'max_physical_row_bytes': max_row,
                    'unclassified_user_rows': 0, 'declared_text_scope_complete': True,
                    'native_origin_class': 'human_cli',
                    'human_authorship_independently_proven': False, 'adoption_verified': False}}
    finally:
        os.close(fd)


def refresh(reference, threads, derived_root, deadline_seconds=90):
    require(type(deadline_seconds) is int and 1 <= deadline_seconds <= 300,
            'refresh-deadline-invalid')
    native = pinned_module('pye_refresh_native', 'native_context.py', NATIVE_SHA)
    compiler = pinned_module('pye_refresh_compiler', 'continuation.py', COMPILER_SHA)
    deadline = time.monotonic() + deadline_seconds
    require(isinstance(reference, dict) and isinstance(reference.get('path'), str)
            and isinstance(reference.get('sha256'), str) and native._SHA.fullmatch(reference['sha256']),
            'refresh-bookmark-reference-invalid')
    require(isinstance(threads, list) and 0 < len(threads) <= native.THREAD_LIMIT
            and all(isinstance(t, str) and native._THREAD.fullmatch(t) for t in threads)
            and len(set(threads)) == len(threads), 'refresh-selected-threads-invalid')
    raw, receipt = compiler.read_source(reference['path'], derived_root, native.BOOK_LIMIT)
    require(native._sha(raw) == reference['sha256'], 'refresh-bookmark-hash-mismatch')
    book = native._json(raw)
    require(type(book.get('version')) is int and book['version'] == 1
            and isinstance(book.get('bookmarks'), list), 'refresh-bookmark-schema-invalid')
    by_thread = {}
    for entry in book['bookmarks']:
        require(isinstance(entry, dict) and isinstance(entry.get('thread_id'), str)
                and entry['thread_id'] not in by_thread, 'refresh-bookmark-duplicate-or-invalid-thread')
        by_thread[entry['thread_id']] = entry
    require(all(t in by_thread for t in threads), 'refresh-selected-thread-missing')
    budget = {'prefix_bytes': 0, 'scan_records': 0, 'records': 0, 'text_bytes': 0, 'identities': set()}
    updated = [scan_thread(by_thread[t], native, compiler, deadline, budget) for t in threads]
    requested_bytes = len(updated) * native.HEADER_LIMIT + sum(
        row['byte_end'] - row['byte_start'] + 1 for entry in updated
        for row in entry['all_current_user_text_record_metadata'])
    require(requested_bytes <= native.NATIVE_BYTE_LIMIT,
            'refresh-consumer-requested-bytes-over-limit')
    budget['consumer_requested_bytes'] = requested_bytes
    check_deadline(deadline)
    result = {'version': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
              'purpose': 'current-source-continuity-bookmarks', 'bookmarks': updated,
              'refresh_proof': {'previous_descriptor': receipt, 'selected_source_threads': threads,
                  'status': 'snapshot-captured', 'all_old_selected_row_pins_preserved': True,
                  'coverage_scope': 'all exact native user.text rows in selected captured complete prefixes',
                  'newer_human_inputs_may_exist': True, 'state_reference_switched': False,
                  'source_bodies_stored': False, 'native_module_sha256': NATIVE_SHA,
                  'compiler_module_sha256': COMPILER_SHA,
                  'limits': {'total_prefix_bytes': TOTAL_PREFIX_LIMIT,
                      'thread_prefix_bytes': THREAD_PREFIX_LIMIT, 'physical_row_bytes': SCAN_ROW_LIMIT,
                      'scan_records': SCAN_RECORD_LIMIT, 'user_records': native.RECORD_LIMIT,
                      'user_text_bytes': native.TEXT_LIMIT, 'descriptor_bytes': native.BOOK_LIMIT,
                      'consumer_requested_bytes': native.NATIVE_BYTE_LIMIT,
                      'deadline_seconds': deadline_seconds,
                      'deadline_kind': 'cooperative checks between bounded OS reads'},
                  'observed': {k: v for k, v in budget.items() if k != 'identities'},
                  'qualifiers': ['Captured snapshot only; appends after capture are not classified.',
                      'Prior unpinned prefix history is not retrospectively authenticated.',
                      'Native kind and origin do not independently establish human identity or adoption.',
                      'Equality at two observations does not exclude rewrite-and-restore between them.']}}
    rendered = (json.dumps(result, indent=2, ensure_ascii=True) + '\n').encode('utf-8')
    require(len(rendered) <= native.BOOK_LIMIT, 'refresh-descriptor-over-limit')
    return result, rendered


def save_new(raw, path, root, compiler):
    path = Path(path).expanduser()
    require(path.is_absolute() and '..' not in path.parts and path.is_relative_to(root)
            and not compiler.guarded(path) and not compiler.guarded(root), 'refresh-output-path-invalid')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = following
        out = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(fd)
        except BaseException:
            os.unlink(path.name, dir_fd=fd)
            raise
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description='Capture complete native input snapshot metadata.')
    parser.add_argument('--task-id', help='Read the existing task bookmark and thread references.')
    parser.add_argument('--bookmark', help='Read a bookmark under the configured private derived root.')
    parser.add_argument('--sha256', help='Require the input bookmark SHA256.')
    parser.add_argument('--thread', action='append', default=[], help='Select an authorized source thread.')
    parser.add_argument('--deadline', type=int, default=90, help='Bound capture time to 1–300 seconds.')
    parser.add_argument('--output', help='Create a new private metadata descriptor without overwriting.')
    args = parser.parse_args()
    try:
        require(1 <= args.deadline <= 300, 'refresh-deadline-invalid')
        compiler = pinned_module('pye_refresh_cli', 'continuation.py', COMPILER_SHA)
        native = pinned_module('pye_refresh_cli_native', 'native_context.py', NATIVE_SHA)
        root, state_path, _ = compiler.settings()
        if args.task_id:
            require(not args.bookmark and not args.sha256 and not args.thread,
                    'refresh-input-route-conflict')
            state_raw, _ = compiler.read_source(state_path, root, 2 * 1024 * 1024)
            state = native._json(state_raw)
            _, task = compiler.select_task(state, '', args.task_id)
            current = task['current']
            reference, threads = current.get('current_source_bookmarks'), current.get('source_threads')
        else:
            reference, threads = {'path': args.bookmark, 'sha256': args.sha256}, args.thread
        _, raw = refresh(reference, threads, root, args.deadline)
        if args.output:
            save_new(raw, args.output, root, compiler)
            print(json.dumps({'status': 'snapshot-captured', 'path': args.output,
                              'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                              'state_reference_switched': False}))
        else:
            sys.stdout.buffer.write(raw)
        return 0
    except (RefreshError, OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        diagnostic = str(error) if isinstance(error, (RefreshError,)) or type(error).__name__ in (
            'NativeSourceError', 'ContextError') else 'refresh-source-or-output-unavailable'
        print(json.dumps({'status': 'unavailable', 'descriptor_published': False,
                          'diagnostic': diagnostic,
                          'coverage': error.coverage if isinstance(error, RefreshError) else {},
                          'source_bodies_stored': False, 'state_reference_switched': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Read authenticated bookmarked native user rows without scanning session logs."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


BOOK_LIMIT = 256 * 1024
HEADER_LIMIT = 64 * 1024
ROW_LIMIT = 1024 * 1024
TEXT_LIMIT = 128 * 1024
THREAD_LIMIT = 4
RECORD_LIMIT = 64
NATIVE_BYTE_LIMIT = 16 * 1024 * 1024
READ_DEADLINE_SECONDS = 30
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_THREAD = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
_CONTROL_PREFIXES = (
    '# AGENTS.md instructions', '<environment_context>', '<permissions instructions>',
    '<collaboration_mode>', '<multi_agent_role>', '<codex_delegation',
    '<subagent_notification>', '<turn_aborted>', '<INSTRUCTIONS>',
    '<codex_internal_context',
    '<goal>', '<goal_context>', '<automation', 'Message Type: NEW_TASK',
    'Message Type: MESSAGE', 'Message Type: FINAL_ANSWER',
)


class NativeSourceError(ValueError):
    """Fixed, source-free diagnostics only."""


def _require(condition, diagnostic):
    if not condition:
        raise NativeSourceError(diagnostic)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _integer(value):
    return type(value) is int and value >= 0


def _deadline(deadline):
    _require(time.monotonic() <= deadline, 'native-source-read-deadline-exceeded')


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'native-json-duplicate-key')
            result[key] = value
        return result
    def constant(_):
        raise NativeSourceError('native-json-nonfinite')
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError) as error:
        if isinstance(error, NativeSourceError):
            raise
        raise NativeSourceError('native-json-invalid') from None
    _require(isinstance(value, dict), 'native-json-object-required')
    return value


def _header(raw, thread_id, expected_sha):
    _require(raw.endswith(b'\n') and len(raw) <= HEADER_LIMIT,
             'native-header-incomplete-or-over-limit')
    _require(_sha(raw) == expected_sha, 'native-header-hash-mismatch')
    row = _json(raw)
    payload = row.get('payload')
    _require(row.get('type') == 'session_meta' and isinstance(payload, dict),
             'native-first-header-invalid')
    _require(payload.get('id') == thread_id and payload.get('session_id', thread_id) == thread_id,
             'native-header-thread-mismatch')
    # Own first header is authoritative. Copied later headers are never consulted.
    _require(payload.get('source') in ('cli', 'vscode'), 'native-origin-not-human-cli')
    return {'thread_id': thread_id, 'header_sha256': expected_sha,
            'header_byte_range': [0, len(raw)], 'native_origin_class': 'human_cli',
            'origin_classification_basis': 'own first session_meta source cli or vscode',
            'account_identity_exported': False, 'human_authorship_independently_proven': False,
            'adoption_verified': False}


def _user_row(raw, metadata):
    _require(raw.endswith(b'\n') and len(raw) <= ROW_LIMIT,
             'native-row-incomplete-or-over-limit')
    _require(_sha(raw) == metadata['record_sha256'], 'native-row-hash-mismatch')
    row = _json(raw)
    payload = row.get('payload')
    _require(row.get('type') == 'response_item' and isinstance(payload, dict)
             and payload.get('type') == 'message' and payload.get('role') == 'user',
             'native-row-not-user-message')
    _require(payload.get('id') == metadata['message_id']
             and row.get('timestamp') == metadata['timestamp'], 'native-row-identity-mismatch')
    passthrough = payload.get('internal_chat_message_metadata_passthrough')
    if isinstance(passthrough, str):
        passthrough = _json(passthrough)
    _require(isinstance(passthrough, dict)
             and passthrough.get('content_item_kinds') == ['user.text'],
             'native-row-not-authentic-user-text-kind')
    content = payload.get('content')
    _require(isinstance(content, list) and bool(content), 'native-row-content-invalid')
    blocks = []
    for index, item in enumerate(content):
        _require(isinstance(item, dict) and item.get('type') == 'input_text'
                 and isinstance(item.get('text'), str), 'native-row-nontext-content')
        value = item['text']
        _require(not value.lstrip().startswith(_CONTROL_PREFIXES), 'native-row-control-input')
        blocks.append({'text': value, 'text_pointer': f'/payload/content/{index}/text',
                       'text_sha256': _sha(value.encode('utf-8'))})
    _require(sum(len(block['text']) for block in blocks) == metadata['text_characters'],
             'native-row-text-length-mismatch')
    # Exact blocks are authoritative; combined text has no invented separator.
    return ''.join(block['text'] for block in blocks), blocks


def _open_native(path, guarded):
    path = Path(path).expanduser()
    root = Path.home() / '.codex' / 'sessions'
    _require(path.is_absolute() and '..' not in path.parts and path.is_relative_to(root)
             and not guarded(path) and not guarded(root), 'native-path-outside-allowed-root-or-guarded')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        source_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)
    try:
        _require(stat.S_ISREG(os.fstat(source_fd).st_mode), 'native-source-not-regular')
    except BaseException:
        os.close(source_fd)
        raise
    return source_fd, str(path)


def _metadata(value):
    _require(isinstance(value, dict), 'native-record-metadata-invalid')
    _require(isinstance(value.get('message_id'), str) and 1 <= len(value['message_id']) <= 128
             and isinstance(value.get('timestamp'), str) and 1 <= len(value['timestamp']) <= 96,
             'native-record-identity-invalid')
    start, end = value.get('byte_start'), value.get('byte_end')
    _require(_integer(start) and _integer(end) and 0 < end - start <= ROW_LIMIT,
             'native-record-range-invalid')
    _require(isinstance(value.get('record_sha256'), str) and bool(_SHA.fullmatch(value['record_sha256']))
             and value.get('content_item_kind') == 'user.text'
             and value.get('text_pointer') == '/payload/content'
             and value.get('full_record_checked') is True and value.get('body_stored') is False
             and _integer(value.get('text_characters')), 'native-record-qualification-invalid')
    return value


def _read_thread(entry, guarded, deadline):
    thread_id = entry['thread_id']
    metadata = entry['all_current_user_text_record_metadata']
    _deadline(deadline)
    fd, path = _open_native(entry['original_native_file'], guarded)
    try:
        observed = entry['observed_size_bytes']
        before = os.fstat(fd)
        _require(before.st_size >= observed, 'native-source-shorter-than-bookmarked-snapshot')
        # Probe one byte at a time to avoid reading unbookmarked body bytes.
        first = bytearray()
        while len(first) < HEADER_LIMIT:
            if len(first) % 256 == 0:
                _deadline(deadline)
            byte = os.pread(fd, 1, len(first))
            _require(bool(byte), 'native-header-incomplete-or-over-limit')
            first.extend(byte)
            if byte == b'\n':
                break
        _require(first.endswith(b'\n'), 'native-header-incomplete-or-over-limit')
        header_size = len(first)
        header = _header(bytes(first), thread_id, entry['header_sha256'])
        records = []
        previous_end = header_size
        for item in metadata:
            _deadline(deadline)
            start, end = item['byte_start'], item['byte_end']
            _require(previous_end <= start < end <= observed, 'native-record-overlap-or-outside-snapshot')
            _require(os.pread(fd, 1, start - 1) == b'\n', 'native-record-start-not-line-boundary')
            raw = os.pread(fd, end - start, start)
            _require(len(raw) == end - start and raw.count(b'\n') == 1,
                     'native-record-range-not-one-complete-line')
            text, blocks = _user_row(raw, item)
            records.append({'thread_id': thread_id, 'message_id': item['message_id'],
                            'timestamp': item['timestamp'], 'text': text, 'text_blocks': blocks,
                            'classification': 'native human_cli user.text; historical input; adoption unverified',
                            'source': {'path': path, 'header_sha256': entry['header_sha256'],
                                       'record_sha256': item['record_sha256'], 'byte_range': [start, end],
                                       'text_pointer': item['text_pointer'], 'full_record_checked': True,
                                       'text_complete': True, 'truncated': False,
                                       'text_join_convention': 'input_text blocks concatenated with no separator',
                                       'text_sha256': _sha(text.encode('utf-8'))}})
            previous_end = end
        after = os.fstat(fd)
        _deadline(deadline)
        _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                 (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'native-source-changed-during-read')
        return records, {**header, 'path': path, 'requested_record_count': len(metadata),
                         'authenticated_record_count': len(records), 'snapshot_complete': True,
                         'observed_snapshot_size_bytes': observed, 'current_size_bytes': after.st_size,
                         'whole_prefix_authenticated': False, 'live_tail_scanned': False,
                         'newer_human_inputs_may_exist': True,
                         'read_byte_ranges': [[0, header_size]] +
                         [[item['byte_start'], item['byte_end']] for item in metadata]}
    finally:
        os.close(fd)


def load_current_sources(current, derived_root, read_source, guarded):
    """Authenticate all selected bookmarked rows atomically; return no partial bodies."""
    result = {'status': 'not-configured', 'configured': False, 'snapshot_complete': False,
              'live_tail_scanned': False, 'newer_human_inputs_may_exist': True,
              'scope': 'authenticated bookmarked snapshot; not latest', 'records': [],
              'threads': [], 'bookmark': None,
              'qualifiers': ['Current caller input takes precedence over historical native inputs.',
                             'Only pinned rows and the first header are authenticated; unselected prefix and tail are not read.',
                             'Native origin and user.text markers do not establish owner adoption or independently prove human authorship.']}
    if not isinstance(current, dict) or 'current_source_bookmarks' not in current:
        return result
    result.update(status='unavailable', configured=True)
    try:
        deadline = time.monotonic() + READ_DEADLINE_SECONDS
        bookmark = current['current_source_bookmarks']
        _require(isinstance(bookmark, dict) and isinstance(bookmark.get('path'), str)
                 and isinstance(bookmark.get('sha256'), str) and bool(_SHA.fullmatch(bookmark['sha256'])),
                 'native-bookmark-reference-invalid')
        threads = current.get('source_threads')
        _require(isinstance(threads, list) and 0 < len(threads) <= THREAD_LIMIT
                 and all(isinstance(item, str) and _THREAD.fullmatch(item) for item in threads)
                 and len(set(threads)) == len(threads), 'native-selected-threads-invalid')
        raw, receipt = read_source(bookmark['path'], derived_root, BOOK_LIMIT)
        _require(isinstance(raw, bytes) and len(raw) <= BOOK_LIMIT and _sha(raw) == bookmark['sha256'],
                 'native-bookmark-hash-mismatch-or-over-limit')
        book = _json(raw)
        _deadline(deadline)
        _require(type(book.get('version')) is int and book['version'] == 1
                 and isinstance(book.get('bookmarks'), list),
                 'native-bookmark-schema-invalid')
        by_thread = {}
        for entry in book['bookmarks']:
            _require(isinstance(entry, dict) and isinstance(entry.get('thread_id'), str)
                     and entry['thread_id'] not in by_thread, 'native-bookmark-duplicate-or-invalid-thread')
            by_thread[entry['thread_id']] = entry
        selected = []
        identities = set()
        total_records = 0
        for thread_id in threads:
            _require(thread_id in by_thread, 'native-selected-thread-missing')
            entry = by_thread[thread_id]
            _require(isinstance(entry.get('original_native_file'), str)
                     and isinstance(entry.get('header_sha256'), str) and bool(_SHA.fullmatch(entry['header_sha256']))
                     and _integer(entry.get('observed_size_bytes')), 'native-thread-metadata-invalid')
            rows = entry.get('all_current_user_text_record_metadata')
            _require(isinstance(rows, list) and bool(rows), 'native-required-rows-missing')
            total_records += len(rows)
            _require(total_records <= RECORD_LIMIT, 'native-record-count-over-limit')
            for row in rows:
                _metadata(row)
                _require(row['message_id'] not in identities, 'native-record-id-duplicate')
                identities.add(row['message_id'])
            selected.append(entry)
        _require(sum(row['text_characters'] for entry in selected
                     for row in entry['all_current_user_text_record_metadata']) <= TEXT_LIMIT,
                 'native-text-over-limit')
        requested_bytes = len(selected) * HEADER_LIMIT + sum(
            row['byte_end'] - row['byte_start'] + 1 for entry in selected
            for row in entry['all_current_user_text_record_metadata'])
        _require(requested_bytes <= NATIVE_BYTE_LIMIT, 'native-requested-bytes-over-limit')
        records, coverage = [], []
        text_bytes = 0
        for entry in selected:
            hydrated, covered = _read_thread(entry, guarded, deadline)
            text_bytes += sum(len(row['text'].encode('utf-8')) for row in hydrated)
            _require(text_bytes <= TEXT_LIMIT, 'native-text-over-limit')
            records.extend(hydrated)
            coverage.append(covered)
        result.update(status='read', snapshot_complete=True, records=records, threads=coverage,
                      bookmark={'path': receipt['path'], 'sha256': _sha(raw), 'bytes': len(raw),
                                'read_complete': True}, text_bytes=text_bytes,
                      read_limits={'native_bytes_upper_bound': requested_bytes,
                                   'native_byte_limit': NATIVE_BYTE_LIMIT,
                                   'deadline_seconds': READ_DEADLINE_SECONDS,
                                   'deadline_kind': 'cooperative checks between bounded OS reads'})
    except (NativeSourceError, OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        result['diagnostic'] = str(error) if isinstance(error, NativeSourceError) else 'native-source-read-failed'
    return result

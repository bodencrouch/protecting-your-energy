"""Authenticate explicitly located Codex final messages as revisable proposals."""
from __future__ import annotations

from datetime import datetime
import hashlib
import os
import stat
from pathlib import Path
import time
import types


NATIVE_CONTEXT_SHA = '224064965aed00cb6dec46aa3ba177a0f9a2b07986894075c9236a3b509576b8'
MAX_RECORDS = 1
TEXT_LIMIT = 128 * 1024
READ_DEADLINE_SECONDS = 30
_DESCRIPTOR_KEYS = frozenset(('version', 'thread_id', 'original_native_file',
                              'header_sha256', 'observed_size_bytes', 'records',
                              'text_sha256', 'text_characters', 'delivery_status'))
_RECORD_KEYS = frozenset(('byte_start', 'byte_end', 'record_sha256', 'timestamp',
                         'message_id', 'text_sha256', 'text_characters'))


class ProposalSourceError(ValueError):
    """Fixed diagnostics; never include source text or descriptor values."""


def _require(condition, diagnostic):
    if not condition:
        raise ProposalSourceError(diagnostic)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _native_module():
    path = Path(__file__).resolve().with_name('native_context.py')
    _require(not path.is_symlink(), 'proposal-helper-location-invalid')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode), 'proposal-helper-not-regular')
        raw = os.read(fd, 1024 * 1024 + 1)
        after = os.fstat(fd)
        _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                  before.st_ctime_ns) ==
                 (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                  after.st_ctime_ns), 'proposal-helper-changed-during-read')
    finally:
        os.close(fd)
    _require(_sha(raw) == NATIVE_CONTEXT_SHA, 'proposal-helper-hash-mismatch')
    module = types.ModuleType('pye_proposal_native_context')
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def _timestamp(value):
    _require(isinstance(value, str) and 1 <= len(value) <= 96,
             'proposal-record-timestamp-invalid')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ProposalSourceError('proposal-record-timestamp-invalid') from None
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
             'proposal-record-timestamp-invalid')


def _descriptor(value, native):
    _require(isinstance(value, dict) and set(value) == _DESCRIPTOR_KEYS,
             'proposal-descriptor-schema-invalid')
    _require(type(value['version']) is int and value['version'] == 1
             and value['delivery_status'] == 'delivered',
             'proposal-descriptor-version-or-delivery-invalid')
    _require(isinstance(value['thread_id'], str)
             and native._THREAD.fullmatch(value['thread_id']) is not None,
             'proposal-thread-invalid')
    _require(isinstance(value['original_native_file'], str)
             and 0 < len(value['original_native_file']) <= 4096
             and Path(value['original_native_file']).is_absolute(),
             'proposal-path-invalid')
    for key in ('header_sha256', 'text_sha256'):
        _require(isinstance(value[key], str) and native._SHA.fullmatch(value[key]) is not None,
                 'proposal-descriptor-hash-invalid')
    _require(native._integer(value['observed_size_bytes'])
             and value['observed_size_bytes'] > 0
             and native._integer(value['text_characters'])
             and 0 < value['text_characters'] <= TEXT_LIMIT,
             'proposal-descriptor-size-invalid')
    records = value['records']
    _require(isinstance(records, list) and 0 < len(records) <= MAX_RECORDS,
             'proposal-record-count-invalid')
    previous_end = 0
    for record in records:
        _require(isinstance(record, dict) and set(record) == _RECORD_KEYS,
                 'proposal-record-schema-invalid')
        start, end = record['byte_start'], record['byte_end']
        _require(native._integer(start) and native._integer(end)
                 and previous_end <= start < end <= value['observed_size_bytes']
                 and end - start <= native.ROW_LIMIT,
                 'proposal-record-range-invalid')
        previous_end = end
        for key in ('record_sha256', 'text_sha256'):
            _require(isinstance(record[key], str)
                     and native._SHA.fullmatch(record[key]) is not None,
                     'proposal-record-hash-invalid')
        _timestamp(record['timestamp'])
        message_id = record['message_id']
        _require(message_id is None or isinstance(message_id, str)
                 and 1 <= len(message_id) <= 128, 'proposal-record-id-invalid')
        _require(native._integer(record['text_characters'])
                 and 0 < record['text_characters'] <= TEXT_LIMIT,
                 'proposal-record-text-size-invalid')
    _require(sum(record['text_characters'] for record in records) == value['text_characters'],
             'proposal-combined-text-size-invalid')
    return value


def _assistant_row(raw, metadata, native):
    _require(raw.endswith(b'\n') and len(raw) <= native.ROW_LIMIT,
             'proposal-row-incomplete-or-over-limit')
    _require(_sha(raw) == metadata['record_sha256'], 'proposal-row-hash-mismatch')
    row = native._json(raw)
    payload = row.get('payload')
    _require(row.get('type') == 'response_item' and isinstance(payload, dict)
             and payload.get('type') == 'message' and payload.get('role') == 'assistant'
             and payload.get('phase') == 'final_answer'
             and payload.get('channel') in (None, 'final')
             and payload.get('recipient') in (None, 'all'),
             'proposal-row-not-user-facing-assistant-final')
    _require(row.get('timestamp') == metadata['timestamp'], 'proposal-row-timestamp-mismatch')
    _require(payload.get('id') == metadata['message_id']
             and (metadata['message_id'] is not None or 'id' not in payload or payload['id'] is None),
             'proposal-row-id-mismatch')
    content = payload.get('content')
    _require(isinstance(content, list) and bool(content), 'proposal-row-content-invalid')
    blocks = []
    for index, item in enumerate(content):
        _require(isinstance(item, dict) and item.get('type') == 'output_text'
                 and isinstance(item.get('text'), str), 'proposal-row-nontext-content')
        text = item['text']
        blocks.append({'text': text, 'text_pointer': f'/payload/content/{index}/text',
                       'text_sha256': _sha(text.encode('utf-8'))})
    text = ''.join(block['text'] for block in blocks)
    encoded = text.encode('utf-8')
    _require(len(text) == metadata['text_characters'] and len(encoded) <= TEXT_LIMIT,
             'proposal-row-text-length-mismatch-or-over-limit')
    _require(_sha(encoded) == metadata['text_sha256'], 'proposal-row-text-hash-mismatch')
    return text, blocks


def load_proposal(descriptor, guarded, *, deadline_seconds=READ_DEADLINE_SECONDS):
    """Read all explicitly selected final-message rows, or return no partial body.

    Caller-provided delivery status records selection intent; it does not prove
    approval, adoption, publication, quality, or completion. No latest-row scan.
    """
    result = {'status': 'unavailable', 'classification': 'assistant-artifact-proposal',
              'text_complete': False, 'truncated': False,
              'owner_evidence': False, 'adoption_verified': False,
              'task_completion_verified': False, 'records': [],
              'qualifiers': [
                  'Explicitly selected assistant final text is a revisable proposal, not owner evidence or adopted fact.',
                  'Caller delivery status is not independently established by native final-answer metadata.',
                  'Only the first header and selected rows are authenticated; the whole prefix and later tail are not.',
                  'Exact bytes and native role establish source identity, not narrative fidelity or quality.']}
    fd = None
    try:
        _require(callable(guarded), 'proposal-guard-required')
        _require(type(deadline_seconds) in (int, float)
                 and 0 < deadline_seconds <= READ_DEADLINE_SECONDS,
                 'proposal-deadline-invalid')
        deadline = time.monotonic() + deadline_seconds
        native = _native_module()
        native._deadline(deadline)
        value = _descriptor(descriptor, native)
        fd, path = native._open_native(value['original_native_file'], guarded)
        before = os.fstat(fd)
        _require(before.st_size >= value['observed_size_bytes'],
                 'proposal-source-shorter-than-snapshot')
        first = bytearray()
        while len(first) < native.HEADER_LIMIT:
            if len(first) % 256 == 0:
                native._deadline(deadline)
            byte = os.pread(fd, 1, len(first))
            _require(bool(byte), 'proposal-header-incomplete-or-over-limit')
            first.extend(byte)
            if byte == b'\n':
                break
        _require(len(first) <= value['observed_size_bytes'], 'proposal-header-outside-snapshot')
        header = native._header(bytes(first), value['thread_id'], value['header_sha256'])
        previous_end = len(first)
        rows, texts, raw_rows = [], [], []
        for metadata in value['records']:
            native._deadline(deadline)
            start, end = metadata['byte_start'], metadata['byte_end']
            _require(previous_end <= start, 'proposal-record-overlaps-header')
            _require(os.pread(fd, 1, start - 1) == b'\n', 'proposal-record-start-not-line-boundary')
            raw = os.pread(fd, end - start, start)
            _require(len(raw) == end - start and raw.count(b'\n') == 1,
                     'proposal-record-not-one-complete-line')
            text, blocks = _assistant_row(raw, metadata, native)
            texts.append(text)
            raw_rows.append((start, raw))
            rows.append({'message_id': metadata['message_id'], 'timestamp': metadata['timestamp'],
                         'role': 'assistant', 'phase': 'final_answer', 'text_blocks': blocks,
                         'byte_range': [start, end], 'record_sha256': metadata['record_sha256'],
                         'text_sha256': metadata['text_sha256'], 'full_record_checked': True})
            previous_end = end
        text = ''.join(texts)
        text_bytes = text.encode('utf-8')
        _require(len(text_bytes) <= TEXT_LIMIT and len(text) == value['text_characters']
                 and _sha(text_bytes) == value['text_sha256'],
                 'proposal-combined-text-mismatch-or-over-limit')
        # Re-read only authenticated selections. Normal appends are allowed;
        # unselected prefix bytes remain outside the proof on both reads.
        native._deadline(deadline)
        _require(os.pread(fd, len(first), 0) == bytes(first), 'proposal-header-changed-during-read')
        for start, raw in raw_rows:
            native._deadline(deadline)
            _require(os.pread(fd, len(raw), start) == raw
                     and os.pread(fd, 1, start - 1) == b'\n',
                     'proposal-selected-row-changed-during-read')
        after = os.fstat(fd)
        _require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
                 and after.st_size >= before.st_size, 'proposal-source-identity-or-size-changed')
        if after.st_size == before.st_size:
            _require((before.st_mtime_ns, before.st_ctime_ns) ==
                     (after.st_mtime_ns, after.st_ctime_ns), 'proposal-source-changed-during-read')
        check_fd, _ = native._open_native(path, guarded)
        try:
            linked = os.fstat(check_fd)
            _require((after.st_dev, after.st_ino) == (linked.st_dev, linked.st_ino),
                     'proposal-source-path-replaced-during-read')
        finally:
            os.close(check_fd)
        native._deadline(deadline)
        result.update(status='read', text=text, text_complete=True, records=rows,
                      text_sha256=value['text_sha256'], text_characters=len(text), text_bytes=len(text_bytes),
                      delivery_status='delivered',
                      source={**header, 'path': path, 'role': 'assistant', 'phase': 'final_answer',
                              'observed_snapshot_size_bytes': value['observed_size_bytes'],
                              'current_size_bytes': after.st_size,
                              'append_observed_during_read': after.st_size > before.st_size,
                              'whole_prefix_authenticated': False, 'live_tail_scanned': False,
                              'read_byte_ranges': [[0, len(first)]] +
                              [[item['byte_start'], item['byte_end']] for item in value['records']],
                              'text_join_convention': 'output_text blocks and selected rows concatenated with no separator',
                              'selected_ranges_rechecked': True,
                              'unselected_bytes_mutation_checked': False},
                      read_limits={'text_byte_limit': TEXT_LIMIT, 'row_byte_limit': native.ROW_LIMIT,
                                   'record_limit': MAX_RECORDS, 'deadline_seconds': deadline_seconds,
                                   'deadline_kind': 'cooperative checks between bounded OS reads'})
    except (ProposalSourceError, OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        # NativeSourceError diagnostics are also fixed, but only this module's
        # explicit codes are surfaced; foreign exception messages are omitted.
        result['diagnostic'] = str(error) if isinstance(error, ProposalSourceError) else 'proposal-source-read-failed'
    finally:
        if fd is not None:
            os.close(fd)
    return result

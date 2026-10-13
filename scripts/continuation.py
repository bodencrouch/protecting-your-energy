#!/usr/bin/env python3
"""Compile bounded continuity context to stdout using the existing reclaim runtime."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode = True

DEFAULT_ROOT = Path.home() / 'brain-data/upar-dip/derived/protect-your-energy'
CONFIG = DEFAULT_ROOT / 'config.json'
RECLAIM = Path.home() / 'brain-data/upar-dip/tooling/thought-reconstruction/bin/thought-reconstruction'
DISPLAY_SHA = '1b255b77c5e5c5043f04e73753abf5516e717633a7cb6d172d3d0a092e6706f5'
BLOCKED = ('.sealed', '.sanitized', '.seal-', 'request-only', 'request_only',
           '.request-only', 'quarantine', 'credentials', '.staging')
MARKERS = ('.sealing-in-progress', '.request-only', 'REQUEST_ONLY', '.sealed')
NEUTRAL = set('a an and are as at be been can continue continuation do for from go '
              'have i is it last left me my next of on our please project resume '
              'saved step task that the this to up us we were what where with work '
              'off here there again going working stopped previously source linked experience draft proposal account notes current recovered '
              'idea ideas create build make something want need new'.split())


class ContextError(ValueError):
    """Errors with deliberately fixed, non-source-bearing descriptions."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'continuation: invalid command line; use --help\n')


def display_module():
    path = Path(__file__).resolve().with_name('archive_display.py')
    if path.is_symlink() or path.resolve().parent != Path(__file__).resolve().parent:
        raise ContextError('display-module-location-invalid')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DISPLAY_SHA:
        raise ContextError('display-module-hash-mismatch')
    spec = importlib.util.spec_from_file_location('pye_continuation_display', path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module.sanitize_payload


def guarded(path):
    for candidate in (path.absolute(), path.resolve()):
        if any(part.lower().startswith(BLOCKED) for part in candidate.parts):
            return True
        for ancestor in (candidate, *candidate.parents):
            if any((ancestor / marker).exists() for marker in MARKERS):
                return True
    return False


def read_source(path, root, limit):
    """Read one regular file, refusing symlinks at every path component."""
    path = Path(path).expanduser()
    if not path.is_absolute():
        path = root / path
    if '..' in path.parts or guarded(path) or guarded(root):
        raise ContextError('derived-source-guarded')
    try:
        path.relative_to(root)
    except ValueError:
        raise ContextError('derived-source-outside-configured-root') from None
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ContextError('derived-source-outside-configured-root')
    archive = (Path.home() / 'boden_wizard_chat_context').resolve()
    if resolved.is_relative_to(archive) or 'boden_wizard_chat_context' in path.parts:
        raise ContextError('derived-source-is-raw-archive')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        source_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source_fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ContextError('derived-source-not-regular-or-over-read-limit')
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(raw) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ContextError('derived-source-changed-or-over-read-limit')
    finally:
        os.close(fd)
    return raw, {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                 'bytes': len(raw), 'read_byte_range': [0, len(raw)],
                 'read_complete': True, 'read_limit_bytes': limit,
                 'range_convention': 'zero-based bytes, end exclusive; lines one-based inclusive'}


def parse_json(raw):
    def reject_constant(_):
        raise ContextError('nonfinite-json-value')
    return json.loads(raw, parse_constant=reject_constant)


def settings():
    raw, receipt = read_source(CONFIG, DEFAULT_ROOT, 262144)
    config = parse_json(raw)
    if not isinstance(config, dict):
        raise ContextError('configuration-not-object')
    root = Path(config.get('derived_root', str(CONFIG.parent))).expanduser()
    if not root.is_absolute() or '..' in root.parts or guarded(root):
        raise ContextError('configured-derived-root-invalid')
    archive = Path(config.get('owner_archive', str(Path.home() / 'boden_wizard_chat_context'))).expanduser().resolve()
    if root.resolve().is_relative_to(archive) or archive.is_relative_to(root.resolve()):
        raise ContextError('configured-derived-root-is-raw-archive')
    state = config.get('state', str(root / 'state.json'))
    if not isinstance(state, str):
        raise ContextError('configured-state-invalid')
    return root, state, receipt


def words(text):
    return set(re.findall(r'[^\W_]+', text.casefold(), re.UNICODE)) - NEUTRAL


def select_task(state, cue, task_id=None):
    rows = state.get('records', {}).get('task', {})
    if not isinstance(rows, dict) or len(rows) > 512:
        raise ContextError('task-bucket-invalid-or-over-limit')
    if task_id is not None:
        if task_id not in rows:
            raise ContextError('requested-task-id-not-found')
        return {'status': 'selected', 'method': 'explicit-id', 'task_id': task_id}, rows[task_id]
    tokens = words(cue)
    resume = bool(re.search(r'\b(?:continue|resume)\b', cue.casefold()))
    candidates = []
    for key, row in rows.items():
        if not re.fullmatch('[A-Za-z0-9_.:-]{1,96}', key) or not isinstance(row, dict) or not isinstance(row.get('current'), dict):
            continue
        goal = row['current'].get('goal', '')
        if not isinstance(goal, str):
            continue
        shared = tokens & words(key + ' ' + goal)
        if shared:
            coverage = len(shared) / len(tokens)
            candidates.append({'task_id': key, 'goal': goal[:512],
                               'goal_truncated': len(goal) > 512, 'matched_terms': sorted(shared),
                               'salient_cue_coverage': round(coverage, 3),
                               'selection_qualified': (len(shared) >= 2 and coverage >= 0.75)
                               or (resume and shared == tokens)})
    candidates.sort(key=lambda row: row['task_id'])
    if len(candidates) == 1 and candidates[0]['selection_qualified']:
        key = candidates[0]['task_id']
        return {'status': 'selected', 'method': 'lexical-candidate-only',
                'semantic_selection': False, 'task_id': key, 'candidates': candidates}, rows[key]
    return {'status': 'needs-selection' if not tokens or candidates else 'no-matching-continuity-record',
            'method': 'lexical-candidate-only', 'semantic_selection': False,
            'candidates': candidates[:12], 'candidate_count': len(candidates),
            'candidates_truncated': len(candidates) > 12,
            'available_task_count': len(rows), 'selection_option': '--task-id'}, None


def bounded_record(row, task_id, state_receipt):
    if not isinstance(row, dict) or not isinstance(row.get('current'), dict):
        raise ContextError('selected-task-record-invalid')
    current = row['current']
    encoded = json.dumps(current, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > 32768:
        raise ContextError('selected-task-record-over-display-limit')
    versions = row.get('versions', [])
    if not isinstance(versions, list):
        raise ContextError('selected-task-versions-invalid')
    def recorded_at(value):
        if value is not None and (not isinstance(value, str) or len(value) > 96):
            raise ContextError('selected-task-timestamp-invalid')
        return value
    metadata = []
    for index in range(max(0, len(versions) - 4), len(versions)):
        version = versions[index]
        if not isinstance(version, dict):
            raise ContextError('selected-task-version-invalid')
        metadata.append({'index': index, 'recorded_at': recorded_at(version.get('recorded_at')),
                         'data_present': 'data' in version})
    pointer = '/records/task/' + task_id.replace('~', '~0').replace('/', '~1')
    return {'classification': 'saved-current-account; potentially stale', 'current': current,
            'recorded_at': recorded_at(row.get('recorded_at')), 'versions': metadata,
            'version_count': len(versions), 'versions_truncated': len(versions) > 4,
            'prior_version_content_loaded': False, 'stored_correction_bucket_loaded': False,
            'source': {**state_receipt, 'json_pointer': pointer,
                       'projection_sha256': hashlib.sha256(encoded).hexdigest(),
                       'projection_hash_convention': 'UTF-8 Python JSON of full current object'},
            'current_object_complete': True}


def text_slice(raw, receipt, section=None, char_limit=12000):
    text = raw.decode('utf-8')
    start, end = 0, len(text)
    if section is not None:
        if not isinstance(section, str) or not section.strip() or len(section) > 512:
            raise ContextError('artifact-section-invalid')
        headings = list(re.finditer(r'^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$', text, re.MULTILINE))
        found = [heading for heading in headings if heading[2].strip() == section.strip()]
        if len(found) != 1:
            raise ContextError('artifact-section-missing-or-ambiguous')
        heading = found[0]
        start = heading.start()
        end = next((h.start() for h in headings if h.start() > start and len(h[1]) <= len(heading[1])), len(text))
    shown_end = min(end, start + char_limit)
    return {'source': receipt, 'text': text[start:shown_end], 'section': section,
            'selected_byte_range': [len(text[:start].encode()), len(text[:end].encode())],
            'display_byte_range': [len(text[:start].encode()), len(text[:shown_end].encode())],
            'display_line_range': [text[:start].count('\n') + 1,
                                   text[:shown_end].count('\n') + (0 if shown_end and text[shown_end - 1] == '\n' else 1)],
            'truncated': shown_end < end, 'omitted_characters': end - shown_end,
            'display_limit_characters': char_limit}


def derived_context(current, root):
    artifact = {'status': 'not-configured', 'classification': 'artifact-proposal'}
    accounts = []
    path, section = current.get('artifact'), current.get('artifact_section')
    if path:
        try:
            if not isinstance(path, str) or section is None:
                raise ContextError('artifact-requires-path-and-exact-section')
            raw, receipt = read_source(path, root, 524288)
            artifact = {'status': 'read', 'classification': 'artifact-proposal',
                        **text_slice(raw, receipt, section)}
        except (ContextError, OSError, UnicodeError) as error:
            artifact.update(status='unavailable', error=safe_error(error))
    paths = current.get('source_accounts', [])
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise ContextError('source-accounts-invalid')
    for path in paths[:3]:
        try:
            raw, receipt = read_source(path, root, 524288)
            accounts.append({'status': 'read', 'classification': 'derived-source-account; revisable interpretation',
                             **text_slice(raw, receipt, char_limit=8000)})
        except (ContextError, OSError, UnicodeError) as error:
            accounts.append({'status': 'unavailable', 'error': safe_error(error)})
    return artifact, {'items': accounts, 'requested_count': len(paths),
                      'omitted_count': max(0, len(paths) - 3), 'account_limit': 3}


class OwnedChildren:
    """A standalone Linux invocation owns its verified descendants across sessions."""
    def __init__(self):
        self.owner = os.getpid()
        self.handles = {}
        self.count = 0
        self.previous = ctypes.c_int()
        if (sys.platform != 'linux' or not hasattr(os, 'pidfd_open')
                or not hasattr(signal, 'pidfd_send_signal') or not hasattr(os, 'P_PIDFD')):
            raise ContextError('scoped-process-cleanup-unavailable')
        # Refuse embedding in a process with other work: adopted children can then
        # only descend from this invocation, not another thread's subprocesses.
        if len(list(Path('/proc/self/task').iterdir())) != 1 or self.children(self.owner):
            raise ContextError('scoped-process-cleanup-requires-idle-single-threaded-cli')
        probe = os.pidfd_open(self.owner)
        try:
            signal.pidfd_send_signal(probe, 0, None, 0)
        finally:
            os.close(probe)
        self.prctl = ctypes.CDLL(None, use_errno=True).prctl
        self.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                              ctypes.c_ulong, ctypes.c_ulong]
        self.prctl.restype = ctypes.c_int
        if self.prctl(37, ctypes.addressof(self.previous), 0, 0, 0) != 0:
            raise ContextError('scoped-process-cleanup-unavailable')
        if self.prctl(36, 1, 0, 0, 0) != 0:
            raise ContextError('scoped-process-cleanup-unavailable')

    @staticmethod
    def children(pid):
        try:
            return [int(value) for value in Path(f'/proc/{pid}/task/{pid}/children').read_text().split()]
        except FileNotFoundError:
            return []

    @staticmethod
    def identity(pid):
        try:
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            return int(fields[1]), int(fields[19])  # parent PID and kernel start time
        except (FileNotFoundError, ProcessLookupError):
            return None

    def refresh(self):
        queue = [(self.owner, self.identity(self.owner)[1])]
        seen = set()
        while queue:
            parent, parent_start = queue.pop()
            if (parent, parent_start) in seen:
                continue
            seen.add((parent, parent_start))
            identity = self.identity(parent)
            if identity is None or identity[1] != parent_start:
                continue
            for pid in self.children(parent):
                identity = self.identity(pid)
                if identity is None or identity[0] != parent:
                    continue
                key = (pid, identity[1])
                if key not in self.handles:
                    try:
                        handle = os.pidfd_open(pid)
                    except ProcessLookupError:
                        continue
                    confirmed = self.identity(pid)
                    parent_identity = self.identity(parent)
                    if (confirmed is None or confirmed[1] != identity[1]
                            or confirmed[0] not in (parent, self.owner)
                            or (confirmed[0] == parent and (parent_identity is None
                                or parent_identity[1] != parent_start))):
                        os.close(handle)
                        continue
                    self.handles[key] = handle
                    self.count += 1
                queue.append(key)

    def cleanup(self, proc):
        deadline = time.monotonic() + 5
        confirmed = False
        failure = None
        try:
            while time.monotonic() < deadline:
                self.refresh()
                for handle in self.handles.values():
                    try:
                        signal.pidfd_send_signal(handle, signal.SIGKILL, None, 0)
                    except ProcessLookupError:
                        pass
                if proc is not None:
                    proc.poll()  # Reap the canonical root through its Popen owner.
                for (pid, start), handle in list(self.handles.items()):
                    if proc is not None and pid == proc.pid:
                        continue
                    try:
                        os.waitid(os.P_PIDFD, handle, os.WEXITED | os.WNOHANG)
                    except ChildProcessError:
                        pass
                # Kernel child lists include live children and unreaped zombies.
                if not self.children(self.owner):
                    confirmed = True
                    break
                time.sleep(0.02)
        except (OSError, ValueError, IndexError):
            failure = 'scoped-descendant-cleanup-failed'
        finally:
            for handle in self.handles.values():
                os.close(handle)
            if confirmed and self.prctl(36, self.previous.value, 0, 0, 0) != 0:
                confirmed = False
                failure = 'scoped-subreaper-restore-failed'
        return {'mechanism': 'Linux child subreaper, parent/start-time verification and pidfds',
                'confirmed': confirmed, 'owned_process_count': self.count,
                'cross_session_descendants_in_scope': True,
                'error': failure if failure else None}


def canonical_reclaim(cue, queries, unit_ids, timeout):
    command = [str(RECLAIM), 'reclaim', '--stdout', '--limit', '12']
    for query in queries:
        command.extend(['--query', query])
    for unit_id in unit_ids:
        command.extend(['--unit-id', unit_id])
    command.extend(['--', cue])
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    output = bytearray()
    failure = None
    proc = None
    owned = None
    cleanup = None
    started = time.monotonic()
    try:
        owned = OwnedChildren()
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True, env=env)
        owned.refresh()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(proc.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                owned.refresh()
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    failure = 'canonical-timeout'
                    break
                for key, _ in selector.select(min(remaining, 0.25)):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                    elif key.data == 'stdout':
                        output.extend(block)
                        if len(output) > 4194304:
                            failure = 'canonical-stdout-over-limit'
                            break
                if failure:
                    break
            if not failure:
                try:
                    proc.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    failure = 'canonical-timeout'
        if not failure and proc.returncode:
            failure = 'canonical-nonzero-exit'
        if not failure:
            packet = parse_json(bytes(output))
            if not isinstance(packet, dict):
                failure = 'canonical-response-not-object'
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        failure = safe_error(error) if isinstance(error, ContextError) else 'canonical-unavailable-or-invalid-json'
    finally:
        if owned is not None:
            cleanup = owned.cleanup(proc)
            if not cleanup['confirmed']:
                failure = 'scoped-descendant-cleanup-unconfirmed'
        if proc is not None:
            proc.stdout.close()
            proc.stderr.close()
    result = {'status': 'failed' if failure else 'returned', 'complete': False,
              'execution': {'entrypoint': str(RECLAIM), 'delivery': 'stdout',
                            'runtime_configuration': 'inherited canonical configuration',
                            'timeout_seconds': timeout, 'stdout_limit_bytes': 4194304,
                            'exit_code': proc.returncode if proc is not None else None,
                            'seconds': round(time.monotonic() - started, 3),
                            'stderr_displayed': False, 'corrections_forwarded': False}}
    result['execution']['process_tree_cleanup'] = cleanup
    if failure:
        result['error'] = failure
    else:
        result['packet'] = packet
    return result


def corrections(args):
    values = list(args.correction)
    if args.corrections_stdin:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ContextError('corrections-stdin-over-limit')
        supplied = parse_json(raw)
        if isinstance(supplied, dict) and set(supplied) == {'corrections'}:
            supplied = supplied['corrections']
        if not isinstance(supplied, list):
            raise ContextError('corrections-json-requires-list-or-corrections-object')
        values.extend(supplied)
    if len(values) > 8 or any(not isinstance(value, str) or not value.strip() or len(value) > 4096 for value in values):
        raise ContextError('corrections-require-at-most-eight-nonempty-strings-of-4096-characters')
    if sum(len(value.encode()) for value in values) > 16384:
        raise ContextError('corrections-over-total-byte-limit')
    return values


def safe_error(error):
    return str(error) if isinstance(error, ContextError) else 'derived-input-unavailable-or-invalid'


def compile_context(args):
    cue = ' '.join(args.cue).strip()
    if not cue or len(cue) > 4096 or len(args.query) > 6 or any(not q.strip() or len(q) > 2048 for q in args.query):
        raise ContextError('cue-or-query-outside-bounds')
    if len(args.unit_id) > 6 or any(not re.fullmatch('[0-9a-f]{64}', uid) for uid in args.unit_id):
        raise ContextError('unit-ids-require-at-most-six-lowercase-sha256-anchors')
    if args.task_id is not None and not re.fullmatch('[A-Za-z0-9_.:-]{1,96}', args.task_id):
        raise ContextError('task-id-invalid')
    if not 1 <= args.timeout <= 120:
        raise ContextError('timeout-requires-one-to-120-seconds')
    current_corrections = corrections(args)
    root, state_path, config_receipt = settings()
    state_receipt = None
    state_status = 'read'
    try:
        raw, state_receipt = read_source(state_path, root, 16777216)
        state = parse_json(raw)
    except FileNotFoundError:
        state = {'version': 1, 'revision': 0, 'records': {}}
        state_status = 'not-created'
    if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('records'), dict):
        raise ContextError('continuity-state-schema-invalid')
    selection, row = select_task(state, cue, args.task_id)
    current_record = None
    artifact = {'status': 'not-selected', 'classification': 'artifact-proposal'}
    accounts = {'items': [], 'requested_count': 0, 'omitted_count': 0, 'account_limit': 3}
    if row is not None:
        current_record = bounded_record(row, selection['task_id'], state_receipt)
        artifact, accounts = derived_context(current_record['current'], root)
    substantive = bool(words(cue)) or bool(args.query) or bool(args.unit_id) or row is not None
    evidence = canonical_reclaim(cue, args.query, args.unit_id, args.timeout) if substantive else {
        'status': 'not-attempted', 'reason': 'unqualified-resume-requires-selection', 'complete': False}
    return {'version': 1, 'kind': 'continuation-context', 'complete': False,
            'current_request': {'cue': cue, 'classification': 'caller-supplied-current-request; author/adoption not verified by compiler',
                                'corrections': current_corrections, 'corrections_storage': 'RAM-only'},
            'selection': selection, 'current_record': current_record,
            'selected_artifact': artifact, 'source_accounts': accounts,
            'canonical_evidence': evidence,
            'continuity_source': {'state': state_status, 'revision': state.get('revision'),
                                  'source': state_receipt, 'configuration': config_receipt},
            'qualifiers': ['Context only; no finished artifact or task completion established.',
                           'Current request and corrections remain separate from saved accounts and source facts.',
                           'Task selection is lexical and does not establish semantic applicability.',
                           'Saved artifact is a proposal; derived accounts are interpretations, not additional owner evidence.',
                           'Canonical packet retains attribution, coverage and limitations; retrieval is incomplete.',
                           'Source hashes and ranges describe pre-render bytes; credential rendering may change displayed text.',
                           'No continuity writes, output files, provider selection, hooks or new stores.']}


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument('cue', nargs='+', help='Current ordinary request or rough retrieval cue')
    parser.add_argument('--task-id', help='Select one existing task explicitly')
    parser.add_argument('--query', action='append', default=[], help='Forward an exact retrieval facet; at most six')
    parser.add_argument('--unit-id', action='append', default=[], help='Forward a lowercase SHA256 source anchor; at most six')
    parser.add_argument('--correction', action='append', default=[], help='Current correction kept in memory')
    parser.add_argument('--corrections-stdin', action='store_true', help='Read a JSON list of correction strings, or {"corrections": [...]}')
    parser.add_argument('--timeout', type=float, default=90, help='Canonical subprocess deadline in seconds, 1..120')
    args = parser.parse_args(argv)
    try:
        sanitize = display_module()
        packet = compile_context(args)
        print(json.dumps(sanitize(packet), ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        print(json.dumps({'version': 1, 'kind': 'continuation-context', 'complete': False,
                          'status': 'failed', 'error': safe_error(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

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
import types

sys.dont_write_bytecode = True

DEFAULT_ROOT = Path.home() / 'brain-data/upar-dip/derived/protect-your-energy'
CONFIG = DEFAULT_ROOT / 'config.json'
RECLAIM = Path.home() / 'brain-data/upar-dip/tooling/thought-reconstruction/bin/thought-reconstruction'
DISPLAY_SHA = '1b255b77c5e5c5043f04e73753abf5516e717633a7cb6d172d3d0a092e6706f5'
NATIVE_CONTEXT_SHA = '224064965aed00cb6dec46aa3ba177a0f9a2b07986894075c9236a3b509576b8'
NATIVE_REFRESH_SHA = '266b269dc1e2114f89141cfb8e86d33df5b57f506b7dee06cd9aeed9ae4f901a'
CHAT_PROPOSAL_SHA = '0430f17e6ee0926111fda04f040a6f04b164d88f55a591877dcde66064cd0128'
EXECUTION_CONTEXT_SHA = '60040287a36429b06d693a7df52bf7023f516311a31307f0a752221fe7c12f25'
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


def proposal_sources_module():
    path = Path(__file__).resolve().with_name('chat_proposal.py')
    if path.is_symlink() or path.resolve().parent != Path(__file__).resolve().parent:
        raise ContextError('proposal-module-location-invalid')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CHAT_PROPOSAL_SHA:
        raise ContextError('proposal-module-hash-mismatch')
    module = types.ModuleType('pye_chat_proposal')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module.load_proposal


def execution_context_module():
    path = Path(__file__).resolve().with_name('execution_context.py')
    if path.is_symlink() or path.resolve().parent != Path(__file__).resolve().parent:
        raise ContextError('execution-context-module-location-invalid')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXECUTION_CONTEXT_SHA:
        raise ContextError('execution-context-module-hash-mismatch')
    module = types.ModuleType('pye_execution_context')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module.resolve_execution_context


def native_sources_module():
    path = Path(__file__).resolve().with_name('native_context.py')
    if path.is_symlink() or path.resolve().parent != Path(__file__).resolve().parent:
        raise ContextError('native-context-module-location-invalid')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != NATIVE_CONTEXT_SHA:
        raise ContextError('native-context-module-hash-mismatch')
    spec = importlib.util.spec_from_file_location('pye_native_context', path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module.load_current_sources


def native_refresh_module():
    path = Path(__file__).resolve().with_name('refresh_native_bookmarks.py')
    if path.is_symlink() or path.resolve().parent != Path(__file__).resolve().parent:
        raise ContextError('native-refresh-module-location-invalid')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != NATIVE_REFRESH_SHA:
        raise ContextError('native-refresh-module-hash-mismatch')
    spec = importlib.util.spec_from_file_location('pye_continuation_refresh', path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def current_native_sources(current, root):
    """Refresh metadata in RAM before authenticating selected original input rows."""
    hydrate = native_sources_module()
    if not isinstance(current, dict) or 'current_source_bookmarks' not in current:
        return hydrate(current, root, read_source, guarded)
    try:
        refresh = native_refresh_module()
        compiler_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        book, raw = refresh.refresh(
            current['current_source_bookmarks'], current.get('source_threads'), root,
            deadline_seconds=30,
            compiler_io=types.SimpleNamespace(read_source=read_source, guarded=guarded),
            compiler_sha256=compiler_sha)
        identity = 'ram:current-native-prefix-snapshot'
        digest = hashlib.sha256(raw).hexdigest()

        def read_book(path, derived_root, limit):
            if path != identity or derived_root != root or len(raw) > limit:
                raise ContextError('native-refresh-ram-reference-invalid')
            return raw, {'path': identity, 'sha256': digest, 'bytes': len(raw),
                         'storage': 'RAM-only; no corresponding file'}

        snapshot = {**current, 'current_source_bookmarks': {'path': identity, 'sha256': digest}}
        result = hydrate(snapshot, root, read_book, guarded)
        result['freshness'] = {'status': 'snapshot-captured',
                               'descriptor': {'path': identity, 'sha256': digest, 'bytes': len(raw)},
                               'observed_at': book['observed_at'],
                               'proof': book['refresh_proof'],
                               'prefixes': [{'thread_id': entry['thread_id'],
                                             'prefix_snapshot': entry['prefix_snapshot']}
                                            for entry in book['bookmarks']],
                               'descriptor_stored': False, 'state_reference_switched': False,
                               'helper_sha256': NATIVE_REFRESH_SHA}
        result['scope'] = 'selected captured complete native prefixes; later appends unverified'
        result['qualifiers'] = [
            'Current caller input takes precedence over historical native inputs.',
            'Freshness proof authenticates captured prefixes; hydration separately authenticates exact input rows.',
            'Appends after capture remain outside this snapshot; no perpetual latest-input guarantee.',
            'Native markers do not independently prove human identity or owner adoption.']
        return result
    except (ValueError, OSError, TypeError, KeyError, OverflowError):
        return {'status': 'unavailable', 'configured': True, 'snapshot_complete': False,
                'records': [], 'threads': [], 'bookmark': None,
                'diagnostic': 'required-native-prefix-refresh-failed',
                'freshness': {'status': 'unavailable', 'descriptor_stored': False,
                              'state_reference_switched': False},
                'live_tail_scanned': False, 'newer_human_inputs_may_exist': True,
                'qualifiers': ['Required refresh failed; no stale fallback or partial input bodies returned.']}


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


def is_bare_resume(cue):
    """Recognize only a bounded bare resume, with optional polite punctuation."""
    return (isinstance(cue, str) and len(cue) <= 4096 and not words(cue)
            and bool(re.fullmatch(
                r'\s*(?:please\s+)?(?:continue|resume)(?:\s+please)?[.!?…\s]*',
                cue.casefold())))


def select_for_request(state, cue, args):
    """Use an explicit execution binding only for an unqualified resume."""
    if args.task_id is not None:
        return select_task(state, cue, args.task_id)
    unqualified = is_bare_resume(cue)
    if not unqualified:
        return select_task(state, cue)
    harness = getattr(args, 'execution_harness', None)
    context_id = getattr(args, 'execution_context_id', None)
    if harness is not None or context_id is not None:
        if harness is None or context_id is None:
            raise ContextError('explicit-execution-context-incomplete')
    else:
        context_id = os.environ.get('CODEX_THREAD_ID') or None
        harness = 'codex' if context_id is not None else None
    if harness is None and context_id is None:
        return select_task(state, cue)
    selection, row = execution_context_module()(
        state, harness=harness, context_id=context_id, unqualified_resume=True)
    if selection['status'] == 'not-applicable' and selection.get('lexical_fallback') is True:
        return select_task(state, cue)
    if selection['status'] == 'selected' and row is not None:
        return selection, row
    if selection['status'] == 'needs-selection':
        raise ContextError('execution-association-ambiguous')
    raise ContextError('execution-context-selection-invalid')


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
    chat = current.get('chat_artifact')
    configured_proposal = isinstance(chat, dict) and 'native_proposal' in chat
    if configured_proposal:
        artifact.update(status='unavailable', configured=True, required=True,
                        classification='assistant-artifact-proposal', reference_kind='native-final-message')
        try:
            if path:
                raise ContextError('artifact-source-selection-ambiguous')
            native = proposal_sources_module()(chat['native_proposal'], guarded)
            if native.get('status') != 'read':
                artifact['error'] = 'required-selected-proposal-unavailable'
            else:
                text = native['text'].encode('utf-8')
                receipt = {'sha256': native['text_sha256'], 'bytes': len(text),
                           'hash_scope': 'UTF-8 output_text content concatenated without a separator',
                           'coordinate_scope': 'decoded proposal text; not native JSONL file offsets',
                           'native_source': native['source'],
                           'native_records': [{key: row.get(key) for key in (
                               'message_id', 'timestamp', 'role', 'phase', 'byte_range',
                               'record_sha256', 'text_sha256', 'full_record_checked')}
                               for row in native['records']]}
                artifact.update(status='read', owner_evidence=False, adoption_verified=False,
                                task_completion_verified=False, qualifiers=native['qualifiers'],
                                **text_slice(text, receipt, chat.get('section'), char_limit=128 * 1024))
        except (ContextError, OSError, UnicodeError) as error:
            artifact.update(status='unavailable', error=safe_error(error))
    elif path:
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


def compact_packet(packet):
    """Reading view of verified context, with source text and critical qualifiers."""
    omitted, texts, paths, parents, messages, actors, details = {}, {}, {}, {}, {}, {}, {}
    text_ids, path_ids, parent_ids, message_ids, actor_ids, detail_ids = {}, {}, {}, {}, {}, {}
    protocol = {'observations', 'snapshot_observation', 'part_bytes', 'part_mtime_ns',
                'part_sha256', 'encoded_tokens', 'observed_at', 'retrieval_score',
                'retrieval_reasons', 'score', 'similarity', 'matched_query',
                'parent_version_key', 'raw_observation_version_sha256',
                'payload_container_pointer'}
    presentation = {'avatarUrl', 'color', 'discriminator', 'roles'}
    source_paths = {'source', 'recorded_source', 'normalized_path', 'part', 'archive_path',
                    'origin_path', 'source_export_path', 'captured_asset_path', 'sidecar_path'}
    text_fields = {'text', 'quote', 'preview', 'summary', 'request', 'query', 'queries',
                   'queries_used', 'queries_requested', 'requires', 'limitations',
                   'interpretation', 'attribution_scope', 'media_context'}
    instance_fields = {'indexed_source_parent', 'indexed_owner_parent', 'matched_span', 'matched_span_covered'}
    qualification = re.compile(r'role|attribution|evidence|class|kind|primary|confirm|verif|'
                               r'uncertain|conflict|status|state|error|fail|valid|complete|'
                               r'truncat|exclud|missing|unresolved|scope|limit|reason|'
                               r'interpret|require|known|approval|adoption|match|result|exit|return|code', re.I)

    def encoded(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(',', ':')).encode()

    def digest(value):
        return hashlib.sha256(encoded(value)).hexdigest()

    def pointer(parent, key):
        return parent + '/' + str(key).replace('~', '~0').replace('/', '~1')

    def omit(value, location, field):
        item = omitted.setdefault(field, {'occurrences': 0, 'hasher': hashlib.sha256()})
        item['occurrences'] += 1
        item['hasher'].update(encoded([location, value]))
        item['hasher'].update(b'\0')

    def qualifier_summary(value):
        found = {}
        def walk(row):
            if isinstance(row, dict):
                for key, child in row.items():
                    if isinstance(child, bool) or (qualification.search(key) and not isinstance(child, (dict, list))):
                        found.setdefault(key, {})[digest(child)] = child
                    elif isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(row, list):
                for child in row:
                    walk(child)
        walk(value)
        return {key: list(values.values()) for key, values in found.items()}

    def identity_value(value, field=None):
        if isinstance(value, dict):
            return {key: identity_value(child, key) for key, child in value.items()
                    if key not in protocol and not (field in ('author', 'original_normalized_author') and key in presentation)}
        if isinstance(value, list):
            return [identity_value(child, field) for child in value]
        return value

    def project(value, location='', field=None, bypass_detail=False):
        if isinstance(value, str) and field in text_fields:
            identity = hashlib.sha256(value.encode()).hexdigest()
            if identity in text_ids:
                return {'text_ref': text_ids[identity]}
            key = 't' + str(len(text_ids) + 1)
            text_ids[identity] = key
            texts[key] = {'first_view_pointer': location}
            return value
        if isinstance(value, str) and field in source_paths:
            if value not in path_ids:
                key = 's' + str(len(path_ids) + 1)
                path_ids[value] = key
                paths[key] = value
            return {'source_path_ref': path_ids[value]}
        if field in ('parents', 'parent_locators') and isinstance(value, list):
            references = []
            for index, row in enumerate(value):
                identity = digest(identity_value(row))
                if identity not in parent_ids:
                    key = 'p' + str(len(parent_ids) + 1)
                    parent_ids[identity] = key
                    parents[key] = project(row, pointer(location, index))
                references.append(parent_ids[identity])
            return {'parent_refs': references}
        if field == 'messages' and isinstance(value, list):
            references = []
            for index, row in enumerate(value):
                if not isinstance(row, dict):
                    references.append(project(row, pointer(location, index)))
                    continue
                body = {key: child for key, child in row.items() if key not in instance_fields}
                identity = digest(identity_value(body))
                if identity not in message_ids:
                    key = 'm' + str(len(message_ids) + 1)
                    message_ids[identity] = key
                    messages[key] = project(body, pointer(location, index))
                references.append({'message_ref': message_ids[identity],
                                   **{key: row[key] for key in instance_fields if key in row}})
            return references
        if field in ('author', 'original_normalized_author') and isinstance(value, dict):
            identity = digest(identity_value(value, field))
            if identity not in actor_ids:
                key = 'a' + str(len(actor_ids) + 1)
                actor_ids[identity] = key
                actors[key] = {name: project(child, pointer(location, name), name)
                               for name, child in value.items() if name not in presentation}
            for name in presentation & set(value):
                omit(value[name], pointer(location, name), name)
            return {'actor_ref': actor_ids[identity]}
        if not bypass_detail and field in ('derived_media', 'derived_media_context', 'media_derivations', 'media_context') and isinstance(value, (dict, list)):
            identity = digest(identity_value(value))
            if identity not in detail_ids:
                key = 'd' + str(len(detail_ids) + 1)
                detail_ids[identity] = key
                details[key] = project(value, location, field, bypass_detail=True)
            return {'media_detail_ref': detail_ids[identity]}
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                child_location = pointer(location, key)
                if key in protocol or (field in ('author', 'original_normalized_author') and key in presentation) or (field == 'media_context' and key in ('attachments', 'embeds', 'stickers')):
                    omit(child, child_location, key)
                    if isinstance(child, (dict, list)):
                        flags = qualifier_summary(child)
                        if flags:
                            result[key + '_qualification'] = flags
                    continue
                if field == 'display_redaction' and key == 'fields':
                    omit(child, child_location, 'display_redaction.fields')
                    result['field_receipt_count'] = len(child) if isinstance(child, list) else None
                elif field == 'thought_leads' and key in ('prior_interpretation', 'related'):
                    omit(child, child_location, 'generated.' + key)
                    result[key] = {'detail_in_full_view': True, 'classification': 'prior generated interpretation; not source evidence'}
                else:
                    result[key] = project(child, child_location, key)
            if field == 'display_redaction':
                result['pointer_scope'] = 'Original producer packet before compact projection and anchor ordering.'
            return result
        if isinstance(value, list):
            return [project(child, pointer(location, index), field=field) for index, child in enumerate(value)]
        return value

    # Project anchors before other evidence, retaining original producer indices
    # for receipts. This also creates their actual inline text/registry rows first.
    originals = packet.get('discovery_originals')
    if isinstance(originals, list):
        ordered = sorted(enumerate(originals), key=lambda pair:
            0 if isinstance(pair[1], dict) and pair[1].get('source_selection') == 'explicit-unit-anchor' else 1)
        result = {'discovery_originals': [project(row, pointer('/discovery_originals', index))
                                         for index, row in ordered]}
        result.update({key: project(value, pointer('', key), key) for key, value in packet.items()
                       if key != 'discovery_originals'})
    else:
        result = project(packet)
    result['source_paths'] = paths
    def table(rows):
        fields = list(dict.fromkeys(key for row in rows.values() for key in row))
        return {'fields': fields, 'rows': {key: [row.get(field, {'field_not_returned': True}) for field in fields]
                                         for key, row in rows.items()}}
    result['source_parents'] = table(parents)
    result['source_messages'] = table(messages)
    result['source_actors'] = actors
    result['media_details'] = details
    if isinstance(originals, list):
        result = {'discovery_originals': result['discovery_originals'],
                  'source_messages': result['source_messages'],
                  **{key: value for key, value in result.items()
                     if key not in ('discovery_originals', 'source_messages')}}
    # Text locations describe the final view, including consolidated registries.
    located = set()
    def locate(value, location='', field=None):
        if isinstance(value, str) and field in text_fields:
            identity = hashlib.sha256(value.encode()).hexdigest()
            key = text_ids.get(identity)
            if key is not None and key not in located:
                texts[key]['first_view_pointer'] = location
                located.add(key)
        elif isinstance(value, dict):
            for key, child in value.items():
                locate(child, pointer(location, key), key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                table_field = field
                for registry in ('source_messages', 'source_parents'):
                    if re.fullmatch('/' + registry + '/rows/[^/]+', location):
                        table_field = result[registry]['fields'][index]
                locate(child, pointer(location, index), table_field)
    locate(result)
    result['source_text_references'] = texts
    # Receipts count the original packet, not just deduplicated rendered copies.
    omitted.clear()
    def count_omissions(value, location='', field=None):
        if isinstance(value, dict):
            for key, child in value.items():
                child_location = pointer(location, key)
                if key in protocol or (field in ('author', 'original_normalized_author') and key in presentation) or (field == 'media_context' and key in ('attachments', 'embeds', 'stickers')):
                    omit(child, child_location, key)
                elif field == 'display_redaction' and key == 'fields':
                    omit(child, child_location, 'display_redaction.fields')
                elif field == 'thought_leads' and key in ('prior_interpretation', 'related'):
                    omit(child, child_location, 'generated.' + key)
                else:
                    count_omissions(child, child_location, key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                count_omissions(child, pointer(location, index), field)
    count_omissions(packet)
    result['context_view'] = {
        'kind': 'agent-reading', 'complete': False,
        'canonical_display_packet_sha256': digest(packet),
        'canonical_display_packet_bytes': len(json.dumps(packet, ensure_ascii=False,
                                                         separators=(',', ':')).encode()),
        'hash_scope': 'Canonical returned JSON values after producer verification and display sanitization; not raw source bytes.',
        'omitted_metadata_fields': {field: {'occurrences': row['occurrences'],
                                          'original_values_and_pointers_sha256': row['hasher'].hexdigest()}
                                    for field, row in omitted.items()},
        'omitted_pointer_scope': 'Pointers address the original producer packet, before compact projection or anchor ordering.',
        'references': 'Resolve source_path_ref in source_paths, parent_refs in source_parents/rows, message_ref in source_messages/rows, actor_ref in source_actors, media_detail_ref in media_details. Parent/message row columns follow each registry fields array; field_not_returned marks absence rather than a false result. text_ref resolves through source_text_references/first_view_pointer to exact inline text in the final view. Message-instance owner/source-parent flags stay with each unit reference.',
        'text_policy': 'All returned source messages, evidence quotes, context turns, lead previews and qualified media text retained without new truncation. Duplicate text is shown once with explicit references. Generated prior interpretations are expansion frontiers, not primary source text.',
        'metadata_policy': 'This reading view preserves critical source identity, parent spans/full text hashes, attribution, verification failures and flags; it does not promise full-value metadata roundtrip. Protocol ledgers and presentation fields remain available with --full.',
        'inspection': 'Use --full with the same cue, facets and anchors for complete canonical metadata; a new run can differ.',
        'attribution': 'Existing role, source-parent, owner-parent and authorship flags remain authoritative only within their original scope; no adoption inferred.',
        'coordinate_scope': 'Message ranges, text hashes and truncation remain the producer values before display redaction.'}
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


def retrieval_plan(cue, queries, probes):
    """Keep supplied search hypotheses distinct from explicit facets and evidence."""
    if (not isinstance(probes, list) or len(probes) > 2
            or any(not isinstance(probe, str) or not probe.strip() or len(probe) > 2048
                   for probe in probes)):
        raise ContextError('retrieval-probes-require-at-most-two-bounded-strings')
    selected = list(queries)
    seen = {cue.strip(), *(query.strip() for query in queries)}
    hypotheses = []
    for probe in probes:
        text = probe.strip()
        if text in seen:
            status = 'duplicate-suppressed'
        elif len(selected) >= 6:
            status = 'omitted-explicit-facet-budget'
            seen.add(text)
        else:
            status = 'selected'
            selected.append(text)
            seen.add(text)
        hypotheses.append({'query': text, 'status': status,
                           'classification': 'supplied search hypothesis; not an owner fact or requirement'})
    return selected, {'original_cue_unchanged': True, 'explicit_facets': list(queries),
                      'hypotheses': hypotheses, 'selected_facet_count': len(selected),
                      'planned_query_count_including_cue': len(dict.fromkeys([cue] + selected)),
                      'facet_scope': 'supplementary facets; original cue is separate',
                      'dispatch_status': 'not-attempted',
                      'probe_limit': 2, 'total_facet_limit': 6,
                      'qualifiers': ['The compiler does not independently verify who formulated a probe.',
                                     'Retrieved hits do not confirm a hypothesis; inspect their actual context.',
                                     'Speculative search wording must not become source facts or artifact requirements.']}


def prepare_context(args):
    """Capture current context once, without canonical retrieval or generation."""
    cue = ' '.join(args.cue).strip()
    if not cue or len(cue) > 4096 or len(args.query) > 6 or any(not q.strip() or len(q) > 2048 for q in args.query):
        raise ContextError('cue-or-query-outside-bounds')
    if len(args.unit_id) > 6 or any(not re.fullmatch('[0-9a-f]{64}', uid) for uid in args.unit_id):
        raise ContextError('unit-ids-require-at-most-six-lowercase-sha256-anchors')
    if args.task_id is not None and not re.fullmatch('[A-Za-z0-9_.:-]{1,96}', args.task_id):
        raise ContextError('task-id-invalid')
    if not 1 <= args.timeout <= 120:
        raise ContextError('timeout-requires-one-to-120-seconds')
    queries, query_plan = retrieval_plan(cue, args.query, getattr(args, 'probe', []))
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
    selection, row = select_for_request(state, cue, args)
    current_record = None
    artifact = {'status': 'not-selected', 'classification': 'artifact-proposal'}
    accounts = {'items': [], 'requested_count': 0, 'omitted_count': 0, 'account_limit': 3}
    if row is not None:
        current_record = bounded_record(row, selection['task_id'], state_receipt)
        artifact, accounts = derived_context(current_record['current'], root)
    native_sources = current_native_sources(current_record['current'] if current_record else {}, root)
    evidence = {'status': 'not-attempted', 'reason': 'current-context-prepared', 'complete': False}
    return {'version': 1, 'kind': 'continuation-context', 'complete': False,
            'current_request': {'cue': cue, 'classification': 'caller-supplied-current-request; author/adoption not verified by compiler',
                                'corrections': current_corrections, 'corrections_storage': 'RAM-only'},
            'selection': selection, 'current_record': current_record,
            'selected_artifact': artifact, 'source_accounts': accounts,
            'current_native_sources': native_sources,
            'query_plan': query_plan,
            'canonical_evidence': evidence,
            'continuity_source': {'state': state_status, 'revision': state.get('revision'),
                                  'source': state_receipt, 'configuration': config_receipt},
            'qualifiers': ['Context only; no finished artifact or task completion established.',
                           'Current request and corrections remain separate from saved accounts and source facts.',
                           'Native source records authenticate a refreshed captured snapshot; later appends and adoption remain unverified.',
                           'Task selection uses explicit IDs, execution associations or lexical cues; semantic applicability is not established.',
                           'Saved artifact is a proposal; derived accounts are interpretations, not additional owner evidence.',
                           'Canonical packet retains attribution, coverage and limitations; retrieval is incomplete.',
                           'Source hashes and ranges describe pre-render bytes; credential rendering may change displayed text.',
                           'No continuity writes, output files, provider selection, hooks or new stores.']}


def retrieve_context(prepared, args, probes=None, *, retrieve=True):
    """Retrieve against the captured context without rescanning native inputs.

    The prepared object is an in-process product of prepare_context, not an
    external source-attestation format. Caller code must keep it immutable.
    """
    cue = prepared['current_request']['cue']
    if cue != ' '.join(args.cue).strip():
        raise ContextError('prepared-current-cue-changed')
    if not 1 <= args.timeout <= 120 or len(args.unit_id) > 6 or any(
            not re.fullmatch('[0-9a-f]{64}', uid) for uid in args.unit_id):
        raise ContextError('retrieval-deadline-or-anchors-outside-bounds')
    if len(args.query) > 6 or any(not q.strip() or len(q) > 2048 for q in args.query):
        raise ContextError('retrieval-facets-outside-bounds')
    queries, plan = retrieval_plan(cue, args.query, getattr(args, 'probe', []) if probes is None else probes)
    native = prepared['current_native_sources']
    artifact = prepared.get('selected_artifact', {})
    substantive = bool(words(cue)) or bool(queries) or bool(args.unit_id) or prepared['current_record'] is not None
    if artifact.get('required') and artifact.get('status') != 'read':
        evidence = {'status': 'not-attempted', 'reason': 'required-selected-proposal-unavailable', 'complete': False}
    elif native.get('configured') and native.get('status') != 'read':
        evidence = {'status': 'not-attempted', 'reason': 'required-current-original-sources-unavailable', 'complete': False}
    elif not retrieve:
        evidence = {'status': 'not-attempted', 'reason': 'retrieval-skipped-by-caller', 'complete': False}
    elif substantive:
        evidence = canonical_reclaim(cue, queries, args.unit_id, args.timeout)
        plan['dispatch_status'] = 'canonical-reclaim-requested'
    else:
        evidence = {'status': 'not-attempted', 'reason': 'unqualified-resume-requires-selection', 'complete': False}
    plan['canonical_status'] = evidence.get('status')
    if not getattr(args, 'full', False) and isinstance(evidence.get('packet'), dict):
        evidence = {**evidence, 'packet': compact_packet(evidence['packet'])}
    return {**prepared, 'query_plan': plan, 'canonical_evidence': evidence}


def compile_context(args):
    return retrieve_context(prepare_context(args), args)


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument('cue', nargs='+', help='Current ordinary request or rough retrieval cue')
    parser.add_argument('--task-id', help='Select one existing task explicitly')
    parser.add_argument('--execution-harness', help='Host-supplied codex or claude execution association for an unqualified resume')
    parser.add_argument('--execution-context-id', help='Exact host-supplied execution context ID; pair with --execution-harness')
    parser.add_argument('--query', action='append', default=[], help='Forward an exact retrieval facet; at most six')
    parser.add_argument('--probe', action='append', default=[], help='Supply a provisional search hypothesis; at most two')
    parser.add_argument('--unit-id', action='append', default=[], help='Forward a lowercase SHA256 source anchor; at most six')
    parser.add_argument('--correction', action='append', default=[], help='Current correction kept in memory')
    parser.add_argument('--corrections-stdin', action='store_true', help='Read a JSON list of correction strings, or {"corrections": [...]}')
    parser.add_argument('--timeout', type=float, default=90, help='Canonical subprocess deadline in seconds, 1..120')
    parser.add_argument('--full', action='store_true', help='Retain the complete canonical packet on stdout instead of the reading view')
    args = parser.parse_args(argv)
    try:
        sanitize = display_module()
        packet = compile_context(args)
        print(json.dumps(sanitize(packet), ensure_ascii=False, allow_nan=False, separators=(',', ':')))
        return 0
    except (ValueError, OSError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        print(json.dumps({'version': 1, 'kind': 'continuation-context', 'complete': False,
                          'status': 'failed', 'error': safe_error(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

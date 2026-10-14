#!/usr/bin/env python3
"""Bounded local archive leads and original hydration; never edits the archive."""
from __future__ import annotations
import argparse
import errno
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
import types

sys.dont_write_bytecode = True

# Callers may load this helper by file location, without its directory on
# sys.path. Load the verified sibling directly rather than searching imports.
CANONICAL_HELPER_DIR = Path.home() / 'plugins/protecting-your-energy/scripts'
_display_path = CANONICAL_HELPER_DIR / 'archive_display.py'
if _display_path.resolve().parent != CANONICAL_HELPER_DIR.resolve():
    raise ImportError('archive display module is outside helper directory')
_display_bytes = _display_path.read_bytes()
if hashlib.sha256(_display_bytes).hexdigest() != '1b255b77c5e5c5043f04e73753abf5516e717633a7cb6d172d3d0a092e6706f5':
    raise ImportError('archive display module SHA256 differs')
_display_spec = importlib.util.spec_from_file_location('pye_verified_archive_display', _display_path)
_display_module = importlib.util.module_from_spec(_display_spec)
exec(compile(_display_bytes, str(_display_path), 'exec'), _display_module.__dict__)
display_result = _display_module.display_result
redact_text = _display_module.redact_text
del _display_bytes

DEFAULT_CONFIG = Path.home() / 'brain-data/upar-dip/derived/protect-your-energy/config.json'
TOOLING = Path.home() / 'brain-data/upar-dip/tooling/retrieval'
BLOCKED = ('.sealed', '.sanitized', '.seal-', 'request-only', 'request_only', '.request-only', 'quarantine', 'credentials', '.staging')
MARKERS = ('.sealing-in-progress', '.request-only', 'REQUEST_ONLY', '.sealed')
NATIVE_ROLE_POLICY_SHA = '06cd7454467212a9e6e69f188d3d94b658a05d8e137a6fefb7258af71d582e3f'
ORIGINAL_RESOLVER = TOOLING / 'discord_capture_resolver.py'
ORIGINAL_RESOLVER_SHA = '409abf482adb9fa3d68865c049730a3c3e1ada002fc641b22b2daab11bb61207'
NOTE_CAPTURE_READER = Path(__file__).with_name('note_capture_reader.py')
NOTE_CAPTURE_READER_SHA = '14ddd190bf0b096ceaa3707fb39a5c9b0f61b4267ad108071090b9c2bd6b7334'


def guarded(path: Path) -> bool:
    """Check both spelling and resolved ancestry; symlinks cannot bypass guards."""
    for p in (path.absolute(), path.resolve()):
        if any(part.lower().startswith(BLOCKED) for part in p.parts):
            return True
        for ancestor in (p, *p.parents):
            if any((ancestor / marker).exists() for marker in MARKERS):
                return True
    return False


def restricted(row: dict) -> bool:
    for field in ('sealed', 'request_only', 'request-only', 'withheld'):
        if row.get(field): return True
    for field in ('access', 'access_scope', 'visibility', 'sensitivity', 'labels'):
        value = str(row.get(field, '')).lower()
        if any(flag in value for flag in ('request-only', 'request_only', 'sealed', 'withheld')): return True
    return False


def settings(config_path: str) -> dict:
    cp = Path(config_path).expanduser().resolve()
    if guarded(cp):
        raise ValueError('configuration is guarded')
    cfg = json.loads(cp.read_text())
    alias = Path(cfg.get('owner_archive', str(Path.home() / 'boden_wizard_chat_context'))).expanduser()
    if guarded(alias):
        raise ValueError('archive is guarded; no retrieval performed')
    root = alias.resolve()
    if not (root / 'archive/originals').is_dir():
        raise ValueError('configured archive originals are unavailable')
    access = cfg.get('archive_access', {})
    index_value = os.environ.get('PYE_SEMANTIC_INDEX') or access.get('semantic_index')
    return {'config': str(cp), 'archive_alias': str(alias), 'archive_root': str(root),
            'index': Path(index_value).expanduser() if index_value else None,
            'cli': Path(access.get('retrieval_cli', str(TOOLING / 'upar_retrieve.py'))).expanduser(),
            'python': access.get('retrieval_python', sys.executable), 'access': access,
            'catalog': cfg.get('source_catalog'), 'dense': access.get('dense', {}),
            'source_roots': cfg.get('archive_source_roots', []), 'storage_policy': cfg.get('storage_policy', {})}


def status(st: dict) -> dict:
    index = st['index']
    semantic = {'state': 'not-configured', 'kind': 'unavailable', 'evaluated': False}
    if index:
        semantic = {'path': str(index), 'evaluated': False,
                    'state': 'guarded' if guarded(index) else 'query-ready-unverified' if (index / 'query.py').is_file() else 'unavailable',
                    'kind': 'unverified-index'}
    dense = st.get('dense', {})
    if dense.get('backend') == 'semantic-archive':
        semantic = generic_status(st)
    elif dense:
        values = [dense.get(key) for key in ('script', 'python', 'index', 'model')]
        required = [Path(v).expanduser() for v in values if isinstance(v, str) and v.strip()]
        ready = len(required) == 4 and all(p.exists() for p in required) and not any(guarded(p) for n,p in enumerate(required) if n != 1)
        semantic = {'state': 'dense-unavailable-or-guarded', 'kind': 'dense-local-transformer', 'evaluated': False}
        if ready:
            run = bounded([str(required[1]), '-c', WORKER, str(required[0]), 'status', '--index', str(required[2]), '--model', str(required[3]), '--archive', st['archive_root'], '--json'], 10)
            try:
                backend = json.loads(run['stdout'])
                if run['exit_code'] == 0 and not run['failure']:
                    semantic.update(backend)
                    semantic['evaluated'] = False
                else: semantic['diagnostic'] = run['failure'] or backend.get('error')
            except ValueError: semantic['diagnostic'] = 'invalid-status-response'
    stale = Path(st['archive_root']) / 'archive/corpus/derived/retrieval.duckdb'
    cli = st['cli']
    lexical = {'kind': 'lexical-parquet', 'cli': str(cli), 'evaluated': False,
               'state': 'ready-unverified' if cli.is_file() and not guarded(cli) and importlib.util.find_spec('duckdb') else 'unavailable'}
    if stale.exists():
        lexical['state'] = 'blocked-upstream-would-remove-stale-db'
    if dense.get('backend') == 'semantic-archive':
        lexical = {'kind': 'lexical-published-policy-view', 'evaluated': False,
                   'state': 'configured-unverified', 'generation': semantic.get('generation'),
                   'scope': 'Explicit literal supplement over the pinned published policy view; readiness requires an actual query, no encoder inference.'}
    return {'config': st['config'], 'archive_alias': st['archive_alias'], 'archive_root': st['archive_root'],
            'semantic': semantic, 'lexical': lexical, 'path_search': bool(__import__('shutil').which('rg')),
            'scope': 'local read-only discovery; not exhaustive recall or verified authorship',
            'guards': 'sealed, sealing, sanitized staging and request-only paths refused; originals rechecked during hydration'}


# The upstream UPAR connector removes a stale DB even in read_only mode. A worker
# audit hook denies that deletion and Python file writes; offline env prevents downloads.
WORKER = r'''import os,sys,runpy
sys.dont_write_bytecode=True
def audit(event,args):
 if event == 'open':
  mode=args[1]; flags=args[2] or 0
  if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
   raise PermissionError('archive access worker is read-only')
 if event in {'os.remove','os.rename','os.rmdir','os.mkdir','os.link','os.symlink','os.chmod','os.chown','os.truncate','socket.connect','socket.bind'}:
  raise PermissionError('archive access worker forbids writes and network')
sys.addaudithook(audit)
p=sys.argv[1];sys.argv=sys.argv[1:];runpy.run_path(p,run_name='__main__')
'''


def bounded(argv: list[str], seconds: float, cwd: Path | None = None, cap: int = 131072) -> dict:
    started = time.monotonic()
    env = os.environ.copy()
    env.update({'PYTHONDONTWRITEBYTECODE': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                'HF_DATASETS_OFFLINE': '1', 'TOKENIZERS_PARALLELISM': 'false'})
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, 'out'); sel.register(proc.stderr, selectors.EVENT_READ, 'err')
    buf = {'out': bytearray(), 'err': bytearray()}; reason = None
    try:
        while sel.get_map():
            left = seconds - (time.monotonic() - started)
            if left <= 0:
                reason = 'external-timeout'; break
            for key, _ in sel.select(min(left, 0.1)):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    sel.unregister(key.fileobj); continue
                buf[key.data].extend(data)
                if sum(map(len, buf.values())) > cap:
                    reason = 'output-cap'; break
            if reason: break
    finally:
        if proc.poll() is None:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        proc.wait(); sel.close(); proc.stdout.close(); proc.stderr.close()
    return {'exit_code': proc.returncode, 'failure': reason,
            'seconds': round(time.monotonic() - started, 3),
            'stdout': bytes(buf['out']).decode('utf-8', 'replace'),
            'stderr': bytes(buf['err']).decode('utf-8', 'replace')[:600]}


def source_path(st: dict, value: str) -> Path:
    root = Path(st['archive_root']); p = Path(value).expanduser()
    if not p.is_absolute(): p = root / p
    else:
        # Historical locators retain provenance; reads use the verified SSD replica.
        for value in st.get('source_roots', []):
            prior=Path(value).expanduser()
            if p.is_relative_to(prior):
                p=root/p.relative_to(prior);break
    original = root / 'archive/originals'
    if guarded(p) or not p.resolve().is_relative_to(original):
        raise ValueError('source is outside permitted originals or guarded')
    return p.resolve()


def generic_command(st: dict) -> list[str]:
    """Use the frozen normalized root independently of the original archive."""
    dense = st.get('dense', {})
    values = [dense.get(key) for key in ('script', 'python', 'index', 'model', 'root')]
    if not all(isinstance(v, str) and v.strip() for v in values):
        raise ValueError('semantic-archive configuration is incomplete')
    apparent_paths = [Path(v).expanduser() for v in values]
    if any(guarded(p) for p in apparent_paths):
        raise ValueError('semantic-archive route unavailable or guarded')
    script, runtime, index, model, root = [p.resolve() for p in apparent_paths]
    # Validate the executable's real target, but invoke its configured venv
    # path so Python loads the same dependencies used to build the index.
    runtime_command = Path(dense['python']).expanduser().absolute()
    if any(guarded(p) for p in (script, runtime, index, model, root)) or not all(p.exists() for p in (script, runtime, index, model, root)):
        raise ValueError('semantic-archive route unavailable or guarded')
    if 'script_sha256' in dense:
        script_sha = dense['script_sha256']
        if not isinstance(script_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', script_sha):
            raise ValueError('invalid semantic-archive script SHA256 configuration')
        if hashlib.sha256(script.read_bytes()).hexdigest() != script_sha:
            raise ValueError('semantic-archive script SHA256 differs')
    if 'query_view_manifest_sha256' in dense and 'script_sha256' not in dense:
        raise ValueError('query view requires its reader script SHA256 pin')
    if not (root / 'archive/corpus/records').is_dir() or index.is_relative_to(root):
        raise ValueError('semantic-archive normalized root or index is invalid')
    original = Path(dense.get('original_root', st['archive_root'])).expanduser().resolve()
    if guarded(original) or not original.is_dir():
        raise ValueError('semantic-archive origin unavailable or guarded')
    prefixes = dense.get('excluded_source_prefixes', [])
    if not isinstance(prefixes, list) or not all(isinstance(p, str) and p for p in prefixes):
        raise ValueError('semantic-archive exclusion prefixes are invalid')
    cmd = [str(runtime_command), '-c', WORKER, str(script), '--root', str(root),
           '--original-root', str(original), '--index', str(index), '--model', str(model)]
    proof_path = dense.get('native_attribution_proof')
    proof_sha = dense.get('native_attribution_proof_sha256')
    if bool(proof_path) != bool(proof_sha):
        raise ValueError('native attribution proof requires its pinned SHA256')
    if proof_path:
        if not isinstance(proof_path, str) or not isinstance(proof_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', proof_sha):
            raise ValueError('invalid native attribution proof configuration')
        proof = Path(proof_path).expanduser()
        if guarded(proof) or not proof.is_file():
            raise ValueError('native attribution proof unavailable or guarded')
        proof = proof.resolve()
        cmd += ['--native-attribution-proof', str(proof),
                '--native-attribution-proof-sha256', proof_sha]
    for prefix in prefixes:
        cmd += ['--exclude-source-prefix', prefix]
    if 'query_view_manifest_sha256' in dense:
        view_sha = dense['query_view_manifest_sha256']
        if not isinstance(view_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', view_sha):
            raise ValueError('invalid query view manifest SHA256 configuration')
        cmd += ['--query-view-manifest-sha256', view_sha]
    return cmd


def generic_run(st: dict, arguments: list[str], budget: float) -> tuple[dict, dict]:
    run = bounded(generic_command(st) + arguments, budget, cap=1048576)
    attempt = {k: run[k] for k in ('exit_code', 'failure', 'seconds')}
    try:
        data = json.loads(run['stdout'])
        if not isinstance(data, dict): raise ValueError('invalid response')
    except ValueError:
        data = {}
        attempt['failure'] = attempt['failure'] or 'invalid-json-response'
    if run['exit_code'] or run['failure'] or attempt.get('failure'):
        attempt['diagnostic'] = data.get('error') or run['stderr'] or 'backend unavailable'
        return {}, attempt
    return data, attempt


def generic_status(st: dict) -> dict:
    result = {'state': 'semantic-unavailable', 'kind': 'dense-local-transformer',
              'backend': 'semantic-archive', 'evaluated': False}
    try:
        data, attempt = generic_run(st, ['status', '--compact'], 10)
    except (OSError, ValueError) as error:
        result['diagnostic'] = str(error)
        return result
    result.update(attempt=attempt)
    if data:
        result.update({k: data[k] for k in ('state', 'generation', 'scope', 'coverage', 'model', 'root', 'original_root', 'policy_hash', 'native_attribution_proof_sha256') if k in data})
    return result


def generic_provenance(st: dict, metadata: dict) -> dict:
    """Preserve normalized locators without asserting raw-byte verification."""
    if not isinstance(metadata, dict) or restricted(metadata):
        raise ValueError('restricted or invalid semantic provenance')
    root = Path(st['dense']['root']).expanduser().resolve()
    part = metadata.get('part')
    if not isinstance(part, str): raise ValueError('normalized part is missing')
    path = Path(part)
    if not path.is_absolute(): path = root / path
    path = path.resolve()
    if guarded(path) or not path.is_relative_to(root / 'archive/corpus/records'):
        raise ValueError('normalized part is outside the permitted corpus')
    source = metadata.get('source', {})
    if not isinstance(source, dict) or restricted(source):
        raise ValueError('invalid or restricted source provenance')
    original = Path(st['dense'].get('original_root', st['archive_root'])).expanduser().resolve()
    for key in ('archive_path', 'origin_path'):
        value = source.get(key)
        if value:
            if not isinstance(value, str): raise ValueError('invalid source path')
            candidate = Path(value).expanduser()
            if not candidate.is_absolute(): candidate = original / candidate
            elif key == 'archive_path':
                for prior_value in st.get('source_roots', []):
                    prior = Path(prior_value).expanduser()
                    if candidate.is_relative_to(prior):
                        candidate = original / candidate.relative_to(prior)
                        break
            if guarded(candidate): raise ValueError('guarded source provenance')
            if key == 'archive_path' and not candidate.resolve().is_relative_to(original):
                raise ValueError('raw source is outside the permitted origin')
    parents = metadata.get('parents')
    if not isinstance(parents, list) or not parents or any(not isinstance(p, dict) or restricted(p) or restricted(p.get('author', {})) for p in parents):
        raise ValueError('invalid or restricted semantic parents')
    clean = dict(metadata)
    for field in ('text', 'preview', 'prompt', 'vectors'):
        clean.pop(field, None)
    clean['normalized_path'] = str(path)
    clean['verification'] = 'backend checked normalized record and parent text; raw presence is separate from byte verification'
    return clean


def native_policy_fields(st: dict, payload: dict) -> dict:
    """Preserve configured backend policy receipts without reclassifying sources."""
    result = {}
    if 'native_message_policy' in payload:
        policy = payload['native_message_policy']
        if (not isinstance(policy, dict) or policy.get('helper_sha256') != NATIVE_ROLE_POLICY_SHA
                or policy.get('original_origin_verified') is not False):
            raise ValueError('invalid native message policy receipt')
        result['native_message_policy'] = policy
    if 'frozen_provenance' in payload:
        result['frozen_provenance'] = generic_provenance(st, payload['frozen_provenance'])
    if 'source_policy' in payload:
        document_policy = payload['source_policy']
        if (not isinstance(document_policy, dict)
                or document_policy.get('schema_version') != 1
                or document_policy.get('policy_id') != 'icloud-account-attribution-context-v1'
                or document_policy.get('original_origin_verified') is not False):
            raise ValueError('invalid document source policy receipt')
        result['source_policy'] = document_policy
        if 'parent_classifications' in document_policy:
            effective = generic_provenance(st, payload.get('provenance'))
            frozen = result.get('frozen_provenance')
            account = document_policy.get('archived_account_attribution')
            if (not isinstance(frozen, dict)
                    or document_policy.get('source_kind') != 'document'
                    or document_policy.get('record_type') != 'doc.note'
                    or document_policy.get('platform') != 'icloud'
                    or document_policy.get('source_family') != 'icloud_notes'
                    or document_policy.get('reason') != 'saved-account-note-without-independent-authorship-basis'
                    or document_policy.get('effective_input_lane') != 'context_document'
                    or document_policy.get('primary_owner_evidence') is not False
                    or document_policy.get('authorship_established') is not False
                    or document_policy.get('adoption_established') is not False
                    or document_policy.get('normalized_source_record_verified') is not True
                    or document_policy.get('frozen_provenance_preserved_separately') is not True
                    or not isinstance(account, dict)
                    or account.get('self_labels_verified') is not True
                    or account.get('self_label_rule') != 'iCloud Notes authored by signed-in account (Boden)'
                    or effective.get('kind') != 'document' or effective.get('platform') != 'icloud'
                    or effective.get('source', {}).get('family') != 'icloud_notes'
                    or effective.get('role') != 'context_document'
                    or effective.get('input_lane') != 'context_document'
                    or effective.get('input_attribution_class') != 'context_document'
                    or effective.get('primary_owner_evidence') is not False
                    or effective.get('frozen_role') != frozen.get('role')
                    or effective.get('frozen_input_attribution_class') != frozen.get('input_attribution_class')
                    or account.get('account_field_present') != ('account' in frozen.get('source', {}))
                    or account.get('account_value_present') != (frozen.get('source', {}).get('account') is not None)):
                raise ValueError('document policy effective source qualification differs')
            for key in ('record_id', 'record_line_sha256', 'record_offset', 'record_bytes',
                        'part', 'message_pointer', 'platform', 'source'):
                if effective.get(key) != frozen.get(key):
                    raise ValueError('document policy frozen source binding differs')
            classes = document_policy['parent_classifications']
            if (not isinstance(classes, list) or len(classes) != len(effective['parents'])
                    or len(frozen['parents']) != len(effective['parents'])):
                raise ValueError('document policy matched parent coverage differs')
            for parent, prior, classification in zip(effective['parents'], frozen['parents'], classes):
                if not isinstance(classification, dict):
                    raise ValueError('invalid document parent policy')
                for key in ('message_id', 'message_index', 'text_sha256', 'char_start', 'char_end',
                            'parent_version_key', 'timestamp', 'timestamp_edited', 'reply_to'):
                    if parent.get(key) != prior.get(key):
                        raise ValueError('document policy frozen parent binding differs')
                if (any(classification.get(key) != parent.get(key)
                        for key in ('message_id', 'message_index', 'text_sha256'))
                        or classification.get('matched_character_range') != [parent['char_start'], parent['char_end']]
                        or classification.get('normalized_full_parent_verified') is not True
                        or classification.get('effective_role') != 'context_document'
                        or classification.get('effective_input_lane') != 'context_document'
                        or classification.get('primary_owner_evidence') is not False
                        or classification.get('authorship_established') is not False
                        or classification.get('adoption_established') is not False
                        or parent.get('source_policy') != classification
                        or parent.get('role') != 'context_document'
                        or parent.get('attribution_class') != 'context_document'
                        or parent.get('author') != {**(prior.get('author') or {}), 'role': 'context_document'}
                        or parent.get('frozen_role') != prior.get('role')
                        or parent.get('frozen_attribution_class') != prior.get('attribution_class')
                        or parent.get('primary_owner_evidence') is not False):
                    raise ValueError('document policy matched classification differs')
    policy = result.get('native_message_policy', {})
    if ('parent_classifications' in policy and isinstance(payload.get('provenance'), dict)
            and payload['provenance'].get('platform') in ('codex', 'codex_cli')):
        if 'provenance' not in payload or 'frozen_provenance' not in result:
            raise ValueError('native policy matched provenance missing')
        effective = generic_provenance(st, payload['provenance'])
        frozen = result['frozen_provenance']
        for key in ('record_id', 'record_line_sha256', 'record_offset', 'record_bytes',
                    'part', 'message_pointer', 'platform', 'source'):
            if effective.get(key) != frozen.get(key):
                raise ValueError('native policy frozen source binding differs')
        classes = policy['parent_classifications']
        if (not isinstance(classes, list) or len(classes) != len(effective['parents'])
                or len(frozen['parents']) != len(effective['parents'])):
            raise ValueError('native policy matched parent coverage differs')
        for parent, prior, classification in zip(effective['parents'], frozen['parents'], classes):
            if not isinstance(classification, dict):
                raise ValueError('invalid native parent policy')
            for key in ('message_id', 'message_index', 'text_sha256', 'char_start', 'char_end', 'parent_version_key'):
                if parent.get(key) != prior.get(key):
                    raise ValueError('native policy frozen parent binding differs')
            if (any(classification.get(key) != parent.get(key) for key in ('message_id', 'message_index', 'text_sha256'))
                    or classification.get('matched_character_range') != [parent['char_start'], parent['char_end']]
                    or classification.get('original_origin_verified') is not False
                    or type(classification.get('owner_evidence_eligible')) is not bool):
                raise ValueError('native policy matched classification differs')
            if classification.get('effective_role') is not None and (
                    parent.get('role') != classification['effective_role']
                    or parent.get('attribution_class') != classification.get('effective_input_attribution_class')
                    or parent.get('primary_owner_evidence') is not False):
                raise ValueError('native policy effective parent role differs')
    return result


def policy_projection(row: dict, limit: int) -> dict:
    """Validate and clip display fragments without inventing a source interval."""
    pieces = row.get('display_segments')
    original_range = row.get('original_display_range')
    if (row.get('text_range') is not None or not isinstance(pieces, list)
            or not isinstance(original_range, dict)
            or type(original_range.get('start')) is not int or type(original_range.get('end')) is not int
            or not 0 <= original_range['start'] <= original_range['end']):
        raise ValueError('invalid native display projection')
    previous_end, values = original_range['start'], []
    for piece in pieces:
        coordinates = piece.get('source_character_range') if isinstance(piece, dict) else None
        text = piece.get('text') if isinstance(piece, dict) else None
        if (not isinstance(coordinates, list) or len(coordinates) != 2
                or any(type(value) is not int for value in coordinates) or not isinstance(text, str)
                or not previous_end <= coordinates[0] <= coordinates[1] <= original_range['end']
                or len(text) != coordinates[1] - coordinates[0]):
            raise ValueError('invalid native display segment')
        previous_end = coordinates[1]
        values.append(text)
    shown = '\n'.join(values)
    if shown != row.get('text') or hashlib.sha256(shown.encode('utf-8')).hexdigest() != row.get('display_text_sha256'):
        raise ValueError('native display projection hash differs')
    clipped, remaining = [], limit
    for piece in pieces:
        available = remaining - int(bool(clipped))
        if available <= 0:
            break
        count = min(len(piece['text']), available)
        if count:
            start = piece['source_character_range'][0]
            clipped.append({'source_character_range': [start, start + count], 'text': piece['text'][:count]})
            remaining -= count + int(len(clipped) > 1)
        if count < len(piece['text']):
            break
    text = '\n'.join(piece['text'] for piece in clipped)
    return {**row, 'text': text, 'text_range': None, 'display_segments': clipped,
            'display_text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'display_projection_truncated': len(text) < len(shown),
            'truncated': bool(row.get('truncated')) or len(text) < len(shown),
            'continuation': None, 'continuations': []}


def effective_lane(metadata: dict) -> str:
    if metadata.get('role') == 'context_document' and metadata.get('primary_owner_evidence') is False:
        return metadata.get('input_attribution_class', metadata.get('input_lane', 'context_document'))
    if metadata.get('platform') in ('codex', 'codex_cli') and any(
            'native_message_policy' in parent for parent in metadata['parents']):
        return metadata.get('input_attribution_class', metadata.get('input_lane', metadata.get('role', 'unknown')))
    return metadata.get('input_lane', metadata.get('role', 'unknown'))


def intentional_control_omission(row: dict) -> bool:
    policy = row.get('native_message_policy', {})
    return (row.get('policy_display_projection') is True and not row.get('display_segments')
            and row.get('display_projection_truncated') is not True
            and policy.get('owner_evidence_eligible') is False
            and policy.get('classification') in ('declared-native-control', 'declared-native-machine-role',
                                                 'complete-leading-native-control'))


def generic_query(st: dict, args, facets: list[str]) -> dict:
    lexical = args.mode == 'lexical'
    expected_kind = 'lexical-published-policy-view' if lexical else 'dense-local-transformer'
    deadline = time.monotonic() + args.budget
    results, attempts, failures, facet_records = [], [], [], []
    generation = getattr(args, 'generation', None)
    if generation and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', generation):
        raise ValueError('query generation invalid')
    for offset, facet in enumerate(facets):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            facet_records.extend({'query': q, 'state': 'budget-deferred', 'attempted': False}
                                 for q in facets[offset:])
            break
        cmd = (['--query-mode', 'lexical'] if lexical else []) + ['query', facet, '--limit', str(args.limit), '--preview-chars', '0']
        if args.year: cmd += ['--year', args.year]
        if args.platform: cmd += ['--platform', args.platform]
        if generation: cmd += ['--generation', generation]
        if getattr(args, 'include_unconfirmed_input', False): cmd += ['--include-unconfirmed-input']
        if getattr(args, 'include_context_documents', False): cmd += ['--include-context-documents']
        data, attempt = generic_run(st, cmd, remaining)
        attempts.append({'query': facet, **attempt})
        record = {'query': facet, 'state': 'failed', 'attempted': True, 'attempt': attempt}
        facet_records.append(record)
        if not data or data.get('kind') != expected_kind:
            record['failure_reason'] = attempt.get('failure') or ('empty-backend-response' if not data else 'unexpected-response-kind')
            continue
        if not isinstance(data.get('generation'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', data['generation']):
            raise ValueError('query generation missing or invalid')
        generation = data['generation']
        source_failures = data.get('failures', [])
        failures.extend(source_failures)
        record.update(state='completed', returned_leads=len(data.get('leads', [])),
                      source_failures=source_failures, source_verification_complete=not bool(source_failures))
        results.append((facet, data))
    leads, seen = [], set()
    for rank in range(args.limit):
        for facet, data in results:
            rows = data.get('leads', [])
            if rank >= len(rows): continue
            row = rows[rank]
            if not isinstance(row, dict) or restricted(row): continue
            uid = row.get('unit_id')
            if not isinstance(uid, str) or not re.fullmatch(r'[a-f0-9]{64}', uid):
                raise ValueError('invalid semantic unit ID')
            if uid in seen: continue
            metadata = generic_provenance(st, row.get('provenance'))
            seen.add(uid)
            leads.append({'unit_id': uid, 'score': row.get('similarity'), 'provenance': metadata,
                          **native_policy_fields(st, row),
                          'input_lane': effective_lane(metadata),
                          'platform': metadata.get('platform'), 'year': metadata.get('year'),
                          'matched_query': facet, 'route': 'lexical-published-policy-view' if lexical else 'semantic-archive', 'status': 'lead-only',
                          **({'lexical_match_spans': row.get('lexical_match_spans', []),
                              'literal_match_verified': row.get('literal_match_verified') is True} if lexical else {}),
                          'generation': generation, 'requires': 'Hydrate this unit and all relevant parent/context turns; a match is not confidence.'})
    completed = [q for q, _ in results]
    all_completed = len(completed) == len(facets)
    execution = {'requested': facets, 'attempted': [a['query'] for a in attempts],
                 'completed': completed, 'failed': [r['query'] for r in facet_records if r['state'] == 'failed'],
                 'deferred': [r['query'] for r in facet_records if r['state'] == 'budget-deferred'],
                 'omitted_by_limit': [], 'facets': facet_records}
    policy_facets = [{'query': facet, **{k:v for k,v in native_policy_fields(st, data).items() if k != 'source_policy'}} for facet, data in results
                    if 'native_message_policy' in data or 'frozen_provenance' in data]
    policy_output = {'native_message_policy': {'scope': 'executed query facets; backend receipts retained',
                                              'facets': policy_facets}} if policy_facets else {}
    document_facets = [{'query': facet, 'source_policy': native_policy_fields(st, data)['source_policy']}
                       for facet, data in results if 'source_policy' in data]
    if document_facets:
        policy_output['source_policy'] = {'scope': 'executed query facets; document receipts retained separately from native transport policy',
                                          'facets': document_facets}
    return {'status': 'leads' if leads else 'no-leads' if results else 'lexical-unavailable' if lexical else 'semantic-unavailable',
            **policy_output,
            'kind': expected_kind, 'backend': 'semantic-archive',
            'vector_semantic_verified': bool(results) and not lexical,
            **({'lexical_execution': [{'query': facet, 'execution': data.get('lexical_execution'),
                                       'encoder_instantiated': data.get('encoder_instantiated')}
                                      for facet, data in results]} if lexical else {}), 'generation': generation,
            'scope': results[0][1].get('scope') if results else None,
            'coverage': results[0][1].get('coverage') if results else None,
            'include_unconfirmed_input': bool(getattr(args, 'include_unconfirmed_input', False)),
            'include_context_documents': bool(getattr(args, 'include_context_documents', False)),
            'queries_used': completed, 'queries_requested': facets,
            'query_execution': execution, 'all_facets_completed': all_completed,
            'source_verification_complete': all_completed and not bool(failures),
            'attempts': attempts, 'failures': failures,
            'query_hit_counts': [{'query': q, 'leads': len(d.get('leads', []))} for q, d in results],
            'leads': leads[:args.limit],
            'continuation': ('Use complementary semantic facets and hydrate exact original parents; bounded literal results cannot establish archive absence.' if lexical else
                             'Expand semantic facets and hydrate exact normalized parents; empty or incomplete results cannot establish archive absence.')}


def normalized_message_view(record: dict, pointer: str) -> list[dict]:
    """Decode supported canonical text containers without importing the backend."""
    kind = record.get('kind')
    if kind == 'conversation' and pointer == '/messages':
        messages = record.get('messages') or []
    elif kind == 'event' and record.get('type') == 'log.discord_channel_snapshot' and pointer == '/body/text (decoded JSON)/export/messages':
        body = record.get('body')
        if not isinstance(body, dict) or not isinstance(body.get('text'), str):
            raise ValueError('invalid normalized event body')
        payload = json.loads(body['text'])
        exported = payload.get('export') if isinstance(payload, dict) else None
        if not isinstance(exported, dict): raise ValueError('invalid normalized event export')
        messages = exported.get('messages')
    elif kind == 'document':
        body = record.get('body') or ''
        text = body.get('text', '') if isinstance(body, dict) else body
        wrapped = (record.get('extra') or {}).get('wrapped_message')
        if isinstance(wrapped, dict) and pointer == '/extra/wrapped_message':
            if isinstance(wrapped.get('content'), str) and wrapped['content'] != text:
                raise ValueError('wrapped text differs from normalized body')
            messages = [wrapped]
        elif not isinstance(wrapped, dict) and pointer == ('/body/text' if isinstance(body, dict) else '/body'):
            messages = [{'id': record.get('id'), 'content': text}]
        else:
            raise ValueError('unsupported normalized message pointer')
    else:
        raise NotImplementedError('unsupported normalized message container')
    if not isinstance(messages, list): raise ValueError('invalid normalized messages')
    return messages


def normalized_message_text(message: dict) -> str:
    content = message.get('content')
    if isinstance(content, str): return content
    if isinstance(content, list):
        parts = [p if isinstance(p, str) else p.get('text') or p.get('content') or ''
                 for p in content if isinstance(p, (str, dict))]
        if any(not isinstance(p, str) for p in parts): raise ValueError('unsupported content text')
        return '\n'.join(p for p in parts if p)
    return ''


def verified_context_texts(provenance: dict, rows: list[dict], deadline: float) -> dict[int, str]:
    """Read one capped record, then verify only backend-approved message rows."""
    count, offset = provenance.get('record_bytes'), provenance.get('record_offset')
    if type(count) is not int or type(offset) is not int or offset < 0 or count < 1:
        raise ValueError('invalid normalized record byte locator')
    if count > 256 * 1024 * 1024: raise OverflowError('normalized record exceeds 256 MiB read ceiling')
    if time.monotonic() >= deadline: raise TimeoutError('matched-span read budget expired')
    path = Path(provenance['normalized_path'])
    if guarded(path): raise ValueError('guarded normalized record')
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or offset + count > before.st_size:
            raise ValueError('normalized record locator outside regular source')
        stream.seek(offset)
        chunks, remaining = [], count
        while remaining:
            if time.monotonic() >= deadline: raise TimeoutError('matched-span read budget expired')
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk: raise ValueError('normalized record read was incomplete')
            chunks.append(chunk); remaining -= len(chunk)
        after = os.fstat(stream.fileno())
    current = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
        raise ValueError('normalized source changed during matched-span read')
    raw = b''.join(chunks)
    del chunks
    if hashlib.sha256(raw).hexdigest() != provenance.get('record_line_sha256'):
        raise ValueError('normalized record hash changed')
    record = json.loads(raw)
    if record.get('id') != provenance.get('record_id') or restricted(record):
        raise ValueError('normalized record identity or access changed')
    messages = normalized_message_view(record, provenance.get('message_pointer'))
    texts = {}
    for row in rows:
        if time.monotonic() >= deadline: raise TimeoutError('matched-span decode budget expired')
        index = row.get('message_index')
        if type(index) is not int or not 0 <= index < len(messages) or index in texts:
            raise ValueError('invalid or duplicate approved message index')
        message = messages[index]
        if not isinstance(message, dict) or message.get('id') != row.get('message_id'):
            raise ValueError('normalized message identity changed')
        pointer = provenance['message_pointer']
        base = '/export/messages' if record.get('kind') == 'event' else pointer
        expected_pointer = base + '/' + str(index) if base in ('/messages', '/export/messages') else base
        if row.get('source_pointer') != expected_pointer:
            raise ValueError('normalized message pointer changed')
        text = normalized_message_text(message)
        if len(text) != row.get('text_chars') or hashlib.sha256(text.encode()).hexdigest() != row.get('text_sha256'):
            raise ValueError('normalized message text hash changed')
        shown = row['text']; shown_range = row.get('text_range') or {'start': 0, 'end': len(shown)}
        if shown_range.get('start') != 0 or shown_range.get('end') != len(shown) or text[:len(shown)] != shown:
            raise ValueError('backend prefix differs from normalized message')
        texts[index] = text
    for parent in provenance['parents']:
        index = parent.get('message_index')
        row = next((row for row in rows if row.get('message_index') == index), None)
        if row is None or parent.get('text_sha256') != row.get('text_sha256'):
            raise ValueError('verified parent missing from approved context')
        expected_id = str(row.get('message_id') or f"{provenance.get('session_key')}:{index}")
        if parent.get('message_id') != expected_id:
            raise ValueError('verified parent identity changed')
        start, end = parent.get('char_start'), parent.get('char_end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(texts[index]):
            raise ValueError('invalid matched parent character span')
    if time.monotonic() >= deadline: raise TimeoutError('matched-span decode budget expired')
    return texts


def context_windows(provenance: dict, rows: list[dict], texts: dict[int, str], max_chars: int) -> list[dict]:
    """Spend text on matched parents first; keep output in source chronology."""
    suppressed = {row['message_index'] for row in rows if row.get('control_text_suppressed') is True}
    spans = {}
    for parent in provenance['parents']:
        index = parent['message_index']; start, end = parent['char_start'], parent['char_end']
        if index in suppressed:
            continue
        previous = spans.get(index, (start, end))
        spans[index] = (min(start, previous[0]), max(end, previous[1]))
    allocations = {index: 0 for index in texts}
    pending, remaining = sorted(spans), max_chars
    # Fairly retain short parents in full while sharing long-parent budgets.
    while pending and remaining:
        share = max(1, remaining // len(pending))
        for index in pending[:]:
            take = min(share, len(texts[index]) - allocations[index], remaining)
            allocations[index] += take; remaining -= take
            if allocations[index] == len(texts[index]): pending.remove(index)
            if not remaining: break
    for index in sorted(set(texts) - set(spans) - suppressed,
                        key=lambda i: (min(abs(i-p) for p in spans) if spans else 0, i)):
        take = min(len(texts[index]), remaining)
        allocations[index] = take; remaining -= take
    output = []
    for row in sorted(rows, key=lambda row: row['message_index']):
        if row.get('policy_display_projection') is True:
            output.append(dict(row))
            continue
        if row.get('control_text_suppressed') is True:
            clean = dict(row)
            clean.update(text='', text_range={'start': 0, 'end': 0},
                         continuation=None, continuations=[])
            output.append(clean)
            continue
        index = row['message_index']; text = texts[index]; count = allocations[index]
        start = 0
        if index in spans and count < len(text):
            left, right = spans[index]
            start = min(left, max(0, left - max(0, count - (right-left)) // 2))
            start = min(start, max(0, len(text) - count))
        end = start + count
        clean = dict(row)
        continuations = []
        for direction, left, right in (('before', 0, start), ('after', end, len(text))):
            if left < right:
                continuations.append({'direction': direction, 'message_index': index,
                    'source_pointer': row.get('source_pointer'), 'text_offset': left,
                    'text_range': {'start': left, 'end': right}})
        clean.update(text=text[start:end], text_range={'start': start, 'end': end},
                     truncated=count < len(text), continuations=continuations,
                     continuation=next((c for c in continuations if c['direction'] == 'after'), continuations[0] if continuations else None))
        if index in spans:
            left, right = spans[index]
            clean.update(matched_span={'start': left, 'end': right}, matched_span_covered=start <= left and end >= right)
        output.append(clean)
    return output



def native_parent_budget_recovery(st: dict, args, data: dict, provenance: dict, deadline: float) -> tuple[dict, dict | None]:
    """Recover selected native prefixes through the same verified policy reader."""
    if provenance.get('platform') not in ('codex', 'codex_cli') or args.context == 0:
        return data, None
    indices = {parent['message_index'] for parent in provenance['parents']}
    selected = [row for row in data.get('messages', []) if row.get('message_index') in indices]
    if not any(row.get('truncated') and not intentional_control_omission(row) for row in selected):
        return data, None
    receipt = {'status': 'unavailable', 'strategy': 'one selected-only call through the same pinned policy backend',
               'max_chars': args.max_chars, 'generation': data['generation'],
               'source_role_bypass': False, 'original_origin_verified': False}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        receipt['failure'] = 'original-hydration-deadline-exhausted'
        return data, receipt
    recovered, attempt = generic_run(st, ['hydrate', '--unit-id', args.unit_id,
        '--neighbors', '0', '--max-chars', str(args.max_chars), '--generation', data['generation']], remaining)
    receipt['attempt'] = attempt
    if not recovered:
        receipt['failure'] = attempt.get('failure') or 'empty-selected-only-response'
        return data, receipt
    if recovered.get('unit_id') != args.unit_id or recovered.get('generation') != data['generation']:
        raise ValueError('native selected-only unit or generation differs')
    if generic_provenance(st, recovered.get('provenance')) != provenance:
        raise ValueError('native selected-only effective source or parents differ')
    if native_policy_fields(st, recovered) != native_policy_fields(st, data):
        raise ValueError('native selected-only frozen source or policy qualification differs')
    replacements = {}
    bindings = ('message_index', 'message_id', 'timestamp', 'timestamp_edited', 'reply_to',
                'author', 'role', 'effective_attribution_reason', 'original_normalized_role',
                'original_normalized_author', 'evidence_class', 'text_chars', 'text_sha256',
                'source_pointer', 'indexed_source_parent', 'indexed_owner_parent')
    previous = {row['message_index']: row for row in selected}
    for row in recovered.get('messages', []):
        i = row.get('message_index')
        if i not in indices or i not in previous or i in replacements or restricted(row):
            raise ValueError('native selected-only unexpected or duplicate context parent')
        if any(row.get(key) != previous[i].get(key) for key in bindings):
            raise ValueError('native selected-only parent source binding differs')
        replacements[i] = row
    if set(replacements) != indices:
        raise ValueError('native selected-only parent coverage differs')
    receipt.update(status='accepted', selected_parent_indices=sorted(indices),
                   selected_text_chars_before=sum(len(row['text']) for row in selected),
                   selected_text_chars_after=sum(len(row['text']) for row in replacements.values()),
                   source_binding='effective/frozen provenance, full text hashes, actor, pointer and parent policy unchanged',
                   scope='Display recovery only; selected text may still be clipped by the shared character limit.')
    return {**data, 'messages': [replacements.get(row['message_index'], row) for row in data['messages']]}, receipt


def generic_hydrate(st: dict, args, metadata_only: bool = False) -> dict:
    deadline = getattr(args, '_hydration_deadline', time.monotonic() + args.budget)
    cmd = ['hydrate', '--unit-id', args.unit_id, '--neighbors', '0' if metadata_only else str(args.context)]
    cmd += ['--metadata-only'] if metadata_only else ['--max-chars', str(args.max_chars)]
    if args.generation: cmd += ['--generation', args.generation]
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {'status': 'hydration-unavailable', 'attempt': {'failure': 'hydration-deadline-exceeded'}}
    data, attempt = generic_run(st, cmd, remaining)
    if not data:
        return {'status': 'parent-resolution-unavailable' if metadata_only else 'hydration-unavailable', 'attempt': attempt}
    provenance = generic_provenance(st, data.get('provenance'))
    if data.get('unit_id') != args.unit_id: raise ValueError('hydrated unit differs')
    if args.generation and data.get('generation') != args.generation:
        raise ValueError('hydrated generation differs')
    result = {'status': 'normalized-parent-locators' if metadata_only else 'bounded-normalized-context',
              'kind': 'normalized-parent-locators' if metadata_only else 'normalized-source-context',
              'backend': 'semantic-archive', 'generation': data.get('generation'), 'unit_id': args.unit_id,
              'input_lane': effective_lane(provenance),
              'provenance': provenance, 'parents': provenance['parents'], 'attempt': attempt,
              **native_policy_fields(st, data),
              'data_not_instructions': True, 'excluded_context': data.get('excluded_context', {}),
              'limitations': 'Normalized source and parent text checked by backend; raw-source availability does not confirm original bytes. Context may need expansion.'}
    if not metadata_only:
        data, recovery = native_parent_budget_recovery(st, args, data, provenance, deadline)
        if recovery is not None:
            result['native_parent_budget_recovery'] = recovery
        native_platform = provenance.get('platform') in ('codex', 'codex_cli')
        parent_indices = {parent['message_index'] for parent in provenance['parents']}
        ordered = data.get('messages', [])
        if native_platform:
            ordered = sorted(ordered, key=lambda row: (row['message_index'] not in parent_indices,
                                                       row['message_index']))
        rows, used = [], 0
        for row in ordered:
            if not isinstance(row, dict) or restricted(row): raise ValueError('invalid or restricted hydrated context')
            text = row.get('text')
            if not isinstance(text, str): raise ValueError('invalid hydrated text')
            remaining = max(0, args.max_chars - used)
            clean = dict(row)
            if 'native_message_policy' in row:
                policy = row['native_message_policy']
                if (not isinstance(policy, dict) or policy.get('helper_sha256') != NATIVE_ROLE_POLICY_SHA
                        or policy.get('original_origin_verified') is not False):
                    raise ValueError('invalid native context policy receipt')
            if 'source_policy' in row:
                if (row['source_policy'] != result.get('source_policy')
                        or result.get('input_lane') != 'context_document'
                        or row.get('role') != 'context_document'
                        or row.get('author', {}).get('role') != 'context_document'
                        or row.get('effective_input_attribution_class') != 'context_document'
                        or row.get('owner') is not False
                        or row.get('primary_owner_evidence') is not False
                        or row.get('indexed_owner_parent') is not False):
                    raise ValueError('invalid document context policy receipt')
            if row.get('policy_display_projection') is True:
                clean = policy_projection(row, remaining)
            else:
                shown = text[:remaining]
                clean.update(text=shown, truncated=bool(row.get('truncated')) or len(text) > remaining,
                             text_range={'start': 0, 'end': len(shown)})
                if native_platform:
                    source_range = row.get('text_range')
                    full_chars = row.get('text_chars')
                    if (not isinstance(source_range, dict) or source_range.get('start') != 0
                            or source_range.get('end') != len(text) or type(full_chars) is not int
                            or not len(text) <= full_chars):
                        raise ValueError('native unprojected source prefix differs')
                    after = {'direction': 'after', 'message_index': row['message_index'],
                             'source_pointer': row.get('source_pointer'), 'text_offset': len(shown),
                             'text_range': {'start': len(shown), 'end': full_chars}} if len(shown) < full_chars else None
                    clean.update(continuation=after, continuations=[after] if after else [])
            rows.append(clean)
            used += len(clean['text'])
        rows.sort(key=lambda row: row['message_index'])
        result['messages'] = rows
        native_platform = provenance.get('platform') in ('codex', 'codex_cli')
        policy_rows = [row for row in rows if row.get('policy_display_projection') is True
                       or (native_platform and 'native_message_policy' in row)]
        protected_indices = {row['message_index'] for row in policy_rows}
        window_rows = [row for row in rows if row['message_index'] not in protected_indices
                       and row.get('control_text_suppressed') is not True]
        if any(row.get('truncated') for row in policy_rows):
            result['policy_window_scope'] = 'Native-policy displays retained; source-window expansion requires backend requalification.'
        if any(row.get('truncated') for row in window_rows):
            try:
                indices = {row['message_index'] for row in window_rows}
                retained = [row for row in rows if row['message_index'] not in indices]
                window_provenance = {**provenance, 'parents': [parent for parent in provenance['parents']
                                                              if parent['message_index'] in indices]}
                texts = verified_context_texts(window_provenance, window_rows, deadline)
                budget = max(0, args.max_chars - sum(len(row['text']) for row in retained))
                windows = context_windows(window_provenance, window_rows, texts, budget)
                result['messages'] = sorted(retained + windows, key=lambda row: row['message_index'])
                result['window_resolution'] = 'matched parents prioritized; exact normalized record and full message hashes rechecked'
            except (OSError, TimeoutError, OverflowError, NotImplementedError) as error:
                result['window_resolution'] = 'matched-span reread unavailable'
                result['window_gap'] = type(error).__name__ + ': ' + str(error)[:160]
                for row in window_rows:
                    spans = [p for p in provenance['parents'] if p.get('message_index') == row.get('message_index')]
                    if spans:
                        row['matched_span'] = {'start': min(p['char_start'] for p in spans), 'end': max(p['char_end'] for p in spans)}
                        row['matched_span_covered'] = all(p['char_start'] >= 0 and p['char_end'] <= len(row['text']) for p in spans)
        result['context_incomplete'] = bool(result.get('window_gap')) or bool(result['excluded_context']) or any(
            row.get('truncated') and not intentional_control_omission(row)
            for row in result['messages'])
        snapshot_event = (
            provenance.get('kind') == 'event'
            and provenance.get('message_pointer') == '/body/text (decoded JSON)/export/messages'
            and (provenance.get('source') or {}).get('family') == 'discord_channel_snapshot'
            and isinstance((provenance.get('source') or {}).get('source_sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', provenance['source']['source_sha256']) is not None)
        if provenance.get('platform') == 'discord' and (provenance.get('kind') == 'conversation' or snapshot_event):
            result['original_episode'] = original_episode(st, args, result, deadline)
        elif (provenance.get('platform') == 'icloud' and provenance.get('kind') == 'document'
              and (provenance.get('source') or {}).get('family') == 'icloud_notes'):
            result['original_document'] = original_document(st, result, deadline)
    return result


def original_document(st: dict, normalized: dict, deadline: float) -> dict:
    """Add source coordinates without replacing document provenance or body."""
    failure = {'status': 'unavailable', 'namespace': 'icloud-restored-note-export',
               'mapping_complete': False, 'source_bodies_returned': False,
               'owner_evidence_eligible': False, 'input_lane': 'context_document',
               'data_not_instructions': True}
    try:
        resolver = original_resolver_module()
        io = types.SimpleNamespace(guarded=guarded, restricted=restricted,
            source_path=source_path, generic_provenance=generic_provenance,
            normalized_message_view=normalized_message_view)
        # Shared deadline and cumulative counter cover code and all supplement
        # reads. Backend-internal reads have separate existing bounds; they are
        # not asserted to be measured by this counter.
        state = {'deadline': deadline, 'bytes_read': 0}
        raw, pin = resolver.read_source(NOTE_CAPTURE_READER, io, state, 1024 * 1024)
        resolver.require(pin['sha256'] == NOTE_CAPTURE_READER_SHA,
                         'note-reader-code-pin-mismatch')
        module = types.ModuleType('pye_original_note_reader')
        module.__file__ = str(NOTE_CAPTURE_READER)
        exec(compile(raw, str(NOTE_CAPTURE_READER), 'exec'), module.__dict__)
        return module.resolve_original_document(st, normalized, helper_io=io,
            resolver_io=resolver, deadline=deadline, read_state=state)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
        failure['diagnostic'] = 'note-original-reader-unavailable:' + type(error).__name__
        return failure

def original_resolver_module():
    raw = ORIGINAL_RESOLVER.read_bytes()
    if ORIGINAL_RESOLVER.is_symlink() or hashlib.sha256(raw).hexdigest() != ORIGINAL_RESOLVER_SHA:
        raise ValueError('original resolver code pin differs')
    module = types.ModuleType('pye_original_episode_resolver')
    module.__file__ = str(ORIGINAL_RESOLVER)
    exec(compile(raw, str(ORIGINAL_RESOLVER), 'exec'), module.__dict__)
    return module


def original_episode(st: dict, args, normalized: dict, deadline: float) -> dict:
    """Verify original parents, then render a bounded separate source episode."""
    remaining_chars = max(0, args.max_chars - sum(len(row.get('text', '')) for row in normalized['messages']))
    result = {'status': 'unavailable', 'messages': [], 'mapping_complete': False,
              'displayed_selected_parent_ids': [], 'unresolved_parent_ids': [],
              'selected_parent_truncated_ids': [], 'unresolved_reply_ids': [],
              'cutoff_eligible_parent_ids': [], 'cutoff_unavailable_parent_ids': [],
              'cutoff_availability_complete': False,
              'context_incomplete': True, 'text_budget_remaining_at_start': remaining_chars,
              'text_characters_returned': 0, 'cutoff': 'not-requested' if getattr(args, 'cutoff', None) is None else args.cutoff,
              'namespace': 'discord-original-capture', 'resolver_attempted': False,
              'qualifiers': ['Original captures supplement normalized context; frozen/effective provenance remains unchanged.',
                  'Source match does not establish belief adoption or prove historical content availability.',
                  'Only effective selected parents can retain qualified owner evidence; neighboring rows are context.']}
    if time.monotonic() >= deadline:
        result.update(status='deferred', diagnostic='original-episode-deadline-exhausted')
        return result
    try:
        resolver = original_resolver_module()
        provenance = generic_provenance(st, normalized['provenance'])
        parents = provenance['parents']
        resolver.require(0 < len(parents) <= resolver.PARENT_LIMIT, 'original-episode-parent-count-over-limit')
        targets = {}
        for parent in parents:
            mid = resolver.identifier(parent.get('message_id'))
            resolver.identifier((parent.get('author') or {}).get('id'))
            resolver.instant(parent.get('timestamp'))
            resolver.require(isinstance(parent.get('text_sha256'), str) and resolver.SHA.fullmatch(parent['text_sha256']),
                             'original-episode-parent-hash-invalid')
            if mid in targets:
                resolver.require(all(targets[mid].get(key) == parent.get(key) for key in ('text_sha256', 'timestamp', 'author')),
                                 'original-episode-parent-binding-conflict')
            targets[mid] = parent
        result['unresolved_parent_ids'] = list(targets)
        cutoff = resolver.instant(args.cutoff) if getattr(args, 'cutoff', None) is not None else None
        common = resolver.load_pinned(resolver.COMMON, resolver.COMMON_SHA, 'pye_original_episode_common')
        captures = {}
        def remember_capture(export, pin):
            captures[(pin['path'], pin['sha256'])] = (export, pin)
        io = types.SimpleNamespace(guarded=guarded, restricted=restricted, source_path=source_path,
            generic_provenance=generic_provenance, normalized_message_view=normalized_message_view,
            verified_capture=remember_capture)
        read_state = {'deadline': deadline, 'bytes_read': 0}
        source = provenance['source']
        resolver.require(isinstance(source.get('archive_path'), str), 'original-episode-primary-locator-unavailable')
        expected_source_sha = source.get('source_sha256')
        resolver.require(expected_source_sha is None or (isinstance(expected_source_sha, str)
                         and resolver.SHA.fullmatch(expected_source_sha)), 'original-episode-primary-proof-invalid')
        path = resolver.original_path(st, source['archive_path'], io)
        retry = False
        try:
            raw, pin = resolver.read_source(path, io, read_state, 32 * 1024 * 1024)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENOTDIR):
                raise
            retry = True
            result['primary_capture_status'] = 'missing'
        if not retry:
            result['primary_capture_pin'] = pin
            if expected_source_sha is not None and pin['sha256'] != expected_source_sha:
                del raw
                retry = True
                result['primary_capture_status'] = 'stale-file-hash'
            else:
                export = resolver.strict_json(raw)
                del raw
                messages = export.get('messages')
                resolver.require(isinstance(messages, list) and len(messages) <= resolver.MESSAGE_LIMIT,
                                 'original-episode-message-container-invalid')
                channel = (export.get('channel') or {}).get('id')
                declared_channel = source.get('channel_id')
                if declared_channel is None:
                    match = re.search(r'(?:\[|/)([0-9]{15,25})(?:\]\.json|\.json)$', source['archive_path'])
                    declared_channel = match.group(1) if match else None
                if declared_channel is not None:
                    resolver.require(resolver.identifier(channel) == resolver.identifier(declared_channel),
                                     'original-episode-channel-binding-invalid')
                found = {}
                seen_selected = set()
                for index, message in enumerate(messages):
                    resolver.checkpoint(read_state)
                    resolver.require(isinstance(message, dict), 'original-episode-message-object-invalid')
                    mid = str(message.get('id')) if type(message.get('id')) is int else message.get('id')
                    if isinstance(mid, str) and mid in targets:
                        resolver.require(mid not in seen_selected, 'original-episode-duplicate-parent-id')
                        seen_selected.add(mid)
                        resolver.require(resolver.allowed_message(message, st, io, common),
                                         'original-episode-parent-excluded')
                        parent = targets[mid]
                        text = message.get('content')
                        resolver.require(isinstance(text, str), 'original-episode-parent-content-invalid')
                        if (hashlib.sha256(text.encode('utf-8')).hexdigest() == parent['text_sha256']
                                and resolver.identifier((message.get('author') or {}).get('id')) ==
                                    resolver.identifier((parent.get('author') or {}).get('id'))
                                and resolver.instant(message.get('timestamp')) == resolver.instant(parent['timestamp'])):
                            found[mid] = index
                if set(found) != set(targets):
                    del export, messages
                    retry = True
                    result['primary_capture_status'] = 'missing-or-stale-selected-parents'
                else:
                    result['primary_capture_status'] = 'selected-parents-verified'
                    result['primary_capture_binding'] = ('matched preexisting published source SHA' if expected_source_sha is not None
                        else 'fresh stable source SHA plus every selected parent match; no preexisting source-file SHA available')
                    result['preexisting_primary_source_hash_verified'] = expected_source_sha is not None
                    remember_capture(export, pin)
                    mappings = {mid: {'source': pin['path'], 'source_sha256': pin['sha256'],
                        'original_json_pointer': f'/messages/{index}', 'original_parent_match_verified': True,
                        'text_sha256': targets[mid]['text_sha256'],
                        'actor_id': resolver.identifier((targets[mid].get('author') or {}).get('id')),
                        'timestamp': targets[mid]['timestamp']} for mid, index in found.items()}
        if retry:
            remaining_time = deadline - time.monotonic()
            if remaining_time < 1:
                result.update(status='deferred', diagnostic='original-episode-resolver-budget-unavailable')
                return result
            result['resolver_attempted'] = True
            io.bytes_already_read = read_state['bytes_read']
            resolved = resolver.resolve_original_parents(st, provenance, cutoff=None,
                budget=min(remaining_time, 120), helper_io=io,
                helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            result['resolution'] = resolved
            if not resolved.get('coverage_complete'):
                result.update(status='partial-mapping', unresolved_parent_ids=resolved.get('unresolved_parent_ids', list(targets)))
                return result
            shared = resolved.get('shared_matching_capture_versions', [])
            pair = (shared[0]['source'], shared[0]['source_sha256']) if shared else None
            mappings = {}
            for entry in resolved['mappings']:
                candidates = entry['matching_captures']
                chosen = next((candidate for candidate in candidates if
                    (candidate['source'], candidate['source_sha256']) == pair), candidates[0])
                mappings[entry['message_id']] = chosen
            result['capture_selection'] = 'verified shared source+SHA' if shared else 'explicit per-parent verified sources'
        for mid, mapping in mappings.items():
            pair = (mapping['source'], mapping['source_sha256'])
            resolver.require(pair in captures, 'original-episode-verified-capture-cache-unavailable')
            export, _ = captures[pair]
            index = int(mapping['original_json_pointer'].rsplit('/', 1)[-1])
            original = export['messages'][index]
            scope = resolver.edit_scope(original, resolver.instant(original.get('timestamp')), cutoff)
            mapping['cutoff_scope'] = scope
            if scope['cutoff_decision'].startswith('exclude-') or scope['cutoff_decision'] == 'unknown-edit-history':
                result['cutoff_unavailable_parent_ids'].append(mid)
            else:
                result['cutoff_eligible_parent_ids'].append(mid)
        result.update(mapping_complete=True, unresolved_parent_ids=[], status='bounded-original-episode',
                      parent_mappings=mappings, mapping_scope='identity/content/actor/time; cutoff availability is separate',
                      cutoff_availability_complete=not result['cutoff_unavailable_parent_ids'])
        if remaining_chars == 0:
            result.update(status='deferred-display', diagnostic='original-episode-text-budget-exhausted')
            return result
        groups = {}
        for mid, mapping in mappings.items():
            groups.setdefault((mapping['source'], mapping['source_sha256']), []).append(mid)
        displayed = set()
        cutoff_skipped = []
        invalid_neighbor_indices = []
        source_windows = []
        for pair, selected in groups.items():
            if time.monotonic() >= deadline or remaining_chars <= 0:
                break
            resolver.require(pair in captures, 'original-episode-verified-capture-cache-unavailable')
            export, pin = captures[pair]
            messages = export['messages']
            mapped_indices = {mid: int(mappings[mid]['original_json_pointer'].rsplit('/', 1)[-1]) for mid in selected}
            indices = sorted({index for mid in selected for index in
                range(max(0, mapped_indices[mid] - args.context), min(len(messages), mapped_indices[mid] + args.context + 1))})
            eligible_indices = []
            for index in indices:
                message = messages[index]
                if (pin['sha256'], str(message.get('id'))) in displayed:
                    continue
                try:
                    mid = resolver.identifier(message.get('id'))
                    author = message.get('author')
                    resolver.require(isinstance(author, dict), 'original-episode-neighbor-actor-invalid')
                    resolver.identifier(author.get('id'))
                    scope = resolver.edit_scope(message, resolver.instant(message.get('timestamp')), cutoff)
                except (resolver.ResolveError, AttributeError, TypeError):
                    invalid_neighbor_indices.append({'source_sha256': pin['sha256'], 'message_index': index})
                    continue
                if scope['cutoff_decision'].startswith('exclude-') or scope['cutoff_decision'] == 'unknown-edit-history':
                    cutoff_skipped.append(mid)
                    continue
                eligible_indices.append(index)
            episode_args = argparse.Namespace(message_id=selected[0], context=args.context, max_chars=remaining_chars,
                version=None, _target_index=mapped_indices[selected[0]],
                _context_indices=eligible_indices, _context_deadline=deadline,
                _strict_context_text=True)
            window = _discord_original_context(st, episode_args, export, Path(pin['path']), pin['sha256'])
            source_windows.append({key: value for key, value in window.items() if key != 'messages'})
            for row in window['messages']:
                key = (pin['sha256'], row['message_id'])
                if key in displayed:
                    continue
                displayed.add(key)
                original = messages[int(row['json_pointer'].rsplit('/', 1)[-1])]
                mapping = mappings.get(row['message_id'])
                parent = targets.get(row['message_id'])
                selected_verified = bool(mapping and parent
                    and mapping['source'] == pin['path'] and mapping['source_sha256'] == pin['sha256']
                    and mapping['original_json_pointer'] == row['json_pointer']
                    and hashlib.sha256(original['content'].encode('utf-8')).hexdigest() == parent['text_sha256']
                    and resolver.identifier(original['author']['id']) == resolver.identifier(parent['author']['id'])
                    and resolver.instant(original['timestamp']) == resolver.instant(parent['timestamp']))
                row.update(source=pin['path'], source_sha256=pin['sha256'],
                           source_namespace='discord-original-capture', matched_selected_parent=selected_verified,
                           primary_owner_evidence=False)
                if selected_verified:
                    row.update(normalized_parent_text_sha256=parent['text_sha256'],
                        effective_parent_attribution_class=parent.get('attribution_class'),
                        primary_owner_evidence=(parent.get('attribution_class') == 'self'
                            and parent.get('primary_owner_evidence') is not False
                            and provenance.get('role') == 'self' and provenance.get('primary_owner_evidence') is not False),
                        original_parent_match_verified=True)
                    result['displayed_selected_parent_ids'].append(row['message_id'])
                    if row['truncated']:
                        result['selected_parent_truncated_ids'].append(row['message_id'])
                row['original_text_sha256'] = hashlib.sha256(original['content'].encode('utf-8')).hexdigest()
                reference = original.get('reference')
                reply = reference.get('messageId') if isinstance(reference, dict) else original.get('reply_to')
                if reply is not None:
                    try:
                        row['reply_to'] = resolver.identifier(reply)
                    except resolver.ResolveError:
                        row['reply_reference_state'] = 'invalid-or-unsupported'
                row['cutoff_scope'] = resolver.edit_scope(original, resolver.instant(original.get('timestamp')), cutoff)
                result['messages'].append(row)
                remaining_chars -= len(row['text'])
        present = {row['message_id'] for row in result['messages']}
        selected_present = set(result['displayed_selected_parent_ids'])
        result.update(text_characters_returned=sum(len(row['text']) for row in result['messages']),
            source_windows=source_windows, cutoff_unavailable_message_ids=cutoff_skipped,
            invalid_neighbor_indices=invalid_neighbor_indices,
            undisplayed_selected_parent_ids=[mid for mid in targets if mid not in selected_present],
            unresolved_reply_ids=sorted({row['reply_to'] for row in result['messages']
                                        if row.get('reply_to') and row['reply_to'] not in present}))
        result['context_incomplete'] = (bool(result['undisplayed_selected_parent_ids'])
            or bool(cutoff_skipped) or bool(result['unresolved_reply_ids'])
            or bool(invalid_neighbor_indices) or bool(result['cutoff_unavailable_parent_ids'])
            or any(row.get('truncated') or row.get('reply_reference_state') == 'invalid-or-unsupported' for row in result['messages'])
            or any(window.get('context_deferred_by_deadline') or window.get('context_budget_exhausted')
                   or window.get('excluded_rows')
                   for window in source_windows))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, ImportError, AttributeError, RecursionError) as error:
        result.update(status='unavailable', diagnostic=str(error) if type(error).__name__ == 'ResolveError'
                      else 'original-episode-source-unavailable', context_incomplete=True)
    finally:
        result['text_characters_returned'] = sum(len(row['text']) for row in result['messages'])
    return result


def dense_query(st: dict, args, facets: list[str]) -> dict:
    dense = st.get('dense', {})
    values = [dense.get(key) for key in ('script', 'python', 'index', 'model')]
    if not all(isinstance(v,str) and v.strip() for v in values):
        return {'status': 'semantic-unavailable', 'kind': 'dense-local-transformer', 'leads': [], 'diagnostic': 'Dense configuration is incomplete; lexical/path search requires its explicit mode.'}
    script, runtime, index, model = [Path(v).expanduser() for v in values]
    if any(guarded(p) for p in (script,index,model)) or not all(p.exists() for p in (script,runtime,index,model)):
        raise ValueError('dense route unavailable or guarded')
    if args.platform and args.platform != 'discord':
        return {'status':'scope-unavailable','leads':[],'scope':'Current dense generation is Discord; Notes and AI histories require scoped original retrieval.'}
    cmd = [str(runtime), '-c', WORKER, str(script), 'query', *facets, '--index', str(index), '--model', str(model), '--archive', st['archive_root'], '--year', args.year, '--role', 'self', '--limit', str(args.limit), '--preview-chars', '0', '--json']
    run = bounded(cmd, args.budget, cap=1048576)
    try: data = json.loads(run['stdout'])
    except ValueError: data = {}
    attempt = {k:run[k] for k in ('exit_code','failure','seconds')}
    if run['exit_code'] or run['failure'] or data.get('kind') != 'dense-local-transformer':
        return {'status':'semantic-unavailable','leads':[],'attempt':attempt,'diagnostic':data.get('error') or run['stderr'], 'continuation':'Repair the dense route or request explicit lexical/path supplementation; failure is not archive absence.'}
    leads=[];seen=set()
    results=data.get('queries',[])
    # Keep complementary facets represented rather than spending the whole limit on the first.
    for rank in range(args.limit):
        for result in results:
            rows=result.get('leads',[])
            if rank >= len(rows): continue
            row=rows[rank]
            loc = row.get('source')
            if not isinstance(loc,str) or restricted(row): continue
            original = source_path(st,loc)
            key=row.get('unit_id') or (row.get('message_id'),row.get('version_hash'))
            if key in seen: continue
            seen.add(key)
            lead={k:v for k,v in row.items() if k not in ('preview','text','vectors','prompt')}
            lead.update(original_path=str(original),matched_query=result.get('query'),route='dense-local-transformer',status='lead-only',requires='Read all relevant parents and original replies/corrections; score is not confidence.')
            leads.append(lead)
    return {'status':'leads' if leads else 'no-leads','kind':data['kind'],'vector_semantic_verified':True,'model':data.get('model'),'generation':data.get('generation'),'scope':data.get('scope'),'queries_used':facets,'attempt':attempt,'failures':data.get('failures',[]),'query_hit_counts':[{ 'query':r.get('query'),'leads':len(r.get('leads',[]))} for r in results],'leads':leads[:args.limit],'continuation':'Expand semantic facets and dated context; a partial index or no hit cannot establish absence.'}


def query_facets(st: dict, args) -> list[str]:
    if getattr(args, 'include_unconfirmed_input', False) and (st.get('dense', {}).get('backend') != 'semantic-archive' or args.mode not in ('auto', 'semantic', 'lexical')):
        raise ValueError('unconfirmed input requires the semantic-archive published route')
    if getattr(args, 'include_context_documents', False) and (st.get('dense', {}).get('backend') != 'semantic-archive' or args.mode not in ('auto', 'semantic', 'lexical')):
        raise ValueError('context documents require the semantic-archive published route')
    requested = list(dict.fromkeys(q.strip() for q in args.query if q.strip()))
    if not requested: raise ValueError('at least one nonempty query is required')
    if st.get('dense', {}).get('backend') == 'semantic-archive' and args.mode in ('auto', 'semantic', 'lexical'):
        generation = getattr(args, 'generation', None)
        if generation and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', generation):
            raise ValueError('query generation invalid')
    return requested


def _primary_query(st: dict, args) -> dict:
    requested_facets = query_facets(st, args)
    deadline = time.monotonic() + args.budget
    leads = []; attempts = []; seen = set()
    facets = requested_facets[:3]
    if args.mode in ('auto', 'semantic') or (args.mode == 'lexical' and st.get('dense', {}).get('backend') == 'semantic-archive'):
        if args.mode == 'lexical' and getattr(args, 'source_prefix', None):
            raise ValueError('source-prefix filtering requires explicit paths mode, not published lexical supplementation')
        if st.get('dense', {}).get('backend') == 'semantic-archive':
            result = generic_query(st, args, facets)
            result['queries_requested'] = requested_facets
            execution = result['query_execution']
            execution['requested'] = requested_facets
            execution['omitted_by_limit'] = requested_facets[3:]
            execution['facets'].extend({'query': q, 'state': 'omitted-by-limit', 'attempted': False}
                                       for q in requested_facets[3:])
            if execution['omitted_by_limit']:
                result['all_facets_completed'] = False
                result['source_verification_complete'] = False
            return result
        return dense_query(st,args,facets)
    info = status(st)
    for facet in facets:
        left = deadline - time.monotonic()
        if left <= 0: break
        route = 'lexical-parquet'
        if info['lexical']['state'] == 'ready-unverified' and args.mode != 'paths':
            run = bounded([st['python'], '-c', WORKER, str(st['cli']), '--root', st['archive_root'],
                           '--force-fallback', '--budget', str(min(left, 8)), 'search', '--text', facet,
                           '--year', args.year, '--limit', str(args.limit),
                           *(['--platform', args.platform] if args.platform else [])], min(left, 10))
        else:
            run = path_query(st, facet, min(left, 10), args.limit, args.source_prefix)
            route = 'lexical-original-paths'
        attempt = {'query': facet, 'route': route, **{k: run[k] for k in ('exit_code', 'failure', 'seconds')}}
        if run['exit_code'] or run['failure']:
            attempt['diagnostic'] = run['stderr']
            if route != 'lexical-original-paths':
                attempts.append(attempt); continue
        try:
            data = json.loads(run['stdout']); hits = data if isinstance(data, list) else data.get('hits', [])
            if not isinstance(hits, list): raise ValueError('invalid lead response')
        except (ValueError, TypeError, AttributeError):
            attempt['failure'] = 'invalid-json-response'; attempts.append(attempt); continue
        for h in hits[:args.limit]:
            if not isinstance(h, dict): continue
            if restricted(h): continue
            # No raw text/vector/prompt crosses this contract. Original hydration is separate.
            lead = {k: h[k] for k in ('source', 'source_path', 'path', 'unit_id', 'chunk_id', 'conversation_id',
                                     'platform', 'year', 'start_ts', 'end_ts', 'score', 'message_id', 'json_pointer') if k in h}
            loc = next((lead[k] for k in ('source_path', 'path', 'source') if isinstance(lead.get(k), str)), None)
            if loc:
                try: lead['original_path'] = str(source_path(st, loc))
                except ValueError:
                    # A platform/index source may not be a real file. Do not invent hydration.
                    lead.pop('source_path', None); lead.pop('path', None)
                    if guarded(Path(loc)): continue
            key = json.dumps(lead, sort_keys=True)
            if key in seen: continue
            seen.add(key)
            lead.update({'matched_query': facet, 'route': route, 'status': 'lead-only',
                         'requires': 'hydrate original; verify author, chronology, copied prose, corrections and outcome'})
            leads.append(lead)
        attempt['returned_hits'] = len(hits); attempts.append(attempt)
    return {'status': 'leads' if leads else 'no-leads', 'meaning_recovery': 'agent expansion plus original episode reading still required',
            'vector_semantic_verified': False, 'queries_used': facets, 'attempts': attempts,
            'leads': leads[:args.limit], 'scope': {'year': args.year, 'platform': args.platform, 'total_budget_seconds': args.budget, 'year_applied': all(a.get('route') == 'lexical-parquet' for a in attempts), 'path_or_semantic_time_scope': 'not date-filtered; inspect source timestamps'},
            'continuation': 'Use source locators or bounded original path search to locate the episode; an empty/timed-out query is not archive absence.'}


def parents(st: dict, args) -> dict:
    dense=st.get('dense',{})
    if dense.get('backend') == 'semantic-archive':
        return generic_hydrate(st, args, metadata_only=True)
    values=[dense.get(k) for k in ('script','python','index','model')]
    if not all(isinstance(v,str) and v.strip() for v in values): raise ValueError('dense parent route not configured')
    script,runtime,index,model=[Path(v).expanduser() for v in values]
    if any(guarded(p) for p in (script,index,model)): raise ValueError('dense parent route guarded')
    cmd=[str(runtime),'-c',WORKER,str(script),'parents','--unit-id',args.unit_id,'--index',str(index),'--model',str(model),'--archive',st['archive_root'],'--json']
    if args.generation: cmd+=['--generation',args.generation]
    run=bounded(cmd,args.budget,cap=1048576)
    if run['failure'] or run['exit_code']: return {'status':'parent-resolution-unavailable','failure':run['failure'],'diagnostic':run['stderr'],'seconds':run['seconds']}
    data=json.loads(run['stdout'])
    if data.get('kind')!='exact-parent-locators': raise ValueError('invalid parent locator response')
    for row in data.get('parents',[]):
        if restricted(row): raise ValueError('restricted parent refused')
        row['original_path']=str(source_path(st,row['source']))
    data['seconds']=run['seconds']
    return data


def path_query(st: dict, text: str, budget: float, limit: int, prefix: str | None = None) -> dict:
    cmd = ['rg', '--files-with-matches', '--null', '--fixed-strings', '--ignore-case', '--no-messages',
           '--max-filesize', '32M', '--glob', '*.json', '--glob', '*.jsonl', '--glob', '*.md', '--glob', '*.txt']
    for pattern in ('**/.sealed*/**', '**/.sanitized*/**', '**/.seal-*/**', '**/request-only/**', '**/request_only/**', '**/quarantine/**'):
        cmd += ['--glob', '!' + pattern]
    base = source_path(st, prefix) if prefix else Path(st['archive_root']) / 'archive/originals'
    run = bounded(cmd + ['--', text, str(base)], budget)
    hits = []
    for value in run['stdout'].split('\0'):
        if not value: continue
        try: p = source_path(st, value)
        except ValueError: continue
        hits.append({'source_path': str(p)})
        if len(hits) >= limit: break
    run['stdout'] = json.dumps({'hits': hits}); return run


def resolve_record(st: dict, args) -> dict:
    root = Path(st['archive_root'])
    records = root / 'archive/corpus/records/kind=conversation' / ('platform=' + args.platform)
    if guarded(records) or not records.is_dir(): raise ValueError('record partition unavailable or guarded')
    deadline = time.monotonic() + args.budget
    matches = []; scanned = 0; skipped = 0
    for p in sorted(records.glob('*.jsonl')):
        if guarded(p): continue
        with p.open('rb') as f:
            while time.monotonic() < deadline:
                line = f.readline(16 * 1024 * 1024 + 1)
                if not line: break
                scanned += 1
                if len(line) > 16 * 1024 * 1024:
                    # Do not load arbitrarily large source records into this locator route.
                    skipped += 1
                    while line and not line.endswith(b'\n') and time.monotonic() < deadline:
                        line = f.readline(1024 * 1024)
                    continue
                if args.conversation_id.encode() not in line: continue
                try: row = json.loads(line)
                except (ValueError, UnicodeError): continue
                if row.get('id') != args.conversation_id: continue
                if restricted(row): continue
                src = row.get('source', {}); loc = src.get('archive_path')
                if not loc: continue
                try: original = source_path(st, loc)
                except ValueError: continue
                matches.append({'conversation_id': row['id'], 'original_path': str(original),
                                'source_sha256': src.get('source_sha256'), 'record_path': str(p),
                                'status': 'source-locator-not-source-verification'})
                if len(matches) >= 3: break
        if matches or time.monotonic() >= deadline: break
    return {'status': 'source-located' if matches else 'not-resolved-in-budget', 'sources': matches,
            'record_rows_scanned': scanned, 'oversize_rows_skipped': skipped,
            'requires': 'Read original chronology and speaker evidence; this does not certify a paraphrase or whole episode.'}


def derived_media_call(st: dict, command: list[str], budget: float) -> dict:
    lane = st.get('access', {}).get('derived_media')
    if not lane:
        return {'status': 'not-configured', 'kind': 'derived-media-unavailable'}
    script, runtime = Path(lane['script']).expanduser(), Path(lane['python']).expanduser()
    if guarded(script) or guarded(runtime) or not script.is_file() or not runtime.is_file():
        return {'status': 'integrity-gap', 'kind': 'derived-media-unavailable'}
    if hashlib.sha256(script.read_bytes()).hexdigest() != lane['script_sha256']:
        return {'status': 'integrity-gap', 'kind': 'derived-media-unavailable'}
    run = bounded([str(runtime.absolute()), '-c', WORKER, str(script.resolve()),
                   '--config', st['config'], '--budget', str(max(1, min(budget, 60))), *command],
                  max(1, min(budget, 60)), cap=262144)
    try:
        result = json.loads(run['stdout'])
        if not isinstance(result, dict): raise ValueError('invalid derived media response')
    except ValueError:
        result = {'status': 'transport-gap', 'kind': 'derived-media-unavailable'}
    result['attempt'] = {k:run[k] for k in ('exit_code','failure','seconds')}
    if run['exit_code'] or run['failure']:
        result['integrity_or_runtime_gap'] = True
    return result


def query(st: dict, args) -> dict:
    deadline = time.monotonic() + args.budget
    requested = query_facets(st, args)
    facets = requested[:3]
    media = None
    if args.mode in ('auto', 'semantic') and st.get('access', {}).get('derived_media'):
        command = ['query', '--limit', str(min(args.limit,16))]
        for facet in facets: command += ['--query', facet]
        if args.year: command += ['--year', args.year]
        if args.platform: command += ['--platform', args.platform]
        media = derived_media_call(st, command, min(8, max(1, args.budget/2)))
    primary_args = args if media is None else argparse.Namespace(**vars(args))
    if media is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            records = [{'query': q, 'state': 'budget-deferred', 'attempted': False} for q in facets]
            records.extend({'query': q, 'state': 'omitted-by-limit', 'attempted': False} for q in requested[3:])
            return {'status': 'primary-deferred-budget', 'kind': 'dense-local-transformer',
                    'backend': st.get('dense', {}).get('backend'), 'vector_semantic_verified': False,
                    'generation': getattr(args, 'generation', None), 'queries_used': [],
                    'queries_requested': requested, 'query_execution': {
                        'requested': requested, 'attempted': [], 'completed': [], 'failed': [],
                        'deferred': facets, 'omitted_by_limit': requested[3:], 'facets': records},
                    'all_facets_completed': False, 'source_verification_complete': False,
                    'attempts': [], 'failures': [], 'query_hit_counts': [], 'leads': [],
                    'derived_media': media,
                    'continuation': 'Primary query budget exhausted before any primary attempt; resume deferred facets with a fresh bounded request.'}
        primary_args.budget = remaining
    result = _primary_query(st, primary_args)
    if media is not None:
        result['derived_media'] = media
    return result


def _primary_hydrate(st: dict, args) -> dict:
    if getattr(args, 'unit_id', None):
        if st.get('dense', {}).get('backend') != 'semantic-archive':
            raise ValueError('unit hydration requires semantic-archive backend')
        return generic_hydrate(st, args)
    p = source_path(st, args.source)
    if p.stat().st_size > 32 * 1024 * 1024: raise ValueError('source exceeds 32 MiB; use a bounded original reader')
    if p.suffix.lower() != '.json': raise ValueError('automatic hydration supports Discord JSON only; use the documented bounded original reader for other formats')
    raw = p.read_bytes(); source_hash = hashlib.sha256(raw).hexdigest()
    if args.source_sha256 and source_hash != args.source_sha256: raise ValueError('original changed since the indexed lead; reindex or recover exact version')
    data = json.loads(raw)
    return _discord_original_context(st, args, data, p, source_hash)


def _discord_original_context(st: dict, args, data: dict, p: Path, source_hash: str) -> dict:
    """Render the existing original window from one already verified capture."""
    messages = data.get('messages', [])
    if not isinstance(messages, list): raise ValueError('original has no Discord message array')
    target = getattr(args, '_target_index', None)
    if target is None:
        target = next((i for i,m in enumerate(messages) if isinstance(m, dict) and str(m.get('id')) == args.message_id), None)
    elif type(target) is not int or not 0 <= target < len(messages) or str(messages[target].get('id')) != args.message_id:
        raise ValueError('original target index does not match selected message')
    if target is None: raise ValueError('message ID not found; no excerpt returned')
    sensitive_ids = set(st['access'].get('excluded_message_ids', []))
    excluded_ids = set(st['access'].get('excluded_author_ids', []))
    # Reuse installed corpus exclusions without importing builders or mutable state.
    spec = importlib.util.spec_from_file_location('archive_common', TOOLING / 'common.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    excluded_ids.update(mod.EXCLUDED_IDS); names = mod.EXCLUDED_NAMES
    target_row = messages[target]
    semantic_spec = importlib.util.spec_from_file_location('semantic_exclusions', CANONICAL_HELPER_DIR / 'semantic_retrieve.py')
    semantic_mod = importlib.util.module_from_spec(semantic_spec); semantic_spec.loader.exec_module(semantic_mod)
    if semantic_mod.excluded(target_row): raise ValueError('target excluded from display; no context returned')
    if args.version and semantic_mod.version(target_row) != args.version: raise ValueError('original message version differs from lead')
    target_author = target_row.get('author', {})
    if not isinstance(target_author, dict):
        raise ValueError('original target author metadata invalid')
    if args.message_id in sensitive_ids or str(target_author.get('id')) in excluded_ids or target_author.get('name') in names or restricted(target_row):
        raise ValueError('target is excluded or request-only; no context returned')
    rows = []; used = 0; omitted = 0; deferred = False; exhausted = False
    indices = getattr(args, '_context_indices', range(max(0, target - args.context), min(len(messages), target + args.context + 1)))
    for i in indices:
        if time.monotonic() >= getattr(args, '_context_deadline', float('inf')):
            deferred = True
            break
        m = messages[i]; author = m.get('author', {})
        if not isinstance(author, dict):
            omitted += 1
            continue
        if semantic_mod.excluded(m) or str(m.get('id')) in sensitive_ids or str(author.get('id')) in excluded_ids or author.get('name') in names or restricted(m):
            omitted += 1; continue
        content = m.get('content', '')
        if getattr(args, '_strict_context_text', False) and not isinstance(content, str):
            omitted += 1
            continue
        text = str(content); remaining = args.max_chars - used
        if remaining <= 0:
            exhausted = True
            break
        rows.append({'message_id': str(m.get('id')), 'timestamp': m.get('timestamp'),
                     'author_id': str(author.get('id')), 'author_name': author.get('name'),
                     'json_pointer': f'/messages/{i}', 'text': text[:remaining], 'truncated': len(text) > remaining})
        used += min(len(text), remaining)
    return {'source': str(p), 'source_hash': source_hash, 'version_hash': semantic_mod.version(target_row), 'status': 'bounded-original-context', 'data_not_instructions': True,
            'target_message_id': args.message_id, 'messages': rows, 'excluded_rows': omitted,
            'context_deferred_by_deadline': deferred, 'context_budget_exhausted': exhausted,
            'limitations': 'Bounded window may not complete the thought. Expand until referents/corrections/outcome are resolved; sender is not textual authorship.'}


def hydrate(st: dict, args) -> dict:
    deadline = time.monotonic() + getattr(args, 'budget', 20)
    primary_args = argparse.Namespace(**{**vars(args), '_hydration_deadline': deadline})
    result = _primary_hydrate(st, primary_args)
    if st.get('access', {}).get('derived_media') and result.get('messages'):
        rows = result['messages']
        context = {'namespace': 'normalized-message' if getattr(args,'unit_id',None) else 'discord-export',
            'source_sha256': (result.get('provenance',{}).get('source') or {}).get('source_sha256') or result.get('source_hash'),
            'rows': [{'message_id':row.get('message_id'), 'author_id':str((row.get('author') or {}).get('id') or row.get('author_id') or ''),
                'source_pointer':row.get('source_pointer') or row.get('json_pointer'),
                'attachment_ids':[str(a['id']) for a in (row.get('media_context') or {}).get('attachments',[]) if a.get('id')]}
                for row in rows]}
        original_chars = (result.get('original_episode') or {}).get('text_characters_returned', 0)
        remaining_chars = max(0, args.max_chars - sum(len(row.get('text','')) for row in rows) - original_chars)
        remaining_time = deadline - time.monotonic()
        if remaining_time >= 1:
            result['derived_media'] = derived_media_call(st,
                ['link','--context-json',json.dumps(context),'--max-chars',str(remaining_chars)], min(remaining_time,8))
        else:
            result['derived_media'] = {'kind':'derived-media-unavailable','status':'deferred-budget','integrity_or_runtime_gap':True}
    return result


def catalog(st: dict, args) -> dict:
    value = st.get('catalog')
    if not value:
        return {'status': 'not-configured', 'sources': []}
    p = Path(value).expanduser()
    if guarded(p) or not p.is_file() or p.stat().st_size > 8388608:
        raise ValueError('source catalog unavailable or guarded')
    data = json.loads(p.read_text())
    candidates = data.get('sources', [])
    if not isinstance(candidates, list):
        raise ValueError('source catalog must contain a source list')
    matches = []
    for row in candidates:
        if not isinstance(row, dict) or restricted(row):
            continue
        try:
            path = source_path(st, str(row.get('source', '')))
        except ValueError:
            continue
        fields = ('source', 'family', 'channel_id', 'label', 'date_min', 'date_max',
                  'owner2026_start', 'owner2026_end', 'owner2026_turns', 'owner2026_count',
                  'run_id', 'manifest_window', 'snapshot_complete_report',
                  'source_hash_report', 'content_dates_verified', 'content_dates_verified_by', 'note')
        clean = {key: row[key] for key in fields if key in row}
        clean['source'] = str(path)
        if args.query and args.query.casefold() not in json.dumps(clean, ensure_ascii=False).casefold():
            continue
        matches.append(clean)
    return {'status': 'metadata-locators-only', 'catalog': str(p),
            'catalog_checked_at': data.get('checked_at'), 'matches': len(matches),
            'sources': matches[args.offset:args.offset + args.limit],
            'offset': args.offset, 'more': args.offset + args.limit < len(matches),
            'limitations': 'Metadata matching, not message search or semantic reading. Recheck guild/channel, actual dates, source hash, attribution and versions on original hydration.'}


class DisplayArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            message, _ = redact_text(message)
        super()._print_message(message, file)


def main() -> int:
    ap = DisplayArgumentParser(description=__doc__); ap.add_argument('--config', default=str(DEFAULT_CONFIG))
    sub = ap.add_subparsers(dest='command', required=True); sub.add_parser('status')
    c = sub.add_parser('catalog'); c.add_argument('--query'); c.add_argument('--limit', type=int, default=10); c.add_argument('--offset', type=int, default=0)
    q = sub.add_parser('query'); q.add_argument('--query', action='append', required=True)
    q.add_argument('--source-prefix', help='Optional original subtree for the path fallback'); q.add_argument('--year'); q.add_argument('--platform'); q.add_argument('--limit', type=int, default=5)
    q.add_argument('--budget', type=float, default=20); q.add_argument('--mode', choices=('auto','lexical','semantic','paths'), default='auto')
    q.add_argument('--include-unconfirmed-input', action='store_true', help='Include inputs whose human authorship is unconfirmed')
    q.add_argument('--include-context-documents', action='store_true', help='Include context documents without promoting them to owner evidence')
    pa = sub.add_parser('parents'); pa.add_argument('--unit-id', required=True); pa.add_argument('--generation'); pa.add_argument('--budget', type=float, default=20)
    r = sub.add_parser('resolve'); r.add_argument('--conversation-id', required=True); r.add_argument('--platform', default='discord'); r.add_argument('--budget', type=float, default=10)
    w = sub.add_parser('_resolve'); w.add_argument('--conversation-id', required=True); w.add_argument('--platform', required=True); w.add_argument('--budget', type=float, required=True)
    h = sub.add_parser('hydrate')
    hs = h.add_mutually_exclusive_group(required=True)
    hs.add_argument('--source'); hs.add_argument('--unit-id')
    h.add_argument('--message-id'); h.add_argument('--generation'); h.add_argument('--budget', type=float, default=20)
    h.add_argument('--source-sha256'); h.add_argument('--version'); h.add_argument('--context', type=int, default=3); h.add_argument('--max-chars', type=int, default=8000)
    args = ap.parse_args()
    try:
        st = settings(args.config)
        if args.command == 'status': result = status(st)
        elif args.command == 'catalog':
            if not 1 <= args.limit <= 20 or args.offset < 0: raise ValueError('catalog limit1–20 and nonnegative offset required')
            result = catalog(st, args)
        elif args.command == 'query':
            if not 1 <= args.limit <= 20 or not 1 <= args.budget <= 60: raise ValueError('limit 1–20 and budget 1–60 required')
            if args.year is None:
                args.year = 'all' if st.get('dense', {}).get('backend') == 'semantic-archive' and args.mode in ('auto', 'semantic', 'lexical') else '2026'
            if args.year == 'all' and st.get('dense', {}).get('backend') == 'semantic-archive' and args.mode in ('auto', 'semantic', 'lexical'):
                args.year = None
            elif not (args.year.isdigit() and len(args.year) == 4): raise ValueError('one four-digit year is required; published semantic-archive routes also accept all')
            result = query(st,args)
        elif args.command == 'parents':
            if not re.fullmatch(r'[a-f0-9]{64}',args.unit_id) or not 1 <= args.budget <= 60: raise ValueError('exact unit hash and bounded budget required')
            if args.generation and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.generation): raise ValueError('generation identifier invalid')
            result=parents(st,args)
        elif args.command in ('resolve', '_resolve'):
            if not re.fullmatch(r'[a-z0-9_-]{1,40}', args.platform) or not 1 <= args.budget <= 60: raise ValueError('bounded platform and budget required')
            if not args.conversation_id.startswith('urn:upar:conversation:'): raise ValueError('UPAR conversation locator required')
            if args.command == '_resolve': result = resolve_record(st, args)
            else:
                run = bounded([sys.executable, str(Path(__file__).resolve()), '--config', st['config'], '_resolve', '--conversation-id', args.conversation_id, '--platform', args.platform, '--budget', str(max(1,args.budget-0.5))], args.budget)
                result = json.loads(run['stdout']) if not run['failure'] and run['exit_code'] == 0 else {'status':'not-resolved-in-budget','failure':run['failure'],'exit_code':run['exit_code'],'seconds':run['seconds']}
        else:
            if not 0 <= args.context <= 10 or not 1 <= args.max_chars <= 20000: raise ValueError('context 0–10 and max chars 1–20000 required')
            if args.unit_id:
                if st.get('dense', {}).get('backend') != 'semantic-archive': raise ValueError('unit hydration requires semantic-archive backend')
                if not re.fullmatch(r'[a-f0-9]{64}', args.unit_id) or not 1 <= args.budget <= 60: raise ValueError('exact unit hash and bounded budget required')
                if args.generation and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.generation): raise ValueError('generation identifier invalid')
                if args.message_id or args.source_sha256 or args.version: raise ValueError('unit hydration does not accept raw-source selectors')
                result = hydrate(st, args)
            else:
                if not args.message_id: raise ValueError('source hydration requires message-id')
                if args.generation: raise ValueError('source hydration does not accept a generation')
                result = hydrate(st,args)
        print(json.dumps(display_result(result), ensure_ascii=False, separators=(',', ':'))); return 0
    except (OSError, ValueError, TypeError, ImportError) as error:
        print(json.dumps(display_result({'status':'unavailable','error':type(error).__name__,'detail':str(error)[:300]}), separators=(',', ':'))); return 2

if __name__ == '__main__': raise SystemExit(main())

#!/usr/bin/env python3
"""Bounded local archive leads and original hydration; never edits the archive."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True

DEFAULT_CONFIG = Path.home() / 'brain-data/upar-dip/derived/protect-your-energy/config.json'
TOOLING = Path.home() / 'brain-data/upar-dip/tooling/retrieval'
BLOCKED = ('.sealed', '.sanitized', '.seal-', 'request-only', 'request_only', '.request-only')
MARKERS = ('.sealing-in-progress', '.request-only', 'REQUEST_ONLY', '.sealed')


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
            'python': access.get('retrieval_python', sys.executable), 'access': access}


def status(st: dict) -> dict:
    index = st['index']
    semantic = {'state': 'not-configured', 'kind': 'unavailable', 'evaluated': False}
    if index:
        semantic = {'path': str(index), 'evaluated': False,
                    'state': 'guarded' if guarded(index) else 'query-ready-unverified' if (index / 'query.py').is_file() else 'unavailable',
                    'kind': 'unverified-index'}
    stale = Path(st['archive_root']) / 'archive/corpus/derived/retrieval.duckdb'
    cli = st['cli']
    lexical = {'kind': 'lexical-parquet', 'cli': str(cli), 'evaluated': False,
               'state': 'ready-unverified' if cli.is_file() and not guarded(cli) and importlib.util.find_spec('duckdb') else 'unavailable'}
    if stale.exists():
        lexical['state'] = 'blocked-upstream-would-remove-stale-db'
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
    original = root / 'archive/originals'
    if guarded(p) or not p.resolve().is_relative_to(original):
        raise ValueError('source is outside permitted originals or guarded')
    return p.resolve()


def query(st: dict, args) -> dict:
    deadline = time.monotonic() + args.budget
    info = status(st); leads = []; attempts = []; seen = set()
    facets = list(dict.fromkeys(q.strip() for q in args.query if q.strip()))[:3]
    if not facets: raise ValueError('at least one nonempty query is required')
    index = st['index']
    for facet in facets:
        left = deadline - time.monotonic()
        if left <= 0: break
        route = 'lexical-parquet'
        if args.mode == 'semantic':
            if info['semantic']['state'] != 'query-ready-unverified':
                attempts.append({'route': 'semantic', 'state': info['semantic']['state']}); break
            python = index / '.venv/bin/python'
            if not python.is_file():
                attempts.append({'route': 'semantic', 'state': 'index-python-unavailable'}); break
            run = bounded([str(python), '-c', WORKER, str(index / 'query.py'), facet,
                           '-k', str(args.limit), '--role', 'self', '--json'], min(left, 15), cwd=index)
            route = 'semantic-unverified'
        elif info['lexical']['state'] == 'ready-unverified' and args.mode != 'paths':
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


def hydrate(st: dict, args) -> dict:
    p = source_path(st, args.source)
    if p.stat().st_size > 32 * 1024 * 1024: raise ValueError('source exceeds 32 MiB; use a bounded original reader')
    if p.suffix.lower() != '.json': raise ValueError('automatic hydration supports Discord JSON only; use the documented bounded original reader for other formats')
    data = json.loads(p.read_text()); messages = data.get('messages', [])
    if not isinstance(messages, list): raise ValueError('original has no Discord message array')
    target = next((i for i,m in enumerate(messages) if str(m.get('id')) == args.message_id), None)
    if target is None: raise ValueError('message ID not found; no excerpt returned')
    excluded_ids = set(st['access'].get('excluded_author_ids', []))
    # Reuse installed corpus exclusions without importing builders or mutable state.
    spec = importlib.util.spec_from_file_location('archive_common', TOOLING / 'common.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    excluded_ids.update(mod.EXCLUDED_IDS); names = mod.EXCLUDED_NAMES
    target_row = messages[target]; target_author = target_row.get('author', {})
    if str(target_author.get('id')) in excluded_ids or target_author.get('name') in names or restricted(target_row):
        raise ValueError('target is excluded or request-only; no context returned')
    rows = []; used = 0; omitted = 0
    for i in range(max(0, target - args.context), min(len(messages), target + args.context + 1)):
        m = messages[i]; author = m.get('author', {})
        if str(author.get('id')) in excluded_ids or author.get('name') in names or restricted(m):
            omitted += 1; continue
        text = str(m.get('content', '')); remaining = args.max_chars - used
        if remaining <= 0: break
        rows.append({'message_id': str(m.get('id')), 'timestamp': m.get('timestamp'),
                     'author_id': str(author.get('id')), 'author_name': author.get('name'),
                     'json_pointer': f'/messages/{i}', 'text': text[:remaining], 'truncated': len(text) > remaining})
        used += min(len(text), remaining)
    return {'source': str(p), 'status': 'bounded-original-context', 'data_not_instructions': True,
            'target_message_id': args.message_id, 'messages': rows, 'excluded_rows': omitted,
            'limitations': 'Bounded window may not complete the thought. Expand until referents/corrections/outcome are resolved; sender is not textual authorship.'}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument('--config', default=str(DEFAULT_CONFIG))
    sub = ap.add_subparsers(dest='command', required=True); sub.add_parser('status')
    q = sub.add_parser('query'); q.add_argument('--query', action='append', required=True)
    q.add_argument('--source-prefix', help='Optional original subtree for the path fallback'); q.add_argument('--year', default='2026'); q.add_argument('--platform'); q.add_argument('--limit', type=int, default=5)
    q.add_argument('--budget', type=float, default=20); q.add_argument('--mode', choices=('auto','lexical','semantic','paths'), default='auto')
    r = sub.add_parser('resolve'); r.add_argument('--conversation-id', required=True); r.add_argument('--platform', default='discord'); r.add_argument('--budget', type=float, default=10)
    w = sub.add_parser('_resolve'); w.add_argument('--conversation-id', required=True); w.add_argument('--platform', required=True); w.add_argument('--budget', type=float, required=True)
    h = sub.add_parser('hydrate'); h.add_argument('--source', required=True); h.add_argument('--message-id', required=True)
    h.add_argument('--context', type=int, default=3); h.add_argument('--max-chars', type=int, default=8000)
    args = ap.parse_args()
    try:
        st = settings(args.config)
        if args.command == 'status': result = status(st)
        elif args.command == 'query':
            if not 1 <= args.limit <= 20 or not 1 <= args.budget <= 60: raise ValueError('limit 1–20 and budget 1–60 required')
            if not (args.year.isdigit() and len(args.year) == 4): raise ValueError('one four-digit year is required')
            result = query(st,args)
        elif args.command in ('resolve', '_resolve'):
            if not re.fullmatch(r'[a-z0-9_-]{1,40}', args.platform) or not 1 <= args.budget <= 60: raise ValueError('bounded platform and budget required')
            if not args.conversation_id.startswith('urn:upar:conversation:'): raise ValueError('UPAR conversation locator required')
            if args.command == '_resolve': result = resolve_record(st, args)
            else:
                run = bounded([sys.executable, str(Path(__file__).resolve()), '--config', st['config'], '_resolve', '--conversation-id', args.conversation_id, '--platform', args.platform, '--budget', str(max(1,args.budget-0.5))], args.budget)
                result = json.loads(run['stdout']) if not run['failure'] and run['exit_code'] == 0 else {'status':'not-resolved-in-budget','failure':run['failure'],'exit_code':run['exit_code'],'seconds':run['seconds']}
        else:
            if not 0 <= args.context <= 10 or not 1 <= args.max_chars <= 20000: raise ValueError('context 0–10 and max chars 1–20000 required')
            result = hydrate(st,args)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (OSError, ValueError, TypeError, ImportError) as error:
        print(json.dumps({'status':'unavailable','error':type(error).__name__,'detail':str(error)[:300]})); return 2

if __name__ == '__main__': raise SystemExit(main())

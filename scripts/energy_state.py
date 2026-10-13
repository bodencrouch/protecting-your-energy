#!/usr/bin/env python3
"""Store small private continuity records with versions and atomic replacement."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

KINDS = {'method', 'idea', 'task', 'decision', 'correction', 'opportunity', 'run',
         'episode', 'interpretation', 'frontier'}
DEFAULT = Path.home() / 'brain-data/upar-dip/derived/protect-your-energy/state.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=DEFAULT)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('summary')
    show = sub.add_parser('show')
    show.add_argument('kind', choices=sorted(KINDS))
    show.add_argument('id', nargs='?')
    put = sub.add_parser('put')
    put.add_argument('kind', choices=sorted(KINDS))
    put.add_argument('id')
    put.add_argument('--expected-revision', type=int)
    args = parser.parse_args(argv)
    requested = args.state.expanduser().absolute()
    if requested.is_symlink():
        raise ValueError('state must not be a symlink')
    path = requested.parent.resolve() / requested.name
    archive = (Path.home() / 'boden_wizard_chat_context').resolve()
    if path == archive or archive in path.parents:
        raise ValueError('continuity state must remain outside the raw archive')
    if any(part.startswith(('.sealed', '.sanitized')) for part in path.parts):
        raise ValueError('request-only stores are not continuity output locations')
    payload = None
    if args.action == 'put':
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}', args.id):
            raise ValueError('record ID must be a short stable identifier')
        raw = sys.stdin.buffer.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('continuity record exceeds 1 MiB')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('continuity record must be a JSON object')
        json.dumps(payload, allow_nan=False)
    if args.action != 'put' and not path.exists():
        print(json.dumps({'version': 1, 'revision': 0, 'counts': {}, 'state': 'not-created'}))
        return 0
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = path.with_suffix(path.suffix + '.lock')
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('continuity lock must be a regular file')
        fcntl.flock(stream, fcntl.LOCK_EX)
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_size > 16777216)):
            raise ValueError('state must be a regular JSON file of at most 16 MiB')
        state = json.loads(path.read_text()) if path.exists() else {'version': 1, 'revision': 0, 'records': {}}
        if state.get('version') != 1 or not isinstance(state.get('records'), dict):
            raise ValueError('unsupported continuity state schema')
        if args.action == 'summary':
            print(json.dumps({'version': 1, 'revision': state['revision'], 'counts': {k: len(v) for k, v in state['records'].items()}}))
            return 0
        bucket = state['records'].get(args.kind, {})
        if args.action == 'show':
            result = bucket.get(args.id) if args.id else bucket
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.expected_revision is not None and state['revision'] != args.expected_revision:
            raise ValueError('state revision changed; reread before updating')
        previous = bucket.get(args.id)
        if previous and previous['current'] == payload:
            print(json.dumps({'stored': args.kind, 'id': args.id, 'changed': False, 'revision': state['revision']}))
            return 0
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        versions = list(previous.get('versions', [])) if previous else []
        if previous:
            versions.append({'recorded_at': previous['recorded_at'], 'data': previous['current']})
        bucket[args.id] = {'current': payload, 'recorded_at': now, 'versions': versions}
        state['records'][args.kind] = bucket
        state['revision'] += 1
        encoded = json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        if len(encoded.encode()) > 16777216:
            raise ValueError('state exceeds 16 MiB; preserve and archive history before continuing')
        name = None
        try:
            with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.state-', delete=False, encoding='utf-8') as output:
                name = output.name
                os.chmod(name, 0o600)
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, path)
            name = None
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if name:
                Path(name).unlink(missing_ok=True)
        print(json.dumps({'stored': args.kind, 'id': args.id, 'changed': True, 'revision': state['revision']}))
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('energy-state: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
"""Prepare source-qualified context from a bounded native prompt submission.

This entry point neither registers hooks nor retains submission bodies. Its
hook output is a protocol envelope, not evidence of host delivery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True
COMPILER_SHA = '7929d2e4db60d784249545d815fa499c925c15132d475f9cd1ddc0152d54828f'
INPUT_LIMIT = 256 * 1024
OUTPUT_LIMIT = 1024 * 1024


class SubmissionError(ValueError):
    pass


def compiler_module():
    path = Path(__file__).resolve().with_name('continuation.py')
    if path.is_symlink() or path.resolve().parent != Path(__file__).resolve().parent:
        raise SubmissionError('compiler-location-invalid')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != COMPILER_SHA:
        raise SubmissionError('compiler-hash-mismatch')
    module = types.ModuleType('pye_submission_compiler')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def submission_args(compiler, event, cli):
    if not isinstance(event, dict):
        raise SubmissionError('submission-not-object')
    event_name = event.get('hook_event_name')
    if cli.hook_output and event_name != 'UserPromptSubmit':
        raise SubmissionError('submission-event-not-user-prompt')
    if event_name is not None and event_name != 'UserPromptSubmit':
        raise SubmissionError('submission-event-not-user-prompt')
    available = [event[key] for key in ('prompt', 'user_prompt') if key in event]
    if (not available or any(not isinstance(value, str) for value in available)
            or any(value != available[0] for value in available[1:])):
        raise SubmissionError('submission-prompt-missing-or-conflicting')
    cue = available[0].strip()
    if not cue or len(cue) > 4096:
        raise SubmissionError('submission-prompt-outside-bounds')
    corrections = event.get('corrections', [])
    if (not isinstance(corrections, list) or len(corrections) > 8
            or any(not isinstance(item, str) or not item.strip() or len(item) > 4096
                   for item in corrections)):
        raise SubmissionError('submission-corrections-outside-bounds')

    # Substantive requests and explicit task choices do not consult an execution
    # association. In particular an inner client cannot inherit a different
    # harness's context identity merely by being launched by that host.
    context_id = None
    context_basis = 'not-used'
    if cli.task_id is None and compiler.is_bare_resume(cue):
        context_id = cli.execution_context_id
        if context_id is not None:
            context_basis = 'explicit-cli-context'
        else:
            context_id = event.get('session_id')
            if context_id is not None:
                context_basis = 'submission-session-id'
            elif cli.harness == 'codex':
                context_id = os.environ.get('CODEX_THREAD_ID') or None
                context_basis = 'codex-host-environment' if context_id else 'not-supplied'
            else:
                context_basis = 'not-supplied'
        if context_id is not None and not isinstance(context_id, str):
            raise SubmissionError('submission-context-id-invalid')
        if cli.harness == 'claude' and context_id is None:
            raise SubmissionError('claude-context-id-not-supplied')
        if (cli.execution_context_id is not None and event.get('session_id') is not None
                and cli.execution_context_id != event['session_id']):
            raise SubmissionError('submission-context-id-conflict')
    args = argparse.Namespace(
        cue=[cue], task_id=cli.task_id, execution_harness=cli.harness if context_id is not None else None,
        execution_context_id=context_id, query=[], probe=[], unit_id=[],
        correction=corrections, corrections_stdin=False, timeout=cli.timeout, full=False)
    return args, {'harness': cli.harness, 'context_basis': context_basis,
                  'context_id': context_id, 'human_identity_authenticated': False,
                  'hook_delivery_observed': False, 'submission_body_stored': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', choices=('codex', 'claude'), required=True)
    parser.add_argument('--execution-context-id')
    parser.add_argument('--task-id')
    parser.add_argument('--hook-output', action='store_true')
    parser.add_argument('--retrieve', action='store_true', help='Also use the existing canonical retrieval route')
    parser.add_argument('--timeout', type=float, default=90)
    cli = parser.parse_args(argv)
    try:
        if cli.hook_output and cli.retrieve:
            raise SubmissionError('canonical-retrieval-requires-explicit-host-invocation')
        raw = sys.stdin.buffer.read(INPUT_LIMIT + 1)
        if len(raw) > INPUT_LIMIT:
            raise SubmissionError('submission-input-over-limit')
        compiler = compiler_module()
        args, provenance = submission_args(compiler, compiler.parse_json(raw), cli)
        if cli.hook_output and not compiler.is_bare_resume(args.cue[0]):
            print('{}')
            return 0
        prepared = compiler.prepare_context(args)
        if cli.hook_output and prepared['selection']['status'] != 'selected':
            raise SubmissionError('submission-continuation-not-selected')
        native = prepared['current_native_sources']
        artifact = prepared['selected_artifact']
        if native.get('configured') and native.get('status') != 'read':
            raise SubmissionError('required-current-original-sources-unavailable')
        if artifact.get('required') and artifact.get('status') != 'read':
            raise SubmissionError('required-selected-proposal-unavailable')
        result = compiler.retrieve_context(prepared, args) if cli.retrieve else prepared
        result['submission_transport'] = provenance
        rendered = compiler.display_module()(result)
        encoded = json.dumps(rendered, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        output = {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
                                        'additionalContext': encoded}} if cli.hook_output else rendered
        final = json.dumps(output, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(final.encode('utf-8')) > OUTPUT_LIMIT:
            raise SubmissionError('submission-context-over-output-limit')
        print(final)
        return 0
    except (ValueError, OSError, TypeError, KeyError, UnicodeError, RecursionError):
        failure = {'status': 'context-not-delivered', 'error': 'submission-context-unavailable',
                   'hook_delivery_observed': False, 'submission_body_stored': False}
        output = {'systemMessage': 'Protect Your Energy continuation context is unavailable.'} if cli.hook_output else failure
        print(json.dumps(output, separators=(',', ':')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

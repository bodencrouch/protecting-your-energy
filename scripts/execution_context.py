"""Resolve an explicit execution association in already authenticated task state.

This module does not discover callers, read files, inspect source threads, or
interpret a cue. The caller supplies its resume-intent decision and context.
"""
from __future__ import annotations

import re

TASK_LIMIT = 512
BINDING_LIMIT = 64
DISPLAY_LIMIT = 12
TASK_ID = re.compile(r'[A-Za-z0-9_.:-]{1,96}\Z')
CONTEXT_ID = re.compile(r'[A-Za-z0-9_.:-]{1,256}\Z')
HARNESSES = frozenset(('codex', 'claude'))
QUALIFIERS = (
    'Selection uses an explicitly recorded execution association, not semantic retrieval.',
    'Caller context is supplied by the host; it does not independently authenticate a human.',
    'An execution association does not establish owner adoption, completion, or source authorship.',
    'Enabled bindings establish applicability; timestamps, source threads, paths, and silence do not.',
)


def _result(status, **fields):
    return {'status': status, 'method': 'explicit-execution-context',
            'semantic_selection': False, 'qualifiers': list(QUALIFIERS),
            **fields}, None


def _invalid(code):
    return _result('invalid-execution-context', error=code, lexical_fallback=False,
                   selection_permitted=False)


def resolve_execution_context(state, *, harness=None, context_id=None,
                              unqualified_resume=False):
    """Return ``(selection, original_task_row_or_None)`` without mutating state.

    Invoke before lexical selection only for an unqualified resume without an
    explicit task ID. ``not-applicable`` permits the unchanged lexical route.
    ``needs-selection`` and ``invalid-execution-context`` must not fall through
    to a lexical guess. Supplied state must have been read with the caller's
    existing private-file guards; this pure function adds no file proof.

    The optional binding ``basis`` is metadata only and is not returned or used
    as evidence. Missing context or no enabled matching association permits
    fallback. A malformed configured association makes unique selection
    unproven, so it is refused during an eligible lookup.
    """
    if type(unqualified_resume) is not bool:
        return _invalid('resume-gate-invalid')
    if not unqualified_resume:
        return _result('not-applicable', reason='cue-not-unqualified-resume',
                       lexical_fallback=True, selection_permitted=False)
    if harness is None or context_id is None:
        return _result('not-applicable', reason='caller-context-not-supplied',
                       lexical_fallback=True, selection_permitted=False)
    if (not isinstance(harness, str) or harness not in HARNESSES
            or not isinstance(context_id, str) or not CONTEXT_ID.fullmatch(context_id)):
        return _invalid('caller-context-invalid')
    if not isinstance(state, dict) or not isinstance(state.get('records', {}), dict):
        return _invalid('state-records-invalid')
    rows = state.get('records', {}).get('task', {})
    if not isinstance(rows, dict) or len(rows) > TASK_LIMIT:
        return _invalid('task-bucket-invalid-or-over-limit')

    matches = []
    for task_id, row in rows.items():
        # Preserve the lexical selector's treatment of unrelated malformed
        # records. A record claiming execution associations cannot be skipped:
        # skipping it would falsely establish that another match is unique.
        current = row.get('current') if isinstance(row, dict) else None
        if not isinstance(current, dict) or 'execution_contexts' not in current:
            continue
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            return _invalid('bound-task-id-invalid')
        bindings = current['execution_contexts']
        if not isinstance(bindings, list) or len(bindings) > BINDING_LIMIT:
            return _invalid('execution-bindings-invalid-or-over-limit')
        seen = set()
        matched = False
        for binding in bindings:
            if not isinstance(binding, dict):
                return _invalid('execution-binding-not-object')
            bound_harness = binding.get('harness')
            bound_id = binding.get('context_id')
            enabled = binding.get('enabled')
            if (not isinstance(bound_harness, str) or bound_harness not in HARNESSES
                    or not isinstance(bound_id, str) or not CONTEXT_ID.fullmatch(bound_id)
                    or type(enabled) is not bool):
                return _invalid('execution-binding-fields-invalid')
            if 'basis' in binding and not isinstance(binding['basis'], dict):
                return _invalid('execution-binding-basis-invalid')
            identity = (bound_harness, bound_id)
            if identity in seen:
                return _invalid('execution-binding-duplicate')
            seen.add(identity)
            if enabled and identity == (harness, context_id):
                matched = True
        if matched:
            goal = current.get('goal', '')
            if not isinstance(goal, str):
                return _invalid('bound-task-goal-invalid')
            matches.append({'task_id': task_id, 'goal': goal[:512],
                            'goal_truncated': len(goal) > 512})

    matches.sort(key=lambda candidate: candidate['task_id'])
    common = {'caller_context': {'harness': harness, 'context_id': context_id,
                                 'provenance': 'caller-supplied; not independently authenticated'},
              'candidate_count': len(matches), 'candidates': matches[:DISPLAY_LIMIT],
              'candidates_truncated': len(matches) > DISPLAY_LIMIT,
              'available_task_count': len(rows)}
    if not matches:
        return _result('not-applicable', reason='no-enabled-execution-association',
                       lexical_fallback=True, selection_permitted=False, **common)
    if len(matches) != 1:
        return _result('needs-selection', reason='execution-association-ambiguous',
                       lexical_fallback=False, selection_permitted=False,
                       selection_option='--task-id', **common)
    selected_id = matches[0]['task_id']
    selection, _ = _result('selected', task_id=selected_id, lexical_fallback=False,
                           selection_permitted=True, **common)
    return selection, rows[selected_id]

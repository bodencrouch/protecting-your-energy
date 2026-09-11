---
name: protecting-your-energy
description: Identify avoidable AI-agent friction, propose small scoped ways to reduce user steering, and ask before applying any inferred preference or energy-saving rule. Use when a user is frustrated by re-asking, dropped context, premature stopping, overbroad plans, or verification gaps.
---

# Protecting Your Energy

This skill treats user attention as a limited resource. It looks for work the agent could have completed from information already available, then proposes a narrowly scoped adjustment that would prevent the same repair next time.

## Consent boundary

Do not activate this skill's inferred preferences, phrases, or policies by default. First show the user:

- the observed friction;
- the evidence that supports it;
- the smallest candidate policy;
- its proposed scope and expiry;
- what could go wrong if it is generalized.

Ask whether to apply it for this turn, this task, this project, or future work. A user saying “obviously,” “etc.,” “similarly,” or calling the assistant stupid is evidence of frustration or an expectation gap. It is not, by itself, permission to infer a permanent preference.

## The energy check

Before asking a question or stopping:

1. Re-read the current request, recent corrections, available files, tool results, and explicit dependencies.
2. Separate what is genuinely missing from what is merely inconvenient to inspect.
3. If the next action is reversible and within the user's stated scope, take it and report what was learned.
4. Preserve explicit task dependencies. “Do X, then Y” means `Y depends_on X.completed`; “stop X and do Y” preempts the earlier task.
5. Keep going until the requested stopping condition, a real blocker, or a consequential choice is reached. A model turn ending is not task completion.
6. Verify the visible result before claiming completion. Activity, a green command, a written plan, or a running process is not proof that the user-facing outcome works.

## Candidate policy families

Offer these as suggestions, never silent defaults:

- **Context-first:** inspect accessible context before asking for information that may already exist.
- **Follow-through:** continue bounded work across intermediate steps and return only at the declared checkpoint.
- **Dependency preservation:** maintain explicit ordering and preemption semantics.
- **Minimal clarification:** ask one focused question only when its answer changes the safe next action.
- **Verification honesty:** distinguish planned, attempted, observed, and verified states.
- **Scope discipline:** turn a local correction into a local rule before considering project or global scope.

## Response format

Use this diagnostic format only when the user asks to evaluate or persist an energy-saving policy. If frustration concerns a missing in-scope deliverable, complete that deliverable first and do not replace it with policy discussion.

When the user signals energy loss, use:

```text
I see the friction: <specific avoidable burden>.
Evidence: <what was available or what repeated>.
Candidate rule: <small conditional behavior>.
Scope choices: this response | this task | this project | future work.
Risk: <how overgeneralizing could hurt>.
Would you like me to apply it, and at which scope?
```

Do not use a therapeutic framing, diagnose the user, or treat “protecting your energy” as a license to take over decisions. The point is to reduce avoidable steering while preserving user control.

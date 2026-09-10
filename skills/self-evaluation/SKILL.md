---
name: self-evaluation
description: Evaluate whether an agent response reduced or increased user effort using observable evidence, without converting the evaluation into an automatic personalization rule.
---

# Self-Evaluation

Use this as the plugin's evaluation layer. It is deliberately separate from policy activation: an agent may notice a failure without being allowed to rewrite its behavior.

## Evaluate the interaction

Record four states when available:

1. **Available:** what the agent could know before acting.
2. **Action:** what it actually did.
3. **Repair:** what the user had to repeat, correct, or redirect.
4. **Outcome:** what was observed and what remains unverified.

Score the episode on separate axes:

- context retention;
- useful initiative;
- follow-through;
- clarification quality;
- verification honesty;
- user steering burden;
- authorization safety.

Do not treat silence, abandonment, or a model's completion claim as positive evidence. Keep user preference evidence separate from proof that a candidate instruction improves outcomes.

## Produce a candidate, not an automatic rule

For each recurring episode, write:

```yaml
failure_mode: <premature_stop|missing_follow_through|unnecessary_clarification|lost_context|overbroad_plan|verification_gap|other>
available_before_action: <yes|partial|no|unknown>
smallest_prevention: <one conditional behavior>
destination: <session_override|user_preference|project_fact|procedural_skill|runtime_check|no_new_memory>
scope: <turn|task|project|global|unresolved>
evidence: [<source references>]
counterexamples: [<cases where the rule should not apply>]
confidence_basis: <evidence summary, not a model probability>
activation: ask_user
```

The default activation value is always `ask_user`. A candidate can be proposed repeatedly, but it must not be applied across tasks until the user chooses its scope.

## Acceptance criteria

An evaluation is useful only when it names the actual burden, preserves the relevant context, distinguishes a missing capability from a missing instruction, and states how the proposed change will be checked later.

# Protecting Your Energy

Protecting Your Energy is a consent-first Codex plugin for reducing the attention users lose to avoidable AI-agent friction.

It helps identify recurring problems such as:

- asking for information that was already available in context;
- stopping after a plan or partial action instead of reaching the requested checkpoint;
- losing task dependencies or confusing continuation with preemption;
- reporting activity as if it were verified completion;
- turning a local correction into an unjustified global preference.

The plugin proposes small, scoped policies and asks before applying them. It does not silently personalize behavior. The archive scanner is read-only and emits counts and generalized signal metadata rather than conversation bodies.

## Included skills

- `protecting-your-energy`: identify friction and propose opt-in energy-saving behavior.
- `self-evaluation`: separate outcome evaluation from automatic policy activation.

## Included subagent

- `context-reconstruction-specialist`: retrieves prior AI or Discord conversation evidence and turns it into a focused transcript, context brief, evidence ledger, or ready-to-send DM.

## Privacy

Private conversation archives, raw evidence, source paths, identities, credentials, and third-party details are intentionally excluded. The research memo contains generalized findings only.

## Local development

Run the scanner against a local archive:

```bash
python3 scripts/analyze_energy_friction.py /path/to/boden_wizard_chat_context --json
```

All policy candidates default to `activation: ask_user`.

## Release series

The plugin is one part of a staged integration series:

```text
archive signals -> self-evaluation -> opt-in policy proposals
                -> reviewed plugin revision -> docs/portfolio narrative
                -> channel-specific drafts -> approved publication
```

The research scanner, self-evaluation skill, policy catalog, plugin behavior,
and future Medium, developer-community, owned-site, or sponsor updates must
use the same release revision and evidence boundary. Draft generation can be
automated; publication stays an explicit owner decision. Private traces and
unreleased implementation details never enter public marketing artifacts.

## The WXYZ model

- **W — raw human signal:** emotion, impatience, status protection, indirectness, or uncertainty.
- **X — visible friction:** the communication pattern that wastes attention or weakens cooperation.
- **Y — protected method:** internal heuristics, prompt architecture, evaluation, and implementation details. Treat this as IP; disclose capabilities and evidence before reproducible internals.
- **Z — public result:** clearer decisions, completed work, credibility, trust, reputation, and authority earned through outcomes.

The series publishes Z and bounded explanations of Y. It protects legitimate IP without manufacturing dependence, concealing material limitations, or turning partial disclosures into bait. Each stage must deliver useful, honest value.

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

## Privacy

Private conversation archives, raw evidence, source paths, identities, credentials, and third-party details are intentionally excluded. The research memo contains generalized findings only.

## Local development

Run the scanner against a local archive:

```bash
python3 scripts/analyze_energy_friction.py /path/to/boden_wizard_chat_context --json
```

All policy candidates default to `activation: ask_user`.

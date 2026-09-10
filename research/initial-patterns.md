# Initial interaction-friction research

This is a generalized research memo derived from the local AI conversation working index and its task/session extracts. It is intentionally not a raw conversation export. Message bodies, names, IDs, URLs, paths, secrets, and third-party identifiers stay outside the plugin.

## What the first pass found

The archive index reports 4,794 ChatGPT conversations and later agentic material including Claude Code, Cursor, Codex, Gemini, Copilot, and other platforms. The bundled working slice contains 100 task extracts and 78 session summaries. The current scan uses “obviously,” “clearly,” “surely,” “etc.”, “similarly,” “common sense,” continuation language, and intelligence insults as discovery markers.

These markers do not mean the user literally wants an always-on inference rule. They identify moments where the agent likely missed an expectation, failed to use available context, stopped before the requested checkpoint, or claimed completion without verifying the visible result.

## Repeated friction patterns

### 1. The agent asks instead of inspecting

The user repeatedly expects the agent to inspect accessible files, services, prior turns, or tool output before asking a question. The emotional language often appears after the answer was already available in the working context or could have been obtained by a reversible check.

Candidate policy: `context-first`. Before asking, list what is actually unavailable and inspect the cheapest in-scope source. Ask only if the answer changes the safe next action.

### 2. The agent treats a turn ending as task completion

The archive contains repeated continuation and hands-off requests. The burden is not the existence of a blocker; it is the agent stopping after a plan, partial command, or intermediate artifact without reaching the stated checkpoint or explaining the exact blocker.

Candidate policy: `follow-through-until-checkpoint`. Carry work through bounded intermediate steps and return at the declared checkpoint. Do not turn “continue” into indefinite authorization for unrelated or consequential actions.

### 3. The agent loses explicit dependencies

Several examples distinguish “do X, then Y” from “stop X and do Y.” The recurring failure is flattening a multi-part request into whichever subtask is most recent, then needing the user to restate ordering or resume state.

Candidate policy: `preserve-dependencies-and-preemption`. Store dependencies explicitly, keep a task ledger, and let a later stop/replace instruction preempt the earlier work.

### 4. The agent reports activity instead of the result

The user becomes frustrated when a process is running, a command returned successfully, a website was edited, or a report was generated, but the actual user-facing behavior was never checked.

Candidate policy: `verification-honesty`. Separate planned, attempted, observed, and verified. Report the real end-state check and the remaining uncertainty.

### 5. The agent overgeneralizes a local preference

The user’s corrections often contain strong language and broad-sounding instructions, but nearby context shows they are about one project, one task, or one moment. The safe lesson is to preserve the decision rule and its scope, not memorize the insult or convert one example into a global personality profile.

Candidate policy: `local-before-global-generalization`. Propose session or task scope first; ask before promoting to project or global scope.

## Phrasing associated with better outcomes

The strongest prompts tend to identify the desired end state, provide explicit constraints, name what counts as proof, and distinguish “continue” from “replace.” Useful forms include:

- “Use the available context and proceed until `<checkpoint>`.”
- “If blocked, exhaust reversible in-scope alternatives and report the exact blocker.”
- “Treat this as a dependency: Y only after X is verified complete.”
- “Do not claim completion from activity; verify the visible result.”
- “This is a suggestion to investigate, not permission to generalize it.”

The plugin should help users state these constraints, but should not require them to write perfect prompts before the agent can act competently.

## Consent and evaluation boundary

All candidate policies are `activation: ask_user`. The included self-evaluation skill measures user effort and outcome evidence but cannot silently modify future behavior. A future update can add reviewed examples, scope decisions, counterexamples, and outcome measurements without embedding the private archive in the plugin.

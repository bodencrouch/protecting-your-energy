# Prompt submission context API

`scripts/submission_context.py` reads one JSON object from stdin and prepares
context with the existing compiler. It does not generate an artifact, save a
submission, install a hook, or select a provider. Stdout contains private source
context; callers must preserve that boundary.

## Input and invocation

`--harness codex|claude` is required. The object contains `prompt` or
`user_prompt`; when both appear they must contain identical strings. Optional
`corrections` are retained in RAM. An optional `hook_event_name` must equal
`UserPromptSubmit`. Input is bounded to 256 KiB and the cue to 4,096 characters.

An explicit `--task-id` selects that record through the compiler without
execution association discovery. Otherwise only the compiler's exact bare
resume gate consults an association. `--execution-context-id` precedes
`session_id`; conflicting supplied IDs are refused. For Codex only, absent IDs
may use `CODEX_THREAD_ID`. A Claude bare resume requires its own supplied
context ID. Cwd and source-thread locators never select an execution binding.

The normal output is a rendered preparation packet, including required
freshly authenticated current originals. `--retrieve` additionally delegates
to the existing canonical archive route. Its deadline is controlled by
`--timeout`, default 90 seconds and bounded by the compiler to 1–120 seconds.
No retrieval or generation is implicit in preparation alone.

## Optional protocol envelope

`--hook-output` requires an explicit `UserPromptSubmit` event. Only an exact
bare resume emits `hookSpecificOutput.additionalContext`, including when a
task ID is supplied. Other substantive submissions emit an empty object.
Canonical retrieval requires a separate explicit host invocation rather than
this envelope mode. No hook registration is included.

Unavailable required originals, primary proposals or related proposals, conflicting IDs, ambiguous or
malformed bindings, pin mismatches and output overflow produce a fixed failure
without partial source context. Final output is bounded to 1 MiB; overflow
refuses delivery rather than silently clipping originals. The existing display
renderer applies before encoding. The adapter makes no completion, owner
adoption or independent human identity claim.

Creating a protocol envelope does not establish that a native host emitted an
event, accepted the envelope or delivered context to a model. Caller-supplied
IDs and host environment signals identify only an execution namespace.

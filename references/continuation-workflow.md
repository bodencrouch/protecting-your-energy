# Explicit continuation workflow

Run `python3 scripts/continuation_workflow.py CUE` from either native client.
This is an explicit coordinator, not an automatically discovered hook or service.
It compiles existing continuity context, then calls the installed Claude CLI
with its existing default authentication and model configuration.

The fixed invocation uses `-p --safe-mode --no-session-persistence`, strict empty
MCP configuration, no tools, JSON output and an exact per-stage JSON schema with
required fields and extra fields refused.
Prompts, source bundles, decisions,
drafts and corrections stay in RAM and stdin. The coordinator creates no prompt,
session, fixture, output, configuration or state files. Native managed settings
and provider policies can still govern their own bookkeeping or retention;
`--no-session-persistence` does not establish zero retention. Existing client
behavior outside this invocation is not changed.

Stages are applicability, artifact and critic. Applicability identifies domain,
craft, current purpose, reader experience, local scope, deferred objectives,
primary versus optional elements and relevant interaction patterns/counterexamples.
It selects at most six exact available hydrated unit IDs. Unknown selections are
refused. Empty selection is allowed only as a source-limited proposal.

The writer receives the current request/corrections, complete selected task,
saved artifact/accounts with their original qualifications, and only the selected
source units. Existing text/path/actor/media/table references are resolved into
self-contained source bundles in RAM. Unselected units, unrelated leads and
generated prior interpretations are excluded from the writer source bundle.
The critic can inspect the full original compiler context. Its runtime handle
catalog identifies the current request, each current correction, available saved
context, applicability, draft and exact available hydrated unit IDs. Native JSON
schema and local validation enforce the same closed catalog; findings cannot
invent pointers or guess context paths. Writers
cannot add unselected unit IDs or extra fields through stage responses.

Criticism checks current purpose, changes/withdrawals, usefulness, unsupported
certainty/adoption/motives/requirements and emotional versus procedural objective
where relevant. It does not judge conformity to verbosity or format preferences.
It checks internal chronology, causality, actions and state, including whether
claims about previous choices/responses/events hold under their stated conditions.
Aggregate encounter counts do not establish a specific interaction history.
Contradictions of prior actions, current changes or primary purpose, and failures
of an explicitly intended experience, are material findings.
Relevant experienced interaction patterns can inform work without factual verdicts
about disputes. Reversible creative choices remain proposed choices; uncertainty
does not automatically prohibit a useful artifact.
Source-grounding restrictions concern historical claims, real-person motives and
owner adoption. Proposed fictional motivations and creative details are allowed.
Moral framing follows the current requested purpose rather than becoming an
invented mandatory requirement.

A material rejection permits one revision using the original request, corrections
and selected sources, followed by one final critic. Maximum five native calls;
there is no further repair loop. Rejection or invalid schema returns `not-accepted`,
the latest available draft and diagnostics. A pass returns `proposed-model-pass`:
the critic is an unverified model verdict, not owner acceptance or objective success.
If critic validation fails, bounded known candidate fields and the validation
diagnostic are retained as explicitly `INVALID` unverified output. Extra response
keys are excluded, and that candidate cannot authorize acceptance or revision.
Generation follows the current local scope and targets a useful first artifact
under 16 KiB unless the request explicitly requires more; the hard cap remains
128 KiB. A cue does not authorize expanding the deliverable into an entire project.

`--task-id`, repeated `--query`/`--unit-id`, repeated `--correction` and
`--corrections-stdin` use the compiler's existing bounds. Stdin accepts correction
strings as a JSON list or `{"corrections": [...]}`. `--timeout` sets the retrieval
deadline to 1–120 seconds (default 90). `--native-timeout` sets each native
generation deadline to 1–600 seconds (default 300). Both bounds are checked before
source access or native calls. Up to five native deadlines can be consumed, plus
retrieval and bounded process cleanup. A timeout returns an unsuccessful result
and any existing draft; it does not establish a ready artifact. `--include-context` explicitly
includes the complete compiler packet in stdout; otherwise source receipts and
available/selected unit handles accompany the artifact without dumping that packet.

Each native prompt and stdout response is limited to 1 MiB; artifacts to 128 KiB.
Prompt preflight reports actual bytes and refuses overflow without clipping source
text. Native stdin is streamed while stdout/stderr are drained; stderr is never
displayed. The hash-pinned compiler supplies Linux scoped descendant cleanup and
the hash-pinned shared credential sanitizer. Source coverage and verification
qualifiers remain incomplete where the original packet says so. Reported native
usage/model names come only from returned JSON metadata; they are not inferred
from configuration or used as provider/model overrides.

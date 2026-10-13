# Read-only continuation context

Run `python3 scripts/continuation.py CUE` with an ordinary current request.
The command returns a JSON context packet on stdout. It does not create an
artifact, finish a task, save corrections or install automatic invocation.

The default reading view retains the full current record, current corrections,
selected artifact and bounded source accounts. Within canonical context it
retains every returned evidence quote, owner reply, hydrated message text and
lead preview/prose, including qualified derived-media text,
including peer/assistant context and existing attribution, hashes, locators,
reply links, ranges and truncation flags. Explicitly anchored contexts appear
first. It introduces no additional source-text cutoff or semantic reranking.
Anchored contexts are projected before other evidence, so their actual inline
text and registry rows come first. Producer coordinates remain unchanged.
Duplicate text appears once; `text_ref` resolves through
`source_text_references/first_view_pointer` to that inline text in the final view.

`source_path_ref` resolves in `source_paths`, `actor_ref` in `source_actors`,
and `media_detail_ref` in `media_details`. `parent_refs` and `message_ref` resolve
in `source_parents/rows` and `source_messages/rows`; their columns follow each
table's `fields` array. `field_not_returned` explicitly distinguishes absence
from false or null. Message-instance source/owner-parent flags remain beside
each unit's message reference. Parent identities, full text hashes, indexed
character spans, dates, reply links, roles and actor classes remain available
for every returned parent, including unhydrated leads.

Protocol observations, timing/score metadata, repeated hash ledgers and actor
presentation fields are omitted with field names, occurrence counts and hashes
of original values and locations. Available qualification flags, failure states
and exit codes remain, including false, conflicting and unknown states.
Attempt outcomes remain ordered with their query/unit associations, errors,
result flags and explicit empty arrays. Actor identity, names, role and
attribution remain; avatars, colors, discriminators
and role presentation lists are omitted. Source checks, unit/generation handles,
derivation IDs, media availability and interpretation qualifiers remain.
Media and lead text stays qualified; attachment content has not been reviewed.
Old generated interpretations are labelled expansion frontiers with detail in
`--full`, rather than additional primary source context. Detailed redaction
field receipts remain in `--full`; counts and scope stay visible. Original-value
and redaction pointers refer to the producer packet before projection.
Coverage, generation, diagnostics, limitations and anchor outcomes remain in
the view. This projection does not establish full coverage, belief adoption or
approval or full-value metadata roundtrip. Display-value hashes identify
returned JSON metadata, not raw bytes.

`--full` returns the complete canonical packet from that invocation on stdout,
without saving it. Use it for detailed provenance inspection; a subsequent
retrieval can return a different snapshot, so compare generation and packet
receipts. Both views receive the final credential sanitizer.

`--task-id ID` explicitly selects an existing continuity task before loading
execution bindings. A bare `continue` or `resume`, with optional `please` and
punctuation, can select an enabled association in the task's `execution_contexts`.
Each association supplies `harness` (`codex` or `claude`), `context_id`, and a
boolean `enabled`. Optional `basis` is metadata, not proof of identity or adoption.
Use `--execution-harness` with `--execution-context-id` to supply the host context.
Otherwise `CODEX_THREAD_ID`, when present, supplies only a Codex namespace.
Missing context or no match retains the existing fallback; malformed or multiple
associations refuse selection before source access. Source threads, timestamps,
and proposal locators never create execution associations. This route adds no
hook registration or semantic task inference.

For substantive requests, task
IDs and goals supply lexical candidates, labelled as lexical rather than
semantic selection. A sole candidate qualifies only with at least two salient
matches covering 75% of salient cue terms, or an explicit `continue`/`resume`
marker with all salient cue terms matched. Weak and ambiguous candidates remain
unselected. This lexical rule cannot establish semantic applicability. An unqualified
resume without a matching association returns selection guidance without dumping history. A substantive cue
can retrieve evidence even when no saved task matches.

The existing private configuration supplies the state path. Its containing
directory is the derived root unless `derived_root` is configured. Direct
state reads avoid the state writer's lock creation. Only the selected current
record, at most four version metadata entries, the exact configured Markdown
artifact section and up to three bounded source account excerpts are returned.
Version content and unrelated records are omitted. Files outside that derived
root, symlinks, guarded/request-only paths and the raw archive are refused.

A selected current record can instead contain `chat_artifact.native_proposal`,
an explicit metadata-only native assistant final-message descriptor. The pinned
[proposal reader](chat-proposal.md) authenticates its original header and exact
row, keeping the body in RAM. Optional `chat_artifact.section` selects one unique
Markdown heading; omission retains the complete selected answer, up to the
reader's 128 KiB UTF-8 limit. Decoded-text slice coordinates are explicitly
distinct from native JSONL source offsets. Owner-evidence, adoption and task
completion remain false. A summary locator restores only that summary.
There is no latest-answer scan or implicit replacement of an existing artifact.
Dual file/native references, missing sections and failed explicit proposal reads
remain unavailable; canonical retrieval and both generation routes stop before
querying or drafting when this configured proposal is required and unavailable.
The host records and resolves a qualified locator; the owner need not supply IDs.

The command delegates evidence retrieval to the installed canonical
`thought-reconstruction reclaim` command with `--stdout`. It inherits that
runtime's existing configuration, including `THOUGHT_RECONSTRUCTION_CONFIG`.
Repeat `--query TEXT` or `--unit-id SHA256` up to six times to forward exact
facets or source anchors. Corrections are never silently added as retrieval
facets. There is no provider/model override, output path or archive path option.

Repeat `--correction TEXT` for current corrections, or use
`--corrections-stdin` to read a JSON list of strings or an object containing
only `corrections` with that list. At most eight strings and 16 KiB total are
accepted. Corrections remain in the current packet in RAM; they do not update
state or become historical evidence. Use stdin when the command line should
not contain the correction text; shell and host logging are outside this helper.

Limits are explicit: configuration 256 KiB, state 16 MiB, selected record
32 KiB, each derived text file 512 KiB, file-artifact display 12,000 characters,
native assistant proposal 128 KiB in UTF-8 without clipping,
account display 8,000 characters each and canonical stdout 4 MiB. `--timeout`
sets a 1–120 second subprocess deadline (default 90). Timeout and overflow
terminate verified owned descendants, including workers that start new process
sessions. The standalone Linux invocation uses a process-local child subreaper,
parent/start-time checks and pidfds, then reaps adopted orphans. It refuses to
launch retrieval when those capabilities are unavailable or the compiler already
has children or multiple threads. Cleanup runs on normal completion as well;
the packet reports confirmation and owned process count. Subprocess stderr is
drained without display.

Receipts identify full-file SHA256, read byte ranges, selected/display ranges
and truncation. Byte ranges start at zero and exclude the end; line ranges
start at one and include the end. All displayed context passes through the
hash-pinned shared credential sanitizer. Coordinates refer to the original
bytes before rendering. Coverage remains incomplete; saved accounts, proposed
artifacts, current corrections and canonical evidence retain separate labels.

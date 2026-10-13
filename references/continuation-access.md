# Read-only continuation context

Run `python3 scripts/continuation.py CUE` with an ordinary current request.
The command returns a JSON context packet on stdout. It does not create an
artifact, finish a task, save corrections or install automatic invocation.

`--task-id ID` explicitly selects an existing continuity task. Otherwise task
IDs and goals supply lexical candidates, labelled as lexical rather than
semantic selection. A sole candidate qualifies only with at least two salient
matches covering 75% of salient cue terms, or an explicit `continue`/`resume`
marker with all salient cue terms matched. Weak and ambiguous candidates remain
unselected. This lexical rule cannot establish semantic applicability. An unqualified
resume returns selection guidance without dumping history. A substantive cue
can retrieve evidence even when no saved task matches.

The existing private configuration supplies the state path. Its containing
directory is the derived root unless `derived_root` is configured. Direct
state reads avoid the state writer's lock creation. Only the selected current
record, at most four version metadata entries, the exact configured Markdown
artifact section and up to three bounded source account excerpts are returned.
Version content and unrelated records are omitted. Files outside that derived
root, symlinks, guarded/request-only paths and the raw archive are refused.

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
32 KiB, each derived text file 512 KiB, artifact display 12,000 characters,
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

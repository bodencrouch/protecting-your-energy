# Explicit native assistant proposals

`chat_proposal.load_proposal(descriptor, guarded, deadline_seconds=30)` reads one explicitly bookmarked Codex assistant final answer into RAM. It does not scan for the latest answer, select a task, generate prose, update state, or write an artifact. The caller supplies the existing guarded-path predicate. The reader authenticates the existing pinned `native_context.py` implementation before reusing its secure opener, first-header validation, strict JSON parser and read bounds.

The descriptor is a dictionary with exactly these keys:

| Key | Required value |
| --- | --- |
| `version` | Integer `1` |
| `thread_id` | Lowercase UUID identifying the own first session header |
| `original_native_file` | Absolute original JSONL path under the current user's `~/.codex/sessions` |
| `header_sha256` | SHA-256 of the complete first header line, including newline |
| `observed_size_bytes` | Positive captured file size; selected ranges must lie within it |
| `records` | List containing exactly one record descriptor |
| `text_sha256` | SHA-256 of the exact concatenated UTF-8 text |
| `text_characters` | Positive Python string character count of the exact text |
| `delivery_status` | Literal `delivered`, supplied by the caller |

The record descriptor has exactly `byte_start`, `byte_end`, `record_sha256`, `timestamp`, `message_id`, `text_sha256`, and `text_characters`. Byte ranges are half-open and must describe one complete JSONL line with its terminating newline. The record hash includes that newline. The timestamp must include a timezone and match the original row exactly. `message_id` must match the actual payload ID; use null only when the original has no ID or a null ID. IDs are not invented from hashes. Text hashes exclude JSON encoding and record framing. Text blocks join without an inserted separator.

The supported original row format is `response_item` with payload `type=message`, `role=assistant`, `phase=final_answer`, and only `output_text` content blocks. A conflicting channel or non-public recipient is refused. Commentary, analysis, user messages, tool output, nontext blocks and withheld descriptors are refused. Native content-kind metadata is not interpreted as human authorship or adoption. Support for another native format requires an explicit format-specific extension with observed evidence.

The first header must identify the selected thread and have source `cli` or `vscode`. Later copied headers do not count. Source paths and every directory component use the existing no-symlink native opener; guarded paths and markers remain refused. The helper source itself must match its pinned digest. Records are at most 1 MiB; selected text is at most 128 KiB in UTF-8. A cooperative deadline of at most 30 seconds is checked between bounded operating-system reads. It cannot preempt a stalled kernel read.

A successful result has `status=read`, exact `text`, exact `text_blocks` and authenticated ranges/hashes, with `classification=assistant-artifact-proposal`. It returns the full selected answer without clipping or selecting a section. A caller can subsequently apply its exact-section reader. If any qualification fails, the result is `status=unavailable`, contains no partial text or source records, and provides only a fixed diagnostic. No exception body or malformed descriptor is returned.

The proof covers the first header and selected row, not the whole captured prefix. The reader rechecks the selected bytes, rejects a shortened or replaced source, and rejects same-size modification during its observations. It permits normal growth when the selected bytes still match. Unselected bytes are not scanned or authenticated; this is not a filesystem lock or proof that an intervening mutation never occurred. Appends after the captured size are outside the selection. `observed_size_bytes` is a range boundary, not a whole-prefix hash.

The literal delivery status expresses the caller's selection intent. A final-answer record alone does not prove that the owner read it, accepted it, or saw all of it in the UI. The result explicitly sets owner-evidence, adoption and task-completion flags false. It remains a revisable assistant proposal. A summary containing a fragment is still a summary; its locator cannot stand in for a separate full draft that exists only in RAM or stdout.

## Explicit contextual collection

The continuity compiler accepts `current.related_chat_artifacts` as an optional list of at most three explicitly selected contextual assistant proposals. Each wrapper has exactly `id`, `role`, and `native_proposal`, plus optional `section`. The ID matches `[A-Za-z0-9_.:-]{1,96}`; the role is the literal `contextual-proposal`. The native descriptor uses the unchanged single-proposal reader above. A supplied section must be a nonempty string of at most 512 characters and identify exactly one heading. Unknown wrapper keys, duplicate IDs, and repeated native row selections are refused. Deduplication includes the primary `chat_artifact` row and its ID when one is supplied; the primary retains its existing selection semantics.

`prepare_context` returns a distinct `related_artifacts` envelope with `status`, `configured`, `required`, and `items`. An absent collection is `not-configured`; a valid empty list is configured and read with no items. Each successful item retains its explicit ID and contextual role, full authenticated text hash and byte count, native source and record provenance, and exact selected text or section. Owner-evidence, adoption, and task-completion flags remain false. Related proposals neither replace the primary `selected_artifact` nor become owner facts, adopted decisions, or completion evidence.

Primary and related reads share a cooperative 30-second deadline. The aggregate UTF-8 byte budget is 128 KiB across the full primary and related texts **before** selecting any sections. A file-based primary contributes its full decoded source bytes, including content outside its displayed section. The reader cannot preempt a stalled kernel read. No latest-row search, implicit task association, truncation, or partial related collection is used to meet the bounds.

Any configured collection failure returns `status=unavailable`, an empty `items` list, and the fixed diagnostic `required-related-proposals-unavailable`. The independently read primary may remain available, but `retrieve_context` refuses canonical retrieval while the required collection is unavailable. Callers must enforce the same gate before generation or downstream delivery; inspecting the primary alone does not establish that the collection succeeded. No source bodies or descriptors are persisted by these operations.

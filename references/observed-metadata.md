# Observe original message metadata

`python3 scripts/observed_metadata.py --unit-id UNIT --generation GENERATION`
returns edit and reply metadata for selected published Discord parents. Repeat
`--message-id ID` to select particular parents. It does not approve surrounding
context. `--cutoff ISO8601` requires a timezone and evaluates current content
against observed creation and edit timestamps. `--budget` permits 1–120 seconds;
the default is 60. No write or automatic scan option is provided.

The module loads the hash-pinned shared archive helper. Its metadata-only
hydration resolves parents with the configured generation, provenance and access
exclusions. The observer independently rereads the exact normalized record range
and compares its published raw line hash. It then reads the rebased permitted
original Discord export and verifies the whole source hash. Every selected parent
must match original and normalized message ID, text hash, actor ID and creation
instant before its additional metadata is returned. Text remains in RAM and is
never included in output. Source bodies, prompts and current task state are not
written or changed.

Published parent fields, normalized observations and original observations stay
separate. Edit aliases `timestampEdited`, `timestamp_edited` and `edited_at`
distinguish absent, null, value, invalid and conflict. Equivalent timezone instants
agree. A reported value mixed with null, contradictory alias values or contradictory
original/normalized values remains unknown. An original exporter null means no
reported edit at that capture, not proof that the content was never edited.
Absent original edit metadata does not become historical availability merely
because a published field is null.

Original `reference.messageId` and direct `reply_to` are observed independently.
Contradictory aliases remain unknown. Reference metadata includes supported type,
message, channel and guild IDs without returning referenced content. Numeric IDs
are rendered as canonical decimal strings, including integers beyond JavaScript's
safe range. Invalid IDs and unsupported reference fields are not coerced into
evidence. A reply reference does not establish that its target was retrieved.

A current message created or edited after a requested cutoff is excluded. Unknown
edit history remains unknown. A message preceding the cutoff with an observed
earlier edit or exporter null is not excluded by those timestamps; this still does
not prove which exact content was available historically. No missing historical
version is reconstructed or silently treated as verified.

Each source read refuses symlinks, guarded paths, nonregular files and source
changes. Only real home NVMe or WorkspaceSSD paths are permitted. Bounds are
128 MiB per original or normalized record, 64 parents, 500,000 original message
rows, a 256 KiB configuration and a 256 KiB output.
The output cap covers the exact indented ASCII-escaped UTF-8 JSON representation,
including its trailing newline, for both the callable module and CLI.
Cooperative deadline checks
cannot interrupt blocked OS reads or an in-progress JSON decode. Unsupported,
missing, excluded or unverifiable originals return `unavailable` with a fixed
diagnostic. A normalized record hash alone never establishes original verification.

The result includes source and code pins, checked ranges, selected scope and
limits. It is a read-only observation overlay suitable for later integration;
existing published data and continuation behavior are unchanged.

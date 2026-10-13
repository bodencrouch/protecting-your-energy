# Capture a native input snapshot

Run `python3 scripts/refresh_native_bookmarks.py --task-id TASK` to capture a
new version 1 bookmark descriptor on stdout. The task must already supply a
bookmark reference and eligible source threads. Alternatively, pass
`--bookmark PATH --sha256 SHA --thread THREAD` with a repeated thread option.
The existing configuration defines the permitted private derived root.

`--output PATH` creates a new descriptor in that root with mode 0600. It refuses
an existing destination or symlink traversal. It does not update configuration,
task state, archive generations, previous descriptors, source logs or current
bookmark references. Saving a descriptor does not activate it.

The refresher loads the existing native reader and continuation compiler from
hash-pinned sibling source. It reuses their guarded file access, strict JSON
decoding, first-header validation and exact native `user.text` classifier.
Every previous selected row retains its exact identity, byte range, timestamp
and raw hash. Missing, changed, overlapping or reclassified pins fail capture.

Each selected log is opened without symlink traversal. Its initial size defines
the captured prefix. The complete prefix is scanned as physical JSONL lines;
each row is parsed without returning source bodies. User messages with the exact
`user.text` marker pass the existing actor, block and control checks. Explicit
native environment, instructions, goal, heartbeat and open-page categories are
excluded. Unknown user-message kinds, malformed records and incompatible blocks
make the declared input scope unavailable. No unknown category is promoted to
human input. The descriptor retains a category inventory, not those bodies.
An exact transport combination containing only known excluded categories is
excluded when each category has its own text block. A mixture of human and
control categories is refused rather than partially promoted.

The same guarded path is reopened and the captured prefix is independently
hashed again. Device and inode must match. Appends beyond the captured prefix
are permitted; truncation, replacement or changed captured bytes fail capture.
A previous refresher descriptor's complete-prefix hash is verified as well.
The first capture from a descriptor with only row pins cannot prove that its
previously unpinned history was unchanged before this capture.

The initial captured EOF must end on a complete line. A partial suffix refuses
publication and reports its complete-line boundary. Capture does not wait for a
writer or silently publish partial coverage. Overflow, missing pins, invalid
attribution, unknown kinds or deadline expiry return `unavailable` without a
partial descriptor. Diagnostics contain no source text. Originals and prompts
remain in RAM or bounded read buffers; no model or semantic index is invoked.

Defaults permit four threads, 128 MiB per captured prefix, 256 MiB total captured
prefixes, 200,000 scanned records and 8 MiB per physical row. These scan bounds
include large tool-output rows; human input still uses the existing 1 MiB raw-row
cap, 64 records total, 128 KiB UTF-8 text and 256 KiB descriptor cap. The
consumer read budget is also enforced: the number of selected threads times its
64 KiB header allowance, plus every selected raw row's length and boundary probe,
must fit its 16 MiB native-byte cap. Both the observed upper bound and limit are
retained in the descriptor proof. Metadata overhead cannot bypass this bound.
The default
cooperative deadline is 90 seconds; `--deadline` permits 1–300 seconds. Deadline
checks cannot interrupt a blocked OS read. Two passes read each captured prefix;
validating a prior full-prefix pin may require another bounded pass.

The resulting descriptor is compatible with the version 1 hydration reader.
Its `prefix_snapshot` receipts authenticate all selected native input rows
within those exact captured prefixes. They do not establish a perpetual latest
view: messages appended after capture remain unclassified, and current caller
corrections keep precedence. Native origin and kind metadata do not independently
prove human identity or adoption of quoted material. Equal hashes at separate
observations do not exclude a rewrite followed by restoration between reads.
The unchanged snapshot reader still authenticates selected exact rows rather
than revalidating the whole prefix on every hydration.

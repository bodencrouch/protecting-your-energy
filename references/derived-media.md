# Access processed media

`archive_access.derived_media` selects a pinned private catalog and a separate
vector cache. The normal archive query keeps its primary `leads` unchanged and
returns supplemental results under `derived_media`. These results have
`derived_unit_id` and `derivation_id`, never primary archive unit IDs.

The catalog binds the original export digest, message and attachment IDs,
captured asset digest, sidecar digest and JSON pointer. Lookup rechecks those
files and source identities. A native-ID association across exports retains
both snapshot digests; it does not establish identical snapshot bytes.

Hydration adds a separate `media_derivations` collection. Original message text
and original/effective actor fields retain their existing values. ASR has a
producer-assigned role, unresolved speaker identity and word-level uncertainty.
Image descriptions retain their inspection and OCR limits. Neither kind proves
poster endorsement or original authorship. Historical producer code identity
remains unknown when the sidecar did not record it.

The local reader is `scripts/derived_media.py`. Its `lookup` command accepts a
message ID and optional attachment IDs. `hydrate` accepts catalog derivation
IDs. `query` uses separately labelled cosine similarity over the derived
passages. `build --output PATH` explicitly creates the auxiliary vector cache
once; readers never rebuild it. The cached encoder and model files are pinned.
All operations use bounded file reads, allowlisted roots and exact hashes.

Supplemental hydration shares the caller's remaining character budget, with
a 2,048-character per-annotation ceiling, at most 16 entries and a 256 KiB
transport ceiling. ASR flag words consume that same budget. Truncation and
remaining sidecar locators remain visible. Runtime, integrity and missing-entry
gaps do not become successful primary archive retrieval.

Thought Reconstruction saves supplemental discovery and hydrated derivations
in separate packet fields. Its six-lead primary hydration ceiling remains;
supplemental context also shares the existing 45-second hydration deadline.
The derived packet context has its own explicit 8,000-character total ceiling.

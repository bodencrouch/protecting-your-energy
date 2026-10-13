# Retrieve by meaning, then hydrate the originals

Use `scripts/semantic_retrieve.py` as the local dense route. It encodes original conversation text with a transformer. It does not rename keyword retrieval as semantic retrieval or send archive text to a hosted model.

## Run the route

The current interpreter is `/home/brunner56/brain-data/upar-dip/derived/protect-your-energy/2026-10-11/deeper-pass/semantic/runtime-ssd/bin/python`. It uses the NVMe system Python 3.14.8 with private copies of `onnxruntime`, `numpy`, `tokenizers` and their required packages. Runtime, model, index, working directory and eligible source originals now reside on NVMe. Historical HDD generations are retained without use by the current route.

The private model and new index live outside the archive:

```bash
PYE_PY=/home/brunner56/brain-data/upar-dip/derived/protect-your-energy/2026-10-11/deeper-pass/semantic/runtime-ssd/bin/python
PYE_SCRIPT=/home/brunner56/plugins/protecting-your-energy/scripts/semantic_retrieve.py
PYE_INDEX=/home/brunner56/brain-data/upar-dip/derived/protect-your-energy/2026-10-11/deeper-pass/semantic
"$PYE_PY" "$PYE_SCRIPT" status --index "$PYE_INDEX"
"$PYE_PY" "$PYE_SCRIPT" query 'Describe the competing aims behind a disputed technical design.' --index "$PYE_INDEX" --role self --limit 8 --json
```

A query accepts multiple free-form paraphrases in one invocation. The model loads once. Use complementary questions about the purpose, constraint or tradeoff discovered in the source. Avoid fixed personality categories or trying to reproduce a preferred answer. Literal IDs are for exact lookup and hydration.

```bash
"$PYE_PY" "$PYE_SCRIPT" hydrate --index "$PYE_INDEX" --message-id ORIGINAL_ID --version VERSION_HASH --neighbors 5
```

Hydration is exact ID/version lookup. Dense ranking is a separate operation. It returns full original message text and neighboring speakers, with original JSON pointers. Attachments remain uninspected. The wrapper additionally supports exact source-file hash checks.

## Know what is indexed

The source catalog is a metadata inventory of eligible original Discord exports. The initial all-catalog plan selected 2026 owner messages from 512 original files. After ID/text-version deduplication it contains 61,047 owner text versions and 7,989 dense windows. These are machine-indexing counts, not contextual reading or claims about belief adoption. Inspect the current generation's manifest and `scope.partial`: a published partial checkpoint covers only its listed indexed parents/channels/dates. Planned counts are explicitly separate.

Documents pack chronologically adjacent owner messages from the same channel, within 15 minutes and a 400-token content target. Long parents are split into overlapping windows using the actual model tokenizer; no reasoning tail is silently discarded. Every window retains original parent IDs, dates, author IDs, versions, full source pointers and parent-relative character spans. Neighboring peer messages are available through exact original hydration instead of consuming separate dense slots.

Snapshot copies with the same ID, normalized timestamp instant, author, text and reply target share an indexed text version. Reactions, avatars and fetched-embed presentation do not create a new text version. Mention/emoji rendering can still produce variants; they are retained as provenance, not labeled changes in belief. Default query diversity prevents the same parent ID from repeatedly filling the review budget. Different original IDs, including intentional later returns, remain distinct. `--include-versions` exposes separately indexed variants when needed.

## Check the source before interpretation

A hit is a retrieval lead. A quoted model answer, copied example, peer report, author experiment and adopted rule require separate contextual attribution. Dense similarity does not establish truth, consent, success or adoption.

Each hit rechecks original path guards and full source hash, original pointer, ID and text-version hash. If its canonical export changed, an unchanged permitted export of the same ID/version may be used; it is reported with the actual locator. Restricted/withheld rows, installed author/name/message exclusions, request-only paths, credentials and memory scaffolding are refused. These checks also apply to neighboring hydration. Credential detection is a filter, not proof that every possible secret format has been identified.

Index creation is refused inside the read-only archive. Model inference has no network client and an audit hook denies Python socket connections. Model loading uses local files. Generated state uses owner-only directories/files and immutable published generations.

## Resume a checkpoint

```bash
"$PYE_PY" "$PYE_SCRIPT" build --index "$PYE_INDEX" --catalog CATALOG_PATH --year 2026 --budget-seconds 600 --threads 6
"$PYE_PY" "$PYE_SCRIPT" build --index "$PYE_INDEX" --resume GENERATION_NAME --budget-seconds 600 --threads 6
"$PYE_PY" "$PYE_SCRIPT" publish --index "$PYE_INDEX" --checkpoint GENERATION_NAME
```

Build writes bounded vector batches and checkpoints. Resume keeps existing chunks/vectors, checks source guards and unchanged file stats, and hashes changed files. Selected query sources are always fully hashed. Thread-count changes are permitted only when model/tokenizer/runtime/prefix/pooling configuration remains identical; new batch thread counts and resume history are recorded.

`publish` freezes an embedded prefix as an immutable partial generation. `combine --source-index INDEX_A --source-index INDEX_B --index DESTINATION` joins compatible ready checkpoints, preserving actual indexed scope. Final completion of the planned build atomically activates its complete generation. No partial publication means the whole catalog has been indexed.

## Understand the backend and its limits

The copied public cache is the Qdrant ONNX export of `BAAI/bge-small-en-v1.5`, 384 dimensions, 512 model tokens, CLS pooling and L2 normalization. Queries alone receive the prefix `Represent this sentence for searching relevant passages: `. These settings follow the [official BGE model card](https://huggingface.co/BAAI/bge-small-en-v1.5). The inspected MiniLM cache had weights/tokenizer but lacked its loading configuration; [its model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) also describes a different 256-token sentence-transformer default. Neither cache name alone was treated as a working runtime.

Direct ONNX initialization was measured at 0.38–0.68 seconds in the initial checks; the first three-paraphrase query invocation took 2.22 seconds. A 24-row actual batch with maximum 482 tokens took 5.48 seconds with 4 threads and 5.315 seconds with 6 threads. This small measured difference is not a general benchmark. Full expansion is substantially slower than querying; private checkpoint receipts record progress.

A genuine miss remains documented: one design-tradeoff paraphrase ranked its fully indexed target 36th. An independent complementary paraphrase with little literal overlap ranked that target 4th and nearby context 2nd. A convergence query found the correct source 5th while a quoted external story ranked first. Preserve these failures and use complementary meaning queries, candidate diversity and original context. Do not turn a plausible top hit into a conclusion or claim single-query recall is solved.

## Continue packed-window metadata

`parents --unit-id HASH --generation GENERATION --index INDEX` returns every original parent locator for one packed window, including source/version hashes, author/date and parent-relative spans. It returns no message text. The pinned generation prevents a CURRENT update from changing the lookup. Query displays only the first four locators; this route recovers the complete list before choosing a bounded hydration pass.

The owner-local continuation job is `pye-semantic-20261011.service`. Its private runner and status/receipts live with the index. It resumes at most eight 600-second chunks, publishes immutable progress, and stops on completion, error or bounded budget. The selected 2026 catalog build completed all 7,989 planned windows, 61,047 owner text versions across 164 channels. It examined 512 eligible exports; 218 canonical files supply indexed parents, with duplicate source alternatives retained. This scope remains separate from the whole archive or contextual reading. The bounded job exited successfully. A requested year or other-speaker dense role outside the generation is explicitly `scope-unavailable`, not evidence of absence.

Length bucketing was measured on the same actual passages: 24-row padded work dropped from 11,568 to 7,752 tokens; runtime under concurrent load was 9.232 versus 8.584 seconds, with maximum absolute vector difference 0.0. Adaptive microbatches preserve row order and apply only when predicted padding reduction exceeds 20%. This optimization does not change the model or cure semantic ranking misses.

## Source path migration

`remap --generation NAME --source-root SSD_ROOT --old-root OLD_ROOT --catalog SSD_CATALOG` makes a new generation with SSD source locators, retaining original source/version hashes and hardlinking verified vector batches. It never changes historical generations or recomputes embeddings. The original transfer must be SHA256 verified separately; remap checks sizes and records new local file stats. A building generation stays resumable; publish it before querying. All runtime and subprocess working directories must be SSD paths.

# Unified Protect Your Energy route

Use `scripts/pye_route.py` when the request is rough and the caller should not
choose a skill. It selects one capability from the request's wording, adds a
small craft profile to the existing RAM-only inference route, and returns the
same qualified artifact and source receipt as `continuation_infer.py`.

```text
python3 scripts/pye_route.py "the rough thing I am trying to do"
```

The default `--route auto` is a routing hint, not a conclusion about the
request. The result reports the selected route, candidate scores and a private
receipt. Use `--route` to correct a route without changing the underlying
retrieval or archive. The supported routes are:

| Route | Use when the requested result is | Existing capability |
| --- | --- | --- |
| `reclaim` | an earlier idea recovered and continued | Thought Reconstruction |
| `make-coherent` | a clear meaning-preserving draft | Semantic Expression |
| `contextual-reply` | the next message in a live conversation | Semantic Expression |
| `choose-next` | one useful action and completion point | Protect Your Energy |
| `finish-loop` | a bounded increment after overruns or repeated failure | Protect Your Energy |
| `recurring-friction` | reusable relief for a repeated obstacle | Protect Your Energy |
| `opportunity-radar` | a verified development connected to an earlier idea | Protect Your Energy |

For a machine-readable request, pass `--input-stdin` with a JSON object
containing `cue`, optional `corrections` (at most seven; one slot is reserved for
the route profile), and optional `current_artifact`. Retrieval flags such as
`--query`, `--unit-id` and `--scope-unit-id` are forwarded to the existing
canonical route. `--dry-run` emits the selected route without invoking a model.

The adapter writes only a mode-0600 receipt under
`$HOME/brain-data/upar-dip/derived/protect-your-energy/route-receipts/`. The
receipt contains hashes, route selection, executor hash and status. It omits the
cue, corrections, source bodies and generated artifact. It never writes to the
raw archive or semantic index, and it does not establish scheduling or owner
acceptance.

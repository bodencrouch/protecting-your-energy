# Bounded contextual search probes

The host agent may supply up to two provisional search probes through repeated
`--probe` arguments to either continuation entry point. `compile_context(args)`
also accepts their list in `args.probe`; omitted attributes preserve the existing
cue-only route. No new provider, native planning call or memory store is created.
The host forms these probes from its current conversation and qualified available
context. The compiler validates and transports them; it does not itself infer them
or independently prove the caller's identity.

A probe asks for a potentially useful experience, action, correction or interaction
pattern. It must remain distinct from a claim that the experience occurred or that
the owner requires a proposed next step. Keep the current cue unchanged. Existing
explicit `--query` facets have priority within the six-facet total limit; excess
probes are reported as omitted rather than silently replacing explicit input.
This limit counts supplementary facets, separately from the original cue; up to
seven distinct search queries can therefore be planned. The receipt reports both
facet count and the exact deduplicated planned query count including the cue.
Duplicate probes are suppressed. Each probe is a nonempty string of at most
2,048 characters; at most two are accepted, even if some would be omitted.

The returned `query_plan` records explicit facets, supplied hypotheses, their
selection/omission and actual dispatch status, plus provenance limits. It is outside `current_request`,
source records and continuity state. The workflow's source receipt includes only
query hashes, byte counts and plan statuses; query bodies remain excluded. It also
excludes the plan from the writer's selected source bundle. Search wording
found in lead metadata is still a probe, never confirmation or source evidence.

With no explicit facets, the original cue and two probes fit the existing
three-query discovery batch. Larger manual facet sets retain existing cross-batch
ordering limitations; this adapter does not repair backend result merging or
increase the six-unit hydration budget. Additional queries consume existing
retrieval time and budget. A missing result does not prove that an episode is absent.

Use probes selectively for underspecified requests whose purpose depends on earlier
thinking. Straightforward rewriting can use direct drafting. This mechanism is
an adaptation of prospective retrieval, not a replication of a published system or
proof of improved personalization. Evaluate useful source recovery separately from
artifact quality, correction effort and owner acceptance.

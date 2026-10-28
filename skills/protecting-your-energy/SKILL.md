---
name: protecting-your-energy
description: Turn a rough request or scattered reasoning into the useful next result, reducing repeated explanation, communication overhead, effort overruns and missed opportunities. Use when writing, creative work or project continuation depends on earlier thinking, context or corrections, and for personal workflow assistance or an explicit energy review.
---

# Protect Your Energy

Deliver the useful result the owner is trying to reach. Recover the problem,
meaning and constraints before choosing a mechanism. Existing tools, profiles
and popular methods are candidates, not definitions of the need.

## Sustain momentum during the work

Whenever this route investigates earlier conversations or continues substantial
work, apply [sustain-momentum](../sustain-momentum/SKILL.md) lightly alongside
the needed capability. Recover repeated priorities and successful conditions,
including efficient workflows and low interaction cost. Investigate “obviously”
as a possible missed expectation; promote only supported, scoped improvements
and evaluate changed skills. Keep this upkeep behind useful delivery.

## Select the execution workflow

For substantial work and phase transitions, apply
[workflow-orchestration](../workflow-orchestration/SKILL.md): recover active
directives, select and use the applicable installed expert workflow, and keep
looking for the next authorized action until the requested outcome is delivered.
Use [policy placement](../../references/policy-placement.md) when deciding whether
an improvement belongs in a rule, hook, memory or reusable procedure. Explicit
user instructions authorize their stated scope; consent for inferred preferences
does not require asking again about already authorized work.

## Route the work

Use only the responsibilities the current request needs:

| Need | Capability | Deliverable |
|---|---|---|
| Implicit goals, recurring concepts or reusable reasoning | [reasoning-map](../reasoning-map/SKILL.md) | Applicable method and useful artifact |
| Pieces of an earlier idea | `reclaim` / Thought Reconstruction | Developed idea and concrete continuation |
| Rough or repetitive explanation | `make-coherent` / Semantic Expression | Meaning-preserving draft for its audience |
| Earlier reasoning needed in a live conversation | `contextual-reply` / Semantic Expression | Relevant next message, ready to use |
| Decide today's effort | [choose-next](../choose-next/SKILL.md) | One useful next action and completion point |
| Work keeps expanding | [finish-loop](../finish-loop/SKILL.md) | Reassessed remaining work and preserved resumption |
| The same obstacle repeatedly consumes effort | [recurring-friction](../recurring-friction/SKILL.md) | Implemented reusable intervention |
| A new development may make an old idea timely | [opportunity-radar](../opportunity-radar/SKILL.md) | Verified, actionable connection at a useful time |
| Evaluate the behavior | [self-evaluation](../self-evaluation/SKILL.md) | Observed coverage, concrete misses and limits |

Resolve installed skills by real path. If an adapter is absent, perform the
supported responsibility from available context; report the missing mechanism
only when it prevents delivery. Do not ask the owner to repair routing.

## Bind the intention

For a request that depends on earlier work, prepare resumed context with
`python3 scripts/continuation.py` from this plugin's root. Pass the rough request as one
argument; use the current conversation to resolve any ambiguous task candidates.
Lexical coverage can miss a valid continuation containing new subject matter.
For an unselected substantive cue, execution-bound candidate metadata may expose
existing task IDs and goals from the caller's host association. These provisional
candidates do not establish relevance or load saved bodies. Resolve applicability
from the live conversation before explicitly selecting an existing ID.
When the live conversation clearly links a returned candidate, the host may select
its existing ID with `--task-id`; record this as host selection, not an automatic
semantic match. Do not require the owner to supply an ID. When that link is uncertain,
retain the uncertainty instead of assuming the saved task defines the new request.
The executable route is documented in
[continuation access](../../references/continuation-access.md). It keeps incoming
corrections separate from saved records and source evidence. A prepared packet is
context for the work, not a completed result.
Configured native source metadata is refreshed in RAM by this ordinary route;
no separate refresh command is required. Treat its captured cutoff and coverage
as explicit limits, and stop on unavailable required originals.
For a sparse cue needing interpretation and a draft through the existing Claude
client, use `python3 scripts/continuation_infer.py`. It captures current context
once, infers a provisional purpose and desired effect, optionally proposes at most
two retrieval probes, and produces an artifact with qualified source uses. The
planner may decide history is unnecessary; the writer may use none of the retrieved
sources. Neither inferred choices nor search wording become requirements or owner
facts. See [infer and draft](../../references/continuation-infer.md). Supply current
corrections and any artifact proposal through stdin in RAM. The host resolves a
saved task from the live conversation when needed; do not ask the owner for task
IDs or search facets. Standalone task selection retains its lexical limits.

When resuming an earlier assistant draft, use the selected task's explicit
`chat_artifact.native_proposal` metadata locator when available. Keep its original
assistant/proposal role separate from the owner's inputs. See
[proposal qualification](../../references/chat-proposal.md). A delivered summary
does not substitute for a separate full draft, and an unavailable required
proposal stops retrieval and generation. Do not ask the owner for record IDs or
silently choose the newest assistant message as the artifact.

Explicit related proposal references accompany the primary when configured.
Keep their individual identity and proposal roles; chronology alone does not
choose a winner or establish adoption. An unavailable required related proposal
stops continuation before retrieval or generation. The proposal qualification
reference describes the collection bounds.

For clear requests, draft directly in the host or use this route with `--direct`,
which skips planning and implicit archive retrieval. When the host has already
inferred a useful brief, it may supply that provisional plan through stdin rather
than making another planning call. For context preparation without generation,
the compiler still accepts at most two host-formulated hypotheses through repeated
`--probe` arguments. See [retrieval probes](../../references/retrieval-probes.md).

Use `python3 scripts/continuation_workflow.py` only when an explicit applicability,
critic and revision chain is useful enough to justify its extra calls. See
[continuation workflow](../../references/continuation-workflow.md). These routes
use the existing client authentication and default model; they do not install a
provider or establish automatic discovery by another client.

Review the actual artifact against the requested result and current corrections.
Use an independent reader when consequential interpretation warrants it, rather
than requiring a separate model chain for every draft. Preserve relevant meaning,
constraints and uncertainty while suppressing unrelated episodes. A successful
context command, schema-valid draft or model verdict does not establish better
quality or owner acceptance. Continue the authorized work when delivery misses
the requested purpose.

When the request points to the owner's earlier thinking, use its wording as a
retrieval cue. Recover the relevant original episodes and later corrections
before compiling the final working prompt. Prioritize the period and sources
the owner identifies, then seek complementary and contrary contexts. An
illustrative example is not the specification of the whole capability.
For broad rediscovery or a challenged interpretation, use `reasoning-map`'s
source-led journey before loading a prior personal model. Preserve source-shaped
accounts, unresolved references and competing readings. Current reasoning models
remain candidates; the owner's corrections determine what needs revisiting.
Use [archive access](../../references/archive-access.md) to carry the actual
read and retrieval routes into each delegated task. The default shared query now executes the configured local dense model.
Check its actual generation scope and retrieve complete parent metadata when
a packed lead is clipped. Use exact-name/lexical routes as labeled supplements.
A filesystem path alone does not establish semantic retrieval. Give investigators source access and
relevant methods, rather than only a lead agent's summary. For source-based
independent evaluations, supply the locked original context without expected
answers. Sustained effort, iterations and outcomes can reveal methods that a
single polished statement misses.

Keep a compact working record: outcome, audience, material meaning, constraints,
relevant evidence, consequential unknowns and the artifact to deliver. Infer
routine connective reasoning; record new design choices privately. The owner
need not fill out a form. Current corrections override historical readings.

Retrieve relevant owner examples from different occasions and the present
setting. Look for counterexamples before generalizing a preference. Unknown
attribution, copied prompts, peers and prior AI work remain distinguishable.
The owner's account defines intended experience and self-description; claims
about external reality need evidence. Preserve creativity and legitimate
disagreement while removing avoidable confusion.

Read [continuity](../../references/continuity.md) when retaining decisions,
corrections, priorities or alerts. Read [methods](../../references/methods.md)
when choosing or evaluating the procedure. Keep investigation running alongside
substantial building. A polished small sample is not whole-design recovery.
Use `reasoning-map` when advice or expression depends on the owner's implicit
methods, recurring effort or a continuation across messages. Recover reusable
reasoning with its purpose, causal basis, applicability and
counterconditions. Apply a rule only when those conditions fit the current
problem; do not promote a vivid episode into a universal personality trait.

## Act and deliver

The requested assistance authorizes reversible work within that request. Use
already available context before asking. Do not run the old policy-consent
questionnaire for actions the owner already requested. A newly inferred durable
policy outside the request still requires scope and authorization; never edit
system memory or unrelated settings silently.

Return the artifact appropriate to the moment. Keep source ledgers, prompt
mechanics and diagnostic labels in private companions. Do not expose personal
archive details in outward drafts, fabricate authorship, hide material limits
or replace unfinished work with a process explanation. Sending, publication and
consequential external changes retain their own authorization requirements.

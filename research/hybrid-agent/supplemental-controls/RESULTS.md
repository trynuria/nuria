# Comparison with simple inspection policies

The supplemental run completed all 24 selected records: twelve schema-first jobs
and twelve stateless jobs. Independent replay passed for 21 submitted final
queries and reconstructed all 108 canonical model-request hashes. Both conditions
submitted two wrong answers; schema first abstained once and stateless twice.

| Inspection policy | Record set       | Accepted / selected | Wrong submitted | Abstentions | Completion tokens | Prompt tokens | Tool calls |
| ----------------- | ---------------- | ------------------: | --------------: | ----------: | ----------------: | ------------: | ---------: |
| Hybrid fixed      | Original primary |              8 / 12 |               2 |           2 |            26,681 |       245,920 |         49 |
| Hybrid neural     | Original primary |              9 / 12 |               2 |           1 |            17,951 |       142,053 |         30 |
| Schema first      | Supplemental     |              9 / 12 |               2 |           1 |            20,782 |       117,338 |         32 |
| Stateless         | Supplemental     |              8 / 12 |               2 |           2 |            20,856 |       177,192 |         48 |

Neural inspection and schema first accept **exactly the same nine questions**;
the remaining three are unaccepted by both. Against stateless, eight questions
are accepted by both, one only by neural inspection, and three by neither.

Neural inspection uses fewer completion tokens than schema first but more prompt
tokens. The neural circuit and its teaching history also require work. This is a
measured input/output tradeoff, not evidence of universally lower compute or
latency. The result supports a functioning grounded analyst; it does not establish
broad superiority over the stronger simple heuristic or isolate a benefit from
synaptic plasticity.

## What was fixed and what was post-hoc

The controls use the same pinned reasoning model, twelve selected questions,
registered tools, seed and per-job ceilings as the original course. They run with
no retrievable memory or neural context. The host supplies schema inspection in
the schema-first condition; the model chooses its first inspection in stateless.
The original none-memory teaching records are not reused as memory.

The helper and supplemental protocol were publicly committed and checked before
these control calls. The two conditions run serially in the published order.
This completion was planned after inspecting the original primary results, so it
is **post-hoc**, not a new unseen or independently randomized preregistration.
Real tool timings, prompt lengths and backend execution can vary. Public Spider
questions may have appeared in model pretraining.

The [original run](../comparison-03/results.md) remains unchanged: 120 selected
records, 64 executed and 56 unexecuted. The supplemental records are retained
separately, rather than rewriting original coverage. The episode-retrieval and
procedure-only reserved conditions are still unexecuted. This table does not
establish the contribution of memory independently of inspection policy.

## Closed accounting and replay

The controls consumed 41,638 known completion tokens and 294,530 prompt tokens.
Together with the semantic work trial's 3,499 completion tokens, additional use
was 45,137 of the declared 60,000 ceiling. Cumulative conservative completion
charge was 227,741, including the retained historical unknown-use ceiling of
12,214. No new ambiguous usage or pending reservation remains. No financial
transaction occurred.

The [portable package](results-01/README.md) retains all original records, the
frozen source and protocol identities, conservative closure projection and
independent checker. Its replay uses no inference, encoder, payment or Python
serialization loading. Hashes establish local consistency rather than external
attestation of model weights, usage or wall-clock ordering.

The [semantic work trial](../work-trials/README.md) and its [numeric weight witness](../weight-delta/README.md)
are separate integration evidence. These comparison rows used the older
identity-texture input, not the new pretrained semantic input. Useful-task
performance of the full-embedding conventional semantic controller remains
unmeasured.

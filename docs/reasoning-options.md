# Reasoning configuration and failed-work recovery

The hosted worker already uses the Responses API. Before adding another custom
memory layer, this study checks the simpler alternative: better input grounding
and an existing stronger reasoning configuration. Neither changes the original
Brian2 worker or its history.

## Two actual failed reports

The earlier delivered-work sequence rejected a component report that interpreted
a commit string as a collection, and an outcome report that read the wrong
utility field. Their original acceptance checks remain unchanged. Each recovery
receives the same rejected program and bounded factual input structure. Expected
answers and independent checks stay outside model requests.

The [prospective protocol](../research/outcome-learning/recovery-05/protocol.json)
and exact execution source were published before inference. These are revealed
development cases, not fresh superiority evidence.

| Configuration           | Accepted / delivered | Model calls | Configured token cost |
| ----------------------- | -------------------- | ----------- | --------------------- |
| Sol, low reasoning      | 2 / 2                | 2           | $0.027686             |
| Sol, medium reasoning   | 2 / 2                | 2           | $0.032626             |
| Astra, medium reasoning | 2 / 2                | 2           | $0.168280             |

All six programs reproduced under independent native acceptance. Identical jobs
replayed both delivery receipts without additional inference or executions;
original journals stayed byte-identical. Sol low remains the cheaper qualified
route for these two mechanical report profiles. The study does not establish
that it is the best general reasoner. Input grounding, prior failure context and
model configuration were not separately randomized, so the repair cannot be
attributed to one of them.

The two accepted Sol-low programs are also acquired as finite
[retained tools](../research/outcome-learning/recovery-05/qualified-skills.json)
using their exact completed artifact receipts. Independent replay reconstructs
the original logical journal and reacquires identical program identities without
changing it. These host-enrolled profiles are available for later checked work;
they are not activated in the original production worker or evidence of general
program correctness.

The complete [programs, usage and deliveries](../research/outcome-learning/recovery-05/courses.json)
and [native verification](../research/outcome-learning/recovery-05/native-verification.json)
retain every configuration. Costs use configured uncached token rates, rather
than a verified provider invoice. Replay requires no provider credential:

```sh
python -m scripts.verify_recovery_options \
  research/outcome-learning/recovery-05 \
  .test-state/recovery-replay
```

## Fresh reasoning transfer

A separate [prospective comparison](../research/outcome-learning/reasoner-transfer-06/protocol.json)
tests the current Sol-low worker against Astra-medium using the same 485 checked
experience records and the same proposal, blind challenge and revision workflow.
The two configurations are a package comparison; model and reasoning effort are
not separately identified.

Metadata-only selection excluded every task enrolled in earlier courses and
selected 79 tasks across seven work families. One reference program failed in
the current isolated environment; it remains unsupported and is not replaced.
The 78 reference-valid tasks were enrolled before any model request. Public
benchmark pretraining contamination remains unknown.

All final proposals must be publicly committed before any independent terminal
result is revealed. The prospective secondary gate requires a gain of at least
four percentage points, paired one-sided exact p at most 0.0125, at least 70
valid tasks and no family losing more than two tasks. Both full arms and all
failures are required. Generation and terminal evaluation are pending; no result
or runtime promotion is claimed.

Both experiments share the existing cumulative inference allowance. Their
sub-envelopes do not create new budgets, release old provisions or permit
ambiguous retries. No model weights, neural control policy or original worker
were changed.

Model and API choices follow the official [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning),
[Astra reference](https://developers.openai.com/api/docs/models/gpt-6-astra),
[Sol reference](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and
[pricing](https://developers.openai.com/api/docs/pricing). Documented capability
is not a measured outcome in this experiment.

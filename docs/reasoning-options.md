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

All 156 programs were [publicly committed before any terminal grading](https://github.com/trynuria/nuria/commit/5f2f2cf8741a3bab2b3fe7842fe8e569b636311a).
The [complete result](../research/outcome-learning/reasoner-transfer-06/results.json)
retains both conditions and every rejected program:

| Configuration           | Original unit-check passes | Requests | Configured token cost |
| ----------------------- | -------------------------- | -------- | --------------------- |
| Sol, low reasoning      | 56 / 78                    | 234      | $2.659978             |
| Astra, medium reasoning | 56 / 78                    | 234      | $15.499610            |

Astra wins two paired tasks and loses two: zero accuracy difference, paired
one-sided exact p = 0.6875. The prospectively frozen improvement gate failed.
The premium configuration cost about 5.83 times as much without improving the
original pass count. Sol remains the cheaper research route; no runtime model
or neural policy was promoted.

All 468 retained provider responses match their work records. The request audit
identifies the configured and reported models and verifies all 156 pairs of
initial-proposal and blind-challenge inputs as identical across conditions.
This is host-retained execution evidence, not independent provider billing.

### Wall-clock uncertainty remains explicit

The first independent native replay matched 155 of 156 outcomes. Astra's
`BigCodeBench/823` changed from rejection to acceptance; its task measures actual
`time.sleep` intervals. Nine predeclared physical repetitions then included a
rejection of the benchmark's own unchanged reference implementation. Both model
programs and the reference passed four fixed scripted-clock calculation cases;
a deliberately incorrect constant-answer control failed.

The original checker and all original labels remain unchanged. The
[complete timing record](../research/outcome-learning/reasoner-transfer-06/timing-823/)
retains every physical result and labels clock controls as fixtures. This is
**not exact binary reproduction**. The exact cause of the first scheduling
observation was not captured; the controls establish wall-clock sensitivity.
Python documents that [sleep can exceed its requested interval](https://docs.python.org/3.14/library/time.html#time.sleep)
because of system scheduling.

Treating this one physical measurement as unknown for both conditions, all four
possible label assignments still fail the original superiority gate. The largest
possible accuracy difference is 1.28 percentage points, below the frozen four-point
minimum. The negative conclusion is robust to this uncertainty; the program
labels are not silently replaced or excluded.

Both experiments share the existing cumulative inference allowance. Their
sub-envelopes do not create new budgets, release old provisions or permit
ambiguous retries. No model weights, neural control policy or original worker
were changed.

Model and API choices follow the official [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning),
[Astra reference](https://developers.openai.com/api/docs/models/gpt-6-astra),
[Sol reference](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and
[pricing](https://developers.openai.com/api/docs/pricing). Documented capability
is not a measured outcome in this experiment.

## Independent comparison verification

The comparison verifier reconstructs each job's original model configuration,
source, common experience, three-call limit and hidden-grading contract from its
append-only journal. It checks the complete pre-grading program commitment,
unchanged provider output hashes and exact paired outcomes. Deterministic test
fixtures check these integrity boundaries; they are not intelligence results.

Host-retained provider transcripts also identify the configured and reported
model, actual token usage and request interface. The audit checks that initial
proposals and blind challenges receive identical information in both conditions.
Known provider errors without retained response transcripts remain explicit
evidence gaps; missing information is not reported as verified equality.

After the complete result package is published, its programs can be replayed
without a provider credential:

```sh
python -m scripts.verify_reasoner_transfer verify \
  research/outcome-learning/reasoner-transfer-06 \
  --workspace .test-state/reasoner-transfer-replay
```

Replay requires a new retained workspace. It executes every committed program
against the original independent checker and reports all mismatches. Configured
token costs are reconstructed for both complete conditions; they remain separate
from independently verified provider invoices. Native diagnostic path redactions,
when needed, retain their original hashes and do not change programs or outcomes.

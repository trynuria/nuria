# Qualification record

The registered comparison passed both gates: learned input synapses improve
bounded digit recognition over a tuned linear readout and an equally trained
readout with frozen input synapses. The best retained comparator is still
5-nearest-neighbours. This is a separate supervised perception specialist, not
a new result for the original recurrent circuit or a general decision-making
agent.

## What changed

Earlier database experiments generated a shared spike stream before feedback;
those features did not measure a feedback-trained choice mechanism. Additional
SQLite and compression-routing development did not establish a neural advantage.
Those failures remain in [service probes](development/service-probes.json).

The specialist learns input synapses and a ten-class output head together from
verified training labels. At inference, an 8×8 image drives 64 leaky integrate-and-
fire cells for sixteen ticks. Actual threshold crossings produce spike counts;
those counts determine the selected digit. Conventional supervised optimization
with an explicit bounded surrogate trains the parameters. There is no language
model or program search in this decision path.

## Qualification chronology

1. Development used a fixed split inside the official training partition.
2. Eleven complete checkpoints, the protocol and seven numerical/test sources
   were frozen at public commit
   [`e13a97cd6aee57d8d3a619814c1d431be2797e36`](https://github.com/trynuria/nuria/commit/e13a97cd6aee57d8d3a619814c1d431be2797e36).
3. The evaluator verified the exact public protocol bytes before projecting test
   inputs. It committed all eleven prediction vectors with unchanged model and
   optimizer state before parsing scoring targets.
4. Independent numerical checks replayed all six spiking branches over all 1,797
   examples. An actual Brian2 reference additionally checked the first eight
   examples of each branch. Non-spiking controls were replayed as well.
5. Only then were targets scored. No source, fitted weights, architecture,
   epoch or seed selection changed after scoring.

The [commitment](qualification/PREDICTIONS-COMMITTED.json),
[start record](qualification/started.json), [predictions](qualification/predictions.json.gz),
[checks](qualification/verification.json) and [result](qualification/result.json)
preserve that sequence and its hashes. Their timestamps and hashes establish
recorded consistency, not an independently witnessed execution. Public GitHub
history establishes the source freeze; it does not independently prove when a
local process read bytes.

## Results and controls

See the [complete table](README.md#held-out-result). Mean trained spiking accuracy
is 96.75385%, compared with 95.26989% linear and 91.68985% input-frozen. The
registered minimum gain was one percentage point with every initialization
strictly improving; both gates passed. The three models share test examples and
are reported as a mean, not as an ensemble or 5,391 independent cases.

The strongest control, kNN5, scores 97.88536%. The matched continuous heads average
96.67965%, only 0.07420 percentage points below the spiking mean. There is no
established spike-specific advantage. The exact finite benchmark observations
have no writer-cluster confidence interval because per-row writer identifiers
are unavailable.

Freezing input synapses throughout otherwise matched training reduces mean
accuracy by 5.06400 percentage points. Restoring initial input synapses while
keeping the trained readout yields only 737, 964 and 712 correct answers. Zeroing
actual spike features yields 183, 181 and 181 correct answers. Those interventions
confirm the decision uses the learned firing pattern; the separately trained
input-frozen branches provide the fair learning comparison.

The frozen scorer emitted paired errors for the three trained models. The
protocol had promised that secondary table for every branch. Rather than rewrite
the original result or scorer, [paired-all.json](qualification/paired-all.json)
adds all eleven comparisons using only the already committed predictions and
already scored targets. It is an arithmetic supplement, not new inference,
training or another qualification.

## Cost and useful scope

On the recorded local process, all 1,797 predictions took about 0.020 CPU seconds
per trained spiking fit, versus 0.00088 linear, 0.36734 kNN and about 0.013
continuous. These are local inference measurements, not end-to-end service
latency, energy savings or a hardware benchmark. Complete JSON checkpoints are
about 426 kB per trained spiking fit versus 26 kB linear and 572 kB kNN. Sizes
include optimizer state and serialization; they are not memory-use measurements.

The implemented skill reads one already segmented handwritten digit from 64
integer pixel summaries. It can support a bounded digit transcription step; it
does not handle full documents, segmentation, arbitrary images or new domains.
Training labels come from the trusted upstream dataset, not token trades or
unverified client feedback.

The [durable service](../../cognition/digit_service.py) records each answer before
its label comparison. Training is separately authorized, source-bound and
precommitted; model, optimizer, receipt and journal updates are atomic. A
qualification instance cannot learn. The dedicated service reproduction is kept
separate from both the original neural history and the unseen qualification.
It has no payment, signing or marketplace authority. Nothing here establishes
better treasury, hiring, market choices, consciousness or superiority over the
strongest simple comparator.

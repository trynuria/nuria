# Learned spiking digit decisions

This study asks whether a trainable neural specialist can make better verified
transcription decisions than a tuned linear policy. It uses actual hard-spike
counts, learned input synapses and a ten-way action readout. It is separate from
Nuria's original 1,024-neuron recurrent circuit and the bounded program-search
learner.

The [frozen protocol](protocol.json), all eleven [checkpoints](models/manifest.json)
and numerical sources were published at
[`e13a97c`](https://github.com/trynuria/nuria/commit/e13a97cd6aee57d8d3a619814c1d431be2797e36)
before this study opened the official test inputs and targets. The qualification
passed its registered linear-control and learned-input gates. The complete
[result](qualification/result.json), [predictions](qualification/predictions.json.gz)
and [independent checks](qualification/verification.json) are retained.

## Held-out result

All methods used the same 3,823 training examples and all 1,797 official test
examples. Upstream assigns different writers to training and testing. The three
initializations are repeated fits on the same test examples, not independent
writers or a deployed ensemble.

| Method                                 | Correct / 1,797 |   Accuracy |
| -------------------------------------- | --------------: | ---------: |
| Tuned, converged linear softmax        |           1,712 |     95.27% |
| Trained spiking head, seed 202611      |           1,741 |     96.88% |
| Trained spiking head, seed 202612      |           1,742 |     96.94% |
| Trained spiking head, seed 202613      |           1,733 |     96.44% |
| Input-frozen spiking head, seed 202611 |           1,658 |     92.26% |
| Input-frozen spiking head, seed 202612 |           1,663 |     92.54% |
| Input-frozen spiking head, seed 202613 |           1,622 |     90.26% |
| Continuous head, seed 202611           |           1,737 |     96.66% |
| Continuous head, seed 202612           |           1,741 |     96.88% |
| Continuous head, seed 202613           |           1,734 |     96.49% |
| **5-nearest-neighbours**               |       **1,759** | **97.89%** |

Mean spiking accuracy was **96.75%**, versus **95.27%** for the linear control
and **91.69%** with frozen input synapses. Every trained initialization beat both
its matched frozen-input control and the linear control. Against the linear
control, the three neural fits corrected 34, 39 and 28 mistakes while introducing
5, 9 and 7 mistakes, respectively.

**Nearest-neighbour remains stronger.** The continuous head's mean accuracy was
96.68%, essentially similar to the spiking head. These results establish useful
learned neural features over the declared linear and output-only controls; they
do not establish a benefit unique to spikes or superiority over every simpler
algorithm. No test result was used to select a model, epoch or seed.

The [full result notes](RESULTS.md) explain the mechanism, retained negative
experiments, execution costs and exact scope. The immutable protocol's prospective
status describes its freeze time; it has not been rewritten after qualification.

## Why a new decision head

The earlier database comparison generated shared spike features before receiving
outcomes. Its separate runner could change synapses, but that did not prove a
useful change in choices. Additional development found identical database index
decisions with raw history, plastic spike features and frozen spike features.
Compression routing also failed to establish a consistent neural advantage over
simple controls. Those [negative probes](development/service-probes.json) remain
part of the record.

The new specialist trains the input synapses that generate its actual firing
pattern and the output weights that choose a digit. The supervised label is an
explicit teaching signal. It is not a reward-only bandit, spontaneous learning,
a human-brain simulation or evidence about consciousness.

## Development and selection

During development, only the official 3,823-row training partition was opened. A fixed stratified
split used 3,060 rows for fitting and 763 for development. All candidate epochs,
architectures and initializations are retained in [development](development).

| Development method                        | Correct / 763 |
| ----------------------------------------- | ------------: |
| Tuned, converged linear softmax           |           740 |
| Stable-tie 5-nearest-neighbours           |           753 |
| Trained 64-cell spiking head, three seeds | 751, 751, 749 |
| Frozen-input spiking head, trained output | 728, 730, 710 |
| Matched continuous 64-cell head           | 748, 751, 750 |

The memory-based control was stronger on development and qualification. The
development figures determined the frozen comparison; none were replaced with
test outcomes.

The frozen fit uses 64 hidden cells, 16 discrete ticks, 125 training epochs and
three fixed seeds. All methods then receive all 3,823 original training labels.
The best linear regularization and the memory control were selected on development,
before any official test row was opened. No best seed or epoch may be selected
from test outcomes.

## Evidence and limits

- [Numerical model](../../cognition/spiking_digits.py) with strict JSON state and
  conventional supervised updates.
- [Training](../../scripts/train_spiking_digits.py) restricted to the exact
  hash-pinned upstream training file.
- [Qualification](../../scripts/evaluate_neural_digits.py) verifies the public
  freeze and commits every prediction before label scoring. All model inference
  states remain unchanged.
- [Independent arithmetic and Brian2 reference](../../scripts/verify_neural_digits.py)
  check hard spikes and readout decisions. Brian2 parity covers eight examples per
  spiking branch; the independent numerical checker covers the entire test set.
- [Durable service](SERVICE.md) commits answers before buyer
  comparisons. Source-bound, precommitted training-label receipts update its model,
  optimizer and journal atomically. Qualification instances cannot train. The local
  registrar supplies trusted labels; commitments establish consistency, not external
  ground-truth authenticity or dataset membership.

The task is limited to already segmented 8×8 handwritten digits. It does not
establish improved market, treasury or hiring choices, nor a benefit from the
original recurrent circuit. CPU, stored-state sizes and stronger controls remain
separate from accuracy. No financial action is involved. Website design, rendering
and existing neural histories are unchanged.

## Service

A [private inference interface](SERVICE.md) accepts segmented digit inputs and
returns durable model-bound decisions. The [server receipt](service-evidence/server.json)
records the complete 1,797-row reproduction, identical-history retry and separate
caller-interface check. This component is separate from the main commerce loop.

## Reproduction

Use the root dependency versions. Check the published evidence offline:

```sh
python -m scripts.check_neural_digit_results research/neural-digits
```

This recomputes the published scores and checks their prediction/model identities;
it does not provide an independent witness of execution timing. To reproduce
fitting and the full two-phase run, extract only `optdigits.tra` for fitting. The
qualification command owns test access after checking the public freeze. A rerun
is a reproduction, not another unseen qualification.

```sh
python -m scripts.train_spiking_digits TRAIN.csv ../nuria-digit-models
python -m scripts.evaluate_neural_digits predict \
  research/neural-digits/protocol.json research/neural-digits/data/optdigits-original.zip \
  research/neural-digits/models ../nuria-digit-qualification --freeze-commit e13a97cd6aee57d8d3a619814c1d431be2797e36
python -m scripts.evaluate_neural_digits score \
  research/neural-digits/protocol.json research/neural-digits/data/optdigits-original.zip \
  research/neural-digits/models ../nuria-digit-qualification --freeze-commit e13a97cd6aee57d8d3a619814c1d431be2797e36
```

Use new output directories. Existing checkpoints, predictions and receipts are
never replaced. See [attribution and method references](DATA-SOURCES.md).

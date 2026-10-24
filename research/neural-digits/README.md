# Learned spiking digit decisions

This study asks whether a trainable neural specialist can make better verified
transcription decisions than a tuned linear policy. It uses actual hard-spike
counts, learned input synapses and a ten-way action readout. It is separate from
Nuria's original 1,024-neuron recurrent circuit and the bounded program-search
learner.

The [protocol](protocol.json), all eleven [frozen checkpoints](models/manifest.json),
training code, inference code and independent verifier are published before the
official test partition is inspected. Qualification is prospective at this commit.
Every model and result will remain visible, including a stronger memory-based
classifier and matched continuous networks.

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

Only the official 3,823-row training partition was opened. A fixed stratified
split used 3,060 rows for fitting and 763 for development. All candidate epochs,
architectures and initializations are retained in [development](development).

| Development method                        | Correct / 763 |
| ----------------------------------------- | ------------: |
| Tuned, converged linear softmax           |           740 |
| Stable-tie 5-nearest-neighbours           |           753 |
| Trained 64-cell spiking head, three seeds | 751, 751, 749 |
| Frozen-input spiking head, trained output | 728, 730, 710 |
| Matched continuous 64-cell head           | 748, 751, 750 |

The memory-based control was stronger on development. These figures motivate a
qualification; they do not establish that the spiking method beats every simpler
method or that spikes are intrinsically better than continuous neurons.

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
- [Durable service](../../cognition/digit_service.py) commits answers before buyer
  comparisons. Source-bound, precommitted training-label receipts update its model,
  optimizer and journal atomically. Qualification instances cannot train. The local
  registrar supplies trusted labels; commitments establish consistency, not external
  ground-truth authenticity or dataset membership.

The task is limited to already segmented 8×8 handwritten digits. It does not
establish improved market, treasury or hiring choices, nor a benefit from the
original recurrent circuit. CPU, stored-state sizes and stronger controls remain
separate from accuracy. No financial action is involved. Website design, rendering
and existing neural histories are unchanged.

## Reproduction

Use the root dependency versions. Extract only `optdigits.tra` for fitting;
the qualification command owns test access after checking the public freeze.

```sh
python -m scripts.train_spiking_digits TRAIN.csv ../nuria-digit-models
python -m scripts.evaluate_neural_digits predict \
  research/neural-digits/protocol.json research/neural-digits/data/optdigits-original.zip \
  research/neural-digits/models ../nuria-digit-qualification --freeze-commit FREEZE_COMMIT
python -m scripts.evaluate_neural_digits score \
  research/neural-digits/protocol.json research/neural-digits/data/optdigits-original.zip \
  research/neural-digits/models ../nuria-digit-qualification --freeze-commit FREEZE_COMMIT
```

Use new output directories. Existing checkpoints, predictions and receipts are
never replaced. See [attribution and method references](DATA-SOURCES.md).

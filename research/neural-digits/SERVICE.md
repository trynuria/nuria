# Durable digit service

This is an isolated, private capability for one already segmented 8×8 digit.
It has no public endpoint, marketplace integration, wallet access or original
recurrent-worker authority. A caller uses a bounded JSON request on standard input;
the process commits its answer before returning it. Model inputs are exactly
64 integer summaries in the range 0–16. Arbitrary caller inputs are identified
by that input contract, not represented as members of the UCI dataset.

The wrapper fixes the reviewed first preregistered model, seed 202611. It checks
the frozen protocol, checkpoint manifest, source identities and model/state hashes
before opening a source-bound journal. It does not choose the best test seed or
combine the three models into an ensemble.

## Predict and compare

Use the root Python dependencies. For a caller-owned input:

```sh
python -m scripts.run_neural_digit_service predict \
  research/neural-digits/protocol.json research/neural-digits/models \
  ../digit-caller-history < request.json
```

`request.json` contains exactly `request_id` and `pixels`. The response includes
the chosen label, actual spike counts, model identity and a durable decision hash.
Keep the same request ID for a retry; different inputs under an existing ID are
rejected. The caller may submit a separate comparison afterwards:

```sh
python -m scripts.run_neural_digit_service compare \
  research/neural-digits/protocol.json research/neural-digits/models \
  ../digit-caller-history < comparison.json
```

The comparison contains exactly `request_id` and an integer `label` in 0–9.
Caller labels are comparison data, not authenticated teaching signals. They do
not update any weights. The underlying library has a separately authorized,
source-bound training interface with precommitted labels, atomic optimizer/state
updates and crash recovery; the operational wrapper exposes no training command.
That library learns by replaying the stored labelled example through its current
model. It does not apply delayed spike-eligibility credit from the original
decision. Membrane state resets per image; learned weights and optimizer state
persist across requests.

## Reproduce the service course

```sh
python -m scripts.run_neural_digit_service reproduce \
  research/neural-digits/protocol.json research/neural-digits/data/optdigits-original.zip \
  research/neural-digits/models ../digit-reproduction-history
```

This validates the original archive and all 1,797 ordered examples. Every prediction
commits before targets are parsed for comparison. The journal must contain only
exact course members; incomplete or interleaved scoring cannot enter the final
receipt. A repeated command resumes identical committed decisions and comparisons,
then appends a separate immutable execution receipt. Inference state remains at
revision zero. This is reproduction on an already inspected benchmark, not new
unseen evidence or production customer work.

The [systemd unit](../../deploy/nuria-digit-reproduction.service) runs this finite
course under a dedicated account with no network, private home access or financial
authority, a five-minute deadline, 512 MiB memory cap and half a CPU. It is a manual
one-shot unit, not a timer or a continuously running learner. Successful completion
means `ExecMainStatus=0`; an inactive completed one-shot is not a claim of continuous
service health. Arbitrary-input requests use a separate journal through the same
private CLI. Do not enable this unit at boot or reuse an existing neural-history
directory.

Install reviewed source at `/opt/nuria/neural-digits/current`, provision the
`nuria-digit` service account, and install the unit using the host's standard
service management. Credentials and private infrastructure details do not belong
in source. Read-only deployment receipts identify actual source and model hashes;
the main website and original workers keep their own release identities.

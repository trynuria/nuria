# Biological data integration

Nuria can capture the current FinalSpark public viewer, retain source-labelled
recordings, derive descriptive waveform features and replay them through an
isolated Brian2 circuit. The provider-recording import and chronological readout
comparison are available separately. Production neural histories are unchanged.

The public viewer is **observation only**. It does not grant a booked organoid,
control of laboratory equipment or an independently verified biological identity.
The current data event has no acquisition timestamp or sequence number. Reception
time is not acquisition time, and reconnecting does not establish continuity.

## Run the public observation path

Use the normal Nuria Python environment. These commands create new directories
and refuse to overwrite earlier evidence:

```sh
python -m scripts.finalspark capture run/biology-capture-001 --frames 3 --seconds 25
python -m scripts.finalspark bridge run/biology-capture-001 run/biology-bridge-001
```

The reader uses the current viewer's same-origin `/controllers`, `/sample` and
Socket.IO `start`/`data` protocol. It selects at most eight electrodes, retains at
most 32 frames and disconnects explicitly. Default network admission limits are
25 seconds and 4 MiB; a final disconnect has a separate 2-second/4-KiB allowance.
The deadline is checked between requests, with socket timeouts up to eight seconds;
it is not a hard real-time guarantee against a server trickling an HTTP body.
Failed or incomplete captures retain a failure receipt and cannot enter replay.
No daemon, scheduled task or automatic reconnection is installed.

Capture files are private by default. They include raw public-view data for local
replay, but public availability does not establish redistribution rights. The
repository publishes receipt hashes and summary results, not raw vendor frames.
The client does not retain internal controller network addresses or transport
session identifiers in its receipt.

The bridge maps per-electrode variation into a fixed bounded drive on the 128
sensory neurons of a new 1,024-neuron circuit. A second new circuit starts from the
same seed with zero external drive. Both retain actual spike-derived features and
state hashes. A changed spike trajectory demonstrates causal wiring. It does not
demonstrate biological learning, useful decisions or consciousness.

## Recording format and import

Waveforms are channel-major: one row per explicit electrode ID. JSON and numeric
NPY arrays need no additional dependency. Object/pickled NPY data are refused.
HDF5 import is optional and requires `h5py` in the data environment; no vendor
format or units are inferred. Supply an explicit dataset key and orient/crop the
array to a bounded window first. External links, external storage and virtual
HDF5 arrays are refused. The optional HDF5 branch has not been exercised against
a FinalSpark-provided file.

```sh
python -m scripts.finalspark import waveform.npy metadata.json run/window-001.json
python -m scripts.finalspark import recording.h5 metadata.json run/window-002.json --dataset /waveforms
```

The output parent directory must already exist. Metadata supplies `schema: 1`,
`mode`, `provider`, `recording_id`, `channels`, `units`, `sample_rate_hz`,
`acquired_utc`, `received_utc` and `provenance`. Modes remain distinct:

| Mode                    | Meaning                                                | Provenance                                                             |
| ----------------------- | ------------------------------------------------------ | ---------------------------------------------------------------------- |
| `public_observed`       | Values received from the public viewer                 | `public_endpoint_unattested`; rate, units and acquisition time unknown |
| `recorded_biological`   | Provider-described biological recording                | `provider_metadata_unverified`; timestamp and rate required            |
| `controlled_biological` | Provider-described recording from a controlled session | Same metadata requirement; not independent proof of control            |
| `synthetic_fixture`     | Explicit generated software-test data                  | Synthetic attribution                                                  |

The mode is attribution, never independent verification of tissue or a claim of
consciousness. RMS, standard deviation, line length and spectral centroid are
descriptive signals. They are not detected spikes or identified individual neurons.
Spectral centroid is expressed in cycles per sample rather than inventing a
physical sample rate for the public viewer.

## Compare against a simple readout

For an already acquired, openly licensed human-organoid source, use the
[DANDI replay](../research/biology/dandi-01/README.md). It reads curated NWB spike
times through PyNWB, retains all 49 sorted units and compares a continuous
recording-driven Brian2 circuit with conventional history models and a zero-drive
control. This is an event-count representation, separate from waveform samples;
it never labels counts as voltages. The current real-data result favours the
simple history model. No booked biological hardware or live feedback is involved.

The later [acquisition experiment](../research/biology/acquisition-01) makes a
resource-limited decision from that source. After four fixed sentinel reads, a
learned policy chooses twelve additional unit records from the same completed
window. It captures 64.1% of later-minute spike entries while selecting 32.7% of
records, versus 57.5% for a fixed most-active selection at the identical budget.
The validation-selected method uses nearest-neighbour memory; the measured
spiking alternative is weaker. This is offline two-stage selection, not future
prediction, physical electrode switching or a measured CPU/bandwidth saving.

```sh
python -m scripts.active_observation verify research/biology/acquisition-01
```

The finite streaming `RecordPicker` reproduces every recorded test acquisition
from four-count observations alone. Its contract, source attribution, complete
selection and failed alternatives are included in the experiment record. It is
not enrolled in the public application.

A labelled course is a JSON object containing `schema: 1`, `target_definition` and
12–128 `rows`. Each row supplies a complete `recording`, a scalar `target`,
`start_utc` and `stop_utc`. Windows must be chronological, non-overlapping, have
consistent recording/electrode identities, and agree with sample count and rate.
Duplicate sample windows are rejected. Public-view frames are ineligible because
their acquisition timing and task annotations are missing.

```sh
python -m scripts.finalspark compare course.json run/biology-comparison-001
python -m scripts.finalspark verify run/biology-comparison-001/result.json
```

Before feature extraction, the run saves its input digest and fixed protocol:
50% training, 25% validation, 25% test in chronological order. Normalization uses
training data only; three declared ridge strengths are selected on validation.
Test targets never enter fitting. The baseline sees waveform statistics. The
candidate sees the same statistics plus a fixed Brian2 response. Neural state is
reset for each window; neither labels nor rewards train the circuit in this test.
The score verifier reconstructs both fitted readouts and test errors from retained
numeric evidence, with a tight floating-point tolerance. It does not verify raw
signal origin or independently regenerate the Brian2 features.

This is a small decoding comparison, not synaptic calibration to a human brain.
Real experiment design still needs an independently defined target, enough
recordings and cultures, explicit artifact controls and a prospective evaluation.
Tune only on training/validation, then test on new sessions or cultures. Retain
failed comparisons. The current [integration results](../research/biology/README.md)
include a synthetic fixture where adding Brian2 worsens the error.

## Granted laboratory access

The current [NeuroPlatform v2 documentation](https://finalspark-np.github.io/np-docs/np_core/doc_v2.html)
specifies Python 3.11/3.12 and authorized lab-network settings. Keep that SDK in a
separate environment; importing it into the site's Python environment is not
required. No SDK or access credentials are bundled here.

`cognition.biological_session.RecordedSession.from_vendor_sdk()` binds only
`DatabaseController.get_spike_event(SpikeEventQuery(...))`. Reads are limited to
60 seconds, a 15-second query timeout and 100,000 returned events. Missing data
raises an error. Returned timestamps and channels must fit the requested window.
This SDK contract has been tested with an explicit substitute controller only;
live granted access remains unverified.

The same module produces non-executable, source-bound stimulation proposals for
operator review. A high-volume trade window proposes at most one configured
pattern, rather than turning every trade into a pulse. There is no trigger,
upload, pump or camera execution method. Before adding one, obtain an assigned
session, exact SDK version, operator-approved electrical and electrode envelope,
artifact exclusion, cooldown and scoped cleanup instructions. A global
`disable_all_stim` call may interfere with shared equipment and is not a generic
cleanup action. Follow the provider's
[laboratory guidance](https://finalspark-np.github.io/np-docs/np_core/faq.html).

Creator fees could pay for authorized research access. They are a funding source,
not a biological reward signal. The public stream cannot turn token trading into
an actual closed-loop organoid experiment.

## Access and data prerequisites

Request a small annotated recording sample, format/units/rate/event definitions,
sample and culture identity, stimulus/artifact annotations and data/derivative
publication rights. Ask FinalSpark to confirm commercial-project eligibility,
SDK licensing, current allocation/quote/start date and how public-view data are
sourced. The published platform paper describes a real organoid research system;
it does not establish consciousness or that a particular public frame came from
an identified live culture:
[FinalSpark platform paper](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2024.1376042/full).

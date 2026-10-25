# Semantic input wiring

A fixed pretrained sentence representation now has a bounded path into Brian2's
actual sensory and memory channels. This evidence bundle records the initial
wiring probe. It does not establish a useful-decision advantage, language learning
by the spiking model or consciousness.

## Recorded observations

Before the encoder processed any text, the protocol froze twelve neutral
questions, six paraphrase-versus-unrelated contrasts and the numerical mapping.
The actual CPU run performed exactly twelve encodes using the pinned
[MiniLM artifact](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41).
All six predeclared similarity contrasts passed. Every representation was finite
and normalized; the largest sensory current was `0.1659471095`, below the existing
`0.2` bound.

Four actual 100 ms Brian2 windows then received those recorded vectors. The same
initial checkpoint and RNG state were restored before each window:

| Input                                           | Recorded spikes |
| ----------------------------------------------- | --------------: |
| Invoice question                                |             827 |
| Invoice paraphrase                              |             847 |
| Unrelated latency question                      |             841 |
| Original invoice question, different provenance |             827 |

The first and fourth windows had identical input, complete spike and final-state
hashes despite different job/source metadata. The unrelated input changed the
actual spike record. No reward, terminal task label, payment or delivered purchase
was supplied in this probe. The metadata values are deliberate invariance
controls, not token activity.

The twelve-text CPU observation took `1.0212 s` of host wall time and peaked at
`175,865,856` RSS bytes. Four Brian2 windows took `0.4730 s` and peaked at
`137,068,544` RSS bytes. Other development processes ran concurrently; these are
recorded resource observations, not isolated latency or throughput benchmarks.

## Inspect the data

The [evidence manifest](evidence-manifest.json) identifies every published file,
its original artifact checksum and any public projection. The gzip files retain
complete JSON/JSONL payloads:

- [Frozen neutral protocol](evidence/text-protocol.json) and
  [pre-encoding source freeze](evidence/prototype-freeze.json).
- [All twelve vectors, currents and receipts](evidence/semantic-output.json.gz),
  [append-only observations](evidence/semantic-observations.jsonl.gz) and
  [CPU summary](evidence/semantic-result.json).
- [Brian2 pre-run freeze](evidence/brian-freeze.json),
  [all four neural frames](evidence/brian-windows.jsonl.gz) and
  [Brian2 summary](evidence/brian-result.json).
- [Final stored-vector mapping equivalence](evidence/public-current-equivalence-final.json)
  and its [earlier wrapper identity](evidence/public-current-equivalence.json).

The captured prototype adapter and probe sources are included as text. The Brian2
probe's host-specific source-root assignment is replaced by a portable relative
path; the manifest distinguishes its original and projected checksums. Model
weights, package caches and the trusted local binary checkpoint are excluded.
Historical freeze references to private preparation receipts remain historical
identities. This bundle is not a claim of an independent replay from the omitted
binary checkpoint.

The first preparation attempt stopped before downloads or environment creation at
its `>21 GiB` free-disk assertion. Its exact failing byte sample was not recorded;
the later refusal receipt used an imprecise 20 GiB explanation. The manifest
preserves that receipt's checksum and this discrepancy. Preparation resumed only
after three separate 22 GiB headroom checks passed. No reserve reduction or file
deletion was used to pass the guard. The public CPU summary omits private host
storage telemetry; every omission is recorded.

## Implementation and limits

The optional [encoder](../../cognition/semantic_encoder.py),
[one-shot CPU worker](../../cognition/semantic_worker.py) and
[semantic bridge](../../cognition/semantic_neural.py) add identity-bound observations,
explicit process bounds, durable reservations and delayed credit. The
[integration guide](../../docs/semantic-neural.md) describes the host contract.
The hardened public wrappers were tested using scripted runtime fixtures and
retained vectors; they have not been silently substituted for the captured
prototype's live source identity. The real probe used twelve encodes; packaging
and mapping verification made none.

The language model supplies its pretrained semantic competence. The current probe
shows that this representation reaches actual neural sensors and is independent
of provenance identities. Whether the circuit makes better useful decisions
requires fresh, prospectively enrolled tasks and a strong conventional selector
using the same representation, observations, feedback and resource budget.
Frozen-state, shuffled-representation, lexical and identity-texture controls are
planned for that comparison; no result from those controls is claimed here.

The retained observations can be checked without loading pretrained weights or
starting a neural simulation:

```sh
python -m scripts.verify_semantic_wiring
```

This recomputes all twelve bounded current mappings, checks text/vector/model and
spike hashes, preserves the full denominator and tests the recorded provenance
invariance. It checks retained evidence consistency, not external attestation of
the original execution or replay from the private binary checkpoint.

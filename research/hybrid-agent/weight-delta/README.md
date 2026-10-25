# Numeric witness for the semantic work audit

The completed semantic work audit retained a positive outcome and a receipt
reporting 205 changed synapses. This additive witness checks the actual exported
numbers against that receipt and the original recorded neural state identities.
It leaves the [original work evidence](../work-trials/README.md) unchanged.

Across all **22,920 excitatory weights**, exactly **205** differ between the saved
chosen and settled checkpoints. Those indices are exactly the saved originating
eligibility tags. The complete after-weight array equals:

```text
after = before
at saved indices only:
    after = clip(before + 0.002 × positive_reward × saved_eligibility, 0.002, 0.18)
```

Every other variable in the recorded state projection is unchanged. All weights
remain within the declared bounds. The absolute changes range from approximately
`1.79e-9` to `9.09e-6`; this is one bounded update, not a large capability claim.

The numeric arrays reproduce both original projected-state SHA256 identities:

```text
before: 196fa5ac1756b3b2434a15ba9250471d8857612078a4b5d5f98e150b86ec3566
after:  6eb0f45c480eb5a0e2c6b7601f32d1675fb63c7ac1bbb9a57f687084e6cf2ed8
```

That projection covers seven neuron arrays, five plastic-synapse arrays and the
clock/offset pair, in the original `AdaptiveBrain.state_hash` order. It does not
cover every saved queue, RNG value or connectivity variable. The original opaque
checkpoint bytes remain hash-bound in the work-evidence archive.

## Derivation and trust

The export was made from Nuria's **original locally generated checkpoints**, after
validating the complete four-capsule chain, frozen source identities, original
terminal outcome, decision and saved eligibility. The two original checkpoints
were decoded with a restricted NumPy-only unpickler. This trusted-local conversion
is disclosed in `derivation.json`; it is not an independent external witness.
Brian2 was not restored or advanced, and no model or encoder was called.

The portable verifier loads only `arrays.npz` with `allow_pickle=False`. It checks
numeric headers, shapes, dtypes, hashes, original opaque checkpoint hashes,
metadata/receipt linkage, both state projections, saved eligibility, every weight
and the exact reward equation. It does **not** unpickle or reexecute the source
checkpoints. Connectivity is a derived export from those opaque bytes, rather
than a component of the original projected-state hash; its provenance relies on
the disclosed conversion.

This establishes local numerical consistency of the recorded update. It does not
establish that the update improves future decisions, changes the foundation
model's weights, creates consciousness, or represents a complete human brain.

## Read-only verification

With the existing NumPy dependency, from the repository root:

```sh
python research/hybrid-agent/weight-delta/verify_weight_delta.py \
  --work-evidence research/hybrid-agent/work-trials

python -m unittest discover \
  -s research/hybrid-agent/weight-delta -p test_weight_delta.py -v
```

The verifier writes no files and performs no network calls. Five fixtures cover
the original witness, changed numeric state, substituted/duplicate tags, a wrong
source archive and object-array refusal before deserialization. Controlled test
fixtures are retained rather than automatically deleted.

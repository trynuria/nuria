# Fresh reasoning transfer

Sol-low and Astra-medium received the same 485 checked experience records and
three-call retrieval workflow. Metadata-only selection preceded task-body access:
79 tasks were selected and 78 reference programs passed the native preflight.
The unsupported case remains recorded without replacement.

| Configuration | Original unit-check passes | Requests | Configured cost |
| ------------- | -------------------------- | -------- | --------------- |
| Sol-low       | 56 / 78                    | 234      | $2.659978       |
| Astra-medium  | 56 / 78                    | 234      | $15.499610      |

Astra won two paired tasks and lost two, p = 0.6875. The frozen superiority gate
failed. No runtime or neural policy was promoted. All programs were publicly
committed at `5f2f2cf8741a3bab2b3fe7842fe8e569b636311a` before any terminal grade.
`request-audit.json` verifies all 468 provider responses and 156 equal-information
pairs. Configured costs are not verified provider invoices.

`independent-replay.json` retains the complete first native replay: 155 matches
and one timing-sensitive Astra outcome changing from false to true. This is not
exact binary reproduction. `timing-823/` contains the predeclared physical and
scripted-clock controls, including a physical rejection of the unchanged reference
and rejection of an incorrect constant-answer control. Original labels are
unchanged. Every possible timing-label assignment still fails the promotion gate.

The default strict replay command retains its nonzero result for a mismatch:

```sh
python -m scripts.verify_reasoner_transfer verify \
  research/outcome-learning/reasoner-transfer-06 \
  --workspace .test-state/reasoner-strict
```

The separate negative-result qualifier runs every original program and checker,
requires all other native outcomes to match, reruns the fixed timing controls and
checks the failed gate under all four timing-label assignments. It exposes the
mismatch and never claims exact reproduction:

```sh
python -m scripts.characterize_timing_contract negative \
  research/outcome-learning/reasoner-transfer-06 \
  .test-state/reasoner-negative
```

See [methods and limits](../../../docs/reasoning-options.md).

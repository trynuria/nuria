# Feedback consistency integration

This provider-free integration uses the complete
[reasoning comparison](../reasoner-transfer-06/), its independent native replay
and both prospectively bounded physical timing characterizations.

All 156 original receipts are imported into a new retained journal. The host
adds 156 native replay observations and twelve model-program observations from
the fixed physical characterizations. Scripted-clock checks use a different
checker and are not admitted as corroborating observations of the original one.

| Result                                         | Count |
| ---------------------------------------------- | ----: |
| Original jobs retained unchanged               |   156 |
| Additional actual host observations            |   168 |
| Jobs withheld from future automatic experience |     2 |
| Remaining eligible experiences                 |   154 |
| New model calls or program executions          |     0 |

Both withheld jobs are `BigCodeBench/823`, one in each model condition. Every
other job remains eligible. Withholding affects future memory only: all 156
original grades remain in the comparison, which still ties at 56/78. There is
no exclusion from evaluation and no revised success-rate claim.

[report.json](report.json) binds the source evidence and implementation hashes,
the unchanged raw-history hash, all withheld job identities and the journal
tail. All 168 duplicate receipt submissions leave the journal unchanged.

```sh
python -m scripts.reconcile_learning_feedback \
  research/outcome-learning/reasoner-transfer-06 \
  run/feedback-reconciliation \
  --expected research/outcome-learning/feedback-consistency-07/report.json
```

The destination must be new. The command checks the complete original package
and retains its own imported records. It calls no model and runs no new program.
These are revealed outcomes and a feedback-integrity integration, not a fresh
reasoning comparison or evidence of a neural intelligence advantage.

See [the work interface and limits](../../../docs/consistent-outcomes.md).

# Primary comparison results

On twelve reserved database questions, Brian2-guided first inspection produced
nine accepted answers; fixed table-list inspection produced eight. There was one
neural-only correct answer and eleven ties. This is a small, bounded result.

| Reserved result         | Fixed inspection | Neural inspection |
| ----------------------- | ---------------: | ----------------: |
| Accepted / selected     |           8 / 12 |            9 / 12 |
| Wrong submitted answers |                2 |                 2 |
| Abstentions             |                2 |                 1 |
| Completion tokens       |           26,681 |            17,951 |
| Prompt tokens           |          245,920 |           142,053 |
| Tool calls              |               49 |                30 |

The neural condition used 32.72% fewer completion tokens on this cohort. Prompt
use and tool use also differed. These are observed within-run differences; total
compute was not matched. Recorded analyst-job time excludes the preceding neural
choice and is not an end-to-end latency measurement.

Both conditions use the same pinned Qwen model, registered read-only tools,
procedural-memory implementation and types of measured neural context. Each has
its own learned history. The fixed condition also runs Brian2. This pair tests
the integrated first-inspection policy; it does not establish that neural
plasticity beats a brain-free agent.

## Coverage and accounting

All 120 selected method-task records are retained. Sixty-four were executed and
settled; fifty-six were unexecuted. Both primary conditions completed eight
teaching and twelve reserved questions. Each of the four secondary conditions
completed six teaching questions and no reserved questions. Those missing
comparisons remain unknown.

The unchanged original allowance was 200,000 completion tokens and 10,800 charged
seconds, including earlier attempts. Final known completion use was 165,541;
the retained historical unknown-usage ceiling was 12,214, for a conservative
charge of 177,755. No new pending or ambiguous model usage remained. The next
four-condition block required a 24,000-token reservation, exceeding the 22,245
remaining. The protocol therefore stopped before starting that block.

## Independent replay

The portable archive reconstructs all 314 original files. Its independent check
passes for the complete selected-record coverage, 52 submitted final queries,
source identities, request and tool records, memory chains, accounting, neural
capsule hashes and origin-linked delayed credit. The reconstructed check is
byte-identical to the retained receipt:

```text
d9680fdb4ab2801346ab636c3c2ac2b06757edb88a6854c2a5b6b21361e2ca48
```

This is finite SQLite result agreement, not universal SQL equivalence or the full
official Spider metric. The checker does not restore or reexecute the saved
spiking state. Hashes establish local consistency, not external attestation or
authenticated cross-file wall-clock ordering. See [extraction and verification](README.md).

## Interpretation

This is one frozen model, seed and order, twelve reserved questions and two
public benchmark databases. Spider may have appeared in model pretraining. The
result does not establish general superiority, new language learning or
consciousness.

These results used the older identity-texture input: hashes, tool statistics and
question byte length. The separate [semantic-input path](../../semantic-input/README.md)
feeds pretrained question vectors into actual Brian2 currents. Its wiring results
are separate from this comparison. The full-embedding [conventional controller](../../../docs/semantic-control.md)
is implemented for a future same-information semantic comparison; its useful-task
performance has not been measured.

The frozen protocol and failed attempts remain in [the attempt archive](../attempts/).
Any supplemental control completion must retain its own protocol, accounting and
post-hoc designation; it does not rewrite this run's coverage.

## Subsequent simple controls

The [separate supplemental run](../supplemental-controls/RESULTS.md) is complete.
Schema first accepts the same nine cases as neural inspection; stateless accepts
eight. Neural inspection uses fewer completion tokens but more prompt tokens than
schema first. The stronger-control tie and post-hoc limitations accompany this
primary result; the original records are unchanged.

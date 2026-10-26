# Workflow execution record

The hosted planner completed six declared goal-to-artifact jobs. Its JSON plans
ran registered local handlers; the final checker rederived the facts and read
the actual saved Markdown. Greedy and exact planning also completed 6/6. This is
a useful execution result, without a demonstrated model or neural advantage.

| Deliverable                                             | Source scope                                        | Result                                |
| ------------------------------------------------------- | --------------------------------------------------- | ------------------------------------- |
| [Provider brief](provider-brief.md)                     | Forty retained reported tasks                       | Accepted                              |
| [Payment brief](payment-brief.md)                       | Same forty reported tasks                           | Accepted                              |
| [Local audit plan](audit-plan.md)                       | Same forty; four proposed rechecks, two deferred    | Accepted                              |
| [Boundary provider brief](boundary-provider-brief.md)   | Declared synthetic 42-row revision                  | Accepted                              |
| [Recovered provider brief](recovered-provider-brief.md) | Forty retained tasks; injected primary-reader fault | Accepted after a retained failed read |
| [Boundary payment brief](boundary-payment-brief.md)     | Declared synthetic 43-row revision                  | Accepted                              |

These links are byte-identical copies of the model arm's saved deliverables.
The synthetic recorded amounts are fixtures, not purchases. Delivery and utility
are server-reported feedback; no independent semantic acceptance or chain
payment verification is implied. The audit artifact is a plan, not executed
audits. Its zero proposed-audit spending excludes model inference.

The model used seven requests and 1,137 output tokens, including reasoning.
Configured inference cost was $0.029298. Its fault case retained the failed JSON
read and switched to SQLite in a second plan. All three methods charged seven
local work units for that case. These static units are not dollars or CPU time.

## Verify without model access

The [archive](workflow-evidence.tar.gz) retains all eighteen selected jobs,
twenty-one proposals/executions, intermediate artifacts, final reports and
eighteen saved Markdown files. The [manifest](archive-manifest.json) pins every
member. The complete source, inputs and protocol were read back from public
commit `c2314618b968feb87c22e12b9d0a409886b982be` before inference.

```sh
python -m scripts.extract_workflow_evidence \
  --evidence research/workflow-reasoning/results-01 \
  --workspace /tmp/nuria-workflow-evidence
python -m scripts.verify_workflow_course \
  --repository . \
  --results /tmp/nuria-workflow-evidence \
  --replay-workspace /tmp/nuria-workflow-replay
```

Both workspace paths must be new. Verification makes no model calls and needs
no credentials. Handler fingerprints in historical receipts identify local code
objects; portable source identity comes from the protocol's file hashes, rather
than claiming those runtime fingerprints are portable attestations.

The active protocol's SHA-256 is
`32b820a2ecc1ca8e65953bfe5aa2d4ba9effd4929bde3652334735473a581c3c`.
The unused preflight protocol was revised before inference to require visible
source-coverage labels; no selected cases, scoring facts or outcomes changed.

# Interrupted trial and model substitution

The first hosted policy trial retained all **54 selected jobs**. It attempted
32, accepted 31, committed one abstention and left 22 unexecuted. It stopped
when the provider returned HTTP 503 during a fixed-review job. No wrong SQL
submission was accepted. This is an interrupted course, not a completed accuracy
comparison.

Validation tied at 4/4 for the original and both proposed policies. The selector
kept the original. The incomplete test portion provides no evidence of a
learned-policy advantage:

| Test method     | Selected | Attempted | Accepted | Abstentions | Unexecuted |
| --------------- | -------: | --------: | -------: | ----------: | ---------: |
| Schema first    |       12 |         4 |        4 |           0 |          8 |
| Selected policy |       12 |         5 |        5 |           0 |          7 |
| Fixed review    |       12 |         5 |        4 |           1 |          7 |

The selected policy is the unchanged baseline. Differences between its partial
coverage and schema first are not learning gains. The failed job and its
abstention remain retained; no repair or retry replaces that result.

## Conservative request accounting

Known provider-reported usage accounts for $2.404038 at the configured uncached
rates. The failed request has unknown usage and a $0.136390 original maximum
reservation. A host-only loss provision permanently counts that entire maximum
against the same $10 allowance. Total configured exposure is therefore
$2.540428 before subsequent work. This is not verified provider billing, a
refund or a claim that the failed request used no tokens.

The provision retains the failure, request hash and unknown usage, with an
auditable evidence hash. It cannot change a reservation's ceiling or increase
the allowance. Other pending requests still block. The 2,048-token unknown
completion liability also remains charged against the aggregate experiment
ceiling. No automatic retry is introduced.

The local token envelope and configured rates are host assumptions rather than
provider invoice evidence. A separate operational guard stops subsequent work
before total configured exposure would cross **$5**, leaving additional margin
under the $10 allowance.

## A distinct question about the model upgrade

The [model-substitution protocol](model-substitution-protocol-01.json) asks
whether the stronger foundation-model backend changes results on the **complete
twelve-case cohort** where the earlier Qwen schema-first condition accepted
9/12. It does not select only the earlier failures.

The analyst, registered data tools, procedural-memory implementation and
working-evidence code match that historical condition. Tool, turn and completion
ceilings match. The new model receives no policy suffix, training records,
reference SQL or expected rows. All twelve new proposals commit before its
oracle is decoded. An incomplete batch remains ungraded.

This is a descriptive historical before/after check on a published, unblinded
public cohort. Different model weights, reasoning implementation, seed support,
hardware and time remain confounds, and model pretraining contamination is
possible. It is not a fresh held-out benchmark or a causal neural comparison.

The follow-up uses the existing shared ledger. It subtracts earlier known and
charged-unknown completion use from the original 328,096-token ceiling and
earlier elapsed model time from the 90-minute allowance. Its own ceilings are
at most 72,000 completions and 30 minutes. The original interrupted run remains
a separate retained result.

The [evaluator](../../scripts/evaluate_model_substitution.py) and protocol are
published before follow-up inference. Results will be reported separately.

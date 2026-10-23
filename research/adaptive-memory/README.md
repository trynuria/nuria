# Adaptive outcome memory

The earlier TVB/Brian2 integration changed spikes but did not establish a useful
forecasting improvement. This follow-up addresses the forecast path: explicit
ordered memory and adaptation when a previously useful rule changes.

## Diagnosis

The [trace replay](diagnosis.json) exactly reconstructs the original council.
On delayed dependency, removing its neural expert increases Brier loss from
0.211862 to 0.231537. Existing neural activity carries useful information.
However, adding TVB changes the neural probability by only 0.00136 on average.
On reversal, that change is 0.00174 and the council probability changes by only
0.00000821. The context logistic specialist scores 0.225992 while the complete
council scores 0.250921: selecting and adapting the predictors matters.

The council has no explicit ordered sequence buffer. Its conditional counts
have a 693-input half-life, while the original reversal leaves only 80 inputs
under the new rule. TVB's structural weights and engineering ports do not learn
an outcome-dependent representation. These observations identify plausible
bottlenecks; they do not assign every failure to one cause.

## Candidate

The original neural/statistical council remains a specialist and initially owns
90% of the new mixture. Other specialists learn lagged relations, suffixes up to
eight events and contextual transitions. Each has slow and fast outcome counts.
Online likelihood weighting keeps useful predictors available after a shift.
The buffer holds 16 observed sides, and each count bank has at most 1,024 cells.

Labels update only the keys and probabilities retained before that label
arrived. Each source owns a separate instance. Checkpoints retain pending
forecasts, counts, mixture weights, ordered history and the original council.
Migration preserves the old parameters and metrics; the new method starts its
own evaluation history at its actual first input.

The implementation is a bounded statistical memory component. It is distinct
from Brian2 plastic synapses and TVB regional connectivity. Variable-order
prediction is established work; this is not a claim of a new CTW algorithm or
its theoretical guarantees. See [Begleiter, El-Yaniv and Yona, 2004](https://arxiv.org/abs/1107.0051)
and [Willems, Shtarkov and Tjalkens, 1995](https://doi.org/10.1109/18.382012).

## Fixed comparison

The [protocol](protocol.json) is byte-frozen by [its digest](protocol.sha256).
[Implementation hashes](implementation.json) are committed before validation.
Development uses three seeds; validation reserves twelve different seeds.
Every task scores 1,792 outcomes after the same 256-transition warm-up.

Eight families cover delayed relations, parity, changing lag rules, changing
amount/fee rules, stable contexts, first-order transitions and independent fair
or biased inputs. Comparators include the old council, sequence/fast-memory
ablations, frozen learning, a learned lag-only mixture and suffix-only backoff.
Neither simple baseline receives the generating lag or rule.

A second metric applies the same fixed information-choice rule to every prior
forecast. It decides whether an offered 90%-accurate measurement is worth its
known virtual cost before either the outcome or delivery is visible. All
models learn the subsequently observed true side. This tests the usefulness of
confidence for cost decisions; it is not a live purchase, vendor evaluation,
new hiring controller or bandit-learning result.

Development output is retained in [development](development). The first fixed
candidate passes its development gates. Validation determines whether it earns
a production update; its results are reported separately.

```sh
python -m unittest discover -s tests -p test_adaptive_forecast.py -v
python -m scripts.diagnose_multiscale research/multiscale/results .test-state/new-diagnosis.json
python -m scripts.evaluate_adaptive_memory .test-state/new-memory-run --phase validation
```

Use a new output directory. The evaluator refuses changed protocol bytes and
uncommitted implementation sources during validation. No RPC, signer or live
neural checkpoint enters the comparison. These are controlled learning and
virtual-choice tests, not market-profit or consciousness evidence.

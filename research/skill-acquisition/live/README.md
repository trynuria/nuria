# Live learning, retained skills and checked consequences

The running cognitive and commerce workers execute the skill loop described in
the [architecture](../../../docs/architecture.md). Neural-context task selection
creates an intent; the local provider returns an executable bounded program; an
independent buyer interpreter checks actual outputs. Successful programs persist
in a source-bound library. Later execution requests apply a retained program to
unlabelled inputs without synthesis. Actual accepted and rejected outcomes update
their originating cognitive context and tagged eligibility. Feedback is
acknowledged only after its checkpoint commits.

This record checks deployment behavior. The [reserved retention comparison](../retention.md)
separately measures the benefit against the identical solver without retention.
Its 51.9% CPU reduction and 54.9% counted-work reduction are amortized efficiency
results with 100% correctness in every branch. Neither result establishes a
neural-policy advantage, autonomous format recognition or general intelligence.

## Deployment and workload

The cognition and commerce components were deployed on 24 October 2026 at
12:36:20 UTC from
[`cf39f0f`](https://github.com/trynuria/nuria/commit/cf39f0f22558210698cdb35c7c24efbe9f964a71).
Their respective component source hashes are
`5c56d9183eb1cf04662222deefdbfb06db779d5058a6cb100df718c965eb36db` and
`5269bcbc3fdd3022db50ace253e37d8bb5816d9f4a88761f448b83132c91efab`.
The website's layout, typography and neural visualization remain unchanged.
Newer verification code and these records do not require a worker restart.

The finite course uses the committed
[public corpus](../retention-tasks.json), not new customer or token activity.
Numeric observations come from UCI Bike Sharing; formatting and arithmetic tasks
are explicitly generated. The first 56 files contain 24 acquisition files,
16 first unseen compositions and 16 repeated formats. Four goal lanes permit
the live policy to choose their execution order; this is not an exact ordered
replay of the comparative study.

## A rejected serving batch and its correction

The first label-free serving batch incorrectly paired programs by list position
with shuffled files at corpus positions 56–71. Only two of sixteen requests
matched their format. Buyer checks rejected fourteen jobs; their nonzero work,
negative rewards and committed neural feedback remain in
[initial-serving.json](initial-serving.json). Nothing was erased or relabelled
as successful. The `complete` flag means the checker verified every job,
including failures; success counts are separate.

The [execution builder](../../../scripts/build_skill_queue.py) now binds an
explicit `format_index` to its successful learning receipt and exact recipe
identity. It rejects missing or duplicate bindings, changed recipes and
same-format source-row or input overlap. A fresh batch uses corpus positions
72–87. Buyer metadata names the program to execute; the provider receives its
hash and inputs, with no examples, expected outputs or replacement-synthesis
permission. This is caller-selected reuse of a known format, not discovery of
which program arbitrary data requires.

The correction changes request construction, not the frozen solver, interpreter,
comparative traces or previously recorded rewards. It does not demonstrate that
Nuria independently diagnosed or repaired the dispatch error.

## Observed completion

[corrected-serving.json](corrected-serving.json) retains all 88 actual jobs:

| Live phase                        | Jobs | Passed | Rejected | Exact final rows |
| --------------------------------- | ---: | -----: | -------: | ---------------: |
| Learning and example-guided reuse |   56 |     56 |        0 |          672/672 |
| Initial incorrect dispatch        |   16 |      2 |       14 |           48/384 |
| Corrected known-format serving    |   16 |     16 |        0 |          384/384 |

The library contains **40 verified programs**. All 88 outcomes have matching
origin-bound cognitive feedback and committed acknowledgments. The independent
interpreter checked 1,440 final rows, of which 1,104 were exact; the fourteen
incorrectly dispatched jobs account for every remaining row. All 72 job receipts
from the initial certificate remain byte-for-byte identical as decoded records
in the final certificate. The new batch contributes sixteen additional records.
Total actual provider work is 97,121 units, with another 12,928 units for final
buyer checks. Failed work is retained in these totals.

These observations show executable skills acquired, persisted and correctly
applied to new rows, plus real outcome feedback reaching the running cognitive
worker. They do not show a policy win against a simpler agent in production;
the separate preregistered study supplies the narrower retention-efficiency
comparison. No paid purchase, customer delivery or token-driven learning result
is claimed here.

## Verification boundaries

[`verify_live_skills.py`](../../../scripts/verify_live_skills.py) opens read-only
WAL transactions and checks actual job contracts, delivery hashes, retained
program identities, independently executed outputs, charged work, commerce
feedback and origin-bound cognitive receipts against committed acknowledgments.
It verifies the full inspected commerce hash chain and relevant cognitive
records; it does not rescan the entire cognitive history or independently
reconstruct synaptic deltas during ongoing plasticity. Recorded changed/tagged
synapse counts are described as recorded counts.

Per-database snapshots are individually consistent, not a globally atomic
snapshot. Component hashes and actual process directories must match the stated
release. Missing financial tables or columns fail verification rather than
becoming zero. The public certificates contain commitments and whitelisted facts,
excluding program bodies, private paths, configuration, row values and evaluation
nonces. They are locally checked evidence, not an external witness or onchain
attestation.

All jobs use included server capacity with zero wallet payment. Financial policy
and external execution remain disabled. Ledger counts establish no recorded
authorization, signing request or settlement in the inspected ledger; they do
not constitute an independent wallet balance/history audit. The finite queue
stops proposing work when exhausted. It does not generate unlimited work,
external customers or paid hiring by itself.

The checker tests include a genuine isolated Brian2-to-learning-to-serving loop,
a buyer-rejected wrong-format request, tampered deliveries and reward records,
missing financial evidence and mismatched release identities. Builder tests cover
permuted formats and withheld buyer targets. Run them with:

```sh
python -m unittest discover -s tests -p test_live_skills.py -v
python -m unittest discover -s tests -p test_build_skill_queue.py -v
python -m scripts.verify_live_skills --help
```

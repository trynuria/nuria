# Verified programs reduce repeated work

This study compares the **same strong factored solver** with and without retaining
buyer-verified programs. It tests a practical form of learning: a successful
executable artifact remains available for later files, rather than being
rediscovered every time. The mechanism is conventional memoization and program
reuse; it does not establish a new AI algorithm or neural advantage.

The precommitted twelve-seed comparison completed **4,896 actual job executions**.
Every method remained exactly correct on every file. Verified program retention
used **51.9% less measured process CPU per correct file** and **54.9% less counted
work**, including acquisition, admission and verification costs. The result is
an amortized efficiency improvement with a 100% competence tie.

The [frozen protocol](retention-protocol.json) and all seven evaluated source
identities were published at
[379b9fb](https://github.com/trynuria/nuria/commit/379b9fb546c27815dccd850436a99e713535faa5)
before the reserved run. The protocol file's SHA-256 is
`635fd426c1a6fc91c44ddfcdf7db959d877d74cbd266e95f8f83d8a1e47bd92c`.
The reserved seeds ran once; no source or parameter changed during execution.

## Why the comparison changed

The [first string study](README.md) was too easy: strong fresh search already
solved everything. The [multi-field study](expressions.md) initially favored
learned fragments over generic enumeration, but a better ordinary constraint
solver erased that advantage. Those failed claims and their evidence remain
visible.

The present comparison holds that better solver fixed. The only useful
experimental difference is whether exact buyer-verified whole programs persist.
No fragment macros, neural features or model-generated reward judge participate.

## One complete loop

1. A request supplies four visible input/output examples for a format.
2. The solver returns an executable, bounded typed AST.
3. A separate buyer interpreter checks eight further observed rows.
4. A successful result admits the exact program hash to the retained library.
5. Twelve additional rows score correctness without entering search or learning.
6. Future files use fresh source rows. The retained branch tries its verified
   programs before invoking the same factored solver; the stateless branch
   solves from scratch.

All branches consume genuine buyer receipts. The stateless branch records audit
history but retains no reusable programs. The frozen control learns the first
24 acquisition files and then refuses to retain new programs, while continuing
to execute and record every job.

## Workload and source splits

A stream contains 136 files: 24 acquisition files, 16 first files with unseen
expression structures, and **six future files for each of those 16 formats**.
The six-repeat horizon was fixed before development, rather than selected after
finding a favorable break-even point.

Inputs use real Bike Sharing numeric observations. Field encodings and tasks are
generated. Count targets check `cnt = casual + registered` against the actual
recorded column; other arithmetic targets are explicitly derived from observed
columns. This is a recurring-import workload, not real customer traffic or
market prediction.

Acquisition uses 2011 rows; unseen and future files use 2012 rows. Development
seeds 9101–9103 and reserved seeds 9201–9212 use disjoint source-row buckets.
Within one format, all seven files use distinct source rows. Rows can repeat
across **different formats** in a seed, so rows are not independent statistical
samples. Comparisons and intervals cluster complete paired seed streams.

Every branch receives the same four visible examples per file. Recipe, source
field, format index, source-row IDs and hidden rows remain buyer metadata. The
comparative cache does not receive an extra oracle specifying which skill to
choose. Branch execution order is counterbalanced across seed triples.

## Costs and acceptance

All methods use the same primitive registry, fifteen-node/depth-eight AST bound
and 100,000 total search units per file, shared across at most two CEGIS rounds.
Actual primitive bodies, unsuccessful candidates, semantic signatures and inverse
constraint arithmetic are charged. Buyer validation and final scored execution
are included in total work.

Measured per-file CPU includes search, buyer execution, library admission,
checkpoint preparation and state hashing. Library initialization is also charged.
Evidence serialization is outside the per-file timer. CPU measurements are
process measurements on one machine; the verifier cannot independently attest
physical CPU consumption.

The practical gate requires exact correctness noninferiority in every seed and
phase, at least a 10% reduction in mean charged work per correct file, and a
paired seed-level 95% upper confidence bound below 0.90 for retained/stateless
CPU per correct file. All acquisition files and their costs are included. This
is an amortized efficiency claim, not a claim that an isolated first request is
free or that competence increased beyond the solver's existing ability.

## Reserved result

The twelve reserved seed streams contain 1,632 unique files and 13,895 distinct
source rows, executed by each of the three methods. Every method achieved 100%
exact success in acquisition, first unseen compositions and every serving round.
The simpler solver already has the required competence; retention avoids
repeated synthesis.

| Method                           | Exact file success | Mean total work / stream | Mean CPU / stream | CPU / correct file |
| -------------------------------- | -----------------: | -----------------------: | ----------------: | -----------------: |
| Same factored solver, stateless  |               100% |                  323,453 |          2.3964 s |           17.62 ms |
| Verified whole-program retention |               100% |                  146,035 |          1.1529 s |            8.48 ms |
| Frozen before unseen formats     |               100% |                  367,234 |          2.8595 s |           21.03 ms |

The paired retained/stateless CPU-per-correct ratio is **0.4811**, with seed-level
95% interval **0.4781–0.4841**. The counted-work ratio is **0.4515**, interval
**0.4505–0.4525**. Both registered practical gates pass while preserving exact
correctness. Intervals describe these generated streams on one measured runtime;
they do not cover arbitrary datasets, hardware or uncontrolled deployment loads.
Other project validation was reported running near the start of measurement,
so this is not an isolated-host hardware benchmark. Branch order was
counterbalanced and CPU was measured per process.

Acquisition and first unseen files are initially more expensive with retention:

| Phase                         | Stateless CPU / file | Retained CPU / file |
| ----------------------------- | -------------------: | ------------------: |
| Acquisition                   |             11.28 ms |            13.03 ms |
| First unseen composition      |             18.78 ms |            23.95 ms |
| First future file per format  |             18.78 ms |             5.97 ms |
| Second future file per format |             18.97 ms |             4.44 ms |
| Sixth future file per format  |             19.17 ms |             4.62 ms |

All 1,152 retained-branch serving files select previously buyer-verified programs.
No acquisition or first-unseen file is counted as a cache hit. In descriptive
postprocessing, cumulative CPU recovers the initial cost by file 49–51 in every
seed, during the first future-file round. That break-even description is not a
new preregistered gate. Six future files per format were fixed before development.

- [Reserved measurements and all paired trials](retention-reserved/result.json)
- [Independent reserved verification](retention-reserved/verification.json)
- [Phase costs, cache selections and break-even evidence](retention-reserved/phases.json)
- [Public preregistration receipt](retention-preregistration.json)

A separate [reserved-checkpoint label-free fixture](retention-reserved/label-free-serving.json)
executes a previously verified program on eight new rows using only its skill
hash and input strings. All outputs check exactly at 180 charged units. Target
examples and synthesis are absent, and verified-success counts do not silently
increase. This establishes reusable artifact execution, not autonomous choice of
the skill or guaranteed correctness on arbitrary future inputs. Its costs are
separate from the comparative run.

## Development evidence

The initial run was invalidated by a concurrent runtime safety correction. Its
[traces and explicit failure status](retention-development-01/execution-status.json)
remain intact; it issued no valid result.

The final formatted-source development run contains 1,224 actual job executions,
408 unique files and 3,445 distinct source rows across three seeds. Every branch
produced exactly correct outputs for every file in every phase:

| Method                           | Exact file success | Mean counted work | Mean CPU | CPU per correct file |
| -------------------------------- | -----------------: | ----------------: | -------: | -------------------: |
| Same factored solver, stateless  |               100% |           319,736 | 2.3670 s |            0.01740 s |
| Verified whole-program retention |               100% |           144,843 | 1.1336 s |            0.00834 s |
| Frozen before unseen formats     |               100% |           363,236 | 2.8163 s |            0.02071 s |

The retained/stateless CPU-per-correct ratio is 0.4790, with development paired
95% interval 0.4640–0.4940. The counted-work ratio is 0.4530, interval
0.4494–0.4566. These development observations preceded the precommitted reserved test. They
are not a claim about arbitrary workloads or hardware.
The 100% competence tie is part of the result: retention saves rediscovery work.

- [Final development measurements](retention-development-03/result.json)
- [Independent final development rescore](retention-development-03/verification.json)
- [Earlier valid development run](retention-development-02/result.json)

A separate [label-free serving check](retention-development-02/label-free-serving.json)
loads a retained checkpoint and executes a named verified program on eight new
rows using only the skill hash and input strings. All outputs check exactly.
This proves that the artifact can run without target examples or synthesis. It
does not prove autonomous selection of that artifact, nor automatic correctness
on arbitrary new data, and is outside the comparative cost measurements.

## Independent checks

The verifier imports neither the learner nor evaluator. It reconstructs source
values, generated fields, complete AST outputs, receipt origins, CEGIS visibility,
row/split coverage, retained-control behavior, all resource arithmetic and the
registered paired gates. It does not replay full synthesis or independently
witness physical execution.

Mutation tests reject changed outputs despite recomputed trace hashes, missing
files, omitted search work, future score labels in visible examples, foreign
receipts, secret retention by the frozen control and altered gate verdicts.
Exact evaluated source snapshots are retained with each valid run. A source
identity change during execution prevents issuance of a result.

```sh
python -m scripts.evaluate_skill_retention --phase development --out .test-state/retention-development-new
python -m scripts.verify_skill_retention .test-state/retention-development-new
python -m unittest tests.test_skill_retention_results -v
```

A reserved reproduction requires the exact published protocol file hash.
Existing output directories cannot be reused. Failed or interrupted attempts
remain recorded and cannot be silently replaced. The finite operational corpus is
[retention-tasks.json](retention-tasks.json): 136 development jobs plus the exact
frozen expression configuration, with `max_macros=0`. Buyer-only recipe and row
metadata must be removed before solver dispatch.

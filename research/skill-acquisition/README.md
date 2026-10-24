# Executable skill acquisition

This experiment asks whether retaining verified transformation programs improves
future work compared with a strong fresh search using the same primitives and
example information. Programs execute actual bounded string and decimal
operations. There are no neural features in this comparison.

The initial development result does **not** establish a useful advantage. Every
registered method solved every acquisition and transfer task. Contextual reuse
reduced counted transformation/search work by 2.5% against fresh search, while its
measured Python CPU time increased. Neither registered benefit gate passed.
Reserved seeds have not been executed.

A subsequent [multi-field expression experiment](expressions.md) retains a stronger
negative result: an ordinary constraint-guided solver beats the learned-fragment
approach despite its initial advantage over generic enumeration.

A [subsequent retention study](retention.md) holds the stronger solver fixed and
measures whether keeping verified programs reduces repeated work. Its preregistered
reserved test confirms lower repeated-task CPU with a 100% correctness tie; all
earlier failed comparisons remain part of the record.

## Task and data

Targets are numeric observations from the retained UCI Bike Sharing hourly data.
Prefixes, suffixes, JSON quoting, delimited fields, whitespace, unit scaling and
all task compositions are generated transformations, not original dataset
labels. The claim is limited to recovering these values under those operations.

Each task contains four visible input/output examples, eight buyer validation
rows and twelve independently scored rows. The solver never sees generator
labels, the source field, row identities, validation inputs or scored inputs
before returning its program. A failed acquisition validation may reveal one
counterexample, followed by at most one additional search. Both searches share
a total allowance of 2,400 search work units. Transfer does not reveal
counterexamples or update the skill library.

Acquisition uses 2011 observations; transfer uses 2012 observations and generated
wrapper structures absent from acquisition. The first 1,728 rows of each year
form three disjoint development buckets. Remaining rows form twelve disjoint
reserved buckets. No row repeats between tasks or registered seed buckets.
Visible examples have distinct targets to avoid accidental constant-output fits.
These are partitions of one dataset, not independent real deployment sites.

## Comparison

All methods receive the same example information and fixed typed operation
registry. Fresh search performs semantic deduplication and needs no library.
Whole-program retrieval, frequency-ranked fragments and contextual retrieval
share the same learned program representation. Macros expand into actual
primitive operations, which are fully charged. They do not execute for free.

The retained branches are:

- Fresh typed search with a new empty library per task.
- Verified whole-program retrieval with the same search fallback.
- Verified program and fragment reuse ranked by usage frequency.
- The same library with ranking by observable example features.
- Contextual acquisition frozen after half of the acquisition stream.
- Verified outcomes withheld from retention.
- Shuffled observable context as a diagnostic, with genuine output checks.

A program is retained only after exact buyer-side validation. Successful
acquisition does not itself prove usefulness on new inputs; frozen transfer
provides that separate test. All methods may infer from the four visible
examples at transfer time, including fresh search.

## Measured work and failures

Every search primitive attempt, candidate and signature byte block is charged.
A primitive attempt adds one unit plus one block per started 32 UTF-8 bytes of
its input, with at least one block. Semantic-signature processing is also
charged. Errors and semantic duplicates remain visible. Buyer validation and
final scored execution are charged separately and included in total work.
Library administration, hashing and Python overhead are captured by measured
CPU and wall time, rather than being represented as free computation.

These units describe bounded DSL operations. They are not energy, dollars or
hardware instructions. The 2,400 limit applies to search across CEGIS attempts;
reported total work also includes verification and scored execution. Failed
attempts and every seed remain in the trace. Nothing is a paid purchase or
external hiring result.

## Development result

[development-01/result.json](development-01/result.json) retains three development
seeds, seven methods and forty tasks per method: 840 actual task records. Each
method achieved 100% exact task success. Mean total counted work was:

| Method                  | Counted work | Python CPU seconds |
| ----------------------- | -----------: | -----------------: |
| Fresh search            |       10,679 |             0.0347 |
| Whole-program retrieval |     11,128.7 |             0.0395 |
| Frequency-ranked reuse  |       11,207 |             0.0415 |
| Contextual reuse        |       10,407 |             0.0451 |
| Frozen halfway          |       10,407 |             0.0458 |
| No retention            |       10,679 |             0.0356 |
| Shuffled context        |       11,014 |             0.0462 |

CPU values are short local measurements and are not a reliable hardware speed
benchmark. They still prevent interpreting lower abstract work as a demonstrated
wall-time efficiency gain. Early and late success are identical. The simple
fresh solver already handles these compositions, so these tasks do not provide
convincing evidence of improved competence through acquisition.

[The independent rescore](development-01/verification.json) reconstructs the
source observations, generated inputs, program outputs, selected receipts,
resource arithmetic, coverage and registered paired gates. It does not replay
the full search or independently witness CPU consumption. Tamper tests reject
changed outputs, missing tasks, uncharged work, illicit retention and an invented
positive gate even if trace hashes are recomputed.

## Preregistration and reproduction

The current study is in development. Before reserved execution, freeze a new
protocol file and publish its hash with the evaluated source identities. Do not
tune from reserved results; a later change requires new reserved tasks.

```sh
python -m scripts.evaluate_skills --phase development --out .test-state/skills-development-new
python -m scripts.verify_skill_results .test-state/skills-development-new
python -m unittest tests.test_skills tests.test_skill_results -v
```

The finite development queue is retained in [tasks.json](tasks.json). Its
`structure`, `field` and `row_ids` are evaluator metadata and must not enter a
solver request. The original two-development-bucket manifest remains in
[tasks-development-v0.json](tasks-development-v0.json) for provenance; its tasks
are not the final three-bucket development protocol.

Fanaee-T, H. (2013). _Bike Sharing_ [Dataset]. UCI Machine Learning Repository.
[DOI:10.24432/C5W894](https://doi.org/10.24432/C5W894).
[CC BY4.0](https://creativecommons.org/licenses/by/4.0/).
The original retained archive is unchanged.

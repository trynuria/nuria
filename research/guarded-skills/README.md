# Versioned composition and applicability

The earlier external qualification did not beat its simple control. Its complete
negative result remains in [active skills](../active-skills/README.md). This
version addresses two specific engineering limits: useful nested operations
were executable but absent from search, and two examples could leave retained
rules ambiguous. Searching more operations without scheduling them carefully
initially made performance worse; that failed pilot is retained.

The new learner searches a finite subset of the existing safe text language,
including case/whitespace or slicing around extracted fields and tokens, followed
by bounded concatenation. It caches intermediate results and can merge equivalent
training-output vectors. The conventional enumerative control uses the same
ordered operation family and cache, with equivalence pruning disabled. Neither
method sees scoring rows or task recipes during selection.

## Development decision

Four development streams contain 192 authored record-repair tasks. They include
deliberate ambiguity in early examples. They are engineering stress tests, not
customer work or an independent external benchmark.

| Method                             | Exact jobs / 192 | Wrong proposals | Labels | Counted work |
| ---------------------------------- | ---------------: | --------------: | -----: | -----------: |
| Original search, two examples      |               53 |              40 |    384 |    5,985,830 |
| Original search, four examples     |               43 |               0 |    768 |    8,265,478 |
| Composed search, three examples    |              143 |               8 |    576 |    3,590,648 |
| Composed search, four examples     |              151 |               0 |    768 |    3,987,825 |
| Enumerative search, three examples |              119 |               3 |    576 |    4,650,122 |
| Enumerative search, four examples  |              115 |               0 |    768 |    4,913,994 |
| Guarded selection and correction   |              152 |               2 |    617 |    5,673,951 |
| Guarded without retained admission |              151 |               0 |    615 |    5,477,597 |

The four-example composed policy was selected before qualification. Guarded
selection remains experimental: its extra machinery did not establish a better
correctness/resource tradeoff. These results do not establish a neural benefit.
The [development archive](development/index.json) preserves failed pilots, the
earlier guarded regression, source snapshots and verified summaries.

## Prospective qualification

The [protocol](protocol.json) fixes twelve fresh generated streams, all nine
methods, the candidate, primary conventional control, budgets and statistical
analysis. The runner verifies this exact protocol at a public commit before
generating any reserved corpus. Every failed job remains in the denominator.
Whole-stream paired statistics account for dependence within retained histories.

An independent checker executes the returned data-only programs and checks all
outcomes, receipts, library transitions and resource arithmetic without importing
the learner or evaluator. CPU and search instrumentation remain locally recorded
evidence; they are not independently witnessed physical execution.

## Frozen result

Commit [`ce29327`](https://github.com/trynuria/nuria/commit/ce293276957d41b59e00b85a36bf97da694e4fd9)
was publicly available and checked by the runner before any reserved corpus was
generated. One run evaluated all nine methods on twelve fresh 48-job streams.
All fifteen frozen source identities remained unchanged. The independent checker
verified all **5,184 method-job records**, including every failed search,
incorrect proposal, abstention and terminal provisional-artifact disposition.

| Method                         | Exact / 576 | Wrong |    Labels |   Counted work | Full process CPU |
| ------------------------------ | ----------: | ----: | --------: | -------------: | ---------------: |
| Original, two examples         |         136 |   100 |     1,152 |     19,399,390 |          7.587 s |
| Original, four examples        |         129 |     0 |     2,304 |     24,724,219 |          7.909 s |
| Composed, two examples         |         285 |   225 |     1,152 |      7,493,136 |          4.348 s |
| Composed, three examples       |         423 |    16 |     1,728 |     11,482,079 |          5.337 s |
| **Composed, four examples**    |     **438** | **1** | **2,304** | **12,673,045** |      **5.188 s** |
| Enumerative, three examples    |         352 |     3 |     1,728 |     14,258,611 |          6.077 s |
| **Enumerative, four examples** |     **337** | **1** | **2,304** | **14,934,915** |      **5.726 s** |
| Guarded                        |         455 |     5 |     1,848 |     17,238,110 |          9.253 s |
| Guarded without admission      |         454 |     5 |     1,829 |     16,724,386 |          7.941 s |

The selected learner solved **76.0% versus 58.5%** for its preregistered
same-language conventional control. It gained 101 exact jobs and lost none of
the control's exact jobs. Both made one incorrect proposal. The accuracy delta
was 17.5 percentage points; the whole-seed paired-bootstrap 95% interval was
15.1–20.0 points. The directional seed-sign permutation result was 1/4,096 under
its stated exchangeability assumption. This is the primary registered comparison;
other comparisons are exploratory and unadjusted.

The chosen method used the same four labels and 50,000-unit synthesis cap per
job, with 15.1% less counted work and 9.4% less measured full-pipeline CPU overall.
These are distinct correctness and resource observations. The separately
registered **matched-correctness efficiency gate did not apply successfully**:
exact count and proposal coverage differed, so its required parity was absent.
The competence gate passed. There is no weighted score hiding that distinction.

The guarded policy solved seventeen more jobs than the chosen policy, with four
more incorrect proposals and higher work/CPU but fewer labels. The selected policy
does not dominate every alternative on every axis. Guarded remains experimental;
the frozen guarded control is also a different policy and cannot establish the
causal benefit of retention for the chosen fixed-four learner.

Full [summary](reserved/summary.json), [paired analysis](reserved/comparison.json),
[independent checks](reserved/verification.json), generated corpora and complete
traces are published. The two large generated artifacts use lossless gzip; no
external benchmark specification or production history is included.

```sh
python -m scripts.verify_guarded_skill_results research/guarded-skills/reserved
python -m scripts.summarize_guarded_skill_comparison \
  --summary research/guarded-skills/reserved/summary.json \
  --verification research/guarded-skills/reserved/verification.json \
  --protocol research/guarded-skills/protocol.json \
  --output comparison-reproduced.json
```

The verifier executes programs independently. It does not replay synthesis or
physically witness CPU/search instrumentation. A public commit and local access
timestamps are provenance commitments, not an external experiment witness.

A separate [full search replication](reserved/search-replication.json) re-executed
all **5,184** original method-job records. Every deterministic field matched:
search actions and work, diagnostic choices, returned programs, buyer checks,
receipts, abandoned-artifact dispositions and complete library histories. Only
the two measured CPU fields were excluded. The fifteen frozen source hashes
matched before and after replay, and the original public protocol was checked.
This is reproduction of the published run, not another qualification or new
random-seed experiment.

```sh
python -m scripts.replay_guarded_skill_search research/guarded-skills/reserved \
  --protocol research/guarded-skills/protocol.json \
  --output search-replication
```

## Previously seen external tasks

A separate [reanalysis](external-reanalysis/summary.json) uses the exact original
seven-program checkpoint, two diagnostic examples and 167 previously observed
Microsoft PROSE tasks. All fourteen unsupported files remain in the denominator.
This is seen-set reanalysis, **not fresh reserved external qualification**; no
learner change was made from these outcomes.

| Search cap | Original exact / 167 | Composed exact / 167 | New wins | Regressions |
| ---------- | -------------------: | -------------------: | -------: | ----------: |
| 10,000     |                   59 |                   72 |       24 |          11 |
| 50,000     |                   73 |                   78 |        9 |           4 |

At 10,000 units, full counted work rose 6.2% and CPU 18.9%; at 50,000 they rose
59.7% and 60.8%. Wrong proposals at the larger cap rose from 38 to 41. The coverage
gain is useful but has costs and regressions. All 668 records and all 421 proposed
programs' disclosed-example agreement were checked. The original failed external
qualification remains untouched.

This demonstrates a bounded program-synthesis improvement, not a neural
advantage, general intelligence or consciousness. Fresh generated tasks do not
establish performance on independent external workloads. The observatory design
is unchanged.

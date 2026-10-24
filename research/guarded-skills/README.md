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

The qualification result will be added after the frozen run. Generated-task
improvement cannot establish external generalization, a consciousness result or
superiority of Nuria's neural architecture. The observatory design is unchanged.

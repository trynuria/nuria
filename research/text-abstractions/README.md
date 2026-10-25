# Reusable text programs: development record

This study adds source-bound, parameterized text subprograms to a bounded
synthesis engine. The strongest original-row result came from conventional
relatedness ordering, not the spiking ranker. A broader-input challenge then
rejected every proposed program. **Neither neural superiority nor semantic
generalization passed.**

The implementation is an optional research component. These policies and
libraries are not enrolled in the running cognitive worker.

## What was implemented

The [extractor](../../cognition/text_abstractions.py) finds repeated native AST
structure in earlier accepted programs. Explicit subtree cuts and paired
anti-unification retain one to three expression arguments, with parent-program,
origin and expansion evidence. Every application expands into the existing
`safe-text-v1` language and pays the native interpreter's declared costs.

The [planner](../../cognition/abstraction_search.py) interleaves native operations
with library applications. Its later scheduler accounts for work spent on each
frontier state and bounds individual concatenation searches. The
[whole-program comparator](../../cognition/template_reuse.py) rebinds fields in
complete earlier programs and uses the established synthesis fallback.

The preparation scanned 1,892 historical origins and found 390 distinct accepted
programs with 30 whole-program skeletons. The abstraction miner selected a bounded
128-parent history. After a recorded static balancing pass, its shared library
contained **63 abstractions: 16 unary, 25 binary and 22 ternary**. The conventional
template comparator received all 390 programs. That history-capacity difference
is disclosed rather than treated as identical data access.

Structural support authenticates neither a buyer nor a general semantic rule.
Earlier finite acceptance can contain shortcuts, as this experiment illustrates.
The [implementation specification](../../docs/text-abstractions.md) describes the
limits and accounting boundaries.

## The 64-task comparison

The authored course has eight structural clusters with eight instances each.
Every job supplies four diagnostic examples; a separate buyer checks one committed
proposal on 24 original rows. Search has a 50,000-unit ceiling per job. The
eight pilot instances had already informed implementation work, so this is a
development comparison, not an unseen qualification or 64 independent structural
observations.

| Method                                       | Accepted on original rows / 64 | Exact on all 32 broader rows / 64 |
| -------------------------------------------- | -----------------------------: | --------------------------------: |
| Native operations + conventional relatedness |                             24 |                                 0 |
| Nearest-neighbor guidance                    |                             23 |                                 0 |
| Linear guidance                              |                             18 |                                 0 |
| Whole-program field templates                |                             16 |                                 0 |
| Spiking guidance                             |                             16 |                                 0 |
| Frozen neural inputs                         |                             16 |                                 0 |
| Matched continuous guidance                  |                             16 |                                 0 |
| Native operations + support frequency        |                             16 |                                 0 |
| Native operations + uniform ordering         |                             16 |                                 0 |
| Native-only relatedness                      |                              0 |                                 0 |
| Native-only spiking guidance                 |                              0 |                                 0 |

All 161 submitted proposals passed their original 24 buyer rows. All 161 failed
at least one broader-input row. The other 543 method/job records were
abstentions or search failures; they are not mislabeled as submitted incorrect
programs. The additional original-row successes of conventional relatedness
over complete templates all came from one structural cluster.

The broader challenge fixes 32 valid inputs per task, varying word and segment
counts, case, digits and whitespace. Its targets come from the ordinary-Python
reference rules. It is a post-hoc distribution diagnostic, not a new training
course. Original scores remain unchanged, and challenge answers were not supplied
to these controllers.

For example, uppercasing the first two words and uppercasing an entire delimiter
segment agree when every original segment has two words. A three-word segment
distinguishes the rules. The original authored distribution failed to distinguish
these hypotheses.

## Retained revisions and failed attempts

| Attempt                                  | Scope                   | Result on original rows                                                             |
| ---------------------------------------- | ----------------------- | ----------------------------------------------------------------------------------- |
| First macro planner                      | Eight known pilot jobs  | Templates 2/8; all three macro orderings 0/8                                        |
| Interleaved macro expansion              | Same eight jobs         | All three macro orderings 2/8                                                       |
| Native operations with macros            | Same eight jobs         | Relatedness 3/8; frequency and uniform 2/8; native-only 0/8                         |
| First frozen learned rankers             | Full 64-instance course | Spiking 16/64; relatedness 24/64                                                    |
| Historical subprogram and context replay | Same eight pilot jobs   | Context-spiking 0/8; frozen-input control and small-first imagined templates 3/8    |
| Work-aware frontier scheduling           | Same eight pilot jobs   | Each spiking variant 2/8; relatedness, frozen-input control and linear guidance 3/8 |

The first planner spent its allowance expanding one state. Interleaving repaired
that engineering problem without establishing a new transferable capability.
One accepted multi-abstraction expression in the second attempt was semantically
redundant. A recorded macro ancestry is not proof that its abstractions were
causally necessary.

The original teaching trace contained 3,812 records and 215 positive labels, of
which 183 were raw-input fields. Token, split and slice operations had no positive
records in that collected search coverage. A later pass explicitly executed
historical nonleaf subprograms and nine controlled contexts, producing 21,386
records with 4,163 positives. It also supplied 1,142 self-generated program
candidates to additional conventional template controls. These are mathematical
replay tasks, not new customer work or external outcome receipts. The revised
teaching did not produce a neural decision advantage.

The work-aware scheduler normalizes guidance confidence, retains a fixed
exploration floor and applies fatigue based on actual metered state work. An
assembly slice returns control after a bounded allowance. The latest pilot's
recorded assembly work never reached that slice, so this experiment does not
attribute its results to slice yielding. Unit tests cover that path separately.
Legacy scheduling reproduced the earlier 32 native-planner cases with identical
programs and work counts.

## Qualification after the shortcut diagnosis

The separate [qualification module](../../cognition/skill_qualification.py)
binds a committed proposal to distinct, domain-labelled cases and explicit
coverage requirements. A receipt names the program, source, domain, case plan,
completed checks, failed outputs and work limit. Every case must pass; exhausted
work or a single failed case prevents a qualified-for-cases result. It does not
automatically admit a skill, authenticate a buyer or prove a universal rule.

An additional rejection diagnostic applied the already revealed broader cases
to **183 learned proposals**: the 161 full-course proposals and 22 later
work-fair pilot proposals. It rejected all 183. The 64 reference task programs
passed as positive controls; these oracle-supplied programs are not counted as
learned successes. The diagnostic used 479,785 declared work units.

This shows the gate can retain and report the known failures without declaring
every program unsuccessful. It is a post-hoc engineering check, not an untouched
qualification of the learners. Coverage labels and expected answers still come
from the caller; their relevance and authenticity require an external contract.
No program from this diagnostic was promoted into live execution.

## Evidence and offline replay

The [manifest](manifest.json) lists every archived payload and public byte hash.
JSON and JSONL artifacts retain their original bytes; large files use deterministic
gzip compression. Frozen implementations for each attempt are retained under
`sources/`. Historical experiment scripts under `archived-scripts/` have local
filesystem paths normalized, with both original and published hashes recorded.
They are forensic source records rather than a portable full-study rerun command.

The public verifier checks archive identities, independently executes all six
recorded result sets, repeats the full 64-task broader-input challenge and checks
the 247 qualification plans:

```sh
python -m scripts.verify_text_abstraction_results
```

The verification executes data-only programs through the separate interpreter in
`scripts/verify_active_skill_results.py`. It does not train rankers, rerun search,
witness original in-memory model invocation or independently measure the original
CPU timings. Recorded source/model associations and trace commitments have their
stated boundaries; the core planner commitment does not itself bind the scorer's
model hash or scale. Finite checks establish only the inputs actually replayed.
Qualification replay reconstructs each plan and receipt, checks 7,904 case
executions and reconciles scalar work. Individual primitive-counter categories
are retained but not independently reconstructed by that check.

The archive includes all 944 original method/job records across six comparisons,
the 704-record full matrix, fixed challenge inputs and counterexamples, retained
model checkpoints, training records, preparation evidence and work accounting.
The final reconciliation records **51,611,476 known study work units**, including
the independent replays and the rejection diagnostic. It retains a 2,000,000-unit
observer reserve within the 75,000,000-unit study ceiling. Earlier freezes retain
their original amounts; the reconciliation lists later executions separately.
One initial verifier invocation lacks a distinct saved receipt, which the ledger
explicitly identifies. Virtual work, fitting CPU, serialized bytes and hardware
instructions are different quantities. No work counter is presented as a complete
hardware-cost measurement.

A post-study review repaired a small-limit bug in the template comparator: its
fallback now receives the remaining candidate allowance instead of a fresh one,
and it is not called when that allowance reaches zero. This is the current v2
implementation. The archived experiments used the retained v1 implementation;
their results have not been rewritten or attributed to v2. A counter-only audit
of all 88 recorded template jobs found at most 1,197 candidates per job, below
the original 30,000-candidate ceiling. That review does not rerun the controller
or establish v2's performance.

## What this changes

The software can extract, validate and combine previously learned text-expression
structure rather than only rebinding complete programs. The recorded development
gain is limited to fitting the original examples under the declared search cap.
This archive also demonstrates why those checks were insufficient for reusable
domain competence. The failed comparisons remain part of the evidence, not a
neural-capability claim.

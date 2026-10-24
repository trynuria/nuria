# Composing verified arithmetic skills

This second development experiment increases task ambiguity: a program must
normalize independent fields and combine them arithmetically. It executes real
programs over observed Bike Sharing records. It has not demonstrated an
advantage over the strongest simple solver.

The first comparison appeared promising: learned fragments solved every unseen
composition while generic fresh search solved none within the generous shared
budget. An adversarial follow-up added a stronger, ordinary constraint-guided
solver. It solved every task with much less work and CPU time. That comparison
invalidates a claim that the retained-fragment learner is the best solution to
these tasks. Both development runs remain available.

## Task contract

Each input is a JSON array of four strings derived from the observed `casual`,
`registered`, `temp` and `hum` columns. Unit changes, affixes and all expression
tasks are generated. Six transfer recipes recover the actual recorded identity
`cnt = casual + registered`; other sums, differences and products are explicitly
source-derived arithmetic targets, not additional recorded measurements.

Twenty-four acquisition tasks teach individual field normalizations and four
raw arithmetic operations. Sixteen transfer tasks combine separately normalized
fields in expression structures absent from acquisition. Four visible examples
specify each task; eight separate rows validate the returned program; twelve
further rows score exact correctness. A task succeeds only if every scored output
is correct. No source field names, recipe, intended AST or withheld row reaches
the solver.

Acquisition uses 2011 records and transfer uses 2012 records. Three development
buckets and twelve untouched reserved buckets have disjoint source rows. The
first 1,728 rows per year are divided among development seeds 9101–9103; the
remainder is divided among reserved seeds 9201–9212. Distinct target values in the
four visible examples avoid accidental constant-output tasks. These are
partitions of one corpus, not independent deployment sites.

## Executable language

The shared typed AST has field leaves, bounded text normalization, decimal
conversion, fixed rational scales, addition, subtraction, multiplication and
canonical formatting. Programs have at most fifteen nodes and depth eight.
There is no file, process, networking or arbitrary-code primitive. The evaluator
constructs reference ASTs only to establish task feasibility; they are never
provided to search or its learned library.

Programs and reusable fragments are retained after genuine exact buyer checks.
The learner can reuse a typed normalization fragment under another field or a
new binary expression. That is conventional verified program-library learning,
not a newly established neural mechanism. All primitive bodies still execute and
are charged when a fragment is used.

## Strong baseline

The generic fresh solver uses typed bottom-up search with semantic
deduplication. It is a useful baseline, but it is not the strongest solver for
this restricted domain.

The added `factored_fresh` branch first enumerates legal unary numeric
normalizations for each field. It then uses the visible example targets to join
candidate operands for one binary operation: for example, the required right
operand of addition is `target − left`. It verifies the resulting complete AST
by actually executing it. It receives no task recipe, target operator, hidden
rows or retained skills. Normalization attempts, signatures, inverse-constraint
arithmetic and final candidate execution are all charged. Its restricted
hypothesis family is disclosed; every current task falls within that family.

This demonstrates why an apparent large gain over a general enumerator cannot
be treated as a broad algorithmic advance without stronger comparisons.

## Development results

Both runs used the fixed allowance of 100,000 search units per task, shared
across at most two CEGIS rounds. This allowance was chosen before the first
scored expression run; it was not lowered to force a learning advantage.
Validation, scored execution and acquisition work are included in total work.
CPU measurements include search, buyer execution, learning and checkpoint
preparation; evidence serialization is outside the per-task timer.

[Development 01](expression-development-01/result.json) contains 840 task records
across three seeds and seven branches. Fragment reuse achieved 100% transfer
against 0% for the original generic fresh comparison. No contextual advantage
appeared; frequency ranking was equally accurate and cheaper in measured CPU.

[Development 02](expression-development-02/result.json) adds the strong stateless
solver and retains 960 actual task records:

| Method                             | Unseen-composition success | Mean total work | Mean CPU seconds | Total CPU / correct transfer task |
| ---------------------------------- | -------------------------: | --------------: | ---------------: | --------------------------------: |
| Generic fresh search               |                         0% |       1,665,738 |           1.8794 |          No correct transfer task |
| Factored fresh solver              |                   **100%** |      **72,495** |       **0.3197** |                      **0.0200 s** |
| Whole-program retrieval            |                         0% |       1,667,796 |           1.8881 |          No correct transfer task |
| Frequency-ranked fragments         |                       100% |         624,936 |           0.6907 |                          0.0432 s |
| Contextual fragments               |                       100% |         622,547 |           0.7081 |                          0.0443 s |
| Frozen halfway through acquisition |                     31.25% |       1,422,033 |           1.4894 |                          0.2979 s |
| No retention                       |                         0% |       1,665,738 |           1.8974 |          No correct transfer task |
| Shuffled context                   |                       100% |         623,754 |           0.7159 |                          0.0447 s |

Every method solved all acquisition tasks. The retained-fragment learner uses
8.62 times the counted work and 2.16 times the measured CPU of the strong
stateless solver. Its practical-benefit and CPU-nonregression gates fail.
Retaining fragments helps relative to generic amnesic search, but does not beat
the better ordinary algorithm. These short local CPU measurements are not a
hardware benchmark or a claim about arbitrary workloads.

## Verification and limits

Independent retained-evidence rescoring checks source observations, generated
inputs, complete AST execution, receipt origin, output commitments, every seed
and task, resource arithmetic and the declared paired gates:

- [Development 01 verification](expression-development-01/verification.json)
- [Development 02 verification](expression-development-02/verification.json)

The verifier imports neither the learner nor evaluator. It does not replay all
search enumeration or independently witness physical CPU consumption. Exact
source snapshots and hashes are retained with each run. Mutation tests reject
changed outputs, missing tasks, omitted work, illicit retention and invented
positive gates even when trace commitments are recomputed.

No reserved expression tasks have been executed, and no algorithm was promoted
on the basis of the weaker comparison. The useful implementation is a bounded
pipeline that proposes executable artifacts, checks them and retains their
outcomes. Demonstrating an operational benefit from persistence requires a
comparison against the same strong solver with and without verified skill
retention on future jobs. No neural advantage, consciousness, biological fidelity,
external hiring or general intelligence claim follows from these results.

```sh
python -m scripts.evaluate_skill_expressions --phase development --out .test-state/expressions-development-new
python -m scripts.verify_skill_expression_results .test-state/expressions-development-new
python -m unittest tests.test_skill_expressions tests.test_skill_expression_results -v
```

The finite development corpus is in
[expression-tasks.json](expression-tasks.json). Recipe and source-row metadata
are buyer-only and must not enter solver requests.

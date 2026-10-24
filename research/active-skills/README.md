# Input-only skill requests and selective evidence

The system can receive inputs without a selected skill hash, obtain explicit
diagnostic labels, reuse an executable program or synthesize a missing one, and
retain it only after buyer checks. This is a useful interface extension built
from conventional programming by example. It is not a new neural algorithm or
evidence of consciousness.

Development selected the simpler **two-label audited-retention policy**. A full
retained-program portfolio with disagreement-based questions used more resources
than a strong direct solver. One audit label also permitted incorrect reuse.
Those failures remain part of the evidence.

## What failed and what changed

On the existing 136-file numeric development corpus, the one-audit-label
experimental policy succeeded on 132 files, while the same solver given two
fixed examples succeeded on all 136. In four cases, an old single-field program
matched the first answer by coincidence while the new task required arithmetic
over two fields. Self-consistency within a retained portfolio was insufficient
evidence of the intended meaning.

Requiring two audit labels repaired those four mistakes on this corpus. It still
used 352 labels, 725,141 counted units and 6.335 seconds of process CPU, compared
with the direct two-example baseline's 272 labels, 523,092 units and 1.869 seconds.
The simpler baseline won; the experimental routing policy was not promoted as an
efficiency improvement.

The selected session requests two distinct labels and invokes the same strong
solver, which lazily checks retained programs before searching. A final parity
check produced exact outputs on all 136 files for both direct and session paths,
using 272 labels each. The session consumed 761,897 units and 2.952 seconds of CPU,
versus 523,092 units and 1.874 seconds directly. This is measured controller and
state-management overhead, not a speedup. The study excludes the production
ledger and neural-worker overhead.

Numeric observations and generated tasks were already used by earlier studies.
This is development and reanalysis, not a fresh reserved observation result.

## External task qualification

Microsoft's independently authored PROSE suite is pinned to revision
`8e3349ae8575c2615926b76170b287b6dddb5a73`. The full transformation universe has
354 tasks. A metadata and license rule selects 183, all marked synthetic. Whole
Currency, Column and Email families supply 16 development tasks; 167 other
eligible tasks are reserved. Unsupported tasks stay in the coverage denominator.
See the [inventory](source-inventory.json) and [selection notes](design-notes.md).

The final development comparison starts every method with the identical seven
programs admitted by an earlier fixed-two-example development pass:

| Method                    | Exact files / 16 | Proposed files | Labels | Counted work | Process CPU |
| ------------------------- | ---------------: | -------------: | -----: | -----------: | ----------: |
| Direct fixed-two baseline |                9 |             13 |     25 |       31,448 |    0.0215 s |
| Audited-retention session |                9 |             12 |     25 |       37,718 |    0.0306 s |
| Experimental disagreement |                9 |             12 |     25 |       37,716 |    0.0324 s |
| Random-probe control      |                9 |             12 |     25 |       37,188 |    0.0328 s |
| Frozen audited session    |                9 |             12 |     25 |       37,537 |    0.0314 s |

Two tasks have only two original examples, leaving no scoring row. A third has
source types outside the string-only language. All three count as unsupported
within the 16-task denominator. The conservative session also abstains when the
two diagnostic rows contain only one distinct input; the direct baseline makes
an incorrect proposal there.

There are 291 original scoring rows: 283 are typed and scorable, and eight belong
to the unsupported source-type task. Two scorable rows repeat diagnostic inputs.
On the 281 novel-input rows, the direct and audited paths both return 240 exact
answers. Nine exact files do not mean sixteen solved tasks, nor nine distinct
retained programs. The final library contains seven program hashes.

These development data do not establish an efficiency benefit, broad external
coverage or a new discovery. The reserved qualification is governed by the
[frozen protocol](protocol.json). An efficiency statement requires matched exact
correctness and proposal coverage plus lower counted work and measured CPU;
there is no weighted score to tune after seeing outcomes.

## Evidence boundaries

[All development summaries and exact code snapshots](development/index.json)
preserve the one-label failure, the more expensive two-label portfolio, a run
invalidated by a concurrent source change, and a stopped warm-start attempt whose
evaluation identity was correctly refused. No result was issued for interrupted
or invalid attempts.

The data-only checker separately executes ASTs and checks requested labels,
scoring outputs, admission and resource arithmetic. It does not independently
witness CPU consumption, replay all synthesis search, or attest arbitrary
production behavior. Source loading and evidence serialization are outside
per-file timings. Complete benchmark bodies and traces remain in private staging;
public artifacts contain metadata, commitments and reproducible code.

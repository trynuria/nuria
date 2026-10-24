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

These development data did not establish an efficiency benefit, broad external
coverage or a new discovery. The reserved qualification below follows the
[frozen protocol](protocol.json). An efficiency statement requires matched exact
correctness and proposal coverage plus lower counted work and measured CPU;
there is no weighted score to tune after seeing outcomes.

## Reserved result: useful execution, no comparative improvement

The implementation and protocol were published at commit
[`2b8503e`](https://github.com/trynuria/nuria/commit/2b8503e189f0a9ecca5be6b7d5f47231342df601)
before reserved specifications were accessed. Exactly one registered evaluation
then ran all five methods on all 167 reserved files. All ten evaluated source
hashes remained unchanged. Each method started with the identical seven-program
development checkpoint; no reserved final label entered program search.

| Method                    | Exact files / 167 | Proposed files | Labels | Counted work | Process CPU |
| ------------------------- | ----------------: | -------------: | -----: | -----------: | ----------: |
| Direct fixed-two baseline |                59 |             90 |    305 |    1,197,780 |    0.7125 s |
| Audited-retention session |                59 |             89 |    305 |    1,393,691 |    1.0530 s |
| Experimental disagreement |                60 |             90 |    305 |    1,358,828 |    1.1071 s |
| Random-probe control      |                60 |             90 |    305 |    1,343,703 |    1.1069 s |
| Frozen audited session    |                60 |             89 |    305 |    1,362,027 |    0.9039 s |

The audited session solved **59 of 167 files exactly (35.3%)**. It proposed
programs for 89 files: 59 passed every scorable original row, and 30 failed at
least one. The remaining 78 produced no proposal, including 14 unsupported files.
All unsupported files remain in the denominator; eligibility by metadata did not
guarantee support by this language or solver.

There are 4,042 original scoring rows: 3,993 are typed and scorable, and 49 are
unscorable. Of the scorable rows, 47 repeat diagnostic inputs. On the remaining
3,946 novel-input rows, both the direct and audited paths return 2,223 exact
answers. These row counts describe accuracy separately from exact-file success;
a program that gets some rows right does not solve that file.

The session admitted 29 additional program hashes after exact buyer checks,
ending with 36. Those programs are executable retained artifacts verified on
past examples. They are not proof of general correctness: the frozen library
control solved 60 files, one more than the adapting session. This run therefore
does **not** demonstrate a competence benefit from retaining these new programs.

A [post-hoc paired diagnosis](retention-interference.json) explains the net
one-file deficit. Retention helps on `BillingCode.000007`, but an earlier program
shadows a correct program on `Name.000013`: both match the same two audit answers,
so the first matching hash wins and fails 15 of 18 scoring rows. On
`Word.000001`, checking 35 retained programs instead of seven consumes 393 extra
units from the shared search cap; the frozen branch finds a correct program
within that cap while the adapting branch stops. These are two concrete
applicability and resource-allocation failures in this one ordered stream, not a
general theorem or a tested remedy.

The frozen control keeps its initial library fixed while retaining the same
per-job synthesis fallback; it is not a no-search baseline.

The registered efficiency gate failed. Audited retention used 16.4% more counted
work and 47.8% more process CPU than the direct baseline, with the same 305 labels
and one fewer proposal. Experimental selection did not beat the random control.
The result supports a bounded, inspectable execution and admission pipeline; it
does not support a smarter neural agent, a new learning algorithm, an efficiency
improvement or broad external task coverage.

The [qualification summary](reserved-summary.json),
[all 167 per-task results](reserved-task-results.json),
[access commitments](reserved-access.json) and
[independent verification](reserved-verification.json) preserve the complete
result. The independent checker passed all 835 method-file records. The
[inventory](source-inventory.json) intentionally remains the pre-access frozen
snapshot; the access report records subsequent retrieval. No raw specification,
label or program body is redistributed in these reports.

## Live numeric integration

The [live course](live/README.md) records actual input-only acquisition and
buyer-checked outcomes in the running numeric route. It preserves the original
course's failed reuse gate, the explicit unsupported control and a separately
matched recurrence append. This is development integration over previously used
observations, not a repeat of this external text qualification or a neural-policy
comparison.

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

# Development record

The final development run delivered six correct answers to six selected Spider
questions. All six were independently replayed against the original databases.
The preceding six-question run delivered five answers and abstained on one
protocol failure. These are development diagnostics on reused questions; they do
not establish unseen-task generalization, a learning advantage or neural value.

| Run      | Accepted | Wrong submissions | Abstentions |  Known completion tokens | Tool calls | Job time |
| -------- | -------: | ----------------: | ----------: | -----------------------: | ---------: | -------: |
| Pilot 01 |      1/1 |                 0 |           0 |                      887 |          4 |  54.31 s |
| Pilot 02 |      5/6 |                 0 |           1 | 8,614 plus unknown usage |         31 | 565.43 s |
| Pilot 03 |      6/6 |                 0 |           0 |                    8,840 |         27 | 588.34 s |

There are **six unique questions**, rather than thirteen independent cases.
Repeated runs used the same pinned pretrained model with thinking enabled.
No procedural history was retrieved and no neural strategy treatment was active
in these pilots.

## What changed

Pilot 01 was an integration check. Its workspace implementation changed after
the source freeze, so the retained correct answer is **not a frozen-source
qualification**. The [source comparison](pilot-01/source-comparison-after-pilot.json)
and [independent check](pilot-01/grounded-pilot01-independent-check.json) retain
that limitation.

Pilot 02 repeatedly requested metadata that had left its recent chat window.
The difficult question reached a truncated response and then a protocol failure.
Its known token totals exclude one response whose exact usage was lost. The
request had a 768-completion-token ceiling, which remains a conservative
liability; its additional prompt usage is unknown. Reporting 8,614 as the exact
full completion total would be incorrect.

Pilot 03 added a host-grounded working-evidence index that keeps observed schemas,
results and errors available as chat turns leave context. It also increased the
per-response completion ceiling from 768 to 1,536, while retaining the
6,000-completion-token job ceiling, and repaired accounting for valid usage on
truncated replies. This is a combined harness revision, **not an ablation that
isolates the evidence index's effect**. The final run made fewer tool calls but
used more known prompt and completion tokens and took longer than Pilot 02.
It therefore demonstrates repaired completion of this development course,
rather than a general efficiency gain.

The [independent Pilot 03 check](pilot-03/independent-check-01.json) verifies six
final answers, all 27 tool receipts, 37 reconstructed request hashes, the
45-event memory chain and matching source identities. Raw model response bodies
were not retained. Token totals are checked for internal consistency with the
stored usage receipts; model weights and inference traffic are not externally
attested.

## Files and identities

[summary.json](summary.json) gives machine-readable outcomes and accounting.
Each run contains its configuration/source freeze, retained summary and a reduced
execution trace. The [manifest](manifest.json) distinguishes:

- SHA256 identities of the original retained files.
- Portable JSON copies with the original data and reformatted whitespace.
- Reduced result projections with their own file hashes.

Trace projections retain executed actions and SQL, full tool receipts, final
answers, model request/response commitments, reported usage and host settlements.
They omit action explanations and proposed procedure annotations. They contain
no raw model reasoning or raw response bodies. An original record hash identifies
the retained original record; it is not the hash of its reduced public view.
The independent checker reconstructed prompts from the retained original
artifacts. The reduced public trace alone is insufficient to reconstruct every
prompt byte.

Local hashes establish recorded consistency. They are not independent witnesses,
onchain attestations or proof that the pretrained model had never seen these
public questions. SQLite execution agreement is finite and does not establish
universal SQL equivalence or official Spider leaderboard accuracy. No reserved
comparison results are included in this directory.

## Dataset attribution

Questions, selected database-derived rows and corresponding execution records
use [Spider 1.0](https://yale-lily.github.io/spider), Tao Yu et al. (2018),
[paper](https://arxiv.org/abs/1809.08887), distributed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
These files select and reorganize official development examples, add generated
SQL and local execution evidence, and retain original row identifiers and source
hashes. The dataset's license does not grant a license to unrelated Nuria-authored
code.

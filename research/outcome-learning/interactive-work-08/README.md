# Interactive program work

This record tests a finite adaptive execution route against the unchanged
retrieved three-call worker. Both receive the same explicit task, checked
history and input structure. Adaptive work can use five responses within 7,200
completion tokens; the control can use three responses of 2,400 tokens. Actual
usage and native executions are reported separately.

The initial development run used all 21 previously rejected Sol programs except
the already identified unstable timing case. It recovered **1/21**, using 45
requests and $0.516464 at configured uncached token rates. These are revealed
development tasks, not fresh evidence of superiority. Every original failure
remains unchanged. More tool interaction alone did not resolve these cases.

The next work contracts require two concrete artifacts: a population-activity
report from a published organoid recording and a cost/outcome audit from actual
Nuria inference receipts. Acceptance includes the complete delivered JSON,
not just a plausible answer. Programs commit before terminal grading. Accepted
candidate and control programs will both be reused on later actual data, with
separate acceptance and no further model calls.

## Completed work

| Measurement                                       | Interactive | Retrieved control |
| ------------------------------------------------- | ----------: | ----------------: |
| Independently accepted and delivered origins      |         2/2 |               2/2 |
| Model requests for those origins                  |           4 |                 6 |
| Configured uncached token cost                    |   $0.073272 |         $0.126406 |
| Later actual-data jobs accepted and delivered     |         3/3 |               3/3 |
| New model requests for later data                 |           0 |                 0 |
| Corrected native reproduction and boundary checks |         9/9 |               9/9 |

Both programs handled later intervals of the same recording and a later actual
inference-log snapshot. Identical transfer replay added no program executions or
journal events. All four origin programs and the later-data rules were publicly
committed at `da7559df1e3aeb9b4fe8be3325b29b2ed834dec9` before terminal grading.
The initial task/source freeze is `c62045b41d55c6385d3fa9fa6d609626bdbf8829`.

The first boundary replay returned **16/18**. The host's authored missing-usage
case wrongly counted only null outcome labels as unknown, although its written
contract classified every non-boolean label as unknown. Both generated programs
implemented the written rule correctly. `qualification-correction.json` retains
the exact diagnosis and changes only that expected count from one to three.
Original expectations, programs and observed failures remain unchanged. A
separate corrected replay returned **18/18**. The frozen `contracts.py` remains
the historical reference, including that documented defect.

This course establishes delivery and same-contract reuse on actual project
data. It does **not** establish better accuracy: both methods tie, and two tasks
are insufficient for the prospective general-superiority gate. The cost
comparison concerns this fixed three-call control; the earlier direct route can
be cheaper still and remains available. No model weights, neural controller or
production worker were updated.

Run the provider-free replay from the repository root with a new output path:

```sh
python research/outcome-learning/interactive-work-08/replay.py \
  run/interactive-replay --corrected-qualification
```

Omit `--corrected-qualification` to reproduce the original oracle's two false
rejections as well. This replay requires no API key, network or paid model.

`protocol.json` binds the execution source, common history and actual data.
`contracts.py` defines explicit independent output expectations; it never enters
the model conversation or its tool workspace. This two-product course is a
capability and reuse test, not the 70-task general-superiority gate.

The recording comes from [DANDI 001603, version 0.260923.0623](https://dandiarchive.org/dandiset/001603/0.260923.0623),
van der Molen et al., _Preconfigured neuronal firing sequences in human brain
organoids_, under CC BY 4.0. Source hashes and full attribution are retained in
the [original import record](../../biology/dandi-01/attribution.json). The input
contains recorded sorted-unit counts. It is not live tissue or Nuria's own
biological brain. Nonempty-bin episodes are an operational statistic, not a
claim about criticality or consciousness.

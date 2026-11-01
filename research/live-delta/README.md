# LiveDelta

Nuria produced a small lossless JSON update encoder that now serves its live
Studio. It reduces repeated transfers of the complete project history and beats
the pinned Python JSON Patch implementation on the recorded workload.

[Use the Studio](https://nuria.network/studio/) ·
[Download the latest validated encoder](https://nuria.network/studio/live-delta.py) ·
[Live research results](https://nuria.network/studio/live-delta-results.json)

The reviewed deployment uses [`studio/sparse_patch.py`](../../studio/sparse_patch.py).
It is dependency-free Python and returns ordinary RFC 6902 operations:

```python
from studio.sparse_patch import make_patch

operations = make_patch({"count": 1}, {"count": 2})
# [{"op": "replace", "path": "/count", "value": 2}]
```

The transport packs these operations into `[opcode, path, value]` tuples before
gzip. Codes are `0=add`, `1=remove`, `2=replace`; removals have no value. It expands
back to standard JSON Patch for the independent decoder. The benchmark gives
the existing JSON Patch implementation exactly the same packing as an additional
baseline.

## Measured result

The selected source was frozen before testing nine unseen, unmodified real pairs:
seven archived outcome reports, actual API growth from five to 41 projects, and
an actual changing heartbeat. All nine pairs and 266 adversarial pairs reconstructed
correctly. These are results on one Cherry VM, not a universal performance claim.

The table sums per-case medians, with one warmup and three measured samples per
case and method. Encoding includes delta generation, serialization and gzip-3.
Client CPU includes decompression, parsing and independent patch application.

| Method                                       | Encoding CPU | Client CPU | Compressed bytes |
| -------------------------------------------- | -----------: | ---------: | ---------------: |
| Nuria + tuple transport                      |     38.08 ms |   39.13 ms |          400,819 |
| python-jsonpatch 1.39                        |     86.12 ms |   41.60 ms |          401,499 |
| python-jsonpatch + identical tuple transport |     87.41 ms |   51.15 ms |          401,338 |
| jsondiff 2.2.1                               |  9,687.93 ms |   24.56 ms |          400,390 |
| Full compact JSON + gzip-3                   |     39.81 ms |   21.69 ms |          854,984 |

Nuria used **56% less encoding CPU than python-jsonpatch** and slightly fewer
bytes. It was within **0.11%** of jsondiff's compressed size. jsondiff's nested
list alignment was particularly expensive on the almost unchanged full history.
Full responses still have advantages: over this mixed corpus they use less total
CPU than the incremental path, while transferring more than twice the bytes.

Two subsequent real HTTP updates sent **91 and 90 bytes**, versus **497,351 and
497,344 bytes** for complete gzip responses. Both reconstructed the same complete
target version. This dramatic reduction describes sparse heartbeat updates;
large changes save much less. The HTTP probe also checks conditional 304 responses
and recovery from an unknown base. Its full first-response cost is recorded.

## Evidence and limitations

The original all-baselines gate required exact reconstruction, encoding CPU at
most 70% of the fastest delta baseline, and bytes at most 110% of the smallest.
The final qualification passed all three conditions.

- [Frozen protocol and amendment](evidence/protocol.json),
  [original protocol](evidence/protocol-v1.json).
- [Complete qualification](evidence/qualification.json) and
  [actual HTTP comparison](evidence/http-preview.json).
- [Exact original proposal source](evidence/candidate-01.py.txt) and
  [source provenance](evidence/source-provenance.json). Production formatting
  preserves the selected program's Python AST.
- [Development corpus](evidence/development/cases.json.gz) and
  [held-out corpus](evidence/holdout/cases.json.gz). Decompressed SHA256 values
  are committed in the protocol. All inputs were already public Nuria records.

The first proposal failed because it conflated integer and floating values.
That failure and later losing development attempts remain in `evidence/`.
Development exposed avoidable JSON Patch object-syntax overhead, so tuple packing
was added to **both** Nuria and a stronger existing-library baseline before any
holdout evaluation; thresholds and datasets did not change.

Two combined holdout executions exhausted their CPU bounds without a complete
report. They remain as `holdout-final.json` and `holdout-resource-recovery.json`.
`qualify.py` recovered the same frozen source and corpus using independently
bounded case/method jobs. No holdout feedback was sent to the model and no candidate
was changed during this recovery. All five methods completed; no timeout was
silently excluded from the final comparison.

This reuses established structural comparison, prefix/suffix trimming,
[JSON Patch](https://www.rfc-editor.org/info/rfc6902/), and caching. It is a useful
domain optimization, not a claim of a new compression algorithm or general
intelligence. Nuria authored the encoder; the integration, transport packing and
independent evaluation were engineering work around that proposal.

## Production and continued work

`/api/studio` remains a complete compatible response. `?since=<version>` returns
a smaller update when the server retains that base, otherwise a complete snapshot.
Repeated requests reuse parsed/compressed data. The cache retains at most three
versions and eight MB of raw historical data (the current snapshot is always
retained). No project, field or history is removed to make the response smaller.
The browser uses pinned MIT-licensed
[fast-json-patch 3.1.1](https://github.com/Starcounter-Jack/JSON-Patch) with prototype
protection; invalid patches or unsupported bases recover with one complete read.

`nuria-live-delta-improve.timer` starts another trial five minutes after the
previous one finishes. It runs on Cherry, resumes durable request IDs, and uses
the unchanged original inference ledger and caps. There is no project-count stop.
The previous prototype campaign stays paused with its unfinished work preserved.

Later trials explicitly retire the initial holdout into development data and
freeze a newly observed transition for each next acceptance check. Candidates must
improve the incumbent's development encoding CPU by at least 10%, add at most 2%
compressed bytes and regress combined client/server CPU by at most 5%, then pass
the fresh correctness and baseline checks. Only development feedback informs the
next proposal. Every paid proposal, failed test and accepted artifact is retained.

Generated programs run only in the existing offline OS sandbox. Passing library
artifacts become downloadable automatically. The web process keeps the reviewed
deployment; it does not execute newly generated Python. The timer stops paid work
when the original allowance cannot fund the next bounded proposal.

Reproduction requires Python 3.12, `jsonpatch==1.39`, `jsondiff==2.2.1`, and the
existing enrolled Linux bubblewrap backend. Restore the two compressed corpora
under an isolated experiment root, place the original source at `selected.py`,
and use `qualify.py` with its committed selection hash. `prepare.py` refuses to
overwrite a previously frozen corpus. For HTTP behavior and the actual browser
decoder, run `python3 -m unittest tests.test_live_state`.

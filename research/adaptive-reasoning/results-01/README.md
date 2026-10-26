# Retained reasoning results

The primary course stopped after 32 of 54 jobs following an HTTP 503 response.
Both proposed policies tied the baseline at 4/4 on validation, so neither was
promoted. A separate hosted-model substitution finished at **9/12**, matching
the previously published schema-first count. These results establish no
reasoning-policy or neural advantage.

| Run                                  | Selected jobs | Attempted | Accepted | Other outcomes                                  |
| ------------------------------------ | ------------: | --------: | -------: | ----------------------------------------------- |
| Primary policy course                |            54 |        32 |       31 | One transport-related abstention; 22 unexecuted |
| Historical-cohort model substitution |            12 |        12 |        9 | Three rejected submissions retained             |

The primary result is incomplete. Its uneven test-arm coverage cannot support
an effectiveness comparison. The substitution uses an already published public
cohort, different pretrained weights and reasoning/runtime implementations, and
no equivalent sampling seed. It is descriptive rather than a matched causal
experiment. The three original rejections, `spider-dev-0267`, `spider-dev-0687`
and `spider-dev-0698`, have not been reinterpreted or replaced.

## Replay

Use Python 3.12 or newer with its standard library. No model, API key, network
access, package installation or repository-private input is required.

```sh
python research/adaptive-reasoning/results-01/replay.py \
  --workspace /your/new/replay-directory \
  --output /your/new/replay-result.json
```

Both output paths must be new; their parent directories must exist. A replay
retains its extraction and does not remove an earlier attempt. The archive is
259,082 bytes and expands to 1,449,307 bytes across 328 exact regular members.

The replayer checks archive/member identities, both frozen source closures,
registered database/document hashes, complete selected denominators, actual
committed action/result receipts, report bindings and original verdicts. Its
independent engine executes 31 primary submitted queries and 32 references, then
12 substitution submissions and 12 references. It uses read-only SQLite with an
authorizer, VM/time/row/byte bounds and typed finite-result comparison preserving
NULLs, duplicate rows, column count and declared ordering. This is not the
official Spider test-suite metric or universal SQL equivalence.

The retained `verify.py` engine imports no Nuria planner, evaluator or grading
implementation. `test_verify.py` covers its comparison and execution limits;
`test_replay.py` covers the portable package and archive rejection paths.

```sh
python -m unittest discover \
  -s research/adaptive-reasoning/results-01 -p 'test_*.py' -v
```

## Contents and provenance

`evidence.tar.gz` preserves exact public protocol bytes, 32 full primary job
artifacts, their execution verdicts, the 54-job selection and 22 unexecuted
records, all twelve substitution job artifacts, usage summaries and both
historical source closures. Questions, SQL, structured actions and receipt
hashes remain inspectable; hidden model reasoning and raw provider responses
are not included. Memory SQLite journals are omitted because this package
claims no persistent-memory learning result.

Only 15 unique attempted primary task bodies are included. The seven remaining
selected task bodies are omitted. All twelve substitution task/oracle entries
are included. No Duet data is used. Six unchanged Spider SQLite databases and
their registered source documents make the finite SQL replay self-contained.

Private protocols contain operational filesystem paths. Their complete original
hashes are retained as references; the files themselves are omitted. The public
package instead identifies the selected raw row fragments and oracle entries
that were checked against the retained private inputs. This does **not**
reconstruct or independently attest the omitted private protocol bytes or the
original whole-dataset hash. The earlier independent primary verification is
retained with its exact report/artifact/reference-query hashes.

Usage and budget fields are host records, not provider billing attestations.
The failed request's usage remains unknown; its full original cost ceiling was
permanently charged against the unchanged shared allowance. Replay makes no
financial transaction and does not verify provider settlement.

Spider data and selected reference records retain their
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) terms and author
attribution. The [official project](https://yale-lily.github.io/spider) identifies
that data license; the archive includes `LICENSE-DATA.md`. Code retains the
repository license. Foundation-model exposure to this public benchmark remains
possible.

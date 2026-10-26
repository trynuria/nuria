# Grounded comparison 03: retained evidence

The primary pair finished at 9/12 accepted for neural inspection versus 8/12 for
fixed inspection. The full six-condition course is incomplete. Read [the results
and limits](results.md).

This directory retains the original run, its independent record check,
selected public Spider inputs and the separate experimental Brian2 histories.
The manifest separates original file identities from ZIP identities. Archive
parts use fixed ordering, metadata and timestamps. Files larger than a chunk
limit have numbered parts whose exact offsets and hashes are in the manifest.
All original bytes can be reconstructed without loading Python serialization.

**Do not unpickle untrusted data.** The Brian `network.pkl` files are trusted-local
checkpoints. The record verifier checks hashes and origin-credit metadata; it
does not restore or reexecute their neural state. These histories are separate
from Nuria's original production circuits. Model weights are not included.

Reconstruct into a new private directory:

```sh
python extract_evidence.py --output /path/to/new-restored-evidence
```

Then, from a checkout whose source matches the frozen hashes, replay the records:

```sh
python -m scripts.verify_grounded_comparison /path/to/new-restored-evidence/run \
  --protocol /path/to/new-restored-evidence/evidence/protocol.json \
  --source-freeze /path/to/new-restored-evidence/evidence/source-freeze.json \
  --source-freeze-sha256 a4cff862bda1b3f7136726106f468e5f8153af0372f5cbe437bc3c37965fe342 \
  --output /path/to/new-verification.json
```

The reader verifies every ZIP, chunk and reconstructed original before writing.
It refuses existing destinations, unsafe paths and oversized payloads, and never
unpickles. The source verifier has no model or network dependency.

This evidence establishes finite SQLite agreement and recorded consistency.
Public benchmark pretraining contamination is possible. Token totals are
internally reconciled rather than externally attested. Separate journals do not
authenticate their cross-file wall-clock ordering. Read the final summary and
independent receipt for complete versus partial comparison coverage.

## Source attribution

[Spider 1.0](https://yale-lily.github.io/spider), Tao Yu et al. (2018), Yale LILY;
[paper](https://arxiv.org/abs/1809.08887),
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
The inputs select official development questions/databases and add local SQL
and execution records. Their source hashes and original task IDs are preserved.
Dataset terms do not grant a license to unrelated Nuria-authored code.

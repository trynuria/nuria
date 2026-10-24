# Reproduce the exact initial library

The frozen starting state is the `fixed2` checkpoint from
`external-distinct-evidence`, not a rerun of the current evaluator. Its archived
code predates study namespaces and uses different evaluation identities. Program
bodies alone would not reproduce the complete receipt/checkpoint hash.

The archived snapshot contains all nine modules used in that development run.
Their exact identities are recorded in its
[summary](development/external-distinct-evidence/summary.json). It has no hidden
seed state. Start with a new private source directory and a new private output
directory outside the checkout. Benchmark bodies should remain privately staged.

```sh
nuria_checkout=/absolute/path/to/nuria
nuria_staging=/absolute/path/to/private/new-prose-source
nuria_reproduction=/absolute/path/to/private/new-checkpoint-reproduction
cd "$nuria_checkout"
"$nuria_checkout/.venv/bin/python" - "$nuria_staging" <<'PY'
import json
import sys
from pathlib import Path
from scripts.fetch_active_skill_sources import stage

inventory = json.loads(Path("research/active-skills/source-inventory.json").read_bytes())
names = [
    entry["task_id"]
    for entry in inventory["tasks"]
    if entry["structurally_eligible"] and entry["split"] == "development"
]
stage(sys.argv[1], names)
PY
cd "$nuria_checkout/research/active-skills/development/external-distinct-evidence/sources"
"$nuria_checkout/.venv/bin/python" -m scripts.evaluate_active_skills \
  --domain external \
  --inventory "$nuria_checkout/research/active-skills/source-inventory.json" \
  --source-directory "$nuria_staging" \
  --out "$nuria_reproduction"
"$nuria_checkout/.venv/bin/python" - "$nuria_reproduction/library-fixed2.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_bytes()
artifact = hashlib.sha256(raw).hexdigest()
state = json.loads(raw)
canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
assert artifact == "9bc790e1c8d0b5d2f33b7007006a79d12e0ce8e59c4eb3c6d0629b7a53abc1c3"
assert hashlib.sha256(canonical).hexdigest() == "1a24d8371ef86121d37d2414c3f4cd81bab50372553697e6bdf299b3c156afe3"
assert len(state["programs"]) == 7
print(artifact)
PY
```

Changing into the archived source directory matters: it ensures that Python
imports the archived evaluator and language modules rather than the current
implementation. The current inventory's formatting/access-log changes do not
alter the pinned task bodies or their original example order.

This replay was performed before reserved access and reproduced the checkpoint
**byte for byte**. The [replay receipt](initial-library-reproduction.json) records
the commitments. CPU measurements differ between repetitions and do not enter
the library state. The measured development environment used CPython 3.14.7 and
Unicode database 16.0.0.

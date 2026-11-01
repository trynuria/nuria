"""Freeze actual archived outcomes and observed public snapshots, before proposals."""

import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
if (root / "protocol.json").exists():
    raise SystemExit("The frozen corpus already exists; never regenerate it in place")
archive = Path("/var/lib/nuria-studio/projects")
sets = {"development": [], "holdout": []}
for p in sorted(archive.glob("invention-*/*/outcome-baseline.json")):
    q = p.with_name("outcome-result.json")
    if not q.exists():
        continue
    pid = int(p.parts[-3].split("-")[-1])
    group = "development" if pid <= 30 else "holdout"
    sets[group].append(
        {
            "id": p.parts[-3] + "-" + p.parts[-2],
            "origin": "Unmodified archived before/after acceptance reports",
            "old": json.loads(p.read_text()),
            "new": json.loads(q.read_text()),
        }
    )
old = root / "corpus/status-old.json"
live = root / "corpus/status-observed-1.json"
later = Path("/var/lib/nuria-studio/public/status.json")
sets["development"].append(
    {
        "id": "observed-old-api-unchanged",
        "origin": "Real archived public API, unchanged poll",
        "old": json.loads(old.read_text()),
        "new": json.loads(old.read_text()),
    }
)
sets["holdout"].extend(
    [
        {
            "id": "observed-api-growth",
            "origin": "Two real public API captures, 5 and 41 projects",
            "old": json.loads(old.read_text()),
            "new": json.loads(live.read_text()),
        },
        {
            "id": "observed-api-idle",
            "origin": "Two real public API captures during operator pause",
            "old": json.loads(live.read_text()),
            "new": json.loads(later.read_text()),
        },
    ]
)
manifest = {
    "datasets": {},
    "baselines": {
        "jsonpatch": "1.39",
        "jsondiff": "2.2.1",
        "gzip": "stdlib level 3 + unchanged 304 shortcut",
    },
    "selection": "Development cases only; freeze selected code before one blind holdout run. No holdout case contents or feedback sent to model.",
    "primary_gate": "Exact reconstruction on all real and adversarial cases; sum server encoding time at most 70% of fastest delta baseline; compressed bytes at most 110% of smallest delta baseline, with identical unchanged-response shortcut. Report client reconstruction and full-response comparison separately.",
    "claim_scope": "Nuria public state and outcome reports. No claim of new JSON Patch algorithm or universal superiority.",
}
for group, cases in sets.items():
    p = root / group / "cases.json"
    p.write_text(json.dumps(cases, separators=(",", ":")))
    manifest["datasets"][group] = {
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "case_count": len(cases),
        "cases": [{"id": c["id"], "origin": c["origin"]} for c in cases],
    }
(root / "protocol.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest))

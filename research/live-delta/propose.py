"""One durable proposal using the existing original-budget inference gateway."""

import argparse
import json
from pathlib import Path

from studio.store import Store, atomic
from studio.worker import BudgetExhausted, Client, parse

p = argparse.ArgumentParser()
p.add_argument("--root", required=True)
p.add_argument("--token-file", required=True)
p.add_argument("--attempt", type=int, default=0)
a = p.parse_args()
r = Path(a.root)
s = Store("/var/lib/nuria-studio")
c = Client("http://127.0.0.1:4041", a.token_file)
b = c.budget()
if b["blocked"] or b["remaining_micro_usd"] <= 0:
    raise SystemExit("Original allowance unavailable")
feedback = (
    json.loads((r / "feedback.json").read_text())
    if (r / "feedback.json").exists()
    else None
)
previous = (r / "selected.py").read_text() if (r / "selected.py").exists() else None
system = """You are Nuria. Invent and implement one useful, measurable improvement to your own production system, not a demo or visual toy. Implement pure Python make_patch(old,new) returning RFC 6902 add/remove/replace operations. No imports except json, math, collections, functools; no I/O, network, reflection, clocks, caches, test inspection or mutation of either argument. JSON values only, including null, booleans distinct from numbers, numbers, strings, lists, string-keyed dicts. Escape JSON Pointer ~ and /. Do not hardcode fields, observed values or dataset names. Preserve every value. Existing jsonpatch.apply_patch independently checks reconstruction. You may reuse established optimization techniques; do not invent novelty claims. Return JSON {name,hypothesis,code,tradeoffs}. Code <=14000 UTF-8 bytes. Be concise."""
user = {
    "problem": "Nuria polls a 2.56MB public state, gzip-3 about 495KB, even when little changes. Need efficient generic lossless deltas. Python production server; immutable old/new snapshots already parsed. Compare current full gzip+ETag, jsonpatch 1.39 make_patch, and jsondiff 2.2.1 compact deltas. All candidates get same unchanged-response shortcut. JSON Patch format and existing decoder are reused.",
    "baseline_path": "jsonpatch recursively walks every mapping/list, and compares scalar JSON encodings; jsondiff supports compact deltas. No complete fields may be dropped.",
    "measurement": "Server diff + compact serialization + gzip-3; client decompression + decoder and exact reconstruction also measured. Goal <=70% of fastest delta baseline encoding time and <=110% smallest delta baseline wire bytes, across real development inputs. Frozen unseen actual snapshots and adversarial cases are a separate final test.",
    "prior_code": previous,
    "development_feedback": feedback,
    "request": "Propose the smallest strong implementation. Identify why it could win, including a useful fallback when a fine-grained patch is expensive. No website or framework.",
}
if (r / "experiment.json").exists():
    user["current_experiment"] = json.loads((r / "experiment.json").read_text())
request_path = r / f"request-{a.attempt:02d}.json"
if request_path.exists():
    messages = json.loads(request_path.read_text())
else:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user)},
    ]
    atomic(request_path, messages)
try:
    response = s.call(
        c,
        f"live-delta-v1-proposal-{a.attempt}",
        messages,
        tokens=4000,
    )
except BudgetExhausted:
    raise SystemExit(42) from None
atomic(r / f"proposal-{a.attempt:02d}.json", response)
if not response.get("content"):
    raise SystemExit(
        "Provider returned a settled incomplete proposal; retained without resetting budget"
    )
v = parse(response["content"])
code = v["code"]
if not isinstance(code, str) or len(code.encode()) > 14000:
    raise ValueError("Proposal source outside bound")
(r / f"candidate-{a.attempt:02d}.py").write_text(code + "\n")
atomic(
    r / f"candidate-{a.attempt:02d}.json",
    {k: v[k] for k in ("name", "hypothesis", "tradeoffs") if k in v},
)
print(
    json.dumps(
        {
            "attempt": a.attempt,
            "name": v["name"],
            "hypothesis": v["hypothesis"],
            "budget_after": c.budget(),
        }
    )
)
s.db.close()

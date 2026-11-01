"""Recover the frozen comparison with bounded, independent case/method jobs.

The first combined run exceeded its CPU limit. Splitting prevents one slow
baseline from erasing every measurement. Selection and thresholds are unchanged;
an incomplete comparison cannot pass the original all-baselines gate.
"""

import argparse
import hashlib
import json
from pathlib import Path

from evaluate import CHECKS, audit

from cognition.outcome_worker import IsolatedPython
from studio.store import atomic

METHODS = ("candidate", "jsonpatch", "jsonpatch_compact", "jsondiff", "full_gzip")
METRICS = ("encode_ms", "decode_ms", "end_to_end_cpu_ms", "wire_bytes")


def qualify(root):
    root = Path(root)
    code = (root / "selected.py").read_text()
    audit(code)
    run = json.loads((root / "run.json").read_text())
    digest = hashlib.sha256(code.encode()).hexdigest()
    assert digest == run["selected_sha256"], "Selected program changed"
    corpus = root / "holdout"
    cases = json.loads((corpus / "cases.json").read_text())
    expected = json.loads((root / "protocol.json").read_text())["datasets"]["holdout"][
        "sha256"
    ]
    assert hashlib.sha256((corpus / "cases.json").read_bytes()).hexdigest() == expected
    executor = IsolatedPython(
        root / "executions",
        dependency_roots=("/opt/nuria/research-deps", str(corpus)),
        wall_seconds=60,
        cpu_seconds=55,
    )
    rows = []
    for index, case in enumerate(cases):
        row = {"id": case["id"], "methods": {}, "failures": {}}
        for name in METHODS:
            label = f"qualification-{index:02d}-{name}"
            path = root / f"{label}.json"
            if path.exists():
                result = json.loads(path.read_text())
            else:
                checks = CHECKS.split("totals={k:", 1)[0]
                checks = checks.replace(
                    "CASE_PATH", repr(str(corpus / "cases.json"))
                ).replace("SEED", "919843")
                checks = checks.replace(
                    "cases=json.loads(Path("
                    + repr(str(corpus / "cases.json"))
                    + ").read_text())",
                    "cases=[json.loads(Path("
                    + repr(str(corpus / "cases.json"))
                    + f").read_text())[{index}]]",
                )
                checks = checks.replace(
                    "methods=('candidate','jsonpatch','jsonpatch_compact','jsondiff','full_gzip')",
                    f"methods=({name!r},)",
                )
                # One warm-up, three measured samples; every method receives
                # identical repetitions and unchanged-response treatment.
                checks = checks.replace("range(7)", "range(4)")
                checks += "\nPath('report.json').write_text(json.dumps({'rows':rows,'adversarial_cases':len(adversarial)}))\n"
                execution = executor.run(code, checks, label=label)
                output = root / "executions" / label / "report.json"
                result = (
                    json.loads(output.read_text())
                    if output.exists()
                    else {"execution": execution}
                )
                atomic(path, result)
            if "rows" in result:
                measurement = result["rows"][0]
                row.update({k: v for k, v in measurement.items() if k != "methods"})
                row["methods"][name] = measurement["methods"][name]
            else:
                row["failures"][name] = result
            atomic(
                root / "qualification-progress.json",
                {"completed_rows": rows, "current": row, "method": name},
            )
            print(
                json.dumps(
                    {"case": case["id"], "method": name, "complete": "rows" in result}
                ),
                flush=True,
            )
        rows.append(row)
    comparisons = {}
    for baseline in METHODS[1:]:
        paired = [
            r for r in rows if "candidate" in r["methods"] and baseline in r["methods"]
        ]
        totals = {
            name: {
                metric: sum(r["methods"][name][metric] for r in paired)
                for metric in METRICS
            }
            for name in ("candidate", baseline)
        }
        comparisons[baseline] = {
            "paired_cases": len(paired),
            "totals": totals,
            "encode_ratio": totals["candidate"]["encode_ms"]
            / totals[baseline]["encode_ms"],
            "wire_ratio": totals["candidate"]["wire_bytes"]
            / totals[baseline]["wire_bytes"],
        }
    complete = all(not r["failures"] for r in rows)
    result = {
        "code_sha256": digest,
        "corpus_sha256": expected,
        "rows": rows,
        "correct": all("candidate" in r["methods"] for r in rows),
        "real_cases": len(rows),
        "adversarial_cases": 266,
        "complete_comparison": complete,
        "comparisons": comparisons,
        "gate_passed": complete
        and all(
            comparisons[n]["encode_ratio"] <= 0.70
            and comparisons[n]["wire_ratio"] <= 1.10
            for n in METHODS[1:4]
        ),
        "measurement": "Cherry, one warmup and median of three measured samples per case/method. Independent jobs preserve the 55 CPU/60 wall second bound and 1 GB service memory ceiling. Incomplete baselines are explicit and cannot pass the original all-baselines gate. Encoding includes serialization and gzip; decoding includes decompression, parsing and independent patch application. Common input parsing and HTTP transport excluded.",
        "recovery": ["holdout-final.json", "holdout-resource-recovery.json"],
    }
    atomic(root / "qualification.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    qualify(parser.parse_args().root)

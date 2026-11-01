"""Re-execute committed programs without provider access or model inference."""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from cognition.outcome_worker import IsolatedPython  # noqa: E402


def run(root, workspace, dependencies=(), *, corrected_qualification=False):
    workspace.mkdir(parents=True, exist_ok=False)
    raw = (root / "proposals-before-grade.json").read_bytes()
    proof = json.loads((root / "public-proposals-proof.json").read_text())
    if hashlib.sha256(raw).hexdigest() != proof["sha256"]:
        raise ValueError("Committed program identity changed")
    proposals = json.loads(raw)["records"]
    with gzip.open(root / "tasks.jsonl.gz", "rt") as stream:
        tasks = {row["task_id"]: row for row in map(json.loads, stream)}
    transfers = json.loads((root / "transfer-plan-before-outcomes.json").read_text())[
        "cases"
    ]
    qualification_file = (
        "qualification-cases.corrected.json"
        if corrected_qualification
        else "qualification-cases.json"
    )
    qualifications = json.loads((root / qualification_file).read_text())["cases"]
    executor = IsolatedPython(workspace / "executions", dependency_roots=dependencies)
    results = []
    for condition, records in proposals.items():
        for record in records:
            task = tasks[record["task_id"]]
            profile = task["family"]
            checks = [("origin", task["inputs"], task["checker"])]
            for case in transfers + qualifications:
                if case["profile"] != profile:
                    continue
                source = json.dumps(case["data"], sort_keys=True, separators=(",", ":"))
                if hashlib.sha256(source.encode()).hexdigest() != case["source_sha256"]:
                    raise ValueError("Enrolled transfer bytes changed")
                expected = repr(case["expected"])
                checker = (
                    "assert task_func()=="
                    + expected
                    + "\nimport json\nfrom pathlib import Path\n"
                    + "assert json.loads(Path('report.json').read_text())=="
                    + expected
                )
                checks.append((case["case_id"], {"source.json": source}, checker))
            for case_id, inputs, checker in checks:
                checked = executor.run(
                    record["code"],
                    checker,
                    label=condition + "-" + profile + "-" + case_id,
                    inputs=inputs,
                )
                results.append(
                    {
                        "condition": condition,
                        "profile": profile,
                        "case_id": case_id,
                        "checker": checked,
                    }
                )
    summary = {
        "executions": len(results),
        "accepted": sum(row["checker"]["passed"] for row in results),
        "model_calls": 0,
        "qualification_file": qualification_file,
        "results": results,
        "scope": "Native reproduction and explicit boundary qualification; no new model-effectiveness claim.",
    }
    (workspace / "replay-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--corrected-qualification", action="store_true")
    args = parser.parse_args()
    result = run(
        Path(__file__).resolve().parent,
        args.workspace,
        args.dependency_root,
        corrected_qualification=args.corrected_qualification,
    )
    print(
        json.dumps({k: v for k, v in result.items() if k != "results"}, sort_keys=True)
    )
    if args.corrected_qualification and result["accepted"] != result["executions"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

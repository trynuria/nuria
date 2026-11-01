"""Characterize one registered wall-clock contract without changing its grades.

The original timing-dependent checker is retained and executed. Scripted-clock
controls separately check the calculation; they are fixtures, not intelligence
results. Negative-comparison qualification never promotes a controller.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path

from cognition.outcome_worker import IsolatedPython
from scripts.evaluate_experience_navigation import read_rows
from scripts.verify_reasoner_transfer import material, summarize, verify


def inputs(package):
    package = Path(package)
    protocol = json.loads((package / "timing-823/protocol.json").read_text())
    task = next(
        row
        for row in read_rows(package / "tasks.jsonl.gz")
        if row["task_id"] == protocol["task_id"]
    )
    seal = json.loads((package / "proposals-before-grade.json").read_text())
    programs = {
        name: next(
            row["code"] for row in records if row["task_id"] == protocol["task_id"]
        )
        for name, records in seal["records"].items()
    }
    programs["reference"] = task["canonical_solution"]
    programs["constant-control"] = protocol["negative_control_code"]
    for name, code in programs.items():
        if (
            hashlib.sha256(code.encode()).hexdigest()
            != protocol["program_sha256"][name]
        ):
            raise ValueError("Timing qualification belongs to other programs")
    if (
        hashlib.sha256(task["checker"].encode()).hexdigest()
        != protocol["checker_sha256"]
    ):
        raise ValueError("The original timing checker changed")
    script = Path(__file__).read_bytes()
    if hashlib.sha256(script).hexdigest() != protocol["characterization_source_sha256"]:
        raise ValueError("The prospective timing characterization changed")
    return protocol, task, programs


def characterize(package, workspace, *, dependencies=()):
    workspace = Path(workspace)
    if workspace.exists():
        raise ValueError("Timing characterization needs a new retained workspace")
    protocol, task, programs = inputs(package)
    executor = IsolatedPython(workspace / "executions", dependency_roots=dependencies)
    rows = []
    for round_number, order in enumerate(protocol["physical_rounds"]):
        for name in order:
            checked = executor.run(
                programs[name], task["checker"], label=f"physical-{round_number}-{name}"
            )
            rows.append(
                {
                    "round": round_number,
                    "program": name,
                    "kind": "unchanged_wall_clock",
                    "checker": checked,
                }
            )
    for name, code in programs.items():
        checked = executor.run(
            code, protocol["controlled_checker"], label="scripted-" + name
        )
        rows.append(
            {"program": name, "kind": "scripted_clock_calculation", "checker": checked}
        )
    result = {
        "protocol": protocol,
        "rows": rows,
        "model_calls": 0,
        "original_grades_changed": False,
        "scope": "Physical repeatability plus supplemental scripted-clock semantics; no model or neural advantage claim",
    }
    with (workspace / "characterization.json").open("x") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return result


def qualify_negative(package, workspace, *, dependencies=()):
    """Validate the limited negative conclusion, exposing every native mismatch."""
    package, workspace = Path(package), Path(workspace)
    timing, task, programs = inputs(package)
    comparison, tasks, _ = material(package)
    records = read_rows(package / "executions.jsonl.gz")
    physical = verify(
        package, workspace=workspace / "comparison", dependencies=dependencies
    )
    controls = characterize(package, workspace / "timing", dependencies=dependencies)
    semantic = [
        row for row in controls["rows"] if row["kind"] == "scripted_clock_calculation"
    ]
    if len(semantic) != len(programs) or any(
        row["checker"]["passed"] is not (row["program"] != "constant-control")
        for row in semantic
    ):
        raise ValueError(
            "Registered timing calculations did not satisfy the independent controls"
        )
    unexpected = [
        row for row in physical["mismatches"] if row["task_id"] != timing["task_id"]
    ]
    if unexpected:
        raise ValueError("A non-timing native outcome changed")
    # Treat this single physical measurement as unknown for BOTH conditions.
    # The original benchmark labels and denominator remain unchanged.
    sensitivity = []
    for labels in itertools.product((False, True), repeat=2):
        varied = json.loads(json.dumps(records))
        for item in varied:
            if item["record"]["task_id"] == timing["task_id"]:
                item["record"]["accepted"] = labels[
                    0 if item["condition"] == "astra-medium" else 1
                ]
        result, _ = summarize(comparison, tasks, varied)
        sensitivity.append(
            {
                "astra_timing_label": labels[0],
                "sol_timing_label": labels[1],
                "result": result,
            }
        )
    if any(row["result"]["superiority_supported"] for row in sensitivity):
        raise ValueError(
            "The no-promotion conclusion is not robust to timing uncertainty"
        )
    report = {
        "original_comparison": physical["result"],
        "complete_native_replay": physical,
        "timing_characterization": controls,
        "timing_sensitivity": sensitivity,
        "this_run_matches_recorded_labels": not physical["mismatches"],
        "exact_reproduction_supported": False,
        "registered_timing_sensitive_task": task["task_id"],
        "negative_conclusion_robust_to_all_timing_labels": True,
        "runtime_promoted": False,
        "original_grades_changed": False,
        "scope": "All programs and original checkers run. Wall-clock uncertainty remains explicit; the failed superiority decision is robust, not an exact-reproduction claim.",
    }
    with (workspace / "negative-qualification.json").open("x") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("characterize", "negative"))
    parser.add_argument("package", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    function = characterize if args.stage == "characterize" else qualify_negative
    result = function(args.package, args.workspace, dependencies=args.dependency_root)
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key
                not in {
                    "rows",
                    "protocol",
                    "timing_characterization",
                    "complete_native_replay",
                    "timing_sensitivity",
                }
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

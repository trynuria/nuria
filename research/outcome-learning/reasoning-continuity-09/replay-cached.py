"""Replay unchanged programs with a shared, read-only Matplotlib font index.

Only the cache JSON is shared. Each job retains its own writable configuration
folder, program, inputs and observations. The existing OS boundary protects the
external cache target; the original replayer and all checkers stay unchanged.
"""

import argparse
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path

script = Path(__file__).resolve().with_name("replay.py")
spec = importlib.util.spec_from_file_location("continuity_replay", script)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
OriginalExecutor = replay.IsolatedPython


def cached_executor(cache_directory):
    cache_directory = Path(cache_directory).resolve()
    candidates = sorted(cache_directory.glob("fontlist-v*.json"))
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise ValueError("Supply one regular, existing Matplotlib font index")
    cache_file = candidates[0]
    raw = cache_file.read_bytes()
    if not 1 <= len(raw) <= 1048576:
        raise ValueError("Font index exceeds its finite cache contract")
    value = json.loads(raw)
    if type(value) is not dict or type(value.get("ttflist")) is not list:
        raise ValueError("The input is not a Matplotlib font index")

    class CachedExecutor(OriginalExecutor):
        def __init__(self, root, *, dependency_roots=(), **kwargs):
            super().__init__(
                root,
                dependency_roots=(*dependency_roots, cache_directory),
                **kwargs,
            )

        def _command(self, work, driver):
            config = work / "mplconfig"
            config.mkdir(mode=0o700)
            (config / cache_file.name).symlink_to(cache_file)
            return super()._command(work, driver)

    return CachedExecutor, hashlib.sha256(raw).hexdigest()


def qualify_filesystem(package, workspace, dependencies, report):
    """Check the negative comparison without relabeling a host-sensitive test."""
    registration = json.loads((package / "filesystem-832.json").read_text())
    if (
        registration["qualification_source_sha256"]
        != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    ):
        raise ValueError("Filesystem characterization source changed")
    protocol, tasks, _ = replay.material(package)
    task = next(t for t in tasks if t["task_id"] == registration["task_id"])
    records = replay.read_rows(package / "executions.jsonl.gz")
    programs = {
        item["condition"]: item["record"]["code"]
        for item in records
        if item["record"]["task_id"] == task["task_id"]
    }
    programs["reference"] = task["canonical_solution"]
    if {
        name: hashlib.sha256(code.encode()).hexdigest()
        for name, code in programs.items()
    } != registration["program_sha256"] or hashlib.sha256(
        task["checker"].encode()
    ).hexdigest() != registration["checker_sha256"]:
        raise ValueError("Characterization belongs to other programs or checks")
    executor = replay.IsolatedPython(
        workspace / "filesystem-controls", dependency_roots=dependencies
    )
    controls = []
    for round_number, order in enumerate(registration["rounds"]):
        for name in order:
            controls.append(
                {
                    "round": round_number,
                    "program": name,
                    "checker": executor.run(
                        programs[name],
                        task["checker"],
                        label=f"repeat-{round_number}-{name}",
                    ),
                }
            )
    labels = {row["checker"]["passed"] for row in controls}
    if len(controls) != 9 or len(labels) != 1:
        raise ValueError("Reference and both programs must agree in all fixed repeats")
    label = labels.pop()
    actual = json.loads((workspace / "replay.json").read_text())["programs"]
    if any(row["task_id"] != task["task_id"] for row in report["mismatches"]) or any(
        row["actual"] is not label
        for row in actual
        if row["task_id"] == task["task_id"]
    ):
        raise ValueError("A different native verdict changed or failed to reproduce")
    sensitivity = []
    for pair in itertools.product((False, True), repeat=2):
        varied = json.loads(json.dumps(records))
        for item in varied:
            if item["record"]["task_id"] == task["task_id"]:
                item["record"]["accepted"] = pair[
                    0 if item["condition"] == "continuity" else 1
                ]
        result = replay.summarize(protocol, tasks, varied)
        sensitivity.append({"labels": pair, "result": result})
    if any(item["result"]["superiority_supported"] for item in sensitivity):
        raise ValueError("The failed promotion decision is not robust to this case")
    report["filesystem_qualification"] = {
        "task_id": task["task_id"],
        "post_hoc": True,
        "controls": controls,
        "sensitivity": sensitivity,
        "all_other_native_verdicts_match": True,
        "native_accepted": {
            name: sum(row["actual"] for row in actual if row["condition"] == name)
            for name in ("continuity", "visible")
        },
        "negative_conclusion_robust_to_all_case_labels": True,
        "exact_reproduction_supported": not report["mismatches"],
        "original_grades_changed": False,
        "scope": "The unchanged reference and both programs share filesystem-sensitive behavior. Every native verdict remains reported; this qualifies the negative conclusion, not a new accuracy gain.",
    }
    with (workspace / "filesystem-qualification.json").open("x") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--font-cache", required=True, type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--qualify-filesystem", action="store_true")
    args = parser.parse_args()
    executor, fingerprint = cached_executor(args.font_cache)
    replay.IsolatedPython = executor
    report = replay.verify(script.parent, args.workspace, args.dependency_root)
    report["read_only_font_cache_sha256"] = fingerprint
    report["original_replay_source_sha256"] = hashlib.sha256(
        script.read_bytes()
    ).hexdigest()
    report["programs_and_checkers_changed"] = False
    if args.qualify_filesystem:
        report = qualify_filesystem(
            script.parent, args.workspace, args.dependency_root, report
        )
    print(json.dumps(report, sort_keys=True))
    if report["mismatches"] and not args.qualify_filesystem:
        raise SystemExit("Independent replay differs; original grades remain unchanged")

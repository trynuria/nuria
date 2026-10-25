"""Replay the published text-abstraction deliveries without running a learner.

The independent text interpreter checks committed outputs, disclosed fits and
finite buyer acceptance. Artifact identities and budget arithmetic are checked
separately. Search visitation, model fitting and recorded CPU time are not
independently reconstructed by this command.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.verify_active_skill_results import (
    digest,
    outcome,
    require,
    text_run,
    units,
)

ROOT = Path(__file__).resolve().parents[1]
MAX_PAYLOAD = 64 * 1024 * 1024
RUNS = (
    ("pilot-results.jsonl", "pilot-summary.json", 32, 8),
    ("pilot02-results.jsonl", "pilot02-summary.json", 24, 8),
    ("pilot03-results.jsonl", "pilot03-summary.json", 32, 8),
    ("development64-results.jsonl", "development64-summary.json", 704, 64),
    (
        "context-replay-pilot-results-02.jsonl",
        "context-replay-pilot-summary-02.json",
        64,
        8,
    ),
    ("work-fair-pilot-results-01.jsonl", "work-fair-pilot-summary-01.json", 88, 8),
)


def sha256(value):
    return hashlib.sha256(value).hexdigest()


class Archive:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.artifacts = self.root / "artifacts"

    def path(self, name):
        require(Path(name).name == name, "Artifact name must be a basename")
        plain = self.artifacts / name
        compressed = self.artifacts / (name + ".gz")
        choices = [path for path in (plain, compressed) if path.is_file()]
        require(len(choices) == 1, "Missing or ambiguous artifact: " + name)
        return choices[0]

    def payload(self, name):
        path = self.path(name)
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as stream:
                value = stream.read(MAX_PAYLOAD + 1)
        else:
            require(path.stat().st_size <= MAX_PAYLOAD, "Artifact exceeds bound")
            value = path.read_bytes()
        require(len(value) <= MAX_PAYLOAD, "Expanded artifact exceeds bound")
        return value

    def read(self, name):
        return json.loads(self.payload(name))

    def lines(self, name):
        return [json.loads(line) for line in self.payload(name).splitlines()]

    def original_hash(self, name):
        return sha256(self.payload(name))


def verify_manifest(archive):
    path = archive.root / "manifest.json"
    require(path.is_file(), "Public artifact manifest is required")
    manifest = json.loads(path.read_bytes())
    records = manifest["files"]
    require(type(records) is list and 1 <= len(records) <= 1000, "Invalid manifest")
    names = set()
    total = 0
    for record in records:
        name = record["path"]
        require(type(name) is str and name not in names, "Duplicate manifest path")
        names.add(name)
        target = (archive.root / name).resolve()
        require(target.is_relative_to(archive.root), "Manifest path escapes archive")
        require(target.is_file(), "Manifest file is missing: " + name)
        value = target.read_bytes()
        require(sha256(value) == record["sha256"], "Artifact hash differs: " + name)
        if "bytes" in record:
            require(len(value) == record["bytes"], "Artifact byte count differs")
        if target.suffix == ".gz":
            with gzip.open(target, "rb") as stream:
                payload = stream.read(MAX_PAYLOAD + 1)
        else:
            payload = value
        require(len(payload) <= MAX_PAYLOAD, "Manifest payload exceeds bound")
        if "payload_sha256" in record:
            require(
                sha256(payload) == record["payload_sha256"],
                "Artifact payload identity differs: " + name,
            )
        if record.get("normalized") is False and record.get("original_sha256"):
            require(
                sha256(payload) == record["original_sha256"],
                "Unmodified original identity differs: " + name,
            )
        total += len(value)
    expected = {
        str(path.relative_to(archive.root))
        for folder in ("artifacts", "sources", "archived-scripts")
        if (archive.root / folder).exists()
        for path in (archive.root / folder).rglob("*")
        if path.is_file()
    }
    require(expected == names, "Artifact manifest coverage differs")
    return {"files": len(names), "bytes": total, "sha256": sha256(path.read_bytes())}


def verify_freezes(archive):
    links = (
        ("pilot-summary.json", "source_freeze_sha256", "controller-freeze-02.json"),
        ("pilot02-summary.json", "source_freeze_sha256", "controller-freeze-03.json"),
        ("pilot03-summary.json", "source_freeze_sha256", "controller-freeze-04.json"),
        ("development64-summary.json", "freeze_sha256", "development64-freeze.json"),
        (
            "context-replay-pilot-summary-02.json",
            "freeze_sha256",
            "context-replay-pilot-freeze-02.json",
        ),
        (
            "work-fair-pilot-summary-01.json",
            "freeze_sha256",
            "work-fair-pilot-freeze-01.json",
        ),
    )
    for summary_name, key, freeze_name in links:
        require(
            archive.read(summary_name)[key] == archive.original_hash(freeze_name),
            "Summary/freeze identity differs: " + summary_name,
        )
    snapshots = (
        ("controller-freeze-02.json", "attempt-01-sources"),
        ("controller-freeze-03.json", "attempt-02-sources"),
        ("controller-freeze-04.json", "attempt-03-sources"),
        ("development64-freeze.json", "development64-audit-sources"),
        ("context-replay-pilot-freeze-02.json", "development64-audit-sources"),
        ("work-fair-pilot-freeze-01.json", "work-fair-v4"),
    )
    source_links = 0
    for freeze_name, folder in snapshots:
        freeze = archive.read(freeze_name)
        sources = dict(freeze.get("source_sha256", freeze.get("source_files", {})))
        if "engine_sha256" in freeze:
            sources["cognition/abstraction_search.py"] = freeze["engine_sha256"]
        for relative, expected in sources.items():
            target = (archive.root / "sources" / folder / relative).resolve()
            require(
                target.is_relative_to(archive.root)
                and target.is_file()
                and sha256(target.read_bytes()) == expected,
                "Frozen source snapshot differs: " + folder + "/" + relative,
            )
            source_links += 1
    development = archive.read("development64-freeze.json")
    require(
        sha256((ROOT / "scripts/verify_active_skill_results.py").read_bytes())
        == development["source_sha256"]["scripts/verify_active_skill_results.py"],
        "The independent replay interpreter differs from the frozen version",
    )
    require(
        development["course_sha256"]
        == archive.original_hash("development-course.json"),
        "Development course identity differs",
    )
    require(
        development["training_sha256"] == archive.original_hash("training.json"),
        "Original model training identity differs",
    )
    models = {}
    for method, expected in development["model_sha256"].items():
        name = "model-" + method + ".json"
        require(archive.original_hash(name) == expected, "Frozen model differs")
        models[method] = expected
    later = archive.read("work-fair-pilot-freeze-01.json")
    for method, expected in later["model_sha256"].items():
        name = (
            "model-spiking.json"
            if method == "original-spiking"
            else "model-" + method + "-02.json"
        )
        require(archive.original_hash(name) == expected, "Later frozen model differs")
        models[method] = expected
    return {
        "summary_links": len(links),
        "source_snapshot_links": source_links,
        "frozen_models": models,
    }


def verify_run(archive, jobs, config, trace_name, summary_name, count, tasks):
    rows = archive.lines(trace_name)
    summary = archive.read(summary_name)
    require(len(rows) == count, "Recorded delivery count differs: " + trace_name)
    pairs = {(row["method"], row["job_id"]) for row in rows}
    require(len(pairs) == count, "Duplicate method/job result")
    methods = {row["method"] for row in rows}
    selected_jobs = {row["job_id"] for row in rows}
    require(len(selected_jobs) == tasks, "Recorded task denominator differs")
    expected_jobs = set(jobs) if tasks == 64 else set(list(jobs)[::8])
    require(
        selected_jobs == expected_jobs, "Declared development task selection differs"
    )
    require(
        pairs == {(method, job) for method in methods for job in selected_jobs},
        "Matched method/task coverage differs",
    )
    totals = defaultdict(Counter)
    replay_work = 0
    for row in rows:
        require(row["job_id"] in jobs, "Unknown task in delivery trace")
        job = jobs[row["job_id"]]
        result = row["result"]
        program = result["program"]
        work = result["work"]
        require(
            all(type(value) is int and value >= 0 for value in work.values())
            and work["total"] <= 50000,
            "Recorded search work exceeds the declared ceiling",
        )
        buyer = outcome(program, job["score_inputs"], job["score_outputs"], config)
        replay_work += buyer["total_work"]
        require(buyer == row["buyer"], "Recorded buyer outcome differs")
        if program is not None:
            disclosed = outcome(
                program, job["diagnostic_inputs"], job["diagnostic_outputs"], config
            )
            replay_work += disclosed["total_work"]
            require(disclosed["passed"], "Committed program fails disclosed examples")
        actual = work["total"] + buyer["total_work"]
        if "actual_work" in row:
            require(actual == row["actual_work"], "Complete per-job work differs")
        totals[row["method"]].update(
            {
                "jobs": 1,
                "accepted": int(buyer["passed"]),
                "wrong": int(program is not None and not buyer["passed"]),
                "search_work": work["total"],
                "buyer_work": buyer["total_work"],
                "work": actual,
                "accepted_multi_abstraction": int(
                    buyer["passed"] and len(result.get("abstractions_used", [])) >= 2
                ),
            }
        )
    require(set(summary["reports"]) == methods, "Summary method set differs")
    for method, expected in summary["reports"].items():
        for key, value in totals[method].items():
            if key in expected:
                require(value == expected[key], "Aggregate result differs: " + method)
    return {
        "deliveries": len(rows),
        "tasks": len(selected_jobs),
        "methods": dict(totals),
        "observer_work": replay_work,
    }, rows


def verify_budget(archive, development_rows):
    events = archive.lines("study-budget-journal.jsonl")
    require(len(events) == 1410, "Budget journal event denominator differs")
    rows = {(row["method"], row["job_id"]): row for row in development_rows}
    pending, settled = {}, set()
    previous = "0" * 64
    spent = None
    cap = reserve = prior_work = None
    for sequence, event in enumerate(events, 1):
        require(
            event["sequence"] == sequence
            and event["previous_sha256"] == previous
            and event["sha256"]
            == digest({key: value for key, value in event.items() if key != "sha256"}),
            "Budget hash chain differs",
        )
        previous = event["sha256"]
        kind = event["kind"]
        if kind == "anchor":
            require(sequence == 1, "Misplaced budget anchor")
            spent, cap, reserve = (
                event["prior_known_work"],
                event["maximum_work"],
                event["observer_work_reserved"],
            )
            prior_work = spent
            require(cap == 75000000 and reserve == 2000000, "Study allowance differs")
        elif kind == "reserve":
            key = (event["method"], event["job_id"])
            require(key not in pending and key not in settled, "Duplicate reservation")
            require(
                event["actual_spent"] == spent
                and spent + sum(pending.values()) + event["reserved_work"] + reserve
                <= cap,
                "Reservation exceeds the declared study allowance",
            )
            pending[key] = event["reserved_work"]
        elif kind == "settle":
            key = (event["method"], event["job_id"])
            require(key in pending and key in rows, "Unreserved settlement")
            row = rows[key]
            require(
                event["result_sha256"] == digest(row)
                and event["actual_work"] == row["actual_work"] <= pending[key]
                and event["released_work"] == pending[key] - row["actual_work"],
                "Settlement evidence differs",
            )
            spent += event["actual_work"]
            require(event["actual_spent"] == spent, "Cumulative work differs")
            pending.pop(key)
            settled.add(key)
        elif kind == "complete":
            require(
                sequence == len(events)
                and not pending
                and event["pending_jobs"] == 0
                and event["actual_spent"] == spent,
                "Budget ended with an unresolved reservation",
            )
        else:
            raise ValueError("Unknown budget event")
    summary = archive.read("development64-summary.json")
    require(
        len(settled) == 704
        and spent == summary["actual_known_study_work"]
        and summary["remaining_conservative_bound"] == cap - spent - reserve,
        "Final development work reconciliation differs",
    )
    return {
        "events": len(events),
        "settled": len(settled),
        "pending": len(pending),
        "recorded_prior_work_anchor": prior_work,
        "scope": "Reservations and settlements replayed; earlier mining work is an anchor.",
    }


def verify_challenge(archive, rows, config):
    source = archive.read("posthoc64-string-challenge-inputs.json")
    recorded = archive.read("posthoc64-string-challenge-audit.json")
    data = {row["job_id"]: row for row in source["datasets"]}
    require(len(data) == 64, "Broader-input challenge task count differs")
    for dataset in data.values():
        require(
            len(dataset["inputs"]) == len(dataset["outputs"]) == 32
            and dataset["input_sha256"] == digest(dataset["inputs"])
            and dataset["target_sha256"] == digest(dataset["outputs"]),
            "Challenge data identity differs",
        )
    totals = defaultdict(Counter)
    observer_work = 0
    for row in rows:
        dataset = data[row["job_id"]]
        program = row["result"]["program"]
        result = outcome(program, dataset["inputs"], dataset["outputs"], config)
        observer_work += result["total_work"]
        totals[row["method"]].update(
            {
                "jobs": 1,
                "proposed_jobs": int(program is not None),
                "abstained_jobs": int(program is None),
                "original_exact_jobs": int(row["buyer"]["passed"]),
                "challenge_exact_jobs": int(result["passed"]),
                "challenge_rows": 32,
                "challenge_correct_rows": result["correct"],
                "incorrect_proposed_jobs": int(
                    program is not None and not result["passed"]
                ),
                "execution_work": result["execution_work"],
                "comparison_work": result["comparison_work"],
                "total_work": result["total_work"],
            }
        )
    require(
        {method: dict(value) for method, value in totals.items()}
        == recorded["summaries"],
        "Broader-input challenge aggregate differs",
    )
    return {
        "tasks": 64,
        "unique_inputs": 2048,
        "method_job_evaluations": len(rows),
        "methods": dict(totals),
        "observer_work": observer_work,
    }


def canonical_fields(value):
    return json.dumps(
        json.loads(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def verify_qualification(archive, jobs, config):
    """Check optional post-hoc qualification receipts without its implementation.

    A source-bound proposal is independently executed on every recorded case.
    Plan and receipt commitments, per-case scalar work and aggregate decisions
    are reconstructed. Detailed work-category counters remain recorded metadata.
    """
    freeze = archive.read("qualification-freeze-01.json")
    summary = archive.read("qualification-summary-01.json")
    require(
        summary["source_freeze_sha256"]
        == archive.original_hash("qualification-freeze-01.json")
        and summary["trace_sha256"]
        == archive.original_hash("qualification-results-01.jsonl"),
        "Qualification source/trace identity differs",
    )
    for name, expected in freeze["data_sha256"].items():
        require(archive.original_hash(name) == expected, "Qualification data differs")
    for name, expected in freeze["source_sha256"].items():
        path = (archive.root / "sources/qualification-v1" / name).resolve()
        require(
            path.is_relative_to(archive.root)
            and path.is_file()
            and sha256(path.read_bytes()) == expected,
            "Qualification source snapshot differs",
        )
    proposals = {}
    for stage, name in (
        ("development64", "development64-results.jsonl"),
        ("work_fair_pilot", "work-fair-pilot-results-01.jsonl"),
    ):
        for row in archive.lines(name):
            if row["result"]["program"] is not None:
                proposals[(stage, row["method"], row["job_id"])] = row["result"][
                    "program"
                ]
    for job in jobs.values():
        proposals[("reference_positive_control", "reference_program", job["id"])] = job[
            "private_recipe"
        ]["program"]
    datasets = {
        row["job_id"]: row
        for row in archive.read("posthoc64-string-challenge-inputs.json")["datasets"]
    }
    records = archive.lines("qualification-results-01.jsonl")
    keys = {(row["stage"], row["method"], row["job_id"]) for row in records}
    require(
        len(records) == len(keys) == len(proposals) == 247 and keys == set(proposals),
        "Qualification proposal coverage differs",
    )
    totals = defaultdict(Counter)
    spent, observer_work = freeze["prior_known_work"], 0
    required = {
        "varied_records": 32,
        "one_word_segment": 1,
        "four_plus_word_segment": 1,
        "three_plus_segments": 1,
    }
    for record in records:
        key = (record["stage"], record["method"], record["job_id"])
        program, job = proposals[key], jobs[record["job_id"]]
        dataset, receipt = datasets[job["id"]], record["qualification"]
        cases = []
        disclosed = [canonical_fields(value) for value in job["diagnostic_inputs"]]
        seen = set(disclosed)
        for source, expected, metadata in zip(
            dataset["inputs"],
            dataset["outputs"],
            dataset["generation_metadata"],
            strict=True,
        ):
            fields = json.loads(source)
            segments = [
                field.split(job["private_recipe"]["separator"]) for field in fields
            ]
            require(
                [len(parts) for parts in segments] == metadata["segments_per_field"]
                and [len(part.split()) for parts in segments for part in parts]
                == metadata["word_counts"],
                "Qualification diversity metadata differs from inputs",
            )
            tags = ["varied_records"]
            if 1 in metadata["word_counts"]:
                tags.append("one_word_segment")
            if any(count >= 4 for count in metadata["word_counts"]):
                tags.append("four_plus_word_segment")
            if any(count >= 3 for count in metadata["segments_per_field"]):
                tags.append("three_plus_segments")
            value = canonical_fields(source)
            require(value not in seen, "Qualification input repeats disclosed data")
            seen.add(value)
            cases.append(
                {"input": value, "expected": expected, "coverage": sorted(tags)}
            )
        payload = {
            "schema": "nuria.text-skill-qualification.v1",
            "source_id": "authored-text-abstraction-development",
            "domain": job["private_recipe"]["family"] + ":" + job["id"],
            "config": config,
            "program": program,
            "program_sha256": digest(program),
            "diagnostic_inputs_sha256": digest(disclosed),
            "cases": cases,
            "minimum_coverage": required,
        }
        require(
            receipt["plan_sha256"] == digest(payload)
            and receipt["receipt_sha256"]
            == digest({k: v for k, v in receipt.items() if k != "receipt_sha256"}),
            "Qualification commitment differs",
        )
        for name in (
            "schema",
            "source_id",
            "domain",
            "program_sha256",
            "minimum_coverage",
        ):
            require(
                receipt[name] == payload[name], "Qualification plan metadata differs"
            )
        require(
            receipt["config_sha256"] == digest(config)
            and receipt["diagnostic_inputs_sha256"] == digest(disclosed),
            "Qualification configuration or disclosed identity differs",
        )
        plan_string = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        total_work, errors = units(plan_string), 0
        actual_rows, coverage = [], dict.fromkeys(required, 0)
        for index, case in enumerate(cases):
            answer, execution_work = text_run(program, case["input"], config)
            error = "invalid_input" if answer is None else None
            passed = error is None and answer == case["expected"]
            case_work = execution_work
            if error is None:
                case_work += units(case["expected"]) + 1
            else:
                errors += 1
            observer_work += (
                execution_work
                + 1
                + (
                    (len(answer.encode()) if answer is not None else 0)
                    + len(case["expected"].encode())
                    + 31
                )
                // 32
            )
            total_work += case_work
            actual_rows.append(
                {
                    "case_index": index,
                    "input_sha256": digest(case["input"]),
                    "expected_sha256": digest(case["expected"]),
                    "output": answer,
                    "error": error,
                    "passed": passed,
                    "work": case_work,
                }
            )
            if passed:
                for label in case["coverage"]:
                    coverage[label] += 1
        qualified = all(row["passed"] for row in actual_rows) and all(
            coverage[label] >= count for label, count in required.items()
        )
        status = "qualified_for_cases" if qualified else "rejected"
        require(
            actual_rows == receipt["rows"], "Qualification per-case evidence differs"
        )
        require(
            receipt["planned_cases"] == receipt["completed_cases"] == len(cases) == 32
            and receipt["passed_cases"] == sum(row["passed"] for row in actual_rows)
            and receipt["passed_coverage"] == coverage
            and receipt["qualified_for_cases"] is qualified
            and receipt["status"] == status,
            "Qualification decision differs",
        )
        require(
            receipt["work_limit"] == freeze["per_plan_limit"] == 100000
            and total_work == receipt["work"]["total"] <= receipt["work_limit"]
            and receipt["work"]["errors"] == errors,
            "Qualification total work differs",
        )
        spent += total_work
        require(
            spent == record["cumulative_known_work"],
            "Qualification cumulative work differs",
        )
        totals[record["stage"] + "/" + record["method"]].update(
            {
                "proposed": 1,
                "qualified_for_cases": int(qualified),
                "rejected": int(not qualified),
                "work_limit": 0,
                "work": total_work,
            }
        )
    require(dict(totals) == summary["reports"], "Qualification aggregate differs")
    require(
        summary["prior_known_work"] == freeze["prior_known_work"]
        and summary["actual_known_work"] == spent
        and summary["diagnostic_work"] == spent - freeze["prior_known_work"]
        and summary["remaining_conservative_bound"]
        == freeze["maximum_study_work"] - spent - 2000000,
        "Qualification study arithmetic differs",
    )
    return {
        "plans_replayed": len(records),
        "case_executions": len(records) * 32,
        "learned_proposals_rejected": sum(
            values["rejected"]
            for name, values in totals.items()
            if not name.startswith("reference_positive_control/")
        ),
        "reference_controls_passed": totals[
            "reference_positive_control/reference_program"
        ]["qualified_for_cases"],
        "diagnostic_work": summary["diagnostic_work"],
        "observer_work": observer_work,
        "scope": "Post-hoc rejection diagnostic; reference programs are positive controls, not learned successes.",
        "limitations": [
            "Detailed primitive/byte work-category counters are not separately reconstructed.",
            "Diversity labels are finite input categories, not a universal domain guarantee.",
        ],
    }


def verify(root):
    archive = Archive(root)
    manifest = verify_manifest(archive)
    identities = verify_freezes(archive)
    course = archive.read("development-course.json")
    jobs = {job["id"]: job for job in course["tasks"]}
    require(len(jobs) == 64, "Authored course task count differs")
    require(
        set(Counter(job["cluster"] for job in jobs.values()).values()) == {8},
        "Authored cluster size differs",
    )
    runs = {}
    development = None
    for trace, summary, count, tasks in RUNS:
        report, rows = verify_run(
            archive, jobs, course["config"], trace, summary, count, tasks
        )
        runs[trace.removesuffix(".jsonl")] = report
        if trace == "development64-results.jsonl":
            development = rows
    budget = verify_budget(archive, development)
    challenge = verify_challenge(archive, development, course["config"])
    report = {
        "schema": "nuria.public-text-abstraction-replay.v1",
        "verified": True,
        "manifest": manifest,
        "identities": identities,
        "runs": runs,
        "budget": budget,
        "broader_input_challenge": challenge,
        "deliveries_replayed": sum(run["deliveries"] for run in runs.values()),
        "observer_work": sum(run["observer_work"] for run in runs.values())
        + challenge["observer_work"],
        "scope": [
            "Authored development data; repeatedly exposed pilot tasks are retained.",
            "Recorded proposals replayed in the separate original text interpreter.",
            "Broader challenge labels are fixed recorded reference outputs.",
            "Original search trajectories, gradients and CPU timing are not replayed.",
            "Neural superiority and broader semantic generalization were not shown.",
        ],
    }
    if any(
        (archive.artifacts / ("qualification-freeze-01.json" + suffix)).exists()
        for suffix in ("", ".gz")
    ):
        report["qualification"] = verify_qualification(archive, jobs, course["config"])
        report["observer_work"] += report["qualification"]["observer_work"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=ROOT / "research/text-abstractions"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.root)
    if args.output:
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "deliveries_replayed": report["deliveries_replayed"],
                "challenge_evaluations": report["broader_input_challenge"][
                    "method_job_evaluations"
                ],
                "observer_work": report["observer_work"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

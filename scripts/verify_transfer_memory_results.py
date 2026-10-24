"""Independent disclosed/final output replay for retained program controls."""

import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from scripts.build_guarded_skill_tasks import CONFIG, generate
from scripts.verify_active_skill_results import outcome


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path, *, lines=False):
    path = path if path.exists() else path.with_suffix(path.suffix + ".gz")
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as stream:
            content = stream.read(128 * 1024 * 1024 + 1)
    else:
        content = path.read_bytes()
    require(len(content) <= 128 * 1024 * 1024, "Memory evidence exceeds its bound")
    return (
        [json.loads(row) for row in content.splitlines() if row]
        if lines
        else json.loads(content)
    )


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def verify(development):
    root = Path(development) / "strong-memory-controls-01"
    if not root.exists():
        return None
    summary = load(root / "full-summary.json")
    jobs = {
        job["id"]: job for seed in (0, 1, 2, 3) for job in generate(seed, 96)["tasks"]
    }
    totals = defaultdict(lambda: defaultdict(int))
    seen = set()
    for row in load(root / "full-results.jsonl", lines=True):
        require(row["job_id"] in jobs, "A memory result references another course")
        job = jobs[row["job_id"]]
        fold = (
            int(
                hashlib.sha256(
                    job["private_recipe"]["program_sha256"].encode()
                ).hexdigest()[:8],
                16,
            )
            % 2
        )
        require(row["held_fold"] == fold, "A memory result changed source cohorts")
        require(
            set(row["methods"]) == {"field_templates", "retained_short_circuit"},
            "A stronger control is missing",
        )
        for method, result in row["methods"].items():
            key = (job["id"], method)
            require(key not in seen, "A memory result is duplicated")
            seen.add(key)
            program = result["program"]
            if program is not None:
                require(
                    result["committed_program_sha256"] == digest(program),
                    "A memory delivery changed after commitment",
                )
                disclosed = outcome(
                    program,
                    job["diagnostic_inputs"][:4],
                    job["diagnostic_outputs"][:4],
                    CONFIG,
                )
                require(
                    disclosed["passed"],
                    "A selected program does not fit its disclosed examples",
                )
            checked = outcome(
                program, job["score_inputs"], job["score_outputs"], CONFIG
            )
            require(
                result["buyer_calls"] == 1
                and type(result["accepted"]) is bool
                and result["accepted"] == checked["passed"]
                and result["correct_rows"] == checked["correct"]
                and result["rows"] == checked["rows"]
                and result["buyer_work"] == checked["total_work"],
                "Independent final memory replay differs",
            )
            require(
                type(result["work"]["total"]) is int
                and 0 <= result["work"]["total"] <= 50000,
                "Memory work exceeds its allowance",
            )
            values = {
                "accepted": int(checked["passed"]),
                "wrong_selected": int(program is not None and not checked["passed"]),
                "search_work": result["work"]["total"],
                "buyer_work": checked["total_work"],
                "audit_work": result["audit_work"],
            }
            for name, value in values.items():
                require(
                    type(value) is int and value >= 0, "A memory counter is malformed"
                )
                totals[method][name] += value
    require(
        seen
        == {
            (job, method)
            for job in jobs
            for method in ("field_templates", "retained_short_circuit")
        },
        "Memory delivery coverage is incomplete",
    )
    for method, measurements in totals.items():
        for name, value in measurements.items():
            require(
                summary["totals"][method][name] == value,
                "A memory aggregate differs from its traces",
            )
    return {
        "verified": True,
        "jobs": len(jobs),
        "independently_replayed_deliveries": len(seen),
        "accepted": {method: values["accepted"] for method, values in totals.items()},
        "wrong": {
            method: values["wrong_selected"] for method, values in totals.items()
        },
        "scope": "Complete disclosed-fit, final-output, commitment, acceptance, buyer-work and aggregate replay. Not a re-execution of candidate preparation/search or CPU timing; separately retained audits cover those boundaries.",
    }

"""Reproduce a finite server-reported work grouping without model inference.

The retained expected answer was frozen before the useful-source question ran.
This checker establishes scoped agreement, not payment provenance or an AI gain.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def group_rows(rows):
    """Use the frozen host checker's exact type, null and positive-value rules."""
    grouped = {}
    for row in rows:
        acceptance, settlement, outcome = (
            row.get(key) or {} for key in ("acceptance", "settlement", "outcome")
        )
        counters = grouped.setdefault(row["provider"], [0] * 6)
        counters[0] += 1
        counters[1] += int(acceptance.get("passed") is True)
        utility = outcome.get("utility")
        counters[2] += int(
            type(utility) in (int, float) and math.isfinite(utility) and utility > 0
        )
        counters[3] += int(utility is None)
        counters[4] += int(settlement.get("basis") == "included_capacity")
        amount = settlement.get("total_micro_usd")
        counters[5] += int(type(amount) is int and amount > 0)
    return [[provider, *grouped[provider]] for provider in sorted(grouped)]


def check(root=None):
    root = Path(root or Path(__file__).resolve().parent)
    derivation = json.loads((root / "derivation.json").read_bytes())
    for relative, expected in derivation["derived_file_sha256"].items():
        path = (root / relative).resolve()
        require(
            path.is_relative_to(root.resolve()) and sha(path) == expected,
            "Published projection identity changed: " + relative,
        )
    projection = json.loads((root / "action-task-projection.json").read_bytes())
    domain = json.loads((root / "question-and-check.json").read_bytes())
    require(
        len(projection["action_tasks"]) == 40, "Expected exactly 40 recent task rows"
    )
    require(
        projection["source_sha256"]
        == domain["original_raw_response_sha256"]
        == derivation["original_raw_response_sha256"],
        "Raw source identity differs",
    )
    expected = group_rows(projection["action_tasks"])
    require(expected == domain["expected_rows"], "Frozen independent grouping differs")
    registered = root / "registered"
    registry_path = registered / "registry.json"
    require(
        sha(registry_path) == domain["original_registry_file_sha256"],
        "Original registered source identity differs",
    )
    registry = json.loads(registry_path.read_bytes())
    require(
        registry["schema"] == "nuria.analyst-workspace.v1"
        and registry["revision"] == "sha256:" + projection["source_sha256"]
        and set(registry["databases"]) == {"work"},
        "Registered source scope differs",
    )
    entry = registry["databases"]["work"]
    for record in [entry["database"], *entry["documents"].values()]:
        path = (registered / record["path"]).resolve()
        require(
            path.is_relative_to(registered.resolve()) and sha(path) == record["sha256"],
            "Registered file differs",
        )
    database = registered / entry["database"]["path"]
    before = sha(database)
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        registered_rows = {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT task_id,provider,delivery_passed,settlement_basis,settled_micro_usd,"
                "outcome_utility FROM task_outcomes"
            )
        }
        require(len(registered_rows) == 40, "Registered row coverage differs")
        for row in projection["action_tasks"]:
            passed = row["acceptance"]["passed"]
            require(
                passed is None or type(passed) is bool, "Unknown delivery flag type"
            )
            require(
                registered_rows[row["id"]]
                == (
                    row["provider"],
                    None if passed is None else int(passed),
                    row["settlement"]["basis"],
                    row["settlement"]["total_micro_usd"],
                    row["outcome"]["utility"],
                ),
                "Projected task differs from original registered database",
            )
        sql = """SELECT provider, COUNT(*),
          SUM(CASE WHEN delivery_passed=1 THEN 1 ELSE 0 END),
          SUM(CASE WHEN outcome_utility>0 THEN 1 ELSE 0 END),
          SUM(CASE WHEN outcome_utility IS NULL THEN 1 ELSE 0 END),
          SUM(CASE WHEN settlement_basis='included_capacity' THEN 1 ELSE 0 END),
          SUM(CASE WHEN settled_micro_usd>0 THEN 1 ELSE 0 END)
          FROM task_outcomes GROUP BY provider ORDER BY provider"""
        sql_rows = [list(row) for row in connection.execute(sql)]
        semantic_nulls = connection.execute(
            "SELECT COUNT(*) FROM task_outcomes WHERE semantic_accepted IS NULL"
        ).fetchone()[0]
    finally:
        connection.close()
    require(sha(database) == before, "Read-only check changed source bytes")
    require(sql_rows == expected, "Registered SQL and direct JSON grouping differ")
    return {
        "schema": "nuria.recent-work-projection-check.v1",
        "passed": True,
        "task_rows": 40,
        "expected_rows": expected,
        "semantic_acceptance_unknown_rows": semantic_nulls,
        "original_database_sha256": before,
        "scope": "Finite recent server-reported task grouping. The delivery flag is "
        "the original acceptance.passed field; it is not a new independent "
        "semantic grade. Included capacity is not an external purchase. "
        "No chain-payment verification, lifetime performance or neural "
        "advantage is established.",
    }


if __name__ == "__main__":
    print(json.dumps(check(), allow_nan=False, indent=2))

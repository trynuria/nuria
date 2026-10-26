"""A finite, generated-ledger course for executable analytical report reuse.

This exercises actual SQLite queries and checked memory. The origin SQL is a
declared conventional program, not model-generated reasoning. Challenge runs
require a caller-pinned prospective protocol and implementation hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from contextlib import closing
from pathlib import Path

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace
from cognition.procedural_memory import ProceduralMemory, digest

COURSE_SCHEMA = "nuria.analytical-skill-course.v1"
SOURCE_ID = "generated-ledger-report-v1"
CHECKER_ID = "independent-ledger-grouping-v1"
REPORT_ID = "provider-outcomes-seven-columns-v1"
QUERY = """SELECT provider, COUNT(*) AS tasks,
SUM(CASE WHEN delivered=1 THEN 1 ELSE 0 END) AS delivered,
SUM(CASE WHEN utility>0 THEN 1 ELSE 0 END) AS positive_outcomes,
SUM(CASE WHEN utility IS NULL THEN 1 ELSE 0 END) AS unknown_outcomes,
SUM(CASE WHEN included=1 THEN 1 ELSE 0 END) AS included_capacity,
SUM(CASE WHEN paid_micro_usd>0 THEN 1 ELSE 0 END) AS positive_payments
FROM task_outcomes GROUP BY provider ORDER BY provider"""
TEMPLATE_QUERY = """SELECT provider, COUNT(*),
COUNT(CASE WHEN delivered=1 THEN 1 END),
COUNT(CASE WHEN utility>0 THEN 1 END),
COUNT(*)-COUNT(utility),
COUNT(CASE WHEN included=1 THEN 1 END),
COUNT(CASE WHEN paid_micro_usd>0 THEN 1 END)
FROM task_outcomes GROUP BY provider ORDER BY provider"""
CHALLENGE_VARIANTS = (
    "empty",
    "nulls",
    "duplicate_rows",
    "new_providers",
    "delivery_boundary",
    "utility_boundary",
    "payment_boundary",
    "permutation",
    "sparse",
    "duplicate_ids",
    "mixed",
    "mixed",
)
VARIANTS = frozenset(CHALLENGE_VARIANTS) | {"origin", "standard"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generated_rows(seed, variant):
    """Public synthetic generator; no production data or private task bodies."""
    if variant not in VARIANTS or type(seed) is not int:
        raise ValueError("Unknown generated-ledger case")
    rng = random.Random(seed)
    if variant == "empty":
        return []
    if variant == "origin":
        return [
            [1, "alpha", 1, 2, 1, 0, None],
            [2, "alpha", 0, -1, 1, 0, None],
            [3, "beta", 1, None, 0, 10, None],
        ]
    rows = [
        [
            i,
            rng.choice(["alpha", "beta", "gamma"]),
            rng.choice([None, 0, 1]),
            rng.choice([None, -2, 0, 3]),
            rng.choice([None, 0, 1]),
            rng.choice([None, 0, 1, 100]),
            None,
        ]
        for i in range(1, 25)
    ]
    if variant == "nulls":
        rows.extend(
            [[25, None, None, None, None, None, None], [26, None, 1, 0, 1, 0, None]]
        )
    elif variant == "duplicate_rows":
        rows += [row[:] for row in rows[:6]]
    elif variant == "new_providers":
        for i, row in enumerate(rows):
            row[1] = ["new/one", "new two", "Ω"][i % 3]
    elif variant == "delivery_boundary":
        for i, row in enumerate(rows):
            row[2] = [None, -1, 0, 1, 2, 3][i % 6]
    elif variant == "utility_boundary":
        for i, row in enumerate(rows):
            row[3] = [None, -1, 0, 1][i % 4]
    elif variant == "payment_boundary":
        for i, row in enumerate(rows):
            row[5] = [None, -1, 0, 1][i % 4]
    elif variant == "permutation":
        rng.shuffle(rows)
    elif variant == "sparse":
        rows = [[1, "sparse", None, None, None, None, None]]
    elif variant == "duplicate_ids":
        for row in rows:
            row[0] = 1
    elif variant == "mixed":
        rows += [[1, None, 2, -1, 2, -1, None], [1, "new/one", 1, 1, 1, 1, None]]
    return rows


def expected_rows(rows):
    """Independent Python truth, with SQL NULL and exact-flag semantics."""
    groups = {}
    for _, provider, delivered, utility, included, paid, _ in rows:
        counts = groups.setdefault(provider, [0] * 6)
        counts[0] += 1
        counts[1] += delivered == 1
        counts[2] += utility is not None and utility > 0
        counts[3] += utility is None
        counts[4] += included == 1
        counts[5] += paid is not None and paid > 0
    return [
        [key, *groups[key]]
        for key in sorted(groups, key=lambda v: (v is not None, v or ""))
    ]


def make_revision(root, seed, variant):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    rows = generated_rows(seed, variant)
    (root / "input.json").write_text(
        canonical({"seed": seed, "variant": variant, "rows": rows}) + "\n"
    )
    database = root / "ledger.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE task_outcomes(id INTEGER, provider TEXT, delivered INTEGER, utility INTEGER, included INTEGER, paid_micro_usd INTEGER, semantic_accepted INTEGER)"
        )
        connection.executemany("INSERT INTO task_outcomes VALUES(?,?,?,?,?,?,?)", rows)
    documents = {}
    content = {
        "business": "Count rows, not unique IDs. Delivery and included capacity mean exactly integer 1. Positive utility/payment means greater than zero. NULL utility is unknown. Semantic acceptance is a distinct unknown field and must not be inferred from delivery or utility. Group all providers including NULL.",
        "columns": "id, provider, delivered, utility, included, paid_micro_usd, semantic_accepted. paid_micro_usd is an integer micro-USD quantity; no currency conversion is inferred.",
        "schema": "task_outcomes(id INTEGER, provider TEXT, delivered INTEGER, utility INTEGER, included INTEGER, paid_micro_usd INTEGER, semantic_accepted INTEGER)",
    }
    for name, text in content.items():
        file = root / (name + ".txt")
        file.write_text(text)
        documents[name] = {"path": file.name, "sha256": sha(file)}
    revision = hashlib.sha256(
        canonical(
            {"seed": seed, "variant": variant, "database": sha(database)}
        ).encode()
    ).hexdigest()
    manifest = {
        "schema": SCHEMA,
        "revision": "sha256:" + revision,
        "databases": {
            "ledger": {
                "database": {"path": database.name, "sha256": sha(database)},
                "documents": documents,
            }
        },
    }
    registry = root / "registry.json"
    registry.write_text(canonical(manifest) + "\n")
    return registry, rows


def checked(receipt, rows):
    return (
        receipt.get("complete") is True
        and receipt.get("status") == "ok"
        and len(receipt.get("columns", [])) == 7
        and receipt.get("rows") == expected_rows(rows)
    )


def accepted_origin(output, query=QUERY):
    registry, rows = make_revision(Path(output) / "origin", 17, "origin")
    workspace = AnalystWorkspace(registry)
    receipt = workspace.call("ledger", "query", {"sql": query})
    memory = ProceduralMemory(
        Path(output) / "memory.sqlite3",
        source_id=SOURCE_ID,
        model_id="declared-conventional-query-v1",
        tool_version=SCHEMA,
    )
    episode = memory.begin(
        "origin-report",
        "Return the declared seven-column provider report",
        digest({"registry": workspace.registry_sha256}),
    )
    memory.record_action(
        episode,
        "origin-query",
        "query",
        query,
        receipt["complete"] and receipt["status"] == "ok",
        receipt["cost"]["vm_progress_callbacks"],
        receipt["result_sha256"],
    )
    memory.propose(episode, query)
    outcome = memory.settle(
        episode,
        verify_terminal=lambda request: {
            "accepted": checked(receipt, rows),
            "evidence_sha256": digest(
                {
                    "request": request,
                    "rows": expected_rows(rows),
                    "receipt": receipt["result_sha256"],
                }
            ),
            "checker_id": CHECKER_ID,
        },
    )
    return registry, memory, episode, receipt, outcome


def run_course(output, cases, *, phase, protocol_sha256=None, protocol_bytes=None):
    from cognition.analytical_skills import AnalyticalSkills

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    if protocol_bytes is not None:
        (output / "prospective-protocol.json").write_bytes(protocol_bytes)
    registry, memory, episode, origin_receipt, origin_outcome = accepted_origin(output)
    bank = AnalyticalSkills(
        output / "skills.sqlite3", source_id=SOURCE_ID, checker_id=CHECKER_ID
    )
    try:
        promoted = bank.promote(
            memory, episode, registry, "ledger", REPORT_ID, origin_receipt
        )
        records = []
        for index, case in enumerate(cases):
            fresh_registry, rows = make_revision(
                output / f"revision-{index:02d}", case["seed"], case["variant"]
            )
            workspace = AnalystWorkspace(fresh_registry)
            execution = bank.execute(
                promoted["skill_id"],
                job_id=f"report-{index:02d}",
                registry=fresh_registry,
                db_id="ledger",
                report_id=REPORT_ID,
            )
            qualification = bank.settle(
                f"report-{index:02d}",
                verify_result=lambda request: {
                    "accepted": checked(request["receipt"], rows),
                    "evidence_sha256": digest(
                        {
                            "execution": request["execution_sha256"],
                            "truth": expected_rows(rows),
                        }
                    ),
                    "checker_id": CHECKER_ID,
                },
            )
            controls = {}
            for name, query in [
                ("exact-query-cache", QUERY),
                ("conventional-template", TEMPLATE_QUERY),
            ]:
                receipt = workspace.call("ledger", "query", {"sql": query})
                controls[name] = {
                    "sql": query,
                    "receipt": receipt,
                    "accepted": checked(receipt, rows),
                }
            records.append(
                {
                    "case": case,
                    "registry": str(fresh_registry.relative_to(output)),
                    "input": str(
                        (fresh_registry.parent / "input.json").relative_to(output)
                    ),
                    "expected": expected_rows(rows),
                    "skill_execution": execution,
                    "skill_qualification": qualification,
                    "controls": controls,
                }
            )
        costs = {
            "qualified-skill": {
                "query_calls": len(records) * 2,
                "report_query_calls": len(records),
                "schema_query_calls": len(records),
            },
            "exact-query-cache": {
                "query_calls": len(records),
                "report_query_calls": len(records),
                "schema_query_calls": 0,
            },
            "conventional-template": {
                "query_calls": len(records),
                "report_query_calls": len(records),
                "schema_query_calls": 0,
            },
        }
        results = {
            "schema": COURSE_SCHEMA,
            "phase": phase,
            "protocol_sha256": protocol_sha256,
            "origin": {
                "episode_id": episode,
                "receipt": origin_receipt,
                "outcome": origin_outcome,
                "promoted": promoted,
            },
            "records": records,
            "observed_call_counts": costs,
            "origin_report_query_calls": 1,
            "promotion_schema_query_calls": 1,
            "model_calls": 0,
            "model_completion_tokens": 0,
            "scope": "Actual SQLite execution of a conventional accepted query reused as an exact report skill on generated ledgers; no model reasoning, neural advantage, production activity or general SQL synthesis is tested.",
        }
        (output / "results.json").write_text(canonical(results) + "\n")
        (output / "memory-events.json").write_text(canonical(memory.export()) + "\n")
    finally:
        bank.close()
        memory.close()
    files = {
        str(f.relative_to(output)): sha(f)
        for f in sorted(output.rglob("*"))
        if f.is_file()
    }
    manifest = {
        "schema": COURSE_SCHEMA,
        "phase": phase,
        "protocol_sha256": protocol_sha256,
        "files": files,
    }
    (output / "manifest.json").write_text(canonical(manifest) + "\n")
    return results


def validate_protocol(path, expected_sha256, repository):
    path, repository = Path(path), Path(repository)
    if sha(path) != expected_sha256:
        raise ValueError("Prospective protocol identity changed")
    protocol = json.loads(path.read_text())
    if protocol.get("schema") != COURSE_SCHEMA or protocol.get("phase") != "challenge":
        raise ValueError("Expected a challenge protocol")
    required = {
        "cognition/analytical_skills.py",
        "cognition/analyst_workspace.py",
        "cognition/procedural_memory.py",
        "scripts/evaluate_analytical_skills.py",
        "scripts/verify_analytical_skills.py",
    }
    if not required <= set(protocol.get("implementation_sha256", {})):
        raise ValueError("Prospective protocol omits required implementations")
    for name, identity in protocol["implementation_sha256"].items():
        file = (repository / name).resolve()
        if not file.is_relative_to(repository.resolve()) or sha(file) != identity:
            raise ValueError("Frozen implementation identity changed")
    if protocol["cases"] != [
        {"seed": 420001 + i, "variant": variant}
        for i, variant in enumerate(CHALLENGE_VARIANTS)
    ]:
        raise ValueError("Challenge course differs from the declared full matrix")
    return protocol


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("development", "challenge"), required=True)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--protocol-sha256")
    args = parser.parse_args()
    if args.phase == "challenge":
        if args.protocol is None or args.protocol_sha256 is None:
            parser.error("Challenge execution requires the pinned prospective protocol")
        protocol = validate_protocol(
            args.protocol, args.protocol_sha256, Path(__file__).resolve().parents[1]
        )
        cases = protocol["cases"]
    else:
        cases = [
            {"seed": 31, "variant": "standard"},
            {"seed": 32, "variant": "delivery_boundary"},
        ]
    result = run_course(
        args.output,
        cases,
        phase=args.phase,
        protocol_sha256=args.protocol_sha256,
        protocol_bytes=args.protocol.read_bytes()
        if args.phase == "challenge"
        else None,
    )
    print(
        json.dumps(
            {"phase": args.phase, "cases": len(result["records"]), "model_calls": 0}
        )
    )


if __name__ == "__main__":
    main()

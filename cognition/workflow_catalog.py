"""Registered local tools for checked operational briefs.

The catalog exposes typed operations, not code execution. Readers are bound to
host-selected immutable snapshots; renderers can only create new Markdown files
inside a supplied artifact directory. Planning units are not money or elapsed
seconds. Reported task feedback does not independently verify a payment.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from cognition.workflow_planner import ToolUnavailable

SCOPE = "Server-reported records; no independent payment or semantic acceptance verification."


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def provider_facts(projection):
    groups = {}
    for row in projection["action_tasks"]:
        group = groups.setdefault(
            row["provider"],
            dict.fromkeys(
                (
                    "rows",
                    "delivered",
                    "delivery_failed",
                    "delivery_unknown",
                    "utility_positive",
                    "utility_nonpositive",
                    "utility_unknown",
                    "recorded_micro_usd",
                    "included_capacity",
                ),
                0,
            ),
        )
        group["rows"] += 1
        passed, utility = row["acceptance"]["passed"], row["outcome"]["utility"]
        group[
            "delivered"
            if passed is True
            else "delivery_failed"
            if passed is False
            else "delivery_unknown"
        ] += 1
        group[
            "utility_unknown"
            if utility is None
            else "utility_positive"
            if utility > 0
            else "utility_nonpositive"
        ] += 1
        group["recorded_micro_usd"] += row["settlement"]["total_micro_usd"]
        group["included_capacity"] += row["settlement"]["basis"] == "included_capacity"
    return {
        "row_count": len(projection["action_tasks"]),
        "semantic_unknown": len(projection["action_tasks"]),
        "providers": [{"provider": key, **groups[key]} for key in sorted(groups)],
    }


def payment_facts(projection):
    rows = projection["action_tasks"]
    facts = {
        "row_count": len(rows),
        "semantic_unknown": len(rows),
        "delivery": {"passed": 0, "failed": 0, "unknown": 0},
        "utility": {"positive": 0, "nonpositive": 0, "unknown": 0},
        "settlement": {
            "included_capacity": 0,
            "reported_external": 0,
            "other": 0,
            "total_recorded_micro_usd": 0,
            "positive_recorded_amounts": 0,
        },
        "independently_verified_payments": 0,
    }
    for row in rows:
        passed, utility = row["acceptance"]["passed"], row["outcome"]["utility"]
        facts["delivery"][
            "passed" if passed is True else "failed" if passed is False else "unknown"
        ] += 1
        facts["utility"][
            "unknown"
            if utility is None
            else "positive"
            if utility > 0
            else "nonpositive"
        ] += 1
        basis, amount = row["settlement"]["basis"], row["settlement"]["total_micro_usd"]
        facts["settlement"][
            basis if basis in ("included_capacity", "reported_external") else "other"
        ] += 1
        facts["settlement"]["total_recorded_micro_usd"] += amount
        facts["settlement"]["positive_recorded_amounts"] += amount > 0
    return facts


def audit_facts(projection, policy):
    eligible = [
        row
        for row in projection["action_tasks"]
        if row["acceptance"]["passed"] is False or row["outcome"]["utility"] is None
    ]
    eligible.sort(key=lambda row: (row["acceptance"]["passed"] is not False, row["id"]))
    ids, count = [row["id"] for row in eligible], policy["maximum_tasks"]
    return {
        "row_count": len(projection["action_tasks"]),
        "semantic_unknown": len(projection["action_tasks"]),
        "policy": policy,
        "eligible_ids": ids,
        "selected_ids": ids[:count],
        "deferred_ids": ids[count:],
        "work_units": len(ids[:count]),
        "execution": "planned_only",
        "usd_spent": 0,
    }


def catalog_view(catalog):
    """Exclude handlers and filesystem bindings from model-visible metadata."""
    return {
        name: {key: value for key, value in operation.items() if key != "handler"}
        for name, operation in catalog.items()
    }


def build_catalog(*, projection, json_path, sqlite_path, output_dir, source_spec):
    """Bind operations once. A generated plan cannot supply or replace paths."""
    expected = fingerprint(projection)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)

    def check_spec(inputs):
        if inputs["source"] != source_spec:
            raise ValueError("Source binding differs from the enrolled snapshot")

    def read_json(inputs, arguments):
        check_spec(inputs)
        # Absence is a real I/O failure, not a pre-filled model observation.
        try:
            value = json.loads(Path(json_path).read_bytes())
        except FileNotFoundError as error:
            raise ToolUnavailable(
                "The enrolled JSON snapshot is unavailable"
            ) from error
        if fingerprint(value) != expected:
            raise ValueError("JSON snapshot identity changed")
        return {"rows": value}

    def read_sqlite(inputs, arguments):
        check_spec(inputs)
        uri = "file:" + quote(str(Path(sqlite_path).resolve(strict=True)), safe="/")
        with closing(sqlite3.connect(uri + "?mode=ro", uri=True)) as db:
            rows = db.execute(
                "SELECT task_id,provider,delivery_passed,settlement_basis,"
                "settled_micro_usd,outcome_utility FROM task_outcomes"
            ).fetchall()
        if any(
            row[2] is not None and (type(row[2]) is not int or row[2] not in (0, 1))
            for row in rows
        ):
            raise ValueError("SQLite delivery flag is not boolean or unknown")
        mapped = {
            row[0]: {
                "id": row[0],
                "provider": row[1],
                "acceptance": {"passed": None if row[2] is None else bool(row[2])},
                "settlement": {"basis": row[3], "total_micro_usd": row[4]},
                "outcome": {"utility": row[5]},
            }
            for row in rows
        }
        # SQLite REAL affinity can represent an enrolled JSON integer utility
        # as 1.0. Verify every row/value before retaining the source's original
        # JSON representation and metadata. No unverified row is substituted.
        if len(rows) != len(mapped) or len(mapped) != len(projection["action_tasks"]):
            raise ValueError("SQLite mirror differs from the enrolled projection")
        for row in projection["action_tasks"]:
            observed = mapped.get(row["id"])
            if (
                observed != row
                or type(observed["settlement"]["total_micro_usd"]) is not int
            ):
                raise ValueError("SQLite mirror differs from the enrolled projection")
            utility = observed["outcome"]["utility"]
            if utility is not None and type(utility) not in (int, float):
                raise ValueError("SQLite utility is not numeric or unknown")
        return {"rows": projection}

    def render(kind):
        def save(inputs, arguments):
            facts = inputs["evidence"]
            text = (
                "# "
                + kind.replace("_", " ").capitalize()
                + "\n\n"
                + SCOPE
                + "\n\nSource: `"
                + expected
                + "`\n\n```json\n"
                + json.dumps(facts, indent=2, sort_keys=True, allow_nan=False)
                + "\n```\n"
            )
            path = output_dir / (kind + ".md")
            # Exclusive creation retains an earlier artifact on duplicate execution.
            with path.open("x", encoding="utf-8") as stream:
                stream.write(text)
            return {
                "artifact": {
                    "kind": kind,
                    "path": str(path),
                    "source_sha256": expected,
                    "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            }

        return save

    def op(inputs, outputs, work, description, handler):
        return {
            "inputs": inputs,
            "outputs": outputs,
            "arguments": {},
            "work_units": work,
            "deadline_units": work,
            "description": description,
            "handler": handler,
        }

    catalog = {
        "read_json": op(
            {"source": "source_spec"},
            {"rows": "work_rows"},
            1,
            "Read the enrolled JSON snapshot; may fail if unavailable.",
            read_json,
        ),
        "read_sqlite": op(
            {"source": "source_spec"},
            {"rows": "work_rows"},
            3,
            "Read the independently hash-matched SQLite mirror.",
            read_sqlite,
        ),
        "summarize_provider": op(
            {"rows": "work_rows"},
            {"evidence": "provider_evidence"},
            2,
            "Count delivery and utility separately by provider; preserve unknowns.",
            lambda inputs, args: {"evidence": provider_facts(inputs["rows"])},
        ),
        "separate_delivery_payment": op(
            {"rows": "work_rows"},
            {"evidence": "payment_evidence"},
            2,
            "Separate delivery, utility and reported settlement without chain-proof claims.",
            lambda inputs, args: {"evidence": payment_facts(inputs["rows"])},
        ),
        "plan_local_audits": op(
            {"rows": "work_rows", "policy": "audit_policy"},
            {"evidence": "audit_plan"},
            3,
            "Plan bounded local rechecks, failure-first then full task ID. Does not run them.",
            lambda inputs, args: {
                "evidence": audit_facts(inputs["rows"], inputs["policy"])
            },
        ),
    }
    for name, evidence, kind in (
        ("render_provider", "provider_evidence", "provider_brief"),
        ("render_payment", "payment_evidence", "payment_brief"),
        ("render_audits", "audit_plan", "audit_brief"),
    ):
        catalog[name] = op(
            {"evidence": evidence},
            {"artifact": kind},
            1,
            "Save a new checked-source Markdown artifact; cannot overwrite.",
            render(kind),
        )
    return catalog

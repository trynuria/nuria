"""Replay packaged analytical results without hosted inference or credentials.

The independent SQL/comparison engine is retained verbatim in verify.py. This
wrapper checks a finite path-redacted evidence projection and exact archive
inventory. It does not claim to reconstruct omitted private protocol bytes.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import tarfile
from pathlib import Path

import verify as core


def require(condition, code):
    core.require(condition, code)


def extract(package_dir, workspace):
    package_dir, workspace = Path(package_dir), Path(workspace)
    package = core.read(package_dir / "package.json")
    require(package["archive"] == "evidence.tar.gz", "archive_filename")
    compressed = core.raw(package_dir / package["archive"], 5 * 1024 * 1024)
    require(
        len(compressed) == package["archive_bytes"]
        and core.sha(compressed) == package["archive_sha256"],
        "archive_identity",
    )
    expected = {item["path"]: item for item in package["members"]}
    require(
        len(expected) == package["member_count"] <= 500
        and package["expanded_bytes"] <= 16 * 1024 * 1024,
        "archive_inventory_bound",
    )
    workspace.mkdir(mode=0o700, parents=False, exist_ok=False)
    # Bound decompression before asking tarfile to walk headers or seek payloads.
    with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
        unpacked = stream.read(18 * 1024 * 1024 + 1)
    require(len(unpacked) <= 18 * 1024 * 1024, "archive_decompression_bound")
    with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as archive:
        members = archive.getmembers()
        require(
            len(members) == len(expected)
            and {item.name for item in members} == set(expected),
            "archive_exact_inventory",
        )
        require(
            sum(item.size for item in members) == package["expanded_bytes"],
            "archive_expanded_size",
        )
        for member in members:
            relative = Path(member.name)
            require(
                not relative.is_absolute()
                and ".." not in relative.parts
                and member.isfile()
                and not member.issym()
                and not member.islnk(),
                "archive_regular_member",
            )
            specification = expected[member.name]
            require(
                member.size == specification["bytes"] <= 8 * 1024 * 1024,
                "archive_member_size",
            )
            reader = archive.extractfile(member)
            require(reader is not None, "archive_member_missing")
            data = reader.read(member.size + 1)
            require(
                len(data) == member.size and core.sha(data) == specification["sha256"],
                "archive_member_hash",
            )
            target = workspace / relative
            require(
                target.resolve().is_relative_to(workspace.resolve()),
                "archive_member_escape",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
    manifest = core.read(workspace / "manifest.json")
    require(
        {item["path"]: item for item in manifest["files"]}
        == {name: item for name, item in expected.items() if name != "manifest.json"},
        "inner_manifest_binding",
    )
    return package


def database_context(root):
    path = root / "inputs/registry.json"
    registry = core.read(path)
    paths = {}
    for identity, record in registry["databases"].items():
        for item in [record["database"], *record["documents"].values()]:
            source = (path.parent / item["path"]).resolve()
            require(source.is_relative_to(root.resolve()), "registered_source_escape")
            require(
                core.sha(core.raw(source, 5 * 1024 * 1024)) == item["sha256"],
                "registered_source_hash",
            )
        paths[identity] = (path.parent / record["database"]["path"]).resolve()
    return registry, paths


def source_identity(registry, db_id):
    return {
        "dataset_revision": registry["revision"],
        "db_id": db_id,
        "db_sha256": registry["databases"][db_id]["database"]["sha256"],
        "registry_sha256": core.digest(registry, ascii=False),
    }


def checked_agent(full, row, protocol, registry, db_id):
    require(
        all(
            full[key] == row[key]
            for key in ("phase", "method", "task_id", "attempted", "usage")
        ),
        "committed_report_binding",
    )
    agent = full["agent"]
    require(
        agent is not None
        and all(agent[key] == value for key, value in row["agent"].items()),
        "compact_agent_binding",
    )
    require(
        agent["result_sha256"]
        == core.digest(
            {key: value for key, value in agent.items() if key != "result_sha256"}
        ),
        "agent_result_hash",
    )
    require(agent["source"] == source_identity(registry, db_id), "agent_source_binding")
    require(
        agent["configuration"]["model_binding"]["model_id"]
        == protocol["model_identity"],
        "agent_model_binding",
    )
    return agent


def query_receipt(agent, sql, protocol, registry, db_id):
    receipt = agent["selected_receipt"]
    source = source_identity(registry, db_id)
    core.check_receipt(
        receipt, source, protocol["source_files"]["cognition/analyst_workspace.py"]
    )
    require(
        receipt["status"] == "ok" and receipt["complete"] is True,
        "own_query_incomplete",
    )
    request = {
        "db_id": db_id,
        "tool": "query",
        "arguments": {"sql": sql},
        "limits": agent["configuration"]["tool_limits"],
    }
    require(
        receipt["request_sha256"] == core.digest(request, ascii=False),
        "own_query_request_binding",
    )
    require(
        any(
            item.get("kind") == "action"
            and item["action"]["tool"] == "query"
            and item["action"]["arguments"] == {"sql": sql}
            for item in agent["trace"]
        ),
        "own_query_action_missing",
    )
    require(
        any(
            item.get("kind") == "tool_receipt" and item["receipt"] == receipt
            for item in agent["trace"]
        ),
        "own_query_receipt_missing",
    )
    return receipt


def check_sources(root, protocol, prefix):
    for relative, expected in protocol["source_files"].items():
        source = (root / prefix / relative).resolve()
        require(
            source.is_relative_to((root / prefix).resolve()), "frozen_source_escape"
        )
        require(
            core.sha(core.raw(source)) == expected,
            "frozen_source_hash",
        )


def primary(root, registry, databases):
    protocol = core.read(root / "protocols/primary-public.json")
    projection = core.read(root / "inputs/primary-projection.json")
    report = core.read(root / "primary/report.json")
    original = core.read(root / "independent-primary/verdict-01.json")
    require(
        report["protocol_sha256"]
        == projection["original_private_protocol_sha256"]
        == original["private_protocol_sha256"],
        "primary_original_protocol_reference",
    )
    require(
        core.sha(core.raw(root / "protocols/primary-public.json"))
        == original["public_protocol_sha256"],
        "primary_public_protocol_pin",
    )
    require(
        core.sha(core.raw(root / "primary/report.json")) == original["report_sha256"],
        "primary_report_pin",
    )
    check_sources(root, protocol, "frozen-primary")
    expected = {
        (phase, item["method"], item["task_id"])
        for phase, items in protocol["expected_jobs"].items()
        for item in items
    }
    rows = report["rows"]
    observed = [(row["phase"], row["method"], row["task_id"]) for row in rows]
    require(
        len(rows) == len(set(observed)) == len(expected) == 54
        and set(observed) == expected,
        "primary_full_denominator",
    )
    selection = core.read(root / "primary/selection.json")
    require(
        selection["count"] == 54
        and {
            (row["phase"], row["method"], row["task_id"])
            for row in selection["selected"]
        }
        == expected,
        "primary_selection_binding",
    )
    tasks = {}
    for row in projection["selected_attempted_rows"]:
        source = (root / row["member"]).resolve()
        require(
            source.is_relative_to((root / "gold/primary").resolve()),
            "selected_gold_escape",
        )
        data = core.raw(source)
        require(core.sha(data) == row["sha256"], "selected_raw_row_hash")
        task = core.decode(data)
        require(task["db_id"] == row["db_id"], "selected_raw_row_database")
        tasks[row["task_id"]] = task
    require(
        set(tasks) == {row["task_id"] for row in rows if row["attempted"]}
        and len(tasks) == 15,
        "attempted_task_body_extent",
    )
    original_outcomes = {
        (row["phase"], row["method"], row["task_id"]): row
        for row in original["outcomes"]
    }
    accepted, submitted, abstentions, replayed, validation = 0, 0, 0, 0, {}
    for row in rows:
        identity = core.artifact_id(row)
        require(row["artifact_id"] == identity, "primary_artifact_id")
        if not row["attempted"]:
            require(
                row["agent"] is None and not row.get("verdict", {}).get("complete"),
                "unexecuted_left_ungraded",
            )
            require(
                not (root / "primary" / (identity + ".committed.json")).exists()
                and not (root / "primary" / (identity + ".verdict.json")).exists(),
                "unexecuted_has_no_terminal_result",
            )
            continue
        full_path = root / "primary" / (identity + ".committed.json")
        full = core.read(full_path)
        task = tasks[row["task_id"]]
        agent = checked_agent(full, row, protocol, registry, task["db_id"])
        require(agent["job_id"] == identity, "primary_job_binding")
        evidence_path = root / "primary" / (identity + ".verdict.json")
        evidence = core.read(evidence_path)
        require(
            evidence["row_sha256"] == core.digest(full)
            and core.digest(evidence) == row["verdict"]["evidence_sha256"],
            "primary_verdict_binding",
        )
        prior = original_outcomes[(row["phase"], row["method"], row["task_id"])]
        require(
            core.sha(core.raw(full_path)) == prior["committed_artifact_sha256"]
            and core.sha(core.raw(evidence_path)) == prior["verdict_artifact_sha256"],
            "primary_independent_artifact_pin",
        )
        sql, db_id = agent["final_sql"], task["db_id"]
        reference = core.execute(databases[db_id], task["query"])
        ordered = core.ordered(task["query"])
        require(
            core.sha(task["query"].encode()) == prior["reference_sql_sha256"],
            "primary_reference_sql_pin",
        )
        retained = evidence["reference"]
        core.check_receipt(
            retained,
            source_identity(registry, db_id),
            protocol["source_files"]["cognition/analyst_workspace.py"],
        )
        require(
            retained["columns"] == reference["columns"]
            and core.equal_rows(
                retained["rows"], reference["rows"], preserve_order=ordered
            ),
            "primary_reference_replay",
        )
        decision = False
        if sql is not None:
            receipt = query_receipt(agent, sql, protocol, registry, db_id)
            fresh = core.execute(databases[db_id], sql)
            require(
                fresh["columns"] == receipt["columns"]
                and core.equal_rows(
                    fresh["rows"], receipt["rows"], preserve_order=core.ordered(sql)
                ),
                "primary_submitted_replay",
            )
            decision = len(fresh["columns"]) == len(
                reference["columns"]
            ) and core.equal_rows(
                fresh["rows"], reference["rows"], preserve_order=ordered
            )
            submitted += 1
        else:
            require(agent["selected_receipt"] is None, "primary_abstention_receipt")
            abstentions += 1
        require(
            decision == row["verdict"]["accepted"] == prior["independent_accepted"],
            "primary_original_label_preserved",
        )
        accepted += decision
        replayed += 1
        if row["phase"] == "validation":
            validation[row["method"]] = validation.get(row["method"], 0) + decision
    require(
        (replayed, submitted, accepted, abstentions) == (32, 31, 31, 1),
        "primary_actual_totals",
    )
    policy = core.read(root / "primary/selected-policy.json")
    require(
        policy["artifact_sha256"]
        == core.digest(
            {key: value for key, value in policy.items() if key != "artifact_sha256"}
        ),
        "primary_policy_hash",
    )
    require(
        validation == {"schema_first": 4, "candidate_1": 4, "candidate_2": 4}
        and policy["selected_policy"] == {"policy_id": "baseline", "suffix": ""}
        and policy["candidate_promoted"] is False,
        "primary_tied_validation",
    )
    return {
        "selected": 54,
        "attempted": 32,
        "accepted": 31,
        "abstentions": 1,
        "unexecuted_ungraded": 22,
        "submitted_queries_replayed": 31,
        "reference_queries_replayed": 32,
        "validation": validation,
        "candidate_promoted": False,
    }


def secondary(root, registry, databases):
    protocol = core.read(root / "protocols/secondary-public.json")
    projection = core.read(root / "inputs/secondary-projection.json")
    report = core.read(root / "secondary/report.json")
    check_sources(root, protocol, "frozen-secondary")
    require(
        report["protocol_sha256"] == projection["original_private_protocol_sha256"],
        "secondary_protocol_reference",
    )
    reserved = core.read(root / "inputs/secondary-reserved.json")
    require(
        core.sha(core.raw(root / "inputs/secondary-reserved.json"))
        == protocol["input_hashes"]["reserved_course"],
        "secondary_course_hash",
    )
    tasks = {row["id"]: row for row in reserved["tasks"]}
    require(
        set(tasks) == set(protocol["selected_task_ids"]) and len(tasks) == 12,
        "secondary_full_cohort",
    )
    oracles = {}
    for record in projection["selected_oracle_rows"]:
        source = (root / record["member"]).resolve()
        require(
            source.is_relative_to((root / "gold/secondary").resolve()),
            "selected_oracle_escape",
        )
        data = core.raw(source)
        require(core.sha(data) == record["sha256"], "secondary_selected_oracle_hash")
        oracles[record["task_id"]] = core.decode(data)
    require(
        set(oracles) == set(tasks) and len(report["rows"]) == 12,
        "secondary_oracle_extent",
    )
    decisions = {}
    for row in report["rows"]:
        task_id = row["task_id"]
        require(
            task_id in tasks and task_id not in decisions and row["attempted"],
            "secondary_selected_row",
        )
        full = core.read(root / "secondary" / (task_id + ".committed.json"))
        db_id = tasks[task_id]["db_id"]
        agent = checked_agent(full, row, protocol, registry, db_id)
        require(
            agent["job_id"] == task_id and row["artifact_id"] == core.artifact_id(row),
            "secondary_job_identity",
        )
        sql = agent["final_sql"]
        require(type(sql) is str, "secondary_submitted_sql")
        receipt = query_receipt(agent, sql, protocol, registry, db_id)
        fresh = core.execute(databases[db_id], sql)
        require(
            fresh["columns"] == receipt["columns"]
            and core.equal_rows(
                fresh["rows"], receipt["rows"], preserve_order=core.ordered(sql)
            ),
            "secondary_submitted_replay",
        )
        expected = oracles[task_id]
        require(
            expected["db_id"] == db_id
            and expected["task_id"] == task_id
            and expected["gradeable"],
            "secondary_oracle_task",
        )
        reference = core.execute(databases[db_id], expected["reference_sql"])
        require(
            len(reference["columns"]) == expected["column_count"]
            and core.equal_rows(
                reference["rows"],
                expected["expected_rows"],
                preserve_order=expected["ordered"],
            ),
            "secondary_reference_replay",
        )
        decision = len(fresh["columns"]) == expected[
            "column_count"
        ] and core.equal_rows(
            fresh["rows"], expected["expected_rows"], preserve_order=expected["ordered"]
        )
        require(
            decision == row["verdict"]["accepted"], "secondary_original_label_preserved"
        )
        require(
            row["verdict"]["evidence_sha256"]
            == core.digest(
                {
                    "proposal": agent["proposal_sha256"],
                    "oracle": core.digest(expected),
                    "accepted": decision,
                }
            ),
            "secondary_verdict_hash",
        )
        decisions[task_id] = decision
    require(
        sum(decisions.values()) == report["all_selected"]["accepted"] == 9,
        "secondary_nine_of_twelve",
    )
    return {
        "selected": 12,
        "attempted": 12,
        "accepted": 9,
        "rejected": 3,
        "submitted_queries_replayed": 12,
        "reference_queries_replayed": 12,
        "unchanged_rejected_task_ids": sorted(
            key for key, value in decisions.items() if not value
        ),
        "comparison": "Ties the previously published 9/12 historical schema-first count; descriptive backend substitution, not matched causal evidence.",
    }


def replay(package_dir, workspace):
    package = extract(package_dir, workspace)
    root = Path(workspace)
    registry, databases = database_context(root)
    result = {
        "schema": "nuria.adaptive-reasoning-portable-replay.v1",
        "verified": True,
        "archive_sha256": package["archive_sha256"],
        "primary": primary(root, registry, databases),
        "model_substitution": secondary(root, registry, databases),
        "model_calls": 0,
        "provider_billing_verified": False,
        "private_protocols_reconstructed": False,
    }
    for identity, path in databases.items():
        require(
            core.sha(core.raw(path, 5 * 1024 * 1024))
            == registry["databases"][identity]["database"]["sha256"],
            "database_unchanged_after_replay",
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.package, args.workspace)
    with args.output.open("xb") as stream:
        stream.write(
            (
                json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
            ).encode()
        )
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

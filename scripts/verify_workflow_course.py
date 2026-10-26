"""Verify retained workflow evidence and optionally replay it without a model.

Source hashes bind the published implementation. Historical handler fingerprints
are runtime-local code-object identities and are not portable attestations. Replay
uses registered local handlers, never a provider, credentials or generated code.
It writes only to a new caller-selected workspace; original evidence is read-only.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from cognition.workflow_catalog import build_catalog, fingerprint
from cognition.workflow_planner import (
    digest,
    execute_plan,
    validate_plan,
)
from scripts.check_workflow_artifact import verify_artifact

VERSION = "nuria.workflow-reasoning-course.v1"
METHODS = ("greedy", "exact", "model")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON field")
        result[key] = value
    return result


def decode(raw):
    value = json.loads(raw, object_pairs_hook=unique_fields)
    digest(value)  # Reject non-finite numbers, including JSON's permissive NaN.
    return value


def read(path):
    require(Path(path).stat().st_size <= 8 * 1024**2, "Evidence file exceeds bound")
    return decode(Path(path).read_bytes())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def enrolled_path(root, relative):
    require(type(relative) is str, "An enrolled path must be text")
    path = Path(relative)
    require(
        not path.is_absolute() and ".." not in path.parts, "Enrolled path escapes root"
    )
    target = root / path
    require(
        target.resolve(strict=True).is_relative_to(root), "Enrolled path escapes root"
    )
    require(
        all(
            not item.is_symlink() for item in (target, *target.parents) if item != root
        ),
        "Symlink evidence is unsupported",
    )
    return target


def check_execution(record, plan, validated, case, sources, catalog):
    """Check every historical receipt and artifact hash before replay."""
    identity = record["execution_sha256"]
    payload = {key: value for key, value in record.items() if key != "execution_sha256"}
    require(identity == digest(payload), "Execution receipt hash differs")
    for key in (
        "case_id",
        "source_sha256",
        "plan_sha256",
        "order",
        "work_units",
        "deadline_units",
        "goal_type",
    ):
        require(record[key] == validated[key], "Execution/plan binding differs: " + key)
    nodes = {node["id"]: node for node in plan["nodes"]}
    for key, artifact in record["artifacts"].items():
        payload = {
            name: value for name, value in artifact.items() if name != "artifact_sha256"
        }
        require(
            artifact["artifact_sha256"] == digest(payload),
            "Artifact record hash differs",
        )
        require(
            key == artifact["node"] + "." + artifact["port"],
            "Artifact port binding differs",
        )
        require(
            artifact["case_id"] == case["id"]
            and artifact["source_sha256"] == validated["source_sha256"]
            and artifact["execution_id"] == record["execution_id"],
            "Artifact provenance differs",
        )
    require(
        [row["node"] for row in record["trace"]]
        == validated["order"][: len(record["trace"])],
        "Execution trace order differs",
    )
    for event in record["trace"]:
        node = nodes[event["node"]]
        inputs = {
            port: sources[ref["source"]]["value"]
            if "source" in ref
            else record["artifacts"][ref["node"] + "." + ref["port"]]["value"]
            for port, ref in node["inputs"].items()
        }
        require(
            event["op"] == node["op"]
            and event["arguments_sha256"] == digest(node["arguments"])
            and event["input_sha256"] == digest(inputs),
            "Trace operation/argument binding differs",
        )
        if event["status"] == "completed":
            outputs = {
                port: record["artifacts"][node["id"] + "." + port]["value"]
                for port in catalog[node["op"]]["outputs"]
            }
            require(
                event["output_sha256"] == digest(outputs), "Trace output hash differs"
            )
        require(
            type(event["wall_ms"]) in (int, float) and event["wall_ms"] >= 0,
            "Invalid measured trace duration",
        )
    output = plan["output"]
    if record["final_artifact"] is not None:
        require(
            record["final_artifact"]
            == record["artifacts"][output["node"] + "." + output["port"]],
            "Final artifact binding differs",
        )


def semantic_receipt(record):
    """Exclude only measured time, runtime-local hashes and new output paths."""
    result = {
        key: record[key]
        for key in (
            "status",
            "accepted",
            "charged_work_units",
            "charged_deadline_units",
        )
    }
    result["trace"] = [
        {key: row[key] for key in ("node", "op", "status", "error_type") if key in row}
        for row in record["trace"]
    ]
    result["artifacts"] = {}
    for name, artifact in record["artifacts"].items():
        value = copy.deepcopy(artifact["value"])
        if type(value) is dict and set(value) == {
            "kind",
            "path",
            "source_sha256",
            "content_sha256",
        }:
            value.pop("path")
        result["artifacts"][name] = {"type": artifact["type"], "value": value}
    return result


def verify_course(repository, results, *, replay_workspace, protocol_path=None):
    """Return compact verification evidence; raise on any changed evidence.

    This verifies local evidence, not provider authenticity or actual billing.
    Replay executes all retained proposals without model calls in a new directory.
    """
    root, package = (
        Path(repository).resolve(strict=True),
        Path(results).resolve(strict=True),
    )
    protocol_file = package / "protocol.json"
    protocol = read(protocol_file)
    external = (
        Path(protocol_path)
        if protocol_path
        else root / "research/workflow-reasoning/protocol-02.json"
    )
    require(
        sha(external) == sha(protocol_file),
        "Published protocol differs from packaged protocol",
    )
    report = read(package / "report.json")
    require(
        protocol["version"] == report["version"] == VERSION, "Course version differs"
    )
    require(
        report["protocol_sha256"] == sha(protocol_file),
        "Report protocol identity differs",
    )
    require(
        protocol["methods"] == list(METHODS) and len(protocol["cases"]) == 6,
        "The full six-case three-method course is required",
    )
    cases = {case["id"]: case for case in protocol["cases"]}
    require(len(cases) == 6, "Duplicate course case")
    for name, identity in protocol["source_hashes"].items():
        require(
            sha(enrolled_path(root, name)) == identity,
            "Frozen source closure differs: " + name,
        )
    mirror = enrolled_path(root, protocol["mirror"]["path"])
    require(sha(mirror) == protocol["mirror"]["sha256"], "Mirror identity differs")
    expected = [
        (case["id"], method) for case in protocol["cases"] for method in METHODS
    ]
    jobs = report["jobs"]
    require(
        protocol["selected_jobs"] == 18 and len(jobs) == 18,
        "The complete selected denominator must remain eighteen",
    )
    require(
        [(job["case_id"], job["method"]) for job in jobs] == expected,
        "Job selection/order differs",
    )
    selected = read(package / "selected-jobs.json")
    require(
        [(job["case_id"], job["method"]) for job in selected] == expected,
        "Original selected-job bindings differ",
    )
    require(
        report["runtime_model"] == protocol["model_configuration"],
        "Runtime model contract differs",
    )
    workspace = Path(replay_workspace).absolute()
    require(
        not workspace.resolve().is_relative_to(package),
        "Replay cannot write inside evidence",
    )
    workspace.mkdir(mode=0o700, parents=True, exist_ok=False)
    calls = tokens = artifact_count = proposals = replays = saved = 0
    summaries = {
        method: {"selected": 6, "attempted": 0, "accepted": 0} for method in METHODS
    }
    for index, job in enumerate(jobs, 1):
        case = cases[job["case_id"]]
        if not job["attempted"]:
            require(job["result"] is None, "Unattempted job has a result")
            continue
        require(
            read(package / f"job-{index:02d}.json") == job,
            "Job file/report binding differs",
        )
        summaries[job["method"]]["attempted"] += 1
        result = job["result"]
        require(
            job["error_type"] is None and result is not None,
            "Interrupted jobs require separate reconciliation before portable replay",
        )
        require(
            result["case_id"] == case["id"] and result["method"] == job["method"],
            "Result job identity differs",
        )
        source_path = enrolled_path(root, case["source_path"])
        require(
            sha(source_path) == case["source_file_sha256"], "Case source file differs"
        )
        projection = read(source_path)
        require(
            fingerprint(projection) == case["projection_sha256"],
            "Case projection identity differs",
        )
        spec = {
            "name": "reported_task_snapshot",
            "projection_sha256": fingerprint(projection),
            "scope": case["scope"],
        }
        sources = {
            "snapshot": {"type": "source_spec", "value": spec},
            "audit_policy": {"type": "audit_policy", "value": case["audit_policy"]},
        }
        directory = workspace / "artifacts" / job["method"] / case["id"]
        catalog = build_catalog(
            projection=projection,
            json_path=workspace / "injected-missing-primary.json"
            if case["injected_missing_primary"]
            else source_path,
            sqlite_path=mirror,
            output_dir=directory,
            source_spec=spec,
        )
        limits = dict(
            catalog=catalog,
            case_id=case["id"],
            sources=sources,
            goal_type=case["goal_type"],
            maximum_work=case["maximum_work"],
            deadline_units=case["deadline_units"],
        )
        log, historical_log = {}, result["execution_log"]
        require(
            len(result["attempts"]) <= protocol["limits"]["maximum_calls_per_case"],
            "Case attempt ceiling differs",
        )
        for attempt_index, attempt in enumerate(result["attempts"], 1):
            proposals += 1
            historical = attempt["execution"]
            try:
                plan = decode(attempt["proposal"])
                validated = validate_plan(plan, **limits)
                if historical is not None:
                    check_execution(historical, plan, validated, case, sources, catalog)
                    require(
                        historical_log[historical["execution_id"]] == historical,
                        "Retained execution log differs",
                    )
                    artifact_count += len(historical["artifacts"])
                    if historical["final_artifact"] is not None:
                        verdict = verify_artifact(
                            projection,
                            historical["final_artifact"]["value"],
                            case["goal_type"],
                            case["audit_policy"],
                            case_id=case["id"],
                            artifact_root=package,
                        )
                        require(
                            verdict == historical["verification"],
                            "Original saved artifact verification differs",
                        )
                        saved += 1
                replay = execute_plan(
                    plan,
                    execution_id=f"attempt_{attempt_index}",
                    execution_log=log,
                    checker=lambda record: verify_artifact(
                        projection,
                        record["final_artifact"]["value"],
                        case["goal_type"],
                        case["audit_policy"],
                        case_id=case["id"],
                        artifact_root=workspace,
                    ),
                    **limits,
                )
            except (ValueError, TypeError, KeyError):
                if historical is not None:
                    raise
                require(
                    "validation_error" in attempt, "Missing retained validation failure"
                )
            else:
                require(
                    historical is not None,
                    "A retained validation failure executed on replay",
                )
                require(
                    semantic_receipt(replay) == semantic_receipt(historical),
                    "Replayed execution semantics differ",
                )
                replays += 1
        require(
            len(log) == len(historical_log), "Execution attempt denominator differs"
        )
        accepted = any(row["accepted"] is True for row in historical_log.values())
        require(
            type(result["accepted"]) is bool and result["accepted"] == accepted,
            "Case acceptance differs",
        )
        require(
            result["charged_work_units"]
            == sum(row["charged_work_units"] for row in historical_log.values()),
            "Case charged work differs",
        )
        summaries[job["method"]]["accepted"] += accepted
        expected_calls = len(result["attempts"]) if job["method"] == "model" else 0
        require(result["model_calls"] == expected_calls, "Per-case model calls differ")
        known_tokens = sum(
            attempt["model"]["usage"]["completion_tokens"]
            for attempt in result["attempts"]
            if "model" in attempt
        )
        require(
            result["completion_tokens"] == known_tokens,
            "Per-case completion accounting differs",
        )
        require(
            0 <= known_tokens <= protocol["limits"]["maximum_completion_per_case"],
            "Per-case completion ceiling differs",
        )
        calls += result["model_calls"]
        tokens += known_tokens
    require(
        report["methods"] == summaries, "Reported method denominator/acceptance differs"
    )
    require(
        report["model_calls"] == calls
        and report["completion_tokens_or_reserved_unknown"] == tokens,
        "Aggregate model accounting differs",
    )
    require(
        calls <= protocol["limits"]["maximum_model_calls"]
        and tokens <= protocol["limits"]["maximum_completion_tokens"],
        "Model resource ceiling differs",
    )
    before, after = report["budget_before"], report["budget_after"]
    delta = (
        after["accounted_cost_and_liability_micro_usd"]
        - before["accounted_cost_and_liability_micro_usd"]
    )
    require(
        0 <= delta <= protocol["limits"]["maximum_additional_configured_cost_micro_usd"]
        and delta == report["configured_cost_and_ceiling_delta_micro_usd"]
        and after["cap_micro_usd"] == protocol["limits"]["shared_cap_micro_usd"]
        and after["accounted_cost_and_liability_micro_usd"]
        <= protocol["limits"]["shared_operational_ceiling_micro_usd"],
        "Reported configured budget arithmetic differs",
    )
    resources = protocol["aggregate_resources"]
    require(
        tokens
        + resources["prior_known_completion"]
        + resources["prior_unknown_completion_charged"]
        <= resources["completion_ceiling_including_prior"],
        "Shared completion ceiling differs",
    )
    require(
        0 <= report["wall_seconds"] <= protocol["limits"]["maximum_wall_seconds"]
        and report["wall_seconds"]
        + resources["prior_active_model_wall_seconds_rounded_up"]
        <= resources["total_active_model_wall_ceiling_seconds"],
        "Reported wall allowance differs",
    )
    require(
        report["claims"]
        == {
            "useful_execution_capability": summaries["model"]["accepted"] == 6,
            "model_advantage": False,
            "neural_advantage": False,
            "real_purchases": False,
            "consciousness": False,
        },
        "Reported capability claims differ",
    )
    return {
        "verified": True,
        "protocol_sha256": sha(protocol_file),
        "methods": summaries,
        "source_files": len(protocol["source_hashes"]),
        "retained_proposals": proposals,
        "replayed_executions": replays,
        "artifact_records_verified": artifact_count,
        "saved_markdown_verified": saved,
        "model_calls_replayed": 0,
        "provider_authenticity_or_billing_verified": False,
        "handler_hash_comparison": "Historical hash is internally bound; runtime-local hash is not compared across machines.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--replay-workspace", required=True, type=Path)
    parser.add_argument("--protocol", type=Path)
    args = parser.parse_args()
    result = verify_course(
        args.repository,
        args.results,
        replay_workspace=args.replay_workspace,
        protocol_path=args.protocol,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

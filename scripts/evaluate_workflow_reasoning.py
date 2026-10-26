"""Prospective, finite execution check for a typed goal-directed planner.

This is an integration course on three operational goals, with declared
boundary fixtures and one real injected availability fault. Exact planning is a
strong same-catalog control. A model tie establishes no intelligence advantage.
No job hires workers, sends a payment or changes production.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from cognition.hosted_reasoner import _cost
from cognition.workflow_agent import run_goal
from cognition.workflow_catalog import build_catalog, fingerprint
from scripts.ask_hosted_analyst import load_reasoner
from scripts.check_workflow_artifact import verify_artifact
from scripts.evaluate_model_substitution import CostMarginReasoner, budget_audit

VERSION = "nuria.workflow-reasoning-course.v1"
PREFIX = Path("research/workflow-reasoning")
BASE = Path("research/hybrid-agent/work-snapshot/action-task-projection.json")
MIRROR = Path("research/hybrid-agent/work-snapshot/registered/work.sqlite")
METHODS = ("greedy", "exact", "model")
CLOSURE = (
    "cognition/workflow_planner.py",
    "cognition/workflow_agent.py",
    "cognition/workflow_catalog.py",
    "cognition/hosted_reasoner.py",
    "cognition/reasoner_client.py",
    "scripts/check_workflow_artifact.py",
    "scripts/ask_hosted_analyst.py",
    "scripts/evaluate_model_substitution.py",
    "scripts/evaluate_reasoning_policy.py",
    "scripts/evaluate_workflow_reasoning.py",
    "cognition/reasoning_policy.py",
    "cognition/grounded_agent.py",
    "cognition/analyst_workspace.py",
    "cognition/procedural_memory.py",
    "cognition/working_evidence.py",
    "scripts/ask_grounded_analyst.py",
)
LIMITS = {
    "maximum_model_calls": 12,
    "maximum_completion_tokens": 15000,
    "maximum_calls_per_case": 2,
    "maximum_completion_per_case": 2500,
    "maximum_wall_seconds": 600,
    "minimum_free_disk_bytes": 8 * 1024**3,
    "maximum_retained_bytes": 20 * 1024**2,
    "shared_cap_micro_usd": 10_000_000,
    "shared_operational_ceiling_micro_usd": 5_000_000,
    "maximum_additional_configured_cost_micro_usd": 1_000_000,
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )


def boundary_row(
    label, *, provider, delivered, utility, basis="included_capacity", amount=0
):
    return {
        "id": hashlib.sha256(("workflow-course-v1:" + label).encode()).hexdigest(),
        "provider": provider,
        "acceptance": {"passed": delivered},
        "outcome": {"utility": utility},
        "settlement": {"basis": basis, "total_micro_usd": amount},
    }


def prepare(repository, *, budget_file, model_configuration):
    """Freeze all cases, rules and code before any new model request."""
    root = Path(repository).resolve()
    projection = json.loads((root / BASE).read_bytes())
    if (
        sha(root / BASE)
        != "a5496e9be868e5d050e5b536f2b31e6e4fb4dee8e093e77c765de6463ed10f98"
    ):
        raise ValueError("The retained forty-task projection changed")
    if (
        sha(root / MIRROR)
        != "826f6bb0b66cb9a5ed566e1b0bcedd24293389f3463e9c56ecbc138db12408e0"
    ):
        raise ValueError("The retained SQLite mirror changed")
    boundary = copy.deepcopy(projection)
    boundary["coverage"] = (
        "Declared synthetic boundary revision of the retained forty reported tasks."
    )
    boundary["action_tasks"].append(
        boundary_row(
            "unknown-utility",
            provider="local-record-audit-v1",
            delivered=True,
            utility=None,
        )
    )
    duplicate = copy.deepcopy(
        next(
            row
            for row in projection["action_tasks"]
            if row["id"]
            == "6f4e3b464400152f65afe875728825f246fe8ce9bdf5b2cc2c5fbc14942daf38"
        )
    )
    duplicate["id"] = hashlib.sha256(
        b"workflow-course-v1:duplicate-payload"
    ).hexdigest()
    boundary["action_tasks"].append(duplicate)
    settlement = copy.deepcopy(projection)
    settlement["coverage"] = (
        "Declared synthetic settlement boundary; recorded fixture amounts are not purchases."
    )
    for label, passed, utility, basis, amount in (
        ("reported-money-unknown", None, None, "reported_external", 1_250_000),
        ("included-zero", True, 0, "included_capacity", 0),
        ("reported-money-failed", False, -1, "reported_external", 2_500_000),
    ):
        settlement["action_tasks"].append(
            boundary_row(
                label,
                provider="declared-boundary-provider",
                delivered=passed,
                utility=utility,
                basis=basis,
                amount=amount,
            )
        )
    write(root / PREFIX / "sources/boundary-42.json", boundary)
    write(root / PREFIX / "sources/settlement-43.json", settlement)
    cases = []
    definitions = (
        (
            "provider_original",
            "Write a provider evidence brief separating delivery, positive utility and unknown outcomes.",
            "provider_brief",
            BASE,
            "retained_reported_source",
            4,
            False,
        ),
        (
            "payment_original",
            "Write a payment and delivery audit without treating delivery as payment or recorded amounts as chain verification.",
            "payment_brief",
            BASE,
            "retained_reported_source",
            4,
            False,
        ),
        (
            "audit_original",
            "Plan at most four local rechecks for failed delivery or unknown utility, failure-first then full task ID; list the deferred tasks. Do not run the rechecks.",
            "audit_brief",
            BASE,
            "retained_reported_source",
            5,
            False,
        ),
        (
            "provider_boundary",
            "Write a provider brief for the declared boundary revision, preserving distinct task IDs and unknown utility.",
            "provider_brief",
            PREFIX / "sources/boundary-42.json",
            "declared_synthetic_boundary",
            4,
            False,
        ),
        (
            "reader_unavailable",
            "Produce the provider evidence brief. If a reader fails, use observed execution feedback to find another enrolled route. Do not invent a successful read.",
            "provider_brief",
            BASE,
            "injected_availability_fault_on_real_source",
            7,
            True,
        ),
        (
            "payment_boundary",
            "Write a payment audit for the declared settlement boundary, preserving unknown delivery and distinguishing utility, reported money and independently verified payments.",
            "payment_brief",
            PREFIX / "sources/settlement-43.json",
            "declared_synthetic_boundary",
            4,
            False,
        ),
    )
    for case_id, goal, kind, source, scope, units, fault in definitions:
        body = json.loads((root / source).read_bytes())
        cases.append(
            {
                "id": case_id,
                "goal": goal,
                "goal_type": kind,
                "source_path": source.as_posix(),
                "source_file_sha256": sha(root / source),
                "projection_sha256": fingerprint(body),
                "scope": scope,
                "maximum_work": units,
                "deadline_units": units,
                "injected_missing_primary": fault,
                "audit_policy": {"maximum_tasks": 4},
            }
        )
    protocol = {
        "version": VERSION,
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "cases": cases,
        "methods": METHODS,
        "selected_jobs": len(cases) * len(METHODS),
        "limits": LIMITS,
        "budget_before": budget_audit(budget_file),
        "model_configuration": model_configuration,
        "aggregate_resources": {
            "completion_ceiling_including_prior": 328096,
            "prior_known_completion": budget_audit(budget_file)[
                "known_completion_tokens"
            ],
            "prior_unknown_completion_charged": 2048,
            "prior_active_model_wall_seconds_rounded_up": 604,
            "total_active_model_wall_ceiling_seconds": 5400,
        },
        "source_hashes": {name: sha(root / name) for name in CLOSURE},
        "mirror": {"path": MIRROR.as_posix(), "sha256": sha(root / MIRROR)},
        "acceptance": "Exact independently rederived facts, source hash, scope text and actual saved Markdown readback.",
        "failure_policy": "At most one new plan after observed reader unavailability; charge the failed operation; retain every failure. Stop model calls on transport ambiguity.",
        "interpretation": "Finite useful execution capability; no claimed advantage over exact planning, neural gain, consciousness or real purchasing.",
    }
    write(root / PREFIX / "protocol-01.json", protocol)
    return protocol


class BoundedCourseReasoner:
    def __init__(self, reasoner, *, start, output, budget_file, initial_cost):
        self.base = CostMarginReasoner(reasoner)
        self.start, self.output, self.budget_file = start, Path(output), budget_file
        self.calls, self.tokens, self.initial_cost = 0, 0, initial_cost

    def complete(self, messages, *, max_tokens, seed=0):
        state = budget_audit(self.budget_file)
        config = self.base.configuration
        worst_next = _cost(
            config["max_input_bytes"] + 1024 + 128 * len(messages),
            max_tokens,
            config["input_micro_usd_per_million_tokens"],
            config["output_micro_usd_per_million_tokens"],
        )
        if (
            self.calls >= LIMITS["maximum_model_calls"]
            or self.tokens + max_tokens > LIMITS["maximum_completion_tokens"]
            or time.monotonic() - self.start > LIMITS["maximum_wall_seconds"]
            or shutil.disk_usage(self.output).free < LIMITS["minimum_free_disk_bytes"]
            or sum(p.stat().st_size for p in self.output.rglob("*") if p.is_file())
            > LIMITS["maximum_retained_bytes"]
            or state["accounted_cost_and_liability_micro_usd"]
            - self.initial_cost
            + worst_next
            > LIMITS["maximum_additional_configured_cost_micro_usd"]
        ):
            raise ValueError("Prospective workflow resource ceiling reached")
        self.calls += 1
        # Reserve the whole completion allowance locally before transport. If
        # usage is ambiguous it stays charged; no further requests are retried.
        self.tokens += max_tokens
        result = self.base.complete(messages, max_tokens=max_tokens, seed=seed)
        self.tokens -= max_tokens - result["usage"]["completion_tokens"]
        return result


def run(repository, protocol_path, *, output, config_path):
    root, protocol_path = Path(repository).resolve(), Path(protocol_path).resolve()
    protocol = json.loads(protocol_path.read_bytes())
    if protocol["version"] != VERSION or protocol["limits"] != LIMITS:
        raise ValueError("Prospective workflow contract differs")
    for name, identity in protocol["source_hashes"].items():
        if sha(root / name) != identity:
            raise ValueError("A frozen workflow source changed")
    if sha(root / protocol["mirror"]["path"]) != protocol["mirror"]["sha256"]:
        raise ValueError("Frozen mirror changed")
    client = load_reasoner(config_path)
    if client.configuration != protocol["model_configuration"]:
        raise ValueError("The frozen model contract changed")
    budget_file = client.budget.path
    before = budget_audit(budget_file)
    if before["records_sha256"] != protocol["budget_before"]["records_sha256"]:
        raise ValueError("Another model request occurred after prospective freeze")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    write(output / "protocol.json", protocol)
    previous = Path.cwd()
    start = time.monotonic()
    reasoner = BoundedCourseReasoner(
        client,
        start=start,
        output=output,
        budget_file=budget_file,
        initial_cost=before["accounted_cost_and_liability_micro_usd"],
    )
    jobs = [
        {
            "case_id": case["id"],
            "method": method,
            "attempted": False,
            "result": None,
            "error_type": None,
        }
        for case in protocol["cases"]
        for method in METHODS
    ]
    write(output / "selected-jobs.json", jobs)
    halted = None
    try:
        os.chdir(output)
        for index, job in enumerate(jobs):
            case = next(row for row in protocol["cases"] if row["id"] == job["case_id"])
            if sha(root / case["source_path"]) != case["source_file_sha256"]:
                raise ValueError("Frozen case source changed")
            projection = json.loads((root / case["source_path"]).read_bytes())
            spec = {
                "name": "reported_task_snapshot",
                "projection_sha256": fingerprint(projection),
                "scope": case["scope"],
            }
            sources = {
                "snapshot": {"type": "source_spec", "value": spec},
                "audit_policy": {"type": "audit_policy", "value": case["audit_policy"]},
            }
            primary = (
                (output / "injected-missing-primary.json")
                if case["injected_missing_primary"]
                else root / case["source_path"]
            )
            if case["injected_missing_primary"] and primary.exists():
                raise ValueError("The declared missing fixture unexpectedly exists")
            catalog = build_catalog(
                projection=projection,
                json_path=primary,
                sqlite_path=root / MIRROR,
                output_dir=Path("artifacts") / job["method"] / case["id"],
                source_spec=spec,
            )

            def checker(record):
                return verify_artifact(
                    projection,
                    record["final_artifact"]["value"],
                    case["goal_type"],
                    audit_policy=case["audit_policy"],
                    case_id=case["id"],
                    artifact_root=output,
                )

            log = {}
            job["attempted"] = True
            try:
                job["result"] = run_goal(
                    goal=case["goal"],
                    catalog=catalog,
                    case_id=case["id"],
                    sources=sources,
                    goal_type=case["goal_type"],
                    maximum_work=case["maximum_work"],
                    deadline_units=case["deadline_units"],
                    checker=checker,
                    execution_log=log,
                    reasoner=reasoner,
                    method=job["method"],
                    maximum_calls=LIMITS["maximum_calls_per_case"],
                    maximum_completion=LIMITS["maximum_completion_per_case"],
                )
            except Exception as error:
                job["error_type"] = type(error).__name__
                job["retained_execution_log"] = log
                halted = job["error_type"]
            write(output / ("job-" + str(index + 1).zfill(2) + ".json"), job)
            if halted:
                break
    finally:
        os.chdir(previous)
    after = budget_audit(budget_file)
    report = {
        "version": VERSION,
        "protocol_sha256": sha(protocol_path),
        "jobs": jobs,
        "methods": {
            method: {
                "selected": sum(j["method"] == method for j in jobs),
                "attempted": sum(
                    j["method"] == method and j["attempted"] for j in jobs
                ),
                "accepted": sum(
                    j["method"] == method and bool((j["result"] or {}).get("accepted"))
                    for j in jobs
                ),
            }
            for method in METHODS
        },
        "halted": halted,
        "model_calls": reasoner.calls,
        "completion_tokens_or_reserved_unknown": reasoner.tokens,
        "wall_seconds": time.monotonic() - start,
        "budget_before": before,
        "budget_after": after,
        "configured_cost_and_ceiling_delta_micro_usd": after[
            "accounted_cost_and_liability_micro_usd"
        ]
        - before["accounted_cost_and_liability_micro_usd"],
        "runtime_model": client.configuration,
        "claims": {
            "useful_execution_capability": all(
                (j["result"] or {}).get("accepted")
                for j in jobs
                if j["method"] == "model"
            ),
            "model_advantage": False,
            "neural_advantage": False,
            "real_purchases": False,
            "consciousness": False,
        },
    }
    write(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run"))
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        result = prepare(
            args.repository,
            budget_file=args.budget,
            model_configuration=load_reasoner(args.config).configuration,
        )
        print(json.dumps({"selected": result["selected_jobs"], "frozen": True}))
    else:
        result = run(
            args.repository, args.protocol, output=args.output, config_path=args.config
        )
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in ("methods", "model_calls", "halted", "wall_seconds")
                }
            )
        )


if __name__ == "__main__":
    main()

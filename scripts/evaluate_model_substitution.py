"""Descriptive model substitution on the complete published twelve-task cohort.

This is an unblinded historical before/after check, not a new unseen test or a
causal neural comparison. No training, policy suffix or historical answer enters
the model prompt. The same shared dollar allowance remains in force.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace, grade_result
from cognition.grounded_agent import GroundedAnalyst
from cognition.hosted_reasoner import _cost
from cognition.procedural_memory import ProceduralMemory
from cognition.reasoner_client import digest
from scripts.evaluate_reasoning_policy import (
    CourseReasoner,
    UsageReasoner,
    compact_rows,
    read,
    sha,
    summary,
    write,
)

VERSION = "nuria.model-substitution.v1"
TASK_IDS = (
    "spider-dev-0694",
    "spider-dev-0281",
    "spider-dev-0273",
    "spider-dev-0271",
    "spider-dev-0687",
    "spider-dev-0701",
    "spider-dev-0269",
    "spider-dev-0289",
    "spider-dev-0690",
    "spider-dev-0696",
    "spider-dev-0698",
    "spider-dev-0267",
)
LIMITS = {
    "max_turns": 12,
    "max_output_tokens": 6000,
    "max_tool_calls": 12,
    "max_tokens_per_turn": 1536,
    "memory_bytes": 6000,
}
CORE = (
    "cognition/grounded_agent.py",
    "cognition/analyst_workspace.py",
    "cognition/procedural_memory.py",
    "cognition/working_evidence.py",
)
CLOSURE = CORE + (
    "cognition/reasoner_client.py",
    "cognition/hosted_reasoner.py",
    "cognition/reasoning_policy.py",
    "scripts/ask_hosted_analyst.py",
    "scripts/evaluate_reasoning_policy.py",
    "scripts/evaluate_model_substitution.py",
)


def budget_audit(path):
    """Read request accounting only; never read credentials or prompt content."""
    path = Path(path).resolve(strict=True)
    with closing(
        sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True)
    ) as db:
        policy = db.execute("SELECT cap,maximum FROM policy WHERE id=1").fetchone()
        records = db.execute(
            "SELECT id,request_sha256,contract_sha256,reserved,charged,state,usage "
            "FROM requests ORDER BY id"
        ).fetchall()
        provisions = db.execute(
            "SELECT id,reservation,evidence_sha256,reason FROM reconciliations ORDER BY id"
        ).fetchall()
    if policy is None or policy[0] != 10_000_000 or len(records) > policy[1]:
        raise ValueError("The original shared ten-dollar allowance is required")
    known, pending, settled_cost, ceiling_cost = 0, 0, 0, 0
    charged_ceilings = []
    for request_id, _, _, reserved, charged, state, raw in records:
        if state == "charged_ceiling":
            proof = [row for row in provisions if row[1] == request_id]
            if charged != reserved or raw is not None or len(proof) != 1:
                raise ValueError(
                    "A ceiling charge lacks its original unknown-usage provision"
                )
            ceiling_cost += charged
            charged_ceilings.append(
                {
                    "reservation": request_id,
                    "charged_ceiling_micro_usd": charged,
                    "reconciliation_id": proof[0][0],
                    "evidence_sha256": proof[0][2],
                    "reason": proof[0][3],
                }
            )
            continue
        if state != "settled":
            pending += max(reserved, charged or 0)
            continue
        usage = json.loads(raw)
        tokens = usage.get("completion_tokens")
        if type(tokens) is not int or tokens < 0:
            raise ValueError("Shared ledger completion accounting is malformed")
        known += tokens
        settled_cost += charged
    return {
        "cap_micro_usd": policy[0],
        "maximum_requests": policy[1],
        "requests": len(records),
        "known_completion_tokens": known,
        "settled_cost_micro_usd": settled_cost,
        "conservative_charged_ceiling_micro_usd": ceiling_cost,
        "charged_ceilings": charged_ceilings,
        "unresolved_liability_micro_usd": pending,
        "accounted_cost_and_liability_micro_usd": settled_cost + ceiling_cost + pending,
        "records_sha256": digest(records),
    }


class CostMarginReasoner:
    """Keep configured known cost plus every ceiling below half the user cap."""

    def __init__(self, reasoner):
        self.base, self.halt_reason = reasoner, None

    def __getattr__(self, name):
        return getattr(self.base, name)

    def complete(self, messages, *, max_tokens=768, seed=0):
        state = self.base.budget.snapshot()
        config = self.base.configuration
        maximum_next_cost = _cost(
            config["max_input_bytes"] + 1024 + 128 * len(messages),
            max_tokens,
            config["input_micro_usd_per_million_tokens"],
            config["output_micro_usd_per_million_tokens"],
        )
        if (
            state["blocked"]
            or state["accounted_cost_and_liability_micro_usd"] + maximum_next_cost
            > 5_000_000
        ):
            self.halt_reason = "conservative_cost_margin"
            raise ValueError("The shared half-cap operational margin would be crossed")
        return self.base.complete(messages, max_tokens=max_tokens, seed=seed)


def prepare_protocol(
    directory,
    *,
    repository,
    inputs_root,
    model_configuration,
    budget_file,
    primary_report,
    allow_incomplete_primary=False,
    loss_provision_receipt=None,
    historical_protocol=None,
    historical_result=None,
):
    """Pin metadata and source identities before decoding cohort or oracle bodies."""
    repository, inputs_root = Path(repository).resolve(), Path(inputs_root).resolve()
    historical_protocol = Path(
        historical_protocol
        or repository / "research/hybrid-agent/supplemental-controls/protocol-01.json"
    )
    historical_result = Path(
        historical_result
        or repository
        / "research/hybrid-agent/supplemental-controls/results-01/replay-result.json"
    )
    old, result, primary = map(
        read, (historical_protocol, historical_result, primary_report)
    )
    if tuple(old["selected_task_ids"]) != TASK_IDS or old["limits"] != LIMITS:
        raise ValueError("The complete original cohort and host ceilings are required")
    if (
        result["conditions"]["schema_first"]
        != {
            "selected": 12,
            "executed": 12,
            "skipped_original_complete": False,
            "graded": 12,
            "accepted": 9,
            "abstentions": 1,
            "unknown_or_unexecuted": 0,
        }
        or result.get("passed") is not True
        or result.get("complete") is not True
    ):
        raise ValueError("Historical nine-of-twelve reference differs")
    usage = primary["global_usage_including_policy_proposals"]
    unknown_ceiling = usage["unresolved_completion_ceiling"]
    if type(unknown_ceiling) is not int or not 0 <= unknown_ceiling <= 6000:
        raise ValueError("Primary unknown completion ceiling is invalid")
    if not allow_incomplete_primary and (
        primary["stop_reason"] is not None
        or primary["all_selected"]["committed"] != 54
        or primary["all_selected"]["graded"] != 54
    ):
        raise ValueError(
            "Complete primary results or explicit stopped-run review are required"
        )
    prior_wall = primary["wall_seconds"]
    if (
        type(prior_wall) not in (int, float)
        or not math.isfinite(prior_wall)
        or prior_wall < 0
    ):
        raise ValueError("Primary wall accounting is invalid")
    audit = budget_audit(budget_file)
    if (
        audit["unresolved_liability_micro_usd"]
        or audit["known_completion_tokens"] != usage["completion_tokens"]
    ):
        raise ValueError("Primary report and settled shared ledger disagree")
    loss_receipt = None
    if usage["unknown_additional_usage"] or unknown_ceiling:
        if loss_provision_receipt is None or not unknown_ceiling:
            raise ValueError(
                "Primary unknown usage needs an explicit full-ceiling provision"
            )
        loss_receipt = read(loss_provision_receipt)
        if (
            loss_receipt.get("provider_usage_known") is not False
            or loss_receipt.get("provider_settlement_verified") is not False
        ):
            raise ValueError(
                "A full-ceiling provision cannot make provider usage known"
            )
        if audit["charged_ceilings"] != [
            {
                key: loss_receipt[key]
                for key in (
                    "reservation",
                    "charged_ceiling_micro_usd",
                    "reconciliation_id",
                    "evidence_sha256",
                    "reason",
                )
            }
        ]:
            raise ValueError("The retained provision differs from the shared ledger")
    elif audit["charged_ceilings"]:
        raise ValueError(
            "An existing unknown-use ceiling was omitted from primary accounting"
        )
    token_cap = min(72000, 328096 - audit["known_completion_tokens"] - unknown_ceiling)
    wall_cap = min(1800, int(5400 - prior_wall))
    if token_cap <= 0 or wall_cap < model_configuration["timeout_seconds"]:
        raise ValueError("The existing aggregate resource allowance is exhausted")
    sources = {name: sha(repository / name) for name in CLOSURE}
    if any(sources[name] != old["frozen_source_files"][name] for name in CORE):
        raise ValueError("Historical analyst, tools, memory or evidence code changed")
    inputs = {}
    for name in ("registry", "reserved_course", "oracle"):
        item = old["artifacts"][name]
        path = (inputs_root / item["path"]).resolve(strict=True)
        if not path.is_relative_to(inputs_root) or sha(path) != item["sha256"]:
            raise ValueError("An original input identity differs")
        inputs[name] = {"path": str(path), "sha256": item["sha256"]}
    public = {
        "schema": VERSION,
        "selected_task_ids": list(TASK_IDS),
        "planned_jobs": 12,
        "historical_protocol_sha256": sha(historical_protocol),
        "historical_result_sha256": sha(historical_result),
        "historical_accepted": 9,
        "historical_model": old["model_identity"],
        "source_files": sources,
        "input_hashes": {name: item["sha256"] for name, item in inputs.items()},
        "model_configuration": model_configuration,
        "model_identity": digest(model_configuration),
        "limits": LIMITS,
        "memory_mode": "none",
        "initial_tool": "read_knowledge",
        "initial_section": "schema",
        "neural_context": None,
        "seed": 20261025,
        "seed_limit": "Responses does not support the local model's seeded sampling contract",
        "prior_known_completion_tokens": audit["known_completion_tokens"],
        "prior_charged_unknown_completion_ceiling": unknown_ceiling,
        "prior_elapsed_wall_seconds": prior_wall,
        "maximum_new_completion_tokens": token_cap,
        "maximum_new_wall_seconds": wall_cap,
        "aggregate_completion_cap": 328096,
        "aggregate_wall_seconds": 5400,
        "shared_budget_cap_micro_usd": 10_000_000,
        "starting_budget_audit": audit,
        "operational_margin_micro_usd": 5_000_000,
        "loss_provision_receipt_sha256": sha(Path(loss_provision_receipt))
        if loss_provision_receipt
        else None,
        "provider_usage_of_prior_ceiling_known": False if unknown_ceiling else None,
        "primary_report_sha256": sha(Path(primary_report)),
        "primary_stop_reason": primary["stop_reason"],
        "primary_coverage": primary["all_selected"],
        "allow_incomplete_primary": bool(allow_incomplete_primary),
        "feedback": "All twelve new proposals commit before any oracle is decoded. Incomplete batches remain ungraded. No policy or historical training context is supplied.",
        "interpretation": "Descriptive historical model-backend substitution on a published, unblinded public cohort. Different weights, reasoning implementation, seed support, hardware and time; possible pretraining contamination. No fresh held-out, causal neural, general-intelligence or consciousness claim.",
    }
    directory = Path(directory).resolve()
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    write(directory / "protocol-public.json", public)
    private = {
        "schema": VERSION,
        "repository": str(repository),
        "inputs": inputs,
        "budget_file": str(Path(budget_file).resolve()),
        "primary_report": str(Path(primary_report).resolve()),
        "loss_provision_receipt": str(Path(loss_provision_receipt).resolve())
        if loss_provision_receipt
        else None,
        "historical_protocol": str(historical_protocol.resolve()),
        "historical_result": str(historical_result.resolve()),
        "public_protocol": str(directory / "protocol-public.json"),
        "public_protocol_sha256": sha(directory / "protocol-public.json"),
    }
    write(directory / "protocol-private.json", private)
    return {
        "private_protocol": str(directory / "protocol-private.json"),
        "private_protocol_sha256": sha(directory / "protocol-private.json"),
        "public_protocol": private["public_protocol"],
        "public_protocol_sha256": private["public_protocol_sha256"],
    }


def verify_protocol(path, expected):
    if sha(Path(path)) != expected:
        raise ValueError("Substitution protocol identity differs")
    private = read(path)
    if (
        private.get("schema") != VERSION
        or sha(Path(private["public_protocol"])) != private["public_protocol_sha256"]
    ):
        raise ValueError("Public substitution declaration differs")
    public = read(private["public_protocol"])
    root = Path(private["repository"])
    if any(sha(root / name) != value for name, value in public["source_files"].items()):
        raise ValueError("Frozen substitution source changed")
    for item in private["inputs"].values():
        if sha(Path(item["path"])) != item["sha256"]:
            raise ValueError("Frozen original input changed")
    for field, identity in (
        ("primary_report", "primary_report_sha256"),
        ("historical_protocol", "historical_protocol_sha256"),
        ("historical_result", "historical_result_sha256"),
    ):
        if sha(Path(private[field])) != public[identity]:
            raise ValueError("Frozen prior evidence changed")
    if (
        private["loss_provision_receipt"]
        and sha(Path(private["loss_provision_receipt"]))
        != public["loss_provision_receipt_sha256"]
    ):
        raise ValueError("Frozen ceiling-loss evidence changed")
    return private, public


def evaluate(protocol_path, expected_protocol_sha256, output, reasoner):
    private, protocol = verify_protocol(protocol_path, expected_protocol_sha256)
    if (
        digest(reasoner.configuration) != protocol["model_identity"]
        or reasoner.configuration != protocol["model_configuration"]
    ):
        raise ValueError("Actual model configuration differs from declaration")
    if Path(reasoner.budget.path).resolve() != Path(private["budget_file"]).resolve():
        raise ValueError("The same existing shared budget ledger is required")
    before = budget_audit(private["budget_file"])
    if (
        before != protocol["starting_budget_audit"]
        or before["unresolved_liability_micro_usd"]
    ):
        raise ValueError("Shared accounting changed or remains unresolved")
    output = Path(output)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    write(
        output / "run-freeze.json",
        {"private_protocol_sha256": expected_protocol_sha256, "protocol": protocol},
    )
    rows = [
        {
            "phase": "substitution",
            "method": "schema_first",
            "task_id": task_id,
            "attempted": False,
            "agent": None,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "model_call_attempts": 0,
                "unknown_additional_usage": False,
            },
        }
        for task_id in TASK_IDS
    ]
    write(
        output / "selection.json",
        {"selected_task_ids": list(TASK_IDS), "planned_jobs": 12},
    )
    # Original task bodies are opened only after the complete freeze verifies.
    course = read(private["inputs"]["reserved_course"]["path"])["tasks"]
    if [task["id"] for task in course] != list(TASK_IDS):
        raise ValueError("Original whole-cohort order differs")
    workspace = AnalystWorkspace(private["inputs"]["registry"]["path"])
    source_id = read(private["inputs"]["registry"]["path"])["revision"]
    memory = ProceduralMemory(
        output / "memory.sqlite",
        source_id=source_id,
        model_id=protocol["model_identity"],
        tool_version=SCHEMA,
    )
    margin = CostMarginReasoner(reasoner)
    metered = CourseReasoner(
        margin,
        output,
        maximum_completion=protocol["maximum_new_completion_tokens"],
        maximum_seconds=protocol["maximum_new_wall_seconds"],
    )
    stop, started = None, time.monotonic()
    try:
        for task, row in zip(course, rows):
            if stop:
                row["stop_reason"] = stop
                continue
            verify_protocol(protocol_path, expected_protocol_sha256)
            job = UsageReasoner(metered)
            row["attempted"] = True
            try:
                row["agent"] = GroundedAnalyst(
                    job,
                    workspace,
                    memory,
                    **LIMITS,
                    memory_mode="none",
                    neural_context=None,
                    initial_tool="read_knowledge",
                    initial_section="schema",
                ).run(
                    job_id=task["id"],
                    question=task["question"],
                    db_id=task["db_id"],
                    seed=20261025,
                )
                if row["agent"]["status"] == "prompt_or_client_limit":
                    stop = "client_preflight_limit"
            except BaseException as error:
                stop = "substitution_job_failure"
                row["error_type"] = type(error).__name__
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
            finally:
                row["usage"] = job.usage()
                stop = metered.halt_reason or margin.halt_reason or stop
                if row["usage"]["unknown_additional_usage"]:
                    stop = "unreconciled_model_usage"
                write(output / (task["id"] + ".committed.json"), row)
        verify_protocol(protocol_path, expected_protocol_sha256)
        complete = all(row["agent"] is not None for row in rows)
        write(
            output / "proposals-ended.json",
            {"count": 12, "complete": complete, "rows_sha256": digest(rows)},
        )
        if complete:
            oracle = read(private["inputs"]["oracle"]["path"])["tasks"]
            for row in rows:
                expected, agent = oracle[row["task_id"]], row["agent"]
                accepted = bool(
                    agent["final_sql"] is not None
                    and grade_result(
                        agent["selected_receipt"],
                        expected["expected_rows"],
                        ordered=expected["ordered"],
                        expected_columns=expected["column_count"],
                    )
                )
                row["verdict"] = {
                    "complete": True,
                    "accepted": accepted,
                    "checker_id": "posthoc-finite-sqlite-result-v1",
                    "evidence_sha256": digest(
                        {
                            "proposal": agent["proposal_sha256"],
                            "oracle": digest(expected),
                            "accepted": accepted,
                        }
                    ),
                }
    except BaseException as error:
        stop = stop or "substitution_failure"
        write(
            output / "interruption.json",
            {"error_type": type(error).__name__, "rows": compact_rows(rows)},
        )
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        memory.close()
        audit = budget_audit(private["budget_file"])
        report = {
            "schema": VERSION,
            "protocol_sha256": expected_protocol_sha256,
            "stop_reason": stop,
            "all_selected": summary(rows),
            "rows": compact_rows(rows),
            "new_usage": metered.snapshot(),
            "shared_budget_audit": audit,
            "aggregate_known_completion_tokens": audit["known_completion_tokens"],
            "prior_charged_unknown_completion_ceiling": protocol[
                "prior_charged_unknown_completion_ceiling"
            ],
            "aggregate_conservative_completion_charge": audit["known_completion_tokens"]
            + protocol["prior_charged_unknown_completion_ceiling"]
            + metered.pending,
            "aggregate_elapsed_wall_seconds": protocol["prior_elapsed_wall_seconds"]
            + time.monotonic()
            - started,
            "historical_accepted": 9,
            "historical_selected": 12,
            "scope": protocol["interpretation"],
        }
        write(output / "report.json", report)
    return report


def main(argv=None):
    from scripts.ask_hosted_analyst import load_reasoner

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--inputs-root", type=Path)
    parser.add_argument("--primary-report", type=Path)
    parser.add_argument("--allow-incomplete-primary", action="store_true")
    parser.add_argument("--loss-provision-receipt", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--protocol-sha256")
    args = parser.parse_args(argv)
    reasoner = load_reasoner(args.provider_config)
    if args.prepare:
        if args.inputs_root is None or args.primary_report is None:
            parser.error(
                "Preparation needs original inputs and the closed primary report"
            )
        value = prepare_protocol(
            args.output,
            repository=Path(__file__).resolve().parents[1],
            inputs_root=args.inputs_root,
            model_configuration=reasoner.configuration,
            budget_file=reasoner.budget.path,
            primary_report=args.primary_report,
            allow_incomplete_primary=args.allow_incomplete_primary,
            loss_provision_receipt=args.loss_provision_receipt,
        )
    else:
        if args.protocol is None or args.protocol_sha256 is None:
            parser.error("Evaluation needs an explicit frozen protocol and hash")
        value = evaluate(args.protocol, args.protocol_sha256, args.output, reasoner)
        value = {
            key: value[key]
            for key in (
                "stop_reason",
                "all_selected",
                "new_usage",
                "shared_budget_audit",
            )
        }
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()

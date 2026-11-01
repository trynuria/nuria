"""Compare adaptive execution with the unchanged retrieved three-call worker.

Both conditions receive the same enrolled task and initial checked experience.
The adaptive worker can use five responses within 7,200 completion tokens; the
control can use three responses of 2,400. Actual usage and executions are
reported separately. Terminal feedback is withheld until public commitment.
"""

import argparse
import hashlib
import json
import math
import re
import time
from pathlib import Path

from cognition.experience_navigation import run_navigation_work
from cognition.interactive_work import run_interactive_work
from cognition.outcome_worker import identity
from cognition.program_delivery import SUFFIX
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_experience_navigation import runtime
from scripts.evaluate_outcome_loop import BoundedClient, exact_paired, save
from scripts.evaluate_reasoner_transfer import enrolled, proposals, seal, stores_for
from scripts.evaluate_recovery_options import QualificationEnvelope

CONDITIONS = ("interactive", "retrieval")


def generate(root, config, resource_root, dependencies=()):
    root = Path(root)
    protocol, tasks, history = enrolled(root)
    provider = load_reasoner(config)
    provider.timeout = 90
    if set(protocol["conditions"]) != set(CONDITIONS) or any(
        value != provider.configuration for value in protocol["conditions"].values()
    ):
        raise ValueError("Both conditions need the same enrolled reasoning provider")
    guard = root / "generation-budget.json"
    if guard.exists():
        saved = json.loads(guard.read_text())
        if saved["protocol_sha256"] != identity(protocol):
            raise ValueError("The comparison changed after startup")
    else:
        saved = {
            "protocol_sha256": identity(protocol),
            "initial": provider.budget.snapshot(),
            "started_epoch": time.time(),
        }
        with guard.open("x") as stream:
            stream.write(json.dumps(saved, indent=2, sort_keys=True) + "\n")
    client = QualificationEnvelope(
        provider,
        BoundedClient(provider, resource_root),
        saved["initial"],
        protocol["limits"],
        saved["started_epoch"],
    )
    stores = stores_for(root, protocol)
    executor = runtime(root / "proposals", dependencies)
    for task in tasks:
        rotation = int(hashlib.sha256(task["task_id"].encode()).hexdigest()[:8], 16) % 2
        for name in CONDITIONS[rotation:] + CONDITIONS[:rotation]:
            kwargs = {
                "job_id": "interactive-test-"
                + name
                + "-"
                + task["task_id"].split("/")[1],
                "experience": history,
                "grade": False,
            }
            if name == "interactive":
                result = run_interactive_work(
                    client, executor, stores[name], task, **kwargs
                )
            else:
                result = run_navigation_work(
                    client,
                    executor,
                    stores[name],
                    task,
                    method="retrieval",
                    max_tokens=2400,
                    **kwargs,
                )
            print(
                json.dumps(
                    {
                        "condition": name,
                        "task_id": task["task_id"],
                        "committed": True,
                        "accepted": result["accepted"],
                        "replayed": result["replayed"],
                        "calls": len(result["attempts"]),
                        "budget": provider.budget.snapshot(),
                    }
                ),
                flush=True,
            )
    return {"programs": 2 * len(tasks), "terminal_checks_revealed": False}


def _usage(records, configuration):
    uses = [
        a["usage"] for record in records for a in record["attempts"] if a.get("usage")
    ]
    return {
        "calls": len(uses),
        "prompt_tokens": sum(a["prompt_tokens"] for a in uses),
        "completion_tokens": sum(a["completion_tokens"] for a in uses),
        "configured_micro_usd": sum(
            math.ceil(
                (
                    a["prompt_tokens"]
                    * configuration["input_micro_usd_per_million_tokens"]
                    + a["completion_tokens"]
                    * configuration["output_micro_usd_per_million_tokens"]
                )
                / 1_000_000
            )
            for a in uses
        ),
        "program_probes": sum(
            sum("probe" in a for a in r["attempts"])
            if r.get("execution_route") == "nuria.interactive-work.v1"
            else sum(
                k in r and "code_sha256" in r[k]
                for k in ("initial_smoke", "challenge_execution", "revised_smoke")
            )
            for r in records
        ),
    }


def grade(root, dependencies=()):
    root = Path(root)
    protocol, tasks, records = proposals(root)
    if set(records) != set(CONDITIONS):
        raise ValueError("Use exactly the prospectively enrolled comparison")
    raw = (root / "proposals-before-grade.json").read_bytes()
    proof = json.loads((root / "public-proposals-proof.json").read_text())
    if (
        proof.get("repository") != "trynuria/nuria"
        or not re.fullmatch(r"[a-f0-9]{40}", proof.get("commit", ""))
        or proof.get("sha256") != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError(
            "An independently verified public proposal commitment is required"
        )
    committed = json.loads(raw)
    if committed["protocol_sha256"] != identity(protocol):
        raise ValueError("Public proposals belong to another protocol")
    by_task = {task["task_id"]: task for task in tasks}
    executor = runtime(root / "terminal", dependencies)
    stores = stores_for(root, protocol)
    checked_records = {}
    for name, rows in records.items():
        original = {r["task_id"]: r for r in committed["records"][name]}
        checked_records[name] = []
        for row in rows:
            prior = original[row["task_id"]]
            omit = {"checker", "accepted"}
            if {k: v for k, v in row.items() if k not in omit} != {
                k: v for k, v in prior.items() if k not in omit
            }:
                raise ValueError("Proposed program changed after commitment")
            if row["checker"] is None:
                check = executor.run(
                    row["code"],
                    by_task[row["task_id"]]["checker"],
                    label=row["job_id"] + SUFFIX[row["version"]],
                    inputs=by_task[row["task_id"]].get("inputs"),
                )
                row = stores[name].accept(row["job_id"], check)
            checked_records[name].append(row)
        stores[name].verify()
    vectors = {
        name: [r["accepted"] for r in rows] for name, rows in checked_records.items()
    }
    comparison = exact_paired(vectors["interactive"], vectors["retrieval"])
    families = {
        family: {
            name: sum(r["accepted"] for r in rows if r["family"] == family)
            for name, rows in checked_records.items()
        }
        for family in sorted({task["family"] for task in tasks})
    }
    result = {
        "tasks": len(tasks),
        "accepted": {name: sum(v) for name, v in vectors.items()},
        "comparison": comparison,
        "families": families,
        "usage": {
            name: _usage(rows, protocol["conditions"][name])
            for name, rows in checked_records.items()
        },
        "superiority_supported": len(tasks) >= 70
        and comparison["accuracy_delta"] >= 0.04
        and comparison["one_sided_exact_p"] <= 0.0125
        and all(v["interactive"] >= v["retrieval"] - 2 for v in families.values()),
        "neural_advantage_established": False,
        "runtime_promoted": False,
    }
    save(root / "results.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("generate", "seal", "grade"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--resource-root", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    if args.stage == "generate":
        result = generate(
            args.root, args.config, args.resource_root, args.dependency_root
        )
    elif args.stage == "seal":
        result = seal(args.root)
    else:
        result = grade(args.root, args.dependency_root)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

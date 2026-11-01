"""Matched execute/observe comparison with or without opaque reasoning replay.

The same model, work policy, visible history, examples and execution boundary
apply to both conditions. Every terminal check stays hidden until all programs
are publicly committed. Opaque provider material remains private.
"""

import argparse
import hashlib
import json
import math
import re
import time
from pathlib import Path

from cognition.interactive_work import run_interactive_work
from cognition.outcome_worker import identity
from cognition.program_delivery import SUFFIX
from cognition.reasoning_continuity import ContinuitySession, ContinuityTransport
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_experience_navigation import runtime
from scripts.evaluate_outcome_loop import BoundedClient, exact_paired, save
from scripts.evaluate_reasoner_transfer import enrolled, proposals, seal, stores_for
from scripts.evaluate_recovery_options import QualificationEnvelope

CONDITIONS = ("continuity", "visible")


def generate(root, config, resource_root, dependencies=()):
    root = Path(root)
    protocol, tasks, history = enrolled(root)
    providers = {
        name: ContinuityTransport(load_reasoner(config)) for name in CONDITIONS
    }
    for provider in providers.values():
        provider.base.timeout = 90
        provider.base.max_input_bytes = 262144
        provider.base.effort = "medium"
    guard = root / "generation-budget.json"
    if guard.exists():
        saved = json.loads(guard.read_text())
        if saved["protocol_sha256"] != identity(protocol):
            raise ValueError("The prospective continuity protocol changed")
    else:
        saved = {
            "protocol_sha256": identity(protocol),
            "initial": providers["continuity"].budget.snapshot(),
            "started_epoch": time.time(),
        }
        with guard.open("x") as stream:
            stream.write(json.dumps(saved, indent=2, sort_keys=True) + "\n")

    class CancellableEnvelope(QualificationEnvelope):
        def complete(self, messages, *, max_tokens, seed=0):
            if (root / "stop-requested.json").exists():
                raise ValueError("Operator requested a stop before another model call")
            return super().complete(messages, max_tokens=max_tokens, seed=seed)

    clients = {
        name: ContinuitySession(
            CancellableEnvelope(
                provider,
                BoundedClient(provider, resource_root),
                saved["initial"],
                protocol["limits"],
                saved["started_epoch"],
            ),
            keep_reasoning=name == "continuity",
        )
        for name, provider in providers.items()
    }
    if set(protocol["conditions"]) != set(CONDITIONS) or any(
        clients[name].configuration != protocol["conditions"][name]
        for name in CONDITIONS
    ):
        raise ValueError("Conditions differ from their prospective enrollment")
    stores, executor = (
        stores_for(root, protocol),
        runtime(root / "proposals", dependencies),
    )
    for task in tasks:
        rotation = int(hashlib.sha256(task["task_id"].encode()).hexdigest()[:8], 16) % 2
        for name in CONDITIONS[rotation:] + CONDITIONS[:rotation]:
            for source, expected in protocol["files"].items():
                if source.startswith(("cognition/", "scripts/", "tests/")):
                    if (
                        hashlib.sha256(Path(source).read_bytes()).hexdigest()
                        != expected
                    ):
                        raise ValueError("Frozen execution source changed during work")
            row = run_interactive_work(
                clients[name],
                executor,
                stores[name],
                task,
                job_id="continuity-" + name + "-" + task["task_id"].split("/")[1],
                experience=history,
                grade=False,
            )
            print(
                json.dumps(
                    {
                        "condition": name,
                        "task_id": task["task_id"],
                        "committed": True,
                        "accepted": row["accepted"],
                        "replayed": row["replayed"],
                        "calls": len(row["attempts"]),
                        "budget": providers[name].budget.snapshot(),
                    }
                ),
                flush=True,
            )
    return {"programs": len(tasks) * 2, "terminal_checks_revealed": False}


def usage(records, configuration):
    uses = [a["usage"] for row in records for a in row["attempts"] if a.get("usage")]
    cost = 0
    for use in uses:
        long = use["prompt_tokens"] > configuration["long_context_threshold"]
        input_rate = configuration["input_micro_usd_per_million_tokens"] * (
            2 if long else 1
        )
        output_rate = configuration["output_micro_usd_per_million_tokens"] * (
            1.5 if long else 1
        )
        cost += math.ceil(
            (use["prompt_tokens"] * input_rate + use["completion_tokens"] * output_rate)
            / 1_000_000
        )
    return {
        "calls": len(uses),
        "prompt_tokens": sum(x["prompt_tokens"] for x in uses),
        "completion_tokens": sum(x["completion_tokens"] for x in uses),
        "reasoning_tokens": sum(x["reasoning_tokens"] for x in uses),
        "configured_conservative_micro_usd": cost,
    }


def grade(root, dependencies=()):
    root = Path(root)
    protocol, tasks, records = proposals(root)
    if set(records) != set(CONDITIONS):
        raise ValueError("Use exactly the two enrolled continuity conditions")
    raw = (root / "proposals-before-grade.json").read_bytes()
    proof = json.loads((root / "public-proposals-proof.json").read_text())
    if (
        proof.get("repository") != "trynuria/nuria"
        or not re.fullmatch(r"[a-f0-9]{40}", proof.get("commit", ""))
        or proof.get("sha256") != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError(
            "An independently verified public program commitment is required"
        )
    committed = json.loads(raw)
    if committed["protocol_sha256"] != identity(protocol):
        raise ValueError("Public proposals belong to a different protocol")
    by_task = {x["task_id"]: x for x in tasks}
    stores, executor = (
        stores_for(root, protocol),
        runtime(root / "terminal", dependencies),
    )
    vectors, complete = {}, {}
    for name, rows in records.items():
        before = {x["task_id"]: x for x in committed["records"][name]}
        complete[name] = []
        for row in rows:
            omitted = {"checker", "accepted"}
            if {k: v for k, v in row.items() if k not in omitted} != {
                k: v for k, v in before[row["task_id"]].items() if k not in omitted
            }:
                raise ValueError("The committed program changed before grading")
            if row["checker"] is None:
                task = by_task[row["task_id"]]
                checked = executor.run(
                    row["code"],
                    task["checker"],
                    label=row["job_id"] + SUFFIX[row["version"]],
                    inputs=task.get("inputs"),
                )
                row = stores[name].accept(row["job_id"], checked)
            complete[name].append(row)
        stores[name].verify()
        vectors[name] = [x["accepted"] for x in complete[name]]
    comparison = exact_paired(vectors["continuity"], vectors["visible"])
    families = {
        family: {
            name: sum(row["accepted"] for row in rows if row["family"] == family)
            for name, rows in complete.items()
        }
        for family in sorted({task["family"] for task in tasks})
    }
    result = {
        "tasks": len(tasks),
        "accepted": {name: sum(v) for name, v in vectors.items()},
        "comparison": comparison,
        "families": families,
        "usage": {
            name: usage(rows, protocol["conditions"][name])
            for name, rows in complete.items()
        },
        "superiority_supported": len(tasks) >= 70
        and comparison["accuracy_delta"] >= 0.04
        and comparison["one_sided_exact_p"] <= 0.0125
        and all(v["continuity"] >= v["visible"] - 2 for v in families.values()),
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
        if args.config is None or args.resource_root is None:
            parser.error(
                "generation needs an explicit private config and resource authority"
            )
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

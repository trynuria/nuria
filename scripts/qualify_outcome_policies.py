"""Choose an execution policy using validation outcomes, before fresh work."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cognition.outcome_worker import BASE_POLICY, OutcomeStore, identity, run_work
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_outcome_loop import BoundedClient, executor, load_tasks, save


def select(scores):
    """Greater acceptance wins; the simpler method wins an exact tie."""
    order = ["simple_retrieval", "simple_reflection", "nuria_gepa"]
    if "outcome_playbook" in scores:
        order.insert(2, "outcome_playbook")
    if set(scores) != set(order):
        raise ValueError("All three policy qualifications are required")
    return max(order, key=lambda name: scores[name])


def qualify(root, config, dependencies):
    root = Path(root)
    tasks = load_tasks(root)
    common = json.loads((root / "common-experience.json").read_text())
    client = BoundedClient(load_reasoner(config), root)
    policies = {
        "simple_retrieval": BASE_POLICY,
        "simple_reflection": json.loads((root / "simple-reflection.json").read_text())[
            "policy"
        ],
        "nuria_gepa": json.loads((root / "learned-policy.json").read_text())["policy"],
    }
    runtime = executor(root / "reflection-validation", dependencies)
    store = OutcomeStore(root / "reflection-validation.sqlite")
    for task in tasks["validation"]:
        run_work(
            client,
            runtime,
            store,
            task,
            job_id="val-reflect-" + task["task_id"].split("/")[1],
            policy=policies["simple_reflection"],
            experience=common,
        )
    records = store.experiences()
    gepa = json.loads((root / "gepa-result.json").read_text())
    scores = {
        "simple_retrieval": gepa["val_aggregate_scores"][0],
        "simple_reflection": sum(x["accepted"] for x in records)
        / len(tasks["validation"]),
        "nuria_gepa": max(gepa["val_aggregate_scores"]),
    }
    playbook_file = root / "outcome-playbook.json"
    if playbook_file.exists():
        playbook = json.loads(playbook_file.read_text())
        if playbook["training_experience_sha256"] != identity(common):
            raise ValueError("Playbook training history differs")
        policies["outcome_playbook"] = playbook["policy"]
        play_store = OutcomeStore(root / "playbook-validation.sqlite")
        play_runtime = executor(root / "playbook-validation", dependencies)
        for task in tasks["validation"]:
            run_work(
                client,
                play_runtime,
                play_store,
                task,
                job_id="val-playbook-" + task["task_id"].split("/")[1],
                policy=playbook["policy"],
                experience=common,
            )
        scores["outcome_playbook"] = sum(
            x["accepted"] for x in play_store.experiences()
        ) / len(tasks["validation"])
    selected = select(scores)
    artifact = {
        "version": "nuria.outcome-policy.v1",
        "policy": policies[selected],
        "policy_sha256": identity(policies[selected]),
        "base_policy_sha256": identity(BASE_POLICY),
        "experience_sha256": identity(common),
        "selected_method": selected,
        "validation_scores": scores,
        "model": client.configuration,
        "enrollment": {
            split: [
                {k: task[k] for k in ("task_id", "source_sha256")}
                for task in tasks[split]
            ]
            for split in ("train", "validation")
        },
        "selection_rule": "highest independent validation acceptance, simpler method wins ties; no test feedback",
        "journal": store.verify(),
        "activated": False,
    }
    artifact["artifact_sha256"] = identity(artifact)
    prior = root / "runtime-policy-before-test.json"
    if prior.exists():
        previous = root / "runtime-policy-before-playbook-qualification.json"
        if not previous.exists():
            previous.write_bytes(prior.read_bytes())
    save(root / "runtime-policy-before-test.json", artifact)
    return {
        "selected_method": selected,
        "validation_scores": scores,
        "policy_sha256": artifact["policy_sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    print(
        json.dumps(
            qualify(args.root, args.config, args.dependency_root), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()

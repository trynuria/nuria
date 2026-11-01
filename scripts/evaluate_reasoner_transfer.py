"""Compare two explicitly enrolled reasoning configurations on fresh work.

Both use the same frozen experience and existing three-call retrieval workflow.
Terminal checks remain withheld until every proposal has a public commitment.
This evaluates a model/effort package, not neural learning or consciousness.
"""

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from cognition.experience_navigation import run_navigation_work
from cognition.outcome_worker import OutcomeStore, identity
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_experience_navigation import read_rows, runtime
from scripts.evaluate_outcome_loop import BoundedClient, exact_paired, save
from scripts.evaluate_recovery_options import QualificationEnvelope


def enrolled(root):
    root = Path(root)
    protocol = json.loads((root / "protocol.json").read_text())
    for name, expected in protocol["files"].items():
        path = (
            Path(name)
            if name.startswith(("cognition/", "scripts/", "tests/"))
            else root / name
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Prospective source or evidence identity changed")
    tasks = read_rows(root / "tasks.jsonl.gz")
    reference = json.loads((root / "preflight.json").read_text())
    valid = {row["task_id"] for row in reference if row["checker"]["passed"]}
    tasks = [row for row in tasks if row["task_id"] in valid]
    if [row["task_id"] for row in tasks] != protocol["valid_task_ids"]:
        raise ValueError("Reference-valid task enrollment changed")
    history = json.loads((root / "experience.json").read_text())
    if {row["task_id"] for row in tasks} & {row["task_id"] for row in history}:
        raise ValueError("Fresh work overlaps enrolled experience")
    return protocol, tasks, history


def stores_for(root, protocol):
    return {
        name: OutcomeStore(Path(root) / (name + ".sqlite"))
        for name in protocol["conditions"]
    }


def generate(root, configurations, resource_root, dependencies=()):
    root = Path(root)
    protocol, tasks, history = enrolled(root)
    if set(configurations) != set(protocol["conditions"]):
        raise ValueError("Use exactly the two prospective conditions")
    providers = {name: load_reasoner(path) for name, path in configurations.items()}
    if len({p.budget.path.resolve() for p in providers.values()}) != 1:
        raise ValueError("Conditions must share the existing cumulative allowance")
    for name, provider in providers.items():
        provider.timeout = 90
        provider.max_output_tokens = 2400
        if provider.configuration != protocol["conditions"][name]:
            raise ValueError("Provider differs from its prospective enrollment")
    guard = root / "generation-budget.json"
    if guard.exists():
        saved = json.loads(guard.read_text())
        if saved["protocol_sha256"] != identity(protocol):
            raise ValueError("Reasoning comparison changed after startup")
    else:
        saved = {
            "protocol_sha256": identity(protocol),
            "initial": next(iter(providers.values())).budget.snapshot(),
            "started_epoch": time.time(),
        }
        with guard.open("x") as stream:
            json.dump(saved, stream, sort_keys=True, indent=2)
            stream.write("\n")
    clients = {
        name: QualificationEnvelope(
            provider,
            BoundedClient(provider, resource_root),
            saved["initial"],
            protocol["limits"],
            saved["started_epoch"],
        )
        for name, provider in providers.items()
    }
    stores = stores_for(root, protocol)
    executor = runtime(root / "proposals", dependencies)
    names = list(protocol["conditions"])
    for task in tasks:
        rotation = int(hashlib.sha256(task["task_id"].encode()).hexdigest()[:8], 16) % 2
        for name in names[rotation:] + names[:rotation]:
            row = run_navigation_work(
                clients[name],
                executor,
                stores[name],
                task,
                job_id="transfer-" + name + "-" + task["task_id"].split("/")[1],
                method="retrieval",
                experience=history,
                grade=False,
                max_tokens=2400,
            )
            print(
                json.dumps(
                    {
                        "condition": name,
                        "task_id": task["task_id"],
                        "committed": True,
                        "accepted": row["accepted"],
                        "replayed": row["replayed"],
                    }
                ),
                flush=True,
            )
    for store in stores.values():
        store.verify()
    return {"programs": len(tasks) * 2, "terminal_checks_revealed": False}


def proposals(root):
    protocol, tasks, _ = enrolled(root)
    expected = {row["task_id"] for row in tasks}
    records = {}
    for name, store in stores_for(root, protocol).items():
        store.verify()
        rows = store.experiences()
        if len(rows) != len(tasks) or {row["task_id"] for row in rows} != expected:
            raise ValueError(
                "Every fresh proposal must commit before any terminal grading"
            )
        records[name] = sorted(rows, key=lambda row: row["task_id"])
    return protocol, tasks, records


def seal(root):
    protocol, tasks, records = proposals(root)
    if any(row["checker"] is not None for rows in records.values() for row in rows):
        raise ValueError("Pre-grade commitment cannot contain terminal results")
    result = {
        "version": "nuria.reasoner-proposals.v1",
        "protocol_sha256": identity(protocol),
        "tasks": len(tasks),
        "records": records,
        "terminal_checks_revealed": False,
    }
    path = Path(root) / "proposals-before-grade.json"
    with path.open("x") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return {
        "programs": 2 * len(tasks),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def grade(root, dependencies=()):
    root = Path(root)
    protocol, tasks, records = proposals(root)
    seal_path = root / "proposals-before-grade.json"
    proof = json.loads((root / "public-proposals-proof.json").read_text())
    if (
        proof.get("repository") != "trynuria/nuria"
        or not re.fullmatch(r"[a-f0-9]{40}", proof.get("commit", ""))
        or proof.get("sha256") != hashlib.sha256(seal_path.read_bytes()).hexdigest()
    ):
        raise ValueError(
            "An independently verified public proposal commitment is required"
        )
    committed = json.loads(seal_path.read_text())
    if committed["protocol_sha256"] != identity(protocol):
        raise ValueError("Public proposals belong to a different protocol")
    by_task = {task["task_id"]: task for task in tasks}
    executor = runtime(root / "terminal", dependencies)
    stores = stores_for(root, protocol)
    vectors, families = {}, {}
    for name, rows in records.items():
        original = {row["task_id"]: row for row in committed["records"][name]}
        checked_rows = []
        for row in rows:
            task = by_task[row["task_id"]]
            prior = original[row["task_id"]]
            if {k: v for k, v in row.items() if k not in {"checker", "accepted"}} != {
                k: v for k, v in prior.items() if k not in {"checker", "accepted"}
            }:
                raise ValueError("Proposed program changed after public commitment")
            if row["checker"] is None:
                checked = executor.run(
                    row["code"],
                    task["checker"],
                    label=name + "-" + task["task_id"].split("/")[1],
                )
                row = stores[name].accept(row["job_id"], checked)
            checked_rows.append(row)
        vectors[name] = [row["accepted"] for row in checked_rows]
        for family in {task["family"] for task in tasks}:
            families.setdefault(family, {})[name] = sum(
                row["accepted"] for row in checked_rows if row["family"] == family
            )
        stores[name].verify()
    comparison = exact_paired(vectors["astra-medium"], vectors["sol-low"])
    result = {
        "tasks": len(tasks),
        "accepted": {name: sum(v) for name, v in vectors.items()},
        "comparison": comparison,
        "families": families,
        "superiority_supported": len(tasks) >= 70
        and comparison["accuracy_delta"] >= 0.04
        and comparison["one_sided_exact_p"] <= 0.0125
        and all(v["astra-medium"] >= v["sol-low"] - 2 for v in families.values()),
        "runtime_promoted": False,
        "neural_advantage_established": False,
    }
    save(root / "results.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("generate", "seal", "grade"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--configuration", action="append", default=[])
    parser.add_argument("--resource-root", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    if args.stage == "generate":
        result = generate(
            args.root,
            dict(item.split("=", 1) for item in args.configuration),
            args.resource_root,
            args.dependency_root,
        )
    elif args.stage == "seal":
        result = seal(args.root)
    else:
        result = grade(args.root, args.dependency_root)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

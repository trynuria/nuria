"""Outcome-derived programming policies, with frozen external held-out tasks.

GEPA is an optional dependency. Hosted calls require an explicit private client
configuration and continue its existing shared allowance. This is a bounded
research runner, not a production service or a benchmark score for consciousness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import time
from pathlib import Path

from cognition.outcome_worker import (
    BASE_POLICY,
    IsolatedPython,
    OutcomeStore,
    identity,
    run_work,
)
from scripts.ask_hosted_analyst import load_reasoner


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


class BoundedClient:
    """A sub-envelope of the existing allowance; never an additional budget."""

    def __init__(self, client, root):
        self.client, self.root = client, Path(root)
        self.plan = json.loads((self.root / "resource-plan.json").read_text())
        self.configuration = client.configuration
        self.calls = Path(
            self.plan.get("provider_log_file", self.root / "provider-calls.jsonl")
        )
        self.resource_root = Path(self.plan.get("resource_root", self.root))

    def complete(self, messages, *, max_tokens, seed=0):
        plan = self.plan
        budget = self.client.budget.snapshot()
        rows = (
            [json.loads(x) for x in self.calls.read_text().splitlines()]
            if self.calls.exists()
            else []
        )
        body = {
            "model": self.client.model,
            "reasoning": {"effort": self.client.effort},
            "input": messages,
            "max_output_tokens": max_tokens,
            "store": False,
            "stream": False,
            "service_tier": "default",
            "truncation": "disabled",
        }
        input_bound = (
            len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode())
            + 1024
            + 128 * len(messages)
        )
        ceiling = math.ceil(
            (
                input_bound * self.client.input_rate
                + max_tokens * self.client.output_rate
            )
            / 1_000_000
        )
        spent = (
            budget["accounted_cost_and_liability_micro_usd"]
            - plan["phase_start_charged_micro_usd"]
        )
        if (
            budget["blocked"]
            or spent + ceiling > plan["additional_phase_ceiling_micro_usd"]
        ):
            raise ValueError(
                "Phase or shared spending envelope is exhausted or unresolved"
            )
        if (
            budget["requests"] - plan["phase_start_requests"]
            >= plan["max_provider_calls"]
        ):
            raise ValueError("Phase request allowance exhausted")
        if (
            sum(x["wall_seconds"] for x in rows) + self.client.timeout
            > plan["max_provider_wall_seconds"]
        ):
            raise ValueError("Phase inference wall-time allowance exhausted")
        if max_tokens > plan["max_output_tokens_per_call"]:
            raise ValueError("Per-call completion allowance exceeded")
        if shutil.disk_usage(self.resource_root).free < plan["free_disk_floor_bytes"]:
            raise ValueError("Free-disk floor reached")
        retained = sum(
            p.stat().st_size for p in self.resource_root.rglob("*") if p.is_file()
        )
        if retained > plan["max_retained_bytes"]:
            raise ValueError("Retained-file ceiling reached")
        started = time.monotonic()
        try:
            result = self.client.complete(messages, max_tokens=max_tokens, seed=seed)
        except Exception as error:
            row = {
                "request_sha256": identity(body),
                "state": "failed",
                "wall_seconds": round(time.monotonic() - started, 6),
                "reserved_ceiling_micro_usd": ceiling,
                "usage": getattr(error, "usage", None),
                "error_code": getattr(error, "code", type(error).__name__),
            }
            with self.calls.open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            raise
        row = {
            "request_sha256": identity(body),
            "state": "completed",
            "wall_seconds": round(time.monotonic() - started, 6),
            "usage": result["usage"],
            "response_sha256": identity(result["content"]),
        }
        with self.calls.open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        transcripts = self.calls.parent / "provider-transcripts"
        transcripts.mkdir(exist_ok=True)
        save(
            transcripts / (str(budget["requests"] + 1) + ".json"),
            {"request": body, "response": result},
        )
        return result


def load_tasks(root):
    root = Path(root)
    mode_file = root / "input-mode.json"
    mode = (
        json.loads(mode_file.read_text())["mode"] if mode_file.exists() else "instruct"
    )
    if mode not in ("instruct", "complete"):
        raise ValueError("Unknown specification enrollment")
    cohort = json.loads((root / "cohort-before-task-bodies.json").read_text())
    records = {}
    for file in sorted((root / "corpus").glob("rows-*.json")):
        for item in json.loads(file.read_text())["rows"]:
            row = item["row"]
            records[row["task_id"]] = row
    result = {}
    for split, items in cohort["cohort"].items():
        result[split] = []
        for meta in items:
            row = records[meta["task_id"]]
            checker = row["test"] + (
                "\nimport unittest\n"
                "_suite=unittest.defaultTestLoader.loadTestsFromTestCase(TestCases)\n"
                "_result=unittest.TextTestRunner(verbosity=1).run(_suite)\n"
                "assert _result.testsRun > 0, 'No independent checks ran'\n"
                "assert _result.wasSuccessful(), 'Independent checks failed'\n"
            )
            result[split].append(
                {
                    "task_id": row["task_id"],
                    "family": meta["family"],
                    "prompt": row["complete_prompt"]
                    if mode == "complete"
                    else row["instruct_prompt"]
                    + "\nRequired signature:\n"
                    + row["code_prompt"],
                    "checker": checker,
                    "source_sha256": identity(row),
                    "canonical_solution": row["code_prompt"]
                    + row["canonical_solution"],
                }
            )
    return result


def exact_paired(candidate, control):
    wins = sum(bool(a) and not bool(b) for a, b in zip(candidate, control))
    losses = sum(bool(b) and not bool(a) for a, b in zip(candidate, control))
    total = wins + losses
    p = (
        sum(math.comb(total, k) for k in range(wins, total + 1)) / (2**total)
        if total
        else 1.0
    )
    return {
        "wins": wins,
        "losses": losses,
        "one_sided_exact_p": p,
        "accuracy_delta": (sum(candidate) - sum(control)) / len(candidate),
    }


def executor(root, dependencies):
    return IsolatedPython(Path(root) / "executions", dependency_roots=dependencies)


def preflight(root, dependencies, *, run_name="oracle-preflight"):
    """Check the frozen oracle cohort; retain failures rather than resampling."""
    root = Path(root)
    tasks = load_tasks(root)
    runtime = executor(root / run_name, dependencies)
    records = []
    for split, items in tasks.items():
        for task in items:
            code = task["canonical_solution"]
            # The pinned release stores the body separately from code_prompt.
            report = runtime.run(
                code, task["checker"], label=task["task_id"].replace("/", "-")
            )
            records.append(
                {
                    "task_id": task["task_id"],
                    "split": split,
                    "source_sha256": task["source_sha256"],
                    "checker": report,
                }
            )
    save(root / (run_name + ".json"), records)
    return {
        "checked": len(records),
        "passed": sum(x["checker"]["passed"] for x in records),
    }


def train(root, config, dependencies):
    root = Path(root)
    tasks = load_tasks(root)["train"]
    client = BoundedClient(load_reasoner(config), root)
    runtime, store = (
        executor(root / "training", dependencies),
        OutcomeStore(root / "training.sqlite"),
    )
    for task in tasks:
        # Each seed training task starts from the same strong policy. The
        # common corpus is frozen only after every training outcome exists.
        result = run_work(
            client,
            runtime,
            store,
            task,
            job_id="train-" + task["task_id"].split("/")[1],
            experience=[],
        )
        print(
            json.dumps(
                {
                    "stage": "train",
                    "task_id": task["task_id"],
                    "accepted": result["accepted"],
                }
            ),
            flush=True,
        )
    records = store.experiences()
    save(root / "common-experience.json", records)
    return {
        "jobs": len(records),
        "accepted": sum(x["accepted"] is True for x in records),
        "journal": store.verify(),
    }


def optimize(root, config, dependencies):
    from gepa.optimize_anything import (
        EngineConfig,
        GEPAConfig,
        ReflectionConfig,
        optimize_anything,
    )

    root = Path(root)
    tasks = load_tasks(root)
    experience = json.loads((root / "common-experience.json").read_text())
    client = BoundedClient(load_reasoner(config), root)
    runtime, store = (
        executor(root / "optimization", dependencies),
        OutcomeStore(root / "optimization.sqlite"),
    )
    sequence = []
    optimization_plan = root / "optimization-plan.json"
    limits = (
        json.loads(optimization_plan.read_text())
        if optimization_plan.exists()
        else {"max_metric_calls": 72, "max_candidate_proposals": 3}
    )

    def evaluate(policy, example):
        key = "opt-" + identity([policy, example["task_id"]])[:24]
        result = run_work(
            client,
            runtime,
            store,
            example,
            job_id=key,
            policy=policy,
            experience=experience,
        )
        sequence.append(
            {
                "job_id": key,
                "task_id": example["task_id"],
                "policy_sha256": identity(policy),
                "accepted": result["accepted"],
            }
        )
        save(root / "optimization-sequence.json", sequence)
        # Binary outcome is the objective; diagnostic failures drive reflection.
        return float(result["accepted"] is True), {
            "task": result["prompt"],
            "program": result["code"],
            "outcome": result["checker"],
        }

    def reflect(prompt):
        messages = (
            prompt
            if isinstance(prompt, list)
            else [{"role": "user", "content": str(prompt)}]
        )
        response = client.complete(messages, max_tokens=2400)
        return response["content"]

    # A conventional one-reflection control gets the same whole training
    # corpus, model and bounded reflection request as the learned system.
    control_file = root / "simple-reflection.json"
    if not control_file.exists():
        compact = [
            {
                "task": x["prompt"][:1800],
                "accepted": x["accepted"],
                "code": x["code"][:2500],
                "outcome": {
                    "passed": x["checker"]["passed"],
                    "stderr_tail": x["checker"]["stderr"][-1000:],
                },
            }
            for x in experience
        ]
        reply = client.complete(
            [
                {
                    "role": "system",
                    "content": "Improve a concise general programming policy using these actual checked outcomes. Do not memorize task answers. Return only the complete policy, at most 6000 characters.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"policy": BASE_POLICY, "experience": compact}
                    ),
                },
            ],
            max_tokens=2400,
        )
        save(
            control_file,
            {
                "policy": reply["content"],
                "training_experience_sha256": identity(experience),
            },
        )
    result = optimize_anything(
        seed_candidate=BASE_POLICY,
        evaluator=evaluate,
        dataset=tasks["train"],
        valset=tasks["validation"],
        objective="Improve reliable execution on varied fresh Python work. Learn transferable implementation and checking strategies from real execution failures; never memorize task identifiers or bypass checking.",
        background="Offline Python 3.14, numpy/pandas/matplotlib and standard library; two proposal calls maximum; the same ordinary training-example retrieval for all policies.",
        config=GEPAConfig(
            engine=EngineConfig(
                max_metric_calls=limits["max_metric_calls"],
                max_candidate_proposals=limits["max_candidate_proposals"],
                parallel=False,
                use_cloudpickle=False,
                seed=0,
                candidate_selection_strategy="current_best",
                acceptance_criterion=limits.get(
                    "acceptance_criterion", "strict_improvement"
                ),
            ),
            reflection=ReflectionConfig(
                reflection_lm=reflect,
                reflection_minibatch_size=limits.get("reflection_minibatch_size", 3),
            ),
        ),
    )
    save(root / "gepa-result.json", result.to_dict())
    save(
        root / "learned-policy.json",
        {
            "policy": result.best_candidate,
            "policy_sha256": identity(result.best_candidate),
            "training_experience_sha256": identity(experience),
            "validation_scores": result.val_aggregate_scores,
            "metric_calls": result.total_metric_calls,
        },
    )
    return {
        "candidates": result.num_candidates,
        "validation_scores": result.val_aggregate_scores,
        "policy_changed": result.best_candidate != BASE_POLICY,
        "journal": store.verify(),
    }


def test(root, config, dependencies):
    root = Path(root)
    tasks = load_tasks(root)["test"]
    experience = json.loads((root / "common-experience.json").read_text())
    client = BoundedClient(load_reasoner(config), root)
    policies = {
        "simple_retrieval": BASE_POLICY,
        "simple_reflection": json.loads((root / "simple-reflection.json").read_text())[
            "policy"
        ],
        "nuria_gepa": json.loads((root / "learned-policy.json").read_text())["policy"],
    }
    shared_gepa = policies["nuria_gepa"] == BASE_POLICY
    if (root / "outcome-playbook.json").exists():
        policies["outcome_playbook"] = json.loads(
            (root / "outcome-playbook.json").read_text()
        )["policy"]
    selection_file = root / "runtime-policy-before-test.json"
    selection = (
        json.loads(selection_file.read_text()) if selection_file.exists() else None
    )
    seal = {
        "test_tasks": [
            {k: x[k] for k in ("task_id", "source_sha256", "family")} for x in tasks
        ],
        "policy_sha256": {k: identity(v) for k, v in policies.items()},
        "experience_sha256": identity(experience),
        "scoring": "all independent unit tests pass",
        "method_order": "rotate by task index",
        "test_feedback": "after all proposals are committed",
        "gepa_execution_alias_of": "simple_retrieval" if shared_gepa else None,
        "runtime_selection": None
        if selection is None
        else {
            k: selection[k]
            for k in ("selected_method", "policy_sha256", "artifact_sha256")
        },
        "execution_source_sha256": {
            name: hashlib.sha256(
                (Path(__file__).resolve().parents[1] / name).read_bytes()
            ).hexdigest()
            for name in [
                "cognition/outcome_worker.py",
                "cognition/hosted_reasoner.py",
                "cognition/reasoner_client.py",
                "scripts/evaluate_outcome_loop.py",
                "scripts/ask_hosted_analyst.py",
            ]
        },
    }
    freeze = root / "test-execution-freeze.json"
    if freeze.exists() and json.loads(freeze.read_text()) != seal:
        raise ValueError("A held-out execution cannot be silently changed")
    save(freeze, seal)
    runtimes = {k: executor(root / k, dependencies) for k in policies}
    stores = {k: OutcomeStore(root / (k + ".sqlite")) for k in policies}
    names = list(policies)
    active_names = [
        name for name in names if not (shared_gepa and name == "nuria_gepa")
    ]
    for index, task in enumerate(tasks):
        shift = index % len(active_names)
        for name in active_names[shift:] + active_names[:shift]:
            run_work(
                client,
                runtimes[name],
                stores[name],
                task,
                job_id="test-" + task["task_id"].split("/")[1],
                policy=policies[name],
                experience=experience,
                grade=False,
            )
        print(
            json.dumps(
                {
                    "stage": "test-proposals",
                    "completed_tasks": index + 1,
                    "total": len(tasks),
                }
            ),
            flush=True,
        )
    # No held-out terminal outcome enters any method's experience or prompt.
    summaries = {}
    outcomes = {}
    for name, store in stores.items():
        if name == "nuria_gepa" and shared_gepa:
            outcomes[name] = outcomes["simple_retrieval"].copy()
            summaries[name] = {
                **summaries["simple_retrieval"],
                "execution_alias_of": "simple_retrieval",
            }
            continue
        records = {x["task_id"]: x for x in store.experiences()}
        for task in tasks:
            item = records[task["task_id"]]
            if item["checker"] is None:
                report = runtimes[name].run(
                    item["code"], task["checker"], label=item["job_id"] + "-terminal"
                )
                records[task["task_id"]] = store.accept(item["job_id"], report)
        rows = [records[x["task_id"]] for x in tasks]
        outcomes[name] = [x["accepted"] for x in rows]
        summaries[name] = {
            "accepted": sum(outcomes[name]),
            "tasks": len(rows),
            "families": {
                family: {
                    "accepted": sum(
                        x["accepted"] for x in rows if x["family"] == family
                    ),
                    "tasks": sum(x["family"] == family for x in rows),
                }
                for family in sorted({x["family"] for x in rows})
            },
            "journal": store.verify(),
        }
    comparisons = {
        name: exact_paired(outcomes["nuria_gepa"], outcomes[name])
        for name in ("simple_retrieval", "simple_reflection")
    }
    result = {
        "methods": summaries,
        "paired": comparisons,
        "success_rule": "positive accuracy difference and one-sided exact paired p <= .025 against BOTH simpler controls; no family loses more than one task",
        "budget": client.client.budget.snapshot(),
        "learned_policy_changed": policies["nuria_gepa"] != BASE_POLICY,
        "physical_execution_methods": active_names,
        "reflection_vs_retrieval": exact_paired(
            outcomes["simple_reflection"], outcomes["simple_retrieval"]
        ),
        "runtime_selection": seal["runtime_selection"],
    }
    if selection is not None:
        selected = selection["selected_method"]
        comparison = exact_paired(outcomes[selected], outcomes["simple_retrieval"])
        result["selected_policy_comparison"] = {
            **comparison,
            "method": selected,
            "three_comparison_adjusted_p": min(
                1.0, comparison["one_sided_exact_p"] * 3
            ),
            "improvement_supported": selected != "simple_retrieval"
            and comparison["accuracy_delta"] > 0
            and comparison["one_sided_exact_p"] <= 0.05 / 3,
        }
    if "outcome_playbook" in outcomes:
        result["playbook_comparisons"] = {
            method: {
                **exact_paired(outcomes["outcome_playbook"], outcomes[method]),
                "three_comparison_adjusted_p": min(
                    1.0,
                    exact_paired(outcomes["outcome_playbook"], outcomes[method])[
                        "one_sided_exact_p"
                    ]
                    * 3,
                ),
            }
            for method in ("simple_retrieval", "simple_reflection")
        }
        result["playbook_advantage_demonstrated"] = all(
            item["accuracy_delta"] > 0 and item["three_comparison_adjusted_p"] <= 0.05
            for item in result["playbook_comparisons"].values()
        ) and all(
            summaries["outcome_playbook"]["families"][family]["accepted"]
            >= summaries[control]["families"][family]["accepted"] - 1
            for control in ("simple_retrieval", "simple_reflection")
            for family in summaries[control]["families"]
        )
    result["gepa_advantage_demonstrated"] = (
        result["learned_policy_changed"]
        and all(
            x["accuracy_delta"] > 0 and x["one_sided_exact_p"] <= 0.025
            for x in comparisons.values()
        )
        and all(
            summaries["nuria_gepa"]["families"][family]["accepted"]
            >= summaries[control]["families"][family]["accepted"] - 1
            for control in comparisons
            for family in summaries[control]["families"]
        )
    )
    save(root / "held-out-results.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preflight", "train", "optimize", "test"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--preflight-label", default="oracle-preflight")
    args = parser.parse_args()
    if args.stage != "preflight" and args.config is None:
        parser.error("Paid stages need an explicit private configuration")
    value = (
        preflight(args.root, args.dependency_root, run_name=args.preflight_label)
        if args.stage == "preflight"
        else globals()[args.stage](args.root, args.config, args.dependency_root)
    )
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()

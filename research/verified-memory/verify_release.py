"""Recheck retained model claims; semantic failures make this command fail."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import check_tradeoff
import goal_robust_check as goal
import robust_check
import transplant_check as neural
import transport_check

ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(relative):
    return json.loads((ROOT / relative).read_text())


def reject_contract(spec, mutate):
    changed = copy.deepcopy(spec)
    mutate(changed)
    try:
        neural.NeuralRuntime(changed)
    except ValueError:
        return True
    return False


def verify():
    index = load("evidence/index.json")
    for relative, expected in index["files"].items():
        require(
            hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected,
            f"Changed evidence/source: {relative}",
        )
    models = {}
    for name in ("initial-9102", "initial-9103", "initial-9104", "repair"):
        runtime = neural.NeuralRuntime(load(f"evidence/{name}/network-00.json"))
        closure, _, _ = neural.reachable_check(runtime)
        require(closure["passed"], f"World closure failed: {name}")
        models[name] = runtime

    summary = {"checked_files": len(index["files"]), "models": {}}
    for name, radius, memory_cases, policy_cases in (
        ("factorized", 2, 1896, 5056),
        ("mixed", 3, 7176, 19136),
    ):
        spec = load(f"evidence/{name}/network-00.json")
        runtime = neural.NeuralRuntime(spec)
        models[name] = runtime
        closure, _, _ = neural.reachable_check(runtime)
        require(closure["passed"], f"World closure failed: {name}")
        codes, initial = robust_check.canonical_codes(runtime)
        require(initial["passed"], f"Canonical initialization failed: {name}")
        repair = robust_check.certify(runtime, codes, radius + 1)
        certified = repair["radius_certificates"][radius]
        require(certified["passed"], f"Bounded repair failed: {name}")
        require(
            certified["finite_cases_checked"] == memory_cases,
            f"Repair coverage changed: {name}",
        )
        require(
            not repair["radius_certificates"][radius + 1]["passed"],
            f"Expected outside-bound counterexample absent: {name}",
        )
        geometry = check_tradeoff.classify_codes(codes, 12)
        expected_masks = name == "factorized"
        require(
            all(
                bool(v) == expected_masks
                for v in geometry["canonical_masks_by_factor"].values()
            ),
            f"Canonical editing geometry changed: {name}",
        )
        memory = goal.Memory(spec)
        policy = goal.Policy(load(f"evidence/{name}-goal/goal-policy.json"), 12)
        canonical, goals, world = goal.establish_closure(memory, policy)
        require(world["passed"], f"Policy/world binding failed: {name}")
        direct = goal.evaluate(memory, policy, canonical, goals, radius, False)
        refreshed = goal.evaluate(memory, policy, canonical, goals, radius, True)
        require(not direct["passed"], f"Expected damaged-policy failure absent: {name}")
        require(refreshed["passed"], f"Refreshed policy failed: {name}")
        require(
            refreshed["counts"]["cases"] == policy_cases,
            f"Policy coverage changed: {name}",
        )
        summary["models"][name] = {
            "memory_repair_cases": memory_cases,
            "refreshed_policy_cases": policy_cases,
            "repeated_corruption_bound": radius,
            "minimum_code_distance": geometry["minimum_distance"],
            "direct_corrupted_policy_rejected": True,
        }

    left, right = models["initial-9102"], models["initial-9103"]
    _, source, _ = neural.reachable_check(left)
    _, target, _ = neural.reachable_check(right)
    raw_copy_failures = 0
    for code in source:
        matches = [c for c in target if right.output(c) == left.output(code)]
        require(
            any(
                transport_check.equivalent_future(left, right, code, c)["passed"]
                for c in matches
            ),
            "Behavioral transport failed",
        )
        raw_copy_failures += not transport_check.equivalent_future(
            left, right, code, code
        )["passed"]
    require(raw_copy_failures == 4, "Raw-copy negative control changed")

    spec = load("evidence/mixed/network-00.json")
    require(
        reject_contract(spec, lambda s: s.update(alphabet=["swap", "flip", "noop"])),
        "Swapped alphabet accepted",
    )
    require(
        reject_contract(spec, lambda s: s.update(phase="unknown")),
        "Unknown phase accepted",
    )
    require(
        reject_contract(spec, lambda s: s.update(scale=True)), "Boolean scale accepted"
    )
    require(
        reject_contract(
            spec, lambda s: s["arrays"]["memory.bias"].__setitem__(0, 2**61)
        ),
        "Unsafe accumulator accepted",
    )
    broken = copy.deepcopy(spec)
    broken["arrays"]["output.bias"] = [10**9] * 6
    failed, _, _ = neural.reachable_check(neural.NeuralRuntime(broken))
    require(not failed["passed"], "Semantically false observation model accepted")
    policy_spec = load("evidence/mixed-goal/goal-policy.json")
    policy_spec["arrays"]["action.bias"] = [-(10**9)] * 4
    memory, policy = goal.Memory(spec), goal.Policy(policy_spec, 12)
    canonical, goals, binding = goal.establish_closure(memory, policy)
    if binding["passed"]:
        require(
            not goal.evaluate(memory, policy, canonical, goals, 0, True)["passed"],
            "Suppressed action policy accepted",
        )
    summary["contract_and_semantic_mutations_rejected"] = 6
    summary["independent_memory_transport_states"] = len(source)
    summary["raw_memory_copy_failures"] = raw_copy_failures
    summary["passed"] = True
    return summary


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))

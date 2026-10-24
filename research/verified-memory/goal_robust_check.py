"""Independent finite check of a corrupted neural memory + goal policy loop.

Only Python integer sums and thresholds execute learned networks. No training
module, NumPy, symbolic state decoder, graph planner, or teacher enters either
network's runtime. The separate evaluator binds these dynamics to a four-state
body and proves (or refutes) the stated decision-boundary fault schedule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import deque
from itertools import combinations
from pathlib import Path

ACTIONS = ("flip", "swap", "noop", "STOP")
BODIES = tuple((x, y) for x in (0, 1) for y in (0, 1))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def check_layer(arrays, layer, rows, columns):
    w, b = arrays[layer + ".weight"], arrays[layer + ".bias"]
    if len(w) != rows or len(b) != rows:
        raise ValueError("Layer row/bias shape mismatch: " + layer)
    if any(len(row) != columns for row in w):
        raise ValueError("Layer column shape mismatch: " + layer)
    if any(type(v) is not int for row in w for v in row) or any(
        type(v) is not int for v in b
    ):
        raise ValueError("Only exact integer weights/biases are accepted")
    if any(
        abs(bias) + sum(abs(value) for value in row) >= 2**60 for row, bias in zip(w, b)
    ):
        raise ValueError("Export accumulator exceeds declared 60-bit bound")


def layer(arrays, name, spikes):
    # Python integers give exact, unbounded accumulation; equality fires.
    return tuple(
        int(bias + sum(weight * bit for weight, bit in zip(row, spikes)) >= 0)
        for row, bias in zip(arrays[name + ".weight"], arrays[name + ".bias"])
    )


class Memory:
    def __init__(self, spec):
        if spec["format"] != "clocked-binary-spikes-v1":
            raise ValueError("Unexpected memory format")
        if spec["alphabet"] != list(ACTIONS[:3]):
            raise ValueError("Memory command binding mismatch")
        if (
            spec["phase"]
            != "canonical memory-spike boundary; transition membrane reset"
        ):
            raise ValueError("Memory checkpoint boundary mismatch")
        if spec["tick_order"] != [
            "transition population fires from input and queued memory spikes",
            "memory population fires from transition spikes",
            "output projection thresholded at zero",
        ]:
            raise ValueError("Memory phase order mismatch")
        self.n = spec["state_neurons"]
        self.hidden = spec["transition_neurons"]
        if (
            type(self.n) is not int
            or type(self.hidden) is not int
            or not 1 <= self.n <= 16
            or self.hidden < 1
        ):
            raise ValueError("Unsupported network dimensions")
        if type(spec["scale"]) is not int or spec["scale"] <= 0:
            raise ValueError("Invalid memory weight scale")
        self.a = spec["arrays"]
        check_layer(self.a, "transition", self.hidden, 9 + self.n)
        check_layer(self.a, "memory", self.n, self.hidden)
        check_layer(self.a, "output", 6, self.n)
        self.silenced = tuple(spec.get("silenced_memory_neurons", []))
        if any(type(i) is not int or not 0 <= i < self.n for i in self.silenced):
            raise ValueError("Bad silenced neuron index")

    def bits(self, code):
        if type(code) is not int or not 0 <= code < 2**self.n:
            raise ValueError("Bad spike code")
        return tuple(
            0 if i in self.silenced else (code >> i) & 1 for i in range(self.n)
        )

    def observe(self, code):
        return layer(self.a, "output", self.bits(code))

    def step(self, code, event):
        if len(event) != 9 or any(type(v) is not int or v not in (0, 1) for v in event):
            raise ValueError("Bad event")
        hidden = layer(self.a, "transition", tuple(event) + self.bits(code))
        spikes = list(layer(self.a, "memory", hidden))
        for index in self.silenced:
            spikes[index] = 0
        return sum(bit << i for i, bit in enumerate(spikes))


class Policy:
    def __init__(self, spec, n):
        if spec["format"] != "goal-conditioned-clocked-binary-policy-v1":
            raise ValueError("Unexpected policy format")
        if spec["actions"] != list(ACTIONS):
            raise ValueError("Policy action binding mismatch")
        if (
            spec["runtime"]
            != "hidden=threshold(W1@input+b1); action_spikes=threshold(W2@hidden+b2); threshold >=0"
        ):
            raise ValueError("Policy phase semantics mismatch")
        if type(spec["scale"]) is not int or spec["scale"] <= 0:
            raise ValueError("Invalid policy weight scale")
        self.a = spec["arrays"]
        self.goals = tuple(tuple(g) for g in spec["goal_channels"])
        if (
            len(self.goals) != 4
            or len(set(self.goals)) != 4
            or any(
                len(g) != 6 or any(type(v) is not int or v not in (0, 1) for v in g)
                for g in self.goals
            )
        ):
            raise ValueError("Invalid goal channels")
        hidden = len(self.a["hidden.bias"])
        check_layer(self.a, "hidden", hidden, n + 4)
        check_layer(self.a, "action", 4, hidden)

    def step(self, memory_spikes, goal_channel):
        goal_spikes = tuple(int(i == goal_channel) for i in range(4))
        hidden = layer(self.a, "hidden", tuple(memory_spikes) + goal_spikes)
        return layer(self.a, "action", hidden)


# Evaluator-only world and observations. None of these functions are called by
# Memory.step, Memory.observe, Policy.step, or the layers that execute networks.
def body_step(body, action):
    x, y = body
    if action == 0:
        return 1 - x, y
    if action == 1:
        return y, x
    if action in (2, 3):
        return body
    raise ValueError("Unknown body action")


def fingerprint(body):
    return body + body_step(body, 0) + body_step(body, 1)


def initial_event(body, nuisance):
    event = [0] * 9
    event[body[0]] = 1
    event[2 + body[1]] = 1
    event[7 + nuisance] = 1
    return event


def action_event(action, nuisance):
    event = [0] * 9
    event[4 + action] = 1
    event[7 + nuisance] = 1
    return event


def ranks_from_world(goal):
    # Four-node reachability is part of evaluation only, never inference.
    distance = {goal: 0}
    queue = deque([goal])
    while queue:
        reached = queue.popleft()
        for predecessor in BODIES:
            if predecessor not in distance and any(
                body_step(predecessor, a) == reached for a in range(3)
            ):
                distance[predecessor] = 1 + distance[reached]
                queue.append(predecessor)
    if len(distance) != 4:
        raise AssertionError("The declared body is not goal reachable")
    return distance


def establish_closure(memory, policy):
    canonical, initial_rows, failures = {}, [], []
    queue = deque()
    for body in BODIES:
        codes = []
        for nuisance in (0, 1):
            code = memory.step(0, initial_event(body, nuisance))
            actual = memory.observe(code)
            passed = actual == fingerprint(body)
            codes.append(code)
            initial_rows.append(
                {
                    "body": list(body),
                    "nuisance": nuisance,
                    "memory_code": code,
                    "fingerprint": list(actual),
                    "passed": passed,
                }
            )
            queue.append((body, code))
            if not passed:
                failures.append(
                    {
                        "reason": "initial fingerprint mismatch",
                        "body": list(body),
                        "code": code,
                    }
                )
        if len(set(codes)) != 1:
            failures.append(
                {
                    "reason": "nuisance changes canonical code",
                    "body": list(body),
                    "codes": codes,
                }
            )
        canonical[body] = codes[0]
    if len(set(canonical.values())) != 4:
        failures.append({"reason": "canonical body codes are not distinct"})
    visited, edges = set(), []
    while queue:
        body, code = queue.popleft()
        if (body, code) in visited:
            continue
        visited.add((body, code))
        for action in range(3):
            for nuisance in (0, 1):
                target = body_step(body, action)
                next_code = memory.step(code, action_event(action, nuisance))
                passed = next_code == canonical[target] and memory.observe(
                    next_code
                ) == fingerprint(target)
                row = {
                    "body": list(body),
                    "code": code,
                    "action": ACTIONS[action],
                    "nuisance": nuisance,
                    "next_body": list(target),
                    "next_code": next_code,
                    "passed": passed,
                }
                edges.append(row)
                if not passed:
                    failures.append(
                        {"reason": "canonical world closure mismatch", "edge": row}
                    )
                queue.append((target, next_code))
    goals = []
    for index, expected in enumerate(policy.goals):
        matches = [body for body in BODIES if fingerprint(body) == expected]
        if len(matches) != 1:
            failures.append(
                {"reason": "policy goal has no unique body binding", "channel": index}
            )
        else:
            goals.append(matches[0])
    return (
        canonical,
        goals,
        {
            "passed": not failures,
            "initial_checks": initial_rows,
            "reachable_body_memory_pairs": len(visited),
            "all_command_nuisance_edges_checked": len(edges),
            "edges": edges,
            "failures": failures,
            "canonical_codes": [
                {"body": list(body), "code": code} for body, code in canonical.items()
            ],
            "goal_bindings": [
                {
                    "channel": i,
                    "body": list(body),
                    "observable_fingerprint": list(policy.goals[i]),
                }
                for i, body in enumerate(goals)
            ],
        },
    )


def fault_masks(n, maximum):
    return [
        (weight, sum(1 << i for i in chosen))
        for weight in range(maximum + 1)
        for chosen in combinations(range(n), weight)
    ]


def evaluate(memory, policy, canonical, goals, maximum, refresh):
    masks = fault_masks(memory.n, maximum)
    counts = {
        "cases": 0,
        "passed_cases": 0,
        "unique_action_failures": 0,
        "stop_iff_goal_failures": 0,
        "rank_decrease_failures": 0,
        "canonical_successor_failures": 0,
        "refresh_failures": 0,
    }
    by_weight = {str(w): {"cases": 0, "passed": 0} for w in range(maximum + 1)}
    witnesses, representative_failures = [], {}
    rank_max = 0
    selected_actions = {name: 0 for name in ACTIONS}
    # Exhaust every possible fault before each cycle. If local properties hold,
    # induction closes arbitrary repetitions without enumerating infinite words.
    for body, clean in canonical.items():
        for gi, goal in enumerate(goals):
            ranks = ranks_from_world(goal)
            rank = ranks[body]
            rank_max = max(rank_max, rank)
            for weight, mask in masks:
                damaged = clean ^ mask
                refresh_nuisances = (0, 1) if refresh else (None,)
                for rn in refresh_nuisances:
                    decision_code = (
                        memory.step(damaged, action_event(2, rn))
                        if refresh
                        else damaged
                    )
                    repaired = decision_code == clean and memory.observe(
                        decision_code
                    ) == fingerprint(body)
                    action_spikes = policy.step(memory.bits(decision_code), gi)
                    unique = sum(action_spikes) == 1
                    selected = action_spikes.index(1) if unique else None
                    stop_correct = unique and ((selected == 3) == (body == goal))
                    for an in (0, 1):
                        counts["cases"] += 1
                        by_weight[str(weight)]["cases"] += 1
                        if unique:
                            selected_actions[ACTIONS[selected]] += 1
                        target_body = body_step(body, selected) if unique else None
                        next_code = (
                            (
                                decision_code
                                if selected == 3
                                else memory.step(
                                    decision_code, action_event(selected, an)
                                )
                            )
                            if unique
                            else None
                        )
                        rank_decreased = unique and (
                            selected == 3 or ranks[target_body] < rank
                        )
                        successor_correct = (
                            unique
                            and (
                                next_code == canonical[target_body]
                                and memory.observe(next_code)
                                == fingerprint(target_body)
                            )
                            if (refresh or selected != 3)
                            else unique
                        )
                        # Direct STOP leaves a damaged code, so direct certification
                        # requires no canonical repair after STOP; its goal must
                        # nevertheless be genuinely reached in the body.
                        passed = (
                            unique
                            and stop_correct
                            and rank_decreased
                            and successor_correct
                            and (repaired or not refresh)
                        )
                        counts["passed_cases"] += passed
                        by_weight[str(weight)]["passed"] += passed
                        counts["unique_action_failures"] += not unique
                        counts["stop_iff_goal_failures"] += not stop_correct
                        counts["rank_decrease_failures"] += not rank_decreased
                        counts["canonical_successor_failures"] += not successor_correct
                        counts["refresh_failures"] += refresh and not repaired
                        if not passed:
                            witness = {
                                "body_before_cycle": list(body),
                                "goal_channel": gi,
                                "true_goal_body": list(goal),
                                "original_memory_code": clean,
                                "flipped_neurons": [
                                    i for i in range(memory.n) if mask >> i & 1
                                ],
                                "xor_mask": mask,
                                "damaged_memory_code": damaged,
                                "refresh_nuisance": rn,
                                "post_refresh_or_direct_decision_code": decision_code,
                                "decision_fingerprint": list(
                                    memory.observe(decision_code)
                                ),
                                "action_spikes": list(action_spikes),
                                "selected_action": ACTIONS[selected]
                                if unique
                                else None,
                                "action_nuisance": an,
                                "body_after_action": list(target_body)
                                if unique
                                else None,
                                "next_memory_code": next_code,
                                "current_goal_rank": rank,
                                "next_goal_rank": ranks[target_body]
                                if unique
                                else None,
                                "checks": {
                                    "unique_action": unique,
                                    "stop_iff_true_goal": stop_correct,
                                    "strict_rank_decrease_for_nonstop": rank_decreased,
                                    "correct_next_canonical_code": successor_correct,
                                    "refresh_restores_original_canonical_code": repaired
                                    if refresh
                                    else None,
                                },
                            }
                            if len(witnesses) < 32:
                                witnesses.append(witness)
                            categories = {
                                "nonunique_action": not unique,
                                "unique_but_wrong_stop_condition": unique
                                and not stop_correct,
                                "unique_nonstop_action_fails_rank_decrease": unique
                                and selected != 3
                                and not rank_decreased,
                                "unique_action_wrong_canonical_successor": unique
                                and not successor_correct,
                                "refresh_fails": refresh and not repaired,
                            }
                            for category, occurs in categories.items():
                                if occurs:
                                    representative_failures.setdefault(
                                        category, witness
                                    )
    passed = counts["passed_cases"] == counts["cases"]
    return {
        "passed": passed,
        "counts": counts,
        "by_exact_corruption_weight": by_weight,
        "mask_count_per_canonical_code": len(masks),
        "worst_initial_goal_rank": rank_max,
        "finite_cases_checked": counts["cases"],
        "all_body_goal_pairs": 16,
        "action_distribution_over_cases": selected_actions,
        "first_actual_counterexample": witnesses[0] if witnesses else None,
        "counterexamples_first_32": witnesses,
        "representative_failure_witnesses": representative_failures,
        "unbounded_repeated_decision_boundary_fault_schedule_certified": passed,
        "worst_case_nonstop_body_actions_before_goal": rank_max if passed else None,
        "worst_case_cycles_including_terminal_stop": rank_max + 1 if passed else None,
        "worst_case_memory_ticks_including_refresh_and_terminal_stop_cycle": 2
        * rank_max
        + 1
        if refresh and passed
        else None,
        "proof_if_passed": "Initialization establishes the corresponding canonical body/memory code. Each decision cycle tolerates a fresh arbitrary allowed XOR fault, then selects one unique action. STOP holds exactly at the true body goal; otherwise its action strictly decreases the nonnegative body-goal rank and restores the exact canonical code corresponding to the body's new state. Induction closes arbitrarily many cycles, with at most three non-STOP actions per fixed goal.",
        "fault_schedule": (
            "Before EVERY cycle, an adversary XORs up to k distinct queued memory spike bits. A neural NOOP memory tick with arbitrary nuisance refreshes the state; SAME exported learned goal policy then runs, and its unique non-STOP action drives body and memory with arbitrary nuisance. No fault occurs between refresh, policy inference, and the action tick. A fresh fault may occur before the next cycle. Goal one-hot, synaptic weights, transient neurons and body are uncorrupted."
            if refresh
            else "Before EVERY policy decision, an adversary XORs up to k distinct queued memory spike bits. SAME learned policy acts directly on that damaged state, and its selected non-STOP action then drives body and memory; no fresh fault occurs during the action tick. Nuisance may vary at every tick. Goal one-hot, synaptic weights, transient neurons and body are uncorrupted."
        ),
        "terminal_stop_semantics": "STOP performs no body action or memory action tick. Direct mode does not demand canonical repair after STOP; refreshed mode demands canonical repair before policy, including the terminal STOP cycle.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--max-bits", type=int, default=2)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    nraw, praw = args.network.read_bytes(), args.policy.read_bytes()
    memory, policy = (
        Memory(json.loads(nraw)),
        Policy(json.loads(praw), json.loads(nraw)["state_neurons"]),
    )
    if not 0 <= args.max_bits <= memory.n:
        raise ValueError("Invalid corruption radius")
    canonical, goals, closure = establish_closure(memory, policy)
    direct = (
        evaluate(memory, policy, canonical, goals, args.max_bits, False)
        if closure["passed"]
        else None
    )
    refreshed = (
        evaluate(memory, policy, canonical, goals, args.max_bits, True)
        if closure["passed"]
        else None
    )
    result = {
        "format": "independent-goal-loop-robustness-v1",
        "network_file_sha256": sha256(nraw),
        "policy_file_sha256": sha256(praw),
        "verifier_source_sha256": sha256(Path(__file__).read_bytes()),
        "python_runtime": "Independent pure-Python exact integer synaptic sums and threshold >=0; no NumPy, training helper, symbolic runtime decoder, teacher, graph search, or latent body variables enter learned network inference.",
        "model_world_binding": "Evaluator body=(x,y), flip=(1-x,y), swap=(y,x), noop/STOP=(x,y). Initial observation is one-hot x plus one-hot y plus binary nuisance. Six-bit fingerprint concatenates current observation, observation after flip, observation after swap. This is a declared tiny deterministic body, not physical-world validation.",
        "initial_and_command_world_closure": closure,
        "evaluator_only_goal_rank_table": [
            {
                "body": list(body),
                "goal_channel": gi,
                "goal_body": list(goal),
                "shortest_command_distance": ranks_from_world(goal)[body],
            }
            for gi, goal in enumerate(goals)
            for body in BODIES
        ],
        "direct_corrupted_policy_decisions": direct,
        "neural_refresh_then_same_learned_policy": refreshed,
        "scope_limits": [
            "Exact serialized 12-memory-neuron, 64-transition-neuron clocked threshold model and 64-hidden-neuron goal policy only.",
            "All 16 canonical body/goal combinations are exhaustively checked; these are not held-out learned-policy generalization cases.",
            "The offline goal-policy training teacher used model graph planning. No new autonomous planning capability is claimed.",
            "The fault model affects only queued memory spikes at declared decision-cycle boundaries; continuous faults, faulty goals/synapses/transient neurons, simultaneous body changes and unmodeled sensors are not certified.",
            "Refreshing before action is an explicit scheduling condition, not robustness of policy decisions taken directly on corrupted memory.",
            "The world has four body states and three ordinary commands; finite induction is specific to that complete alphabet and binding.",
            "This does not establish Brian2 LIF equivalence, production Nuria robustness, consciousness, global novelty or a practical advantage over symbolic/error-correcting alternatives.",
        ],
        "elapsed_seconds": time.monotonic() - started,
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(args.out),
                "closure_passed": closure["passed"],
                "direct_passed": direct["passed"] if direct else None,
                "direct_counts": direct["counts"] if direct else None,
                "direct_first_counterexample": direct["first_actual_counterexample"]
                if direct
                else None,
                "refreshed_passed": refreshed["passed"] if refreshed else None,
                "refreshed_counts": refreshed["counts"] if refreshed else None,
                "elapsed_seconds": result["elapsed_seconds"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

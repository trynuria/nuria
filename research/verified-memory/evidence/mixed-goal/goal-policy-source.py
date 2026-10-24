"""Learn and exhaustively check a goal-conditioned binary threshold policy.

The memory network is an input JSON file, interpreted by an independent integer
tick implementation. Offline supervision comes from shortest robust routes
through observed memory transitions/probe fingerprints, not hidden task-state
IDs. Runtime policy inference is exclusively two thresholded matrix products.

This is a clocked binary threshold network, NOT a Brian2 LIF network. Offline
graph planning supplies the training teacher. No autonomous-discovery,
efficiency, consciousness, or novelty claim is made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import deque
from pathlib import Path

import numpy as np

ACTIONS = ("flip", "swap", "noop", "STOP")


def own_matrices(spec):
    values = {k: np.asarray(v, dtype=np.int64) for k, v in spec["arrays"].items()}
    values["_silenced_memory_neurons"] = np.asarray(
        spec.get("silenced_memory_neurons", []), dtype=np.int64
    )
    return values


def own_tick(weights, state, event):
    transition = (
        weights["transition.weight"] @ np.concatenate([event, state])
        + weights["transition.bias"]
        >= 0
    ).astype(np.int64)
    memory = (
        weights["memory.weight"] @ transition + weights["memory.bias"] >= 0
    ).astype(np.int64)
    memory[weights["_silenced_memory_neurons"]] = 0
    observed = (weights["output.weight"] @ memory + weights["output.bias"] >= 0).astype(
        np.int64
    )
    return memory, tuple(observed.tolist())


def observe(weights, state):
    return tuple(
        (weights["output.weight"] @ state + weights["output.bias"] >= 0)
        .astype(int)
        .tolist()
    )


def initial_events():
    # Exhaust all legal observed initial-input combinations. No hidden task
    # transition law, hidden state ID, or prescribed memory code is used.
    for first_channel in [0, 1]:
        for second_channel in [2, 3]:
            for nuisance_channel in [7, 8]:
                event = np.zeros(9, dtype=np.int64)
                event[[first_channel, second_channel, nuisance_channel]] = 1
                yield event


def action_event(action, nuisance):
    event = np.zeros(9, dtype=np.int64)
    event[4 + action] = 1
    event[7 + nuisance] = 1
    return event


def observed_graph(spec):
    weights = own_matrices(spec)
    count = int(spec["state_neurons"])
    queue, vertices, initial = deque(), {}, []
    for event in initial_events():
        state, fingerprint = own_tick(weights, np.zeros(count, dtype=np.int64), event)
        code = tuple(state.tolist())
        queue.append(code)
        initial.append(
            {
                "input_spikes": event.tolist(),
                "memory_spikes": list(code),
                "observable_probe_fingerprint": list(fingerprint),
            }
        )
    while queue:
        code = queue.popleft()
        if code in vertices:
            continue
        if len(vertices) >= 2**count:
            raise RuntimeError("Impossible reachable-code count")
        state = np.asarray(code, dtype=np.int64)
        edges = []
        for action in range(3):
            destinations = []
            for nuisance in range(2):
                next_state, fingerprint = own_tick(
                    weights, state, action_event(action, nuisance)
                )
                destination = tuple(next_state.tolist())
                destinations.append(destination)
                queue.append(destination)
                if fingerprint != observe(weights, next_state):
                    raise AssertionError(
                        "Independent output projection disagrees with tick"
                    )
            edges.append(destinations)
        vertices[code] = {"fingerprint": observe(weights, state), "edges": edges}
    goals = sorted({value["fingerprint"] for value in vertices.values()})
    if len(goals) != 4:
        raise RuntimeError(
            f"Expected four distinct observable fingerprints, observed {len(goals)}"
        )
    return vertices, goals, initial


def shortest_robust_routes(graph, goals):
    # Nuisance can change adversarially at every step. A legal teacher action
    # must decrease distance for BOTH observed nuisance successors.
    distances, teachers = {}, {}
    for goal_index, goal in enumerate(goals):
        d = {
            code: 0 if value["fingerprint"] == goal else np.inf
            for code, value in graph.items()
        }
        for _ in range(len(graph)):
            changed = False
            for code, value in graph.items():
                if d[code] == 0:
                    continue
                choices = [
                    1 + max(d[n] for n in destinations)
                    for destinations in value["edges"]
                ]
                improved = min(choices)
                if improved < d[code]:
                    d[code], changed = improved, True
            if not changed:
                break
        if not all(np.isfinite(list(d.values()))):
            raise RuntimeError(
                "Some reachable code cannot robustly reach an observable goal"
            )
        for code, value in graph.items():
            if d[code] == 0:
                teacher = 3
            else:
                choices = [
                    1 + max(d[n] for n in destinations)
                    for destinations in value["edges"]
                ]
                teacher = int(np.argmin(choices))
                if choices[teacher] != d[code]:
                    raise AssertionError("Teacher is not shortest robust route")
            distances[code, goal_index] = int(d[code])
            teachers[code, goal_index] = teacher
    return distances, teachers


def serialize_policy(params, goals, scale=4096):
    return {
        "format": "goal-conditioned-clocked-binary-policy-v1",
        "scale": scale,
        "actions": list(ACTIONS),
        "goal_channels": [list(g) for g in goals],
        "input": "queued memory spikes followed by four observable-goal one-hot spikes",
        "runtime": "hidden=threshold(W1@input+b1); action_spikes=threshold(W2@hidden+b2); threshold >=0",
        "training": "offline robust shortest-route teacher from observed model transitions/probe outputs",
        "arrays": {
            name: np.rint(value * scale).astype(np.int64).tolist()
            for name, value in params.items()
        },
    }


def policy_tick(policy, memory_spikes, goal_onehot):
    """Entire inference path: no graph, distances, labels, argmax, or lookup."""
    values = own_matrices(policy)
    sensory_spikes = np.concatenate([memory_spikes, goal_onehot]).astype(np.int64)
    hidden_spikes = (
        values["hidden.weight"] @ sensory_spikes + values["hidden.bias"] >= 0
    ).astype(np.int64)
    action_spikes = (
        values["action.weight"] @ hidden_spikes + values["action.bias"] >= 0
    ).astype(np.int64)
    return action_spikes, hidden_spikes


def train_policy(x, labels, goals, seed, hidden_neurons=64, max_updates=8000):
    rng = np.random.default_rng(seed)
    params = {
        "hidden.weight": rng.normal(0, 0.35, (hidden_neurons, x.shape[1])),
        "hidden.bias": rng.normal(0, 0.15, hidden_neurons),
        "action.weight": rng.normal(0, 0.12, (4, hidden_neurons)),
        "action.bias": np.zeros(4),
    }
    first = {k: np.zeros_like(v) for k, v in params.items()}
    second = {k: np.zeros_like(v) for k, v in params.items()}
    target = np.eye(4)[labels]
    sign = 2 * target - 1
    progress = []
    for step in range(1, max_updates + 1):
        membrane = x @ params["hidden.weight"].T + params["hidden.bias"]
        hidden = (membrane >= 0).astype(float)
        logits = hidden @ params["action.weight"].T + params["action.bias"]
        violation = np.maximum(0, 1.0 - logits * sign)
        d_logits = -2 * sign * violation / (len(x) * 4)
        grads = {
            "action.weight": d_logits.T @ hidden,
            "action.bias": d_logits.sum(axis=0),
        }
        d_hidden = d_logits @ params["action.weight"]
        d_membrane = d_hidden / (1 + 2 * np.abs(membrane)) ** 2
        grads["hidden.weight"] = d_membrane.T @ x
        grads["hidden.bias"] = d_membrane.sum(axis=0)
        for key, gradient in grads.items():
            gradient = np.clip(gradient, -1, 1)
            first[key] = 0.9 * first[key] + 0.1 * gradient
            second[key] = 0.999 * second[key] + 0.001 * gradient * gradient
            params[key] -= (
                0.008
                * (first[key] / (1 - 0.9**step))
                / (np.sqrt(second[key] / (1 - 0.999**step)) + 1e-8)
            )
        if step % 100 == 0:
            policy = serialize_policy(params, goals)
            correct = []
            margins = []
            values = own_matrices(policy)
            for row, label in zip(x, labels):
                hidden_spikes = (
                    values["hidden.weight"] @ row.astype(np.int64)
                    + values["hidden.bias"]
                    >= 0
                ).astype(np.int64)
                output_membrane = (
                    values["action.weight"] @ hidden_spikes + values["action.bias"]
                )
                action_spikes = output_membrane >= 0
                correct.append(
                    np.array_equal(action_spikes, np.eye(4, dtype=bool)[label])
                )
                margins.extend((output_membrane * (2 * np.eye(4)[label] - 1)).tolist())
            record = {
                "update": step,
                "margin_loss": float(np.mean(violation**2)),
                "all_examples_unique_correct_action": bool(all(correct)),
                "minimum_integer_output_margin": int(min(margins)),
            }
            progress.append(record)
            if all(correct) and min(margins) >= 256:
                return policy, progress
    return serialize_policy(params, goals), progress


def verify_policy(policy, memory_spec, graph, goals, distances):
    weights = own_matrices(memory_spec)
    checked = []
    failures = []
    for code, value in sorted(graph.items()):
        for goal_index, goal in enumerate(goals):
            goal_onehot = np.eye(4, dtype=np.int64)[goal_index]
            action_spikes, hidden = policy_tick(policy, np.array(code), goal_onehot)
            active = np.flatnonzero(action_spikes)
            record = {
                "memory_spikes": list(code),
                "observable_fingerprint": list(value["fingerprint"]),
                "goal_onehot": goal_onehot.tolist(),
                "goal_fingerprint": list(goal),
                "action_spikes": action_spikes.tolist(),
                "distance": distances[code, goal_index],
                "unique_action_spike": len(active) == 1,
                "successors": [],
            }
            passed = len(active) == 1
            if passed:
                action = int(active[0])
                is_goal = value["fingerprint"] == goal
                passed &= (action == 3) == is_goal
                record["action_name"] = ACTIONS[action]
                if action != 3:
                    for nuisance in range(2):
                        next_state, next_observed = own_tick(
                            weights, np.array(code), action_event(action, nuisance)
                        )
                        next_code = tuple(next_state.tolist())
                        decrease = (
                            distances[next_code, goal_index]
                            < distances[code, goal_index]
                        )
                        record["successors"].append(
                            {
                                "nuisance_spike": nuisance,
                                "next_memory_spikes": list(next_code),
                                "next_observed_fingerprint": list(next_observed),
                                "next_distance": distances[next_code, goal_index],
                                "distance_strictly_decreases": bool(decrease),
                            }
                        )
                        passed &= decrease
            record["passed"] = bool(passed)
            checked.append(record)
            if not passed:
                failures.append(record)
    return {
        "passed": not failures,
        "reachable_memory_codes": len(graph),
        "observable_goals": len(goals),
        "memory_goal_pairs_checked": len(checked),
        "nuisance_successor_edges_checked": sum(len(r["successors"]) for r in checked),
        "worst_case_actions_to_goal": max(distances.values()),
        "claim": "For every reachable memory code and observable goal, exactly one learned action spike; STOP iff goal; every action/nuisance successor strictly decreases robust shortest distance. Finite closed-loop goal reaching follows for exported learned memory dynamics.",
        "scope": "Clocked binary threshold model with offline graph-derived teacher; conditional on exported probe outputs, not an independently verified physical plant",
        "failures": failures,
        "checked_pairs": checked,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=711)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--updates", type=int, default=8000)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    source_bytes = Path(__file__).read_bytes()
    network_bytes = args.network.read_bytes()
    (args.out / "goal-policy-source.py").write_bytes(source_bytes)
    spec = json.loads(network_bytes)
    graph, goals, initial = observed_graph(spec)
    distances, teacher = shortest_robust_routes(graph, goals)
    examples, labels, evidence = [], [], []
    for code in sorted(graph):
        for goal_index, goal in enumerate(goals):
            onehot = np.eye(4, dtype=np.int64)[goal_index]
            examples.append(np.r_[code, onehot])
            labels.append(teacher[code, goal_index])
            evidence.append(
                {
                    "memory_spikes": list(code),
                    "observed_probe_fingerprint": list(graph[code]["fingerprint"]),
                    "goal_fingerprint": list(goal),
                    "goal_onehot": onehot.tolist(),
                    "offline_teacher_action": ACTIONS[teacher[code, goal_index]],
                    "robust_shortest_distance": distances[code, goal_index],
                }
            )
    attempts = []
    for restart in range(5):
        policy, progress = train_policy(
            np.array(examples, dtype=float),
            np.array(labels),
            goals,
            args.seed + 1009 * restart,
            args.hidden,
            args.updates,
        )
        check = verify_policy(policy, spec, graph, goals, distances)
        attempts.append(
            {
                "restart": restart,
                "seed": args.seed + 1009 * restart,
                "training_progress": progress,
                "verification_passed": check["passed"],
            }
        )
        (args.out / f"goal-policy-attempt-{restart:02d}.json").write_text(
            json.dumps(policy, separators=(",", ":")) + "\n"
        )
        if check["passed"]:
            break
    (args.out / "goal-policy.json").write_text(
        json.dumps(policy, separators=(",", ":")) + "\n"
    )
    (args.out / "goal-training-evidence.json").write_text(
        json.dumps({"initial_inputs": initial, "examples": evidence}, indent=2) + "\n"
    )
    (args.out / "goal-verification.json").write_text(json.dumps(check, indent=2) + "\n")
    result = {
        "passed": check["passed"],
        "network_path": str(args.network),
        "network_sha256": hashlib.sha256(network_bytes).hexdigest(),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "seed": args.seed,
        "hidden_neurons": args.hidden,
        "reachable_memory_codes": len(graph),
        "observable_goals": len(goals),
        "memory_goal_pairs_checked": check["memory_goal_pairs_checked"],
        "nuisance_successor_edges_checked": check["nuisance_successor_edges_checked"],
        "worst_case_actions_to_goal": check["worst_case_actions_to_goal"],
        "policy_parameters": sum(np.asarray(v).size for v in policy["arrays"].values()),
        "elapsed_seconds": time.monotonic() - started,
        "attempts": attempts,
        "baseline": "The offline graph planner uses the same observed transitions and supplies exact shortest routes directly. It is simpler and avoids the learned policy's training/inference overhead; no efficiency advantage is claimed.",
        "autonomy_limit": "Policy learns offline planning-teacher labels; no autonomous goal discovery or reward-only learning claim",
        "optimizer": "Full-batch NumPy Adam with binary hidden spikes and surrogate derivative; squared output margin loss",
        "silenced_memory_neurons": spec.get("silenced_memory_neurons", []),
        "runtime_state": "No policy state beyond input queued memory spikes and observable goal onehot; hidden/action neurons reset on each synchronous phase",
        "runtime_no_graph_lookup": True,
        "numpy_version": np.__version__,
    }
    (args.out / "goal-results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)
    if not check["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

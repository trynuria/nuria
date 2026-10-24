"""Exact invariant-region verification for a serialized binary spike circuit.

Unlike robust_check.py, this checker does not require a unique canonical spike
code per behavior, or restoration to it. It finds the greatest set of correctly
read-out complete neural states closed under every bounded bit corruption and
subsequent legal command. Training code and its state labels are not imported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import deque
from itertools import combinations
from pathlib import Path

import transplant_check as reference


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def bounded_masks(neurons, radius):
    return tuple(
        sum(1 << i for i in indices)
        for weight in range(radius + 1)
        for indices in combinations(range(neurons), weight)
    )


def precompute(runtime):
    """All finite neural states, outputs and six ordinary event transitions."""
    fingerprint_to_pair = {
        reference.expected_observations(pair): pair for pair in reference.PAIRS
    }
    fingerprints = [runtime.output(code) for code in range(runtime.limit)]
    labels = [fingerprint_to_pair.get(value) for value in fingerprints]
    events = [reference.command_event(*command) for command in reference.COMMANDS]
    successors = []
    for code in range(runtime.limit):
        row = []
        for event in events:
            next_code, observed = runtime.tick(code, event)
            if observed != fingerprints[next_code]:
                raise AssertionError("Precomputed readout and tick disagree")
            row.append(next_code)
        successors.append(tuple(row))
    return fingerprints, labels, successors


def initial_states(runtime):
    rows = []
    for pair in reference.PAIRS:
        for nuisance in (0, 1):
            code, observed = runtime.tick(0, reference.initial_event(pair, nuisance))
            rows.append(
                {
                    "initial_observation": list(pair),
                    "initial_nuisance": nuisance,
                    "neural_code": code,
                    "observed_fingerprint": list(observed),
                    "expected_fingerprint": list(reference.expected_observations(pair)),
                    "output_correct": observed == reference.expected_observations(pair),
                }
            )
    return rows


def fault_edge(code, mask, event_index, successors):
    return {
        "start_code": code,
        "xor_mask": mask,
        "command_index": event_index,
        "damaged_code": code ^ mask,
        "next_code": successors[code ^ mask][event_index],
    }


def greatest_safe_region(runtime, labels, successors, radius):
    """Compute the greatest fixed point of the universal robust predecessor.

    Invalid current fingerprints are outside the initial candidate. A state
    survives iff every (fault mask, command/nuisance) successor has the expected
    next fingerprint and also survives. Reverse dependency elimination is exact;
    a witness edge records why each excluded candidate was removed.
    """
    masks = bounded_masks(runtime.n, radius)
    candidates = {
        code
        for code, pair in enumerate(labels)
        if pair is not None and not code & runtime.silenced_mask
    }
    reverse = [[] for _ in range(runtime.limit)]
    reasons = {}
    direct_failed = 0
    evaluated_cases = 0
    fully_scanned = 0
    dependencies = 0
    for code in sorted(candidates):
        pair = labels[code]
        edges = {}
        failed = False
        for mask in masks:
            for event_index, (symbol, nuisance) in enumerate(reference.COMMANDS):
                evaluated_cases += 1
                next_code = successors[code ^ mask][event_index]
                expected = reference.reference_action(pair, symbol)
                if labels[next_code] != expected or next_code not in candidates:
                    reasons[code] = {
                        "kind": "incorrect_next_fingerprint_or_forbidden_state",
                        **fault_edge(code, mask, event_index, successors),
                    }
                    direct_failed += 1
                    failed = True
                    break
                # One witness per distinct target suffices for the universal
                # dependency; every edge's target behavior was checked above.
                edges.setdefault(next_code, (mask, event_index))
            if failed:
                break
        if failed:
            continue
        fully_scanned += 1
        for next_code, (mask, event_index) in edges.items():
            reverse[next_code].append((code, mask, event_index))
            dependencies += 1

    bad = set(reasons)
    queue = deque(sorted(bad))
    removed_by_dependency = 0
    while queue:
        next_code = queue.popleft()
        for code, mask, event_index in reverse[next_code]:
            if code in bad:
                continue
            bad.add(code)
            reasons[code] = {
                "kind": "successor_excluded_by_fixed_point",
                **fault_edge(code, mask, event_index, successors),
            }
            removed_by_dependency += 1
            queue.append(code)
    safe = candidates - bad

    # A final independent traversal of the resulting set validates the claimed
    # invariant directly, rather than relying on elimination bookkeeping alone.
    postcheck_cases = 0
    for code in sorted(safe):
        pair = labels[code]
        for mask in masks:
            for event_index, (symbol, nuisance) in enumerate(reference.COMMANDS):
                next_code = successors[code ^ mask][event_index]
                if next_code not in safe or labels[
                    next_code
                ] != reference.reference_action(pair, symbol):
                    raise AssertionError("Claimed safe set is not robustly closed")
                postcheck_cases += 1
    return (
        safe,
        reasons,
        {
            "mask_count_per_state": len(masks),
            "mask_list": list(masks),
            "initial_candidate_count": len(candidates),
            "direct_universal_condition_failures": direct_failed,
            "fully_scanned_candidate_count": fully_scanned,
            "later_dependency_removals": removed_by_dependency,
            "unique_dependency_count": dependencies,
            "scanned_cases_before_short_circuit": evaluated_cases,
            "final_safe_set_full_closure_cases": postcheck_cases,
            "safe_state_count": len(safe),
        },
    )


def counterexample(runtime, initial, safe, reasons, fingerprints):
    start = initial["neural_code"]
    pair = tuple(initial["initial_observation"])
    result = {
        "initialization": dict(initial),
        "fault_command_sequence": [],
        "failure_kind": None,
    }
    if not initial["output_correct"]:
        result["failure_kind"] = "initialization_fingerprint_mismatch"
        return result
    code = start
    seen = set()
    while code not in safe:
        if code in seen or code not in reasons:
            raise AssertionError("Missing or cyclic exclusion witness")
        seen.add(code)
        edge = reasons[code]
        symbol, nuisance = reference.COMMANDS[edge["command_index"]]
        target = reference.reference_action(pair, symbol)
        next_code = edge["next_code"]
        row = {
            **edge,
            "behavior_before_corruption": list(pair),
            "flipped_neurons": [
                i for i in range(runtime.n) if edge["xor_mask"] >> i & 1
            ],
            "corruption_weight": edge["xor_mask"].bit_count(),
            "command": symbol,
            "nuisance": nuisance,
            "event_vector": reference.command_event(symbol, nuisance),
            "observed_before_command_after_corruption": list(
                fingerprints[edge["damaged_code"]]
            ),
            "expected_behavior_after_command": list(target),
            "actual_fingerprint_after_command": list(fingerprints[next_code]),
            "expected_fingerprint_after_command": list(
                reference.expected_observations(target)
            ),
        }
        result["fault_command_sequence"].append(row)
        if fingerprints[next_code] != reference.expected_observations(target):
            result["failure_kind"] = "incorrect_fingerprint_after_ordinary_neural_tick"
            break
        if next_code & runtime.silenced_mask:
            result["failure_kind"] = "forbidden_silenced_state"
            break
        code, pair = next_code, target
    if result["failure_kind"] is None:
        raise AssertionError("Exclusion witness did not end at a real failure")
    result["commands_until_failure"] = len(result["fault_command_sequence"])
    return result


def verify_network(network, maximum, out):
    started = time.monotonic()
    raw = network.read_bytes()
    spec = json.loads(raw)
    runtime = reference.NeuralRuntime(spec)
    if not 0 <= maximum <= runtime.n:
        raise ValueError("Invalid maximum corruption radius")
    fingerprints, labels, successors = precompute(runtime)
    initial = initial_states(runtime)
    table = {
        "state_neurons": runtime.n,
        "code_order": "all integers from zero to 2**state_neurons - 1",
        "commands_and_nuisance_in_column_order": list(reference.COMMANDS),
        "fingerprints": fingerprints,
        "successors": successors,
    }
    table_path = out.with_name(out.stem + "-transitions.json")
    table_raw = canonical_json(table) + b"\n"
    table_path.write_bytes(table_raw)
    certificates = []
    for radius in range(maximum + 1):
        safe, reasons, statistics = greatest_safe_region(
            runtime, labels, successors, radius
        )
        initial_checks = [
            {**row, "belongs_to_safe_set": row["neural_code"] in safe}
            for row in initial
        ]
        passed = all(
            row["output_correct"] and row["belongs_to_safe_set"]
            for row in initial_checks
        )
        excluded = [
            row
            for row in initial
            if not row["output_correct"] or row["neural_code"] not in safe
        ]
        certificate = {
            "max_corrupted_memory_spikes_before_each_command": radius,
            "passed": passed,
            "arbitrarily_repeated_bounded_faults_certified": passed,
            "statistics": statistics,
            "safe_codes_by_evaluator_only_behavior": [
                {
                    "behavior": list(pair),
                    "codes": sorted(code for code in safe if labels[code] == pair),
                }
                for pair in reference.PAIRS
            ],
            "safe_set_sorted_codes_sha256": sha256(canonical_json(sorted(safe))),
            "initial_states": initial_checks,
            "actual_counterexamples_for_excluded_initializations": [
                counterexample(runtime, row, safe, reasons, fingerprints)
                for row in excluded
            ],
            "proof_if_passed": (
                "Every actual initialization belongs to the certified set and has the correct fingerprint. "
                "For every state in that set, every XOR mask of weight <= k and every legal command/nuisance "
                "maps to a correctly updated fingerprint and another state in the same certified set. "
                "Induction therefore permits a fresh adversarial <= k corruption before every command, "
                "for any finite command sequence and without a bound on sequence length."
            ),
            "exact_canonical_restoration_certified": False,
            "canonical_restoration_scope": "Not tested or implied: different codes with the same behavior may remain in an invariant region.",
        }
        certificates.append(certificate)
        print(
            json.dumps(
                {
                    "network": network.parent.name,
                    "radius": radius,
                    "passed": passed,
                    "safe_states": len(safe),
                    "actual_initial_states_in_safe_set": sum(
                        row["belongs_to_safe_set"] for row in initial_checks
                    ),
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
    report = {
        "network_path": str(network),
        "network_file_sha256": sha256(raw),
        "network_canonical_json_sha256": sha256(canonical_json(spec)),
        "verifier_source_sha256": sha256(Path(__file__).read_bytes()),
        "independent_integer_runtime_source_sha256": sha256(
            Path(reference.__file__).read_bytes()
        ),
        "complete_transition_table_path": str(table_path),
        "complete_transition_table_file_sha256": sha256(table_raw),
        "precomputed_neural_states": runtime.limit,
        "precomputed_ordinary_command_transitions": runtime.limit
        * len(reference.COMMANDS),
        "corruption_model": "XOR at most k distinct memory-spike bits at the declared complete inter-symbol spike boundary before every noninitial ordinary command; all command and nuisance sequences included.",
        "success_criterion": "All real initialization codes belong to the greatest correctly read-out invariant region under every permitted corrupted transition. Correct behavior is guaranteed AFTER each ordinary action tick.",
        "radius_certificates": certificates,
        "goal_policy_robustness_certified": False,
        "scope_limits": [
            "Exact serialized learned integer network, alphabet, clock/reset semantics and bit-fault model only.",
            "The six-bit fingerprint gives current behavior plus one-step flip/swap query outputs; it is evaluator-only and never supplied to neural recurrence.",
            "The runtime imports no training code, Torch, NumPy, teacher state IDs or compiled symbolic transition table.",
            "An invariant region is not a canonical codebook. This report does NOT certify exact canonical restoration.",
            "Immediate output between corruption and the subsequent command can be wrong; this is not an always-correct readout claim.",
            "This is not production Nuria Brian2 LIF, online local plasticity, a goal-policy robustness result, a novelty result, or evidence of consciousness.",
        ],
        "elapsed_seconds": time.monotonic() - started,
    }
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(out),
                "network_sha256": report["network_file_sha256"],
                "radius_results": [
                    {
                        "radius": row[
                            "max_corrupted_memory_spikes_before_each_command"
                        ],
                        "passed": row["passed"],
                        "safe_states": row["statistics"]["safe_state_count"],
                    }
                    for row in certificates
                ],
                "elapsed_seconds": report["elapsed_seconds"],
            },
            indent=2,
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--max-bits", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    verify_network(args.network, args.max_bits, args.out)


if __name__ == "__main__":
    main()

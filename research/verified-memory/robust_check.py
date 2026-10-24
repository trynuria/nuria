"""Exact dynamic memory-repair test, independent of neural training.

The neural runtime sees only learned integer weights, a spike bit-vector and
the ordinary event vector. Reference behavior labels exist only in this checker.
Success means one action tick returns EXACTLY to the correctly updated canonical
spike code after every allowed corruption. Correct readout alone is insufficient.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from itertools import combinations
from pathlib import Path

import transplant_check as reference

RUNTIME_SOURCE_BYTES = Path(reference.__file__).read_bytes()


def mask_list(n, weight):
    return [sum(1 << i for i in neurons) for neurons in combinations(range(n), weight)]


def canonical_codes(runtime):
    codes, observations, errors = {}, [], []
    for pair in reference.PAIRS:
        candidates = []
        for nuisance in (0, 1):
            code, out = runtime.tick(0, reference.initial_event(pair, nuisance))
            candidates.append(code)
            good = out == reference.expected_observations(pair)
            row = {
                "initial_observation": list(pair),
                "initial_nuisance": nuisance,
                "neural_code": code,
                "observed_fingerprint": list(out),
                "expected_fingerprint": list(reference.expected_observations(pair)),
                "output_correct": good,
            }
            observations.append(row)
            if not good:
                errors.append(
                    {
                        "reason": "incorrect noise-free initial observation",
                        "actual_case": row,
                    }
                )
        if len(set(candidates)) != 1:
            errors.append(
                {
                    "reason": "initial code depends on nuisance; there is no unique canonical code for this behavior",
                    "initial_observation": list(pair),
                    "neural_codes": candidates,
                }
            )
        else:
            codes[pair] = candidates[0]
    if len(set(codes.values())) != 4:
        errors.append(
            {
                "reason": "canonical codebook does not contain four distinct codes",
                "canonical_codes": {str(k): v for k, v in codes.items()},
            }
        )
    return codes, {
        "passed": not errors,
        "initial_cases": observations,
        "errors": errors,
        "canonical_codes": [
            {
                "evaluator_only_behavior": list(pair),
                **reference.describe(code, runtime.n),
            }
            for pair, code in codes.items()
        ],
    }


def certify(runtime, codes, maximum):
    by_weight, all_failures, pre_wrong_examples = [], [], []
    for weight in range(maximum + 1):
        masks = mask_list(runtime.n, weight)
        exact, correct_out, total, pre_wrong_current, pre_wrong_probe = 0, 0, 0, 0, 0
        corrupt_configurations = 0
        failures = []
        action_counts = {
            str((symbol, nuisance)): {
                "total": 0,
                "exact_canonical_correct": 0,
                "output_correct": 0,
            }
            for symbol, nuisance in reference.COMMANDS
        }
        for pair, canonical in codes.items():
            for mask in masks:
                damaged = canonical ^ mask
                pre = runtime.output(damaged)
                wrong_current = pre[:2] != pair
                wrong_probe = pre != reference.expected_observations(pair)
                pre_wrong_current += wrong_current
                pre_wrong_probe += wrong_probe
                corrupt_configurations += 1
                if wrong_probe and len(pre_wrong_examples) < 16:
                    pre_wrong_examples.append(
                        {
                            "behavior_before_corruption": list(pair),
                            "canonical_code": canonical,
                            "flipped_neurons": [
                                i for i in range(runtime.n) if mask >> i & 1
                            ],
                            "corrupted_code": damaged,
                            "pre_refresh_observed_fingerprint": list(pre),
                            "correct_fingerprint": list(
                                reference.expected_observations(pair)
                            ),
                            "current_observation_wrong": wrong_current,
                        }
                    )
                for symbol, nuisance in reference.COMMANDS:
                    # Reference labels are evaluated outside tick; they are never
                    # encoded as a teacher state or passed to neural recurrence.
                    target_pair = reference.reference_action(pair, symbol)
                    expected_code = codes[target_pair]
                    event = reference.command_event(symbol, nuisance)
                    actual, out = runtime.tick(damaged, event)
                    exact_case = actual == expected_code
                    output_case = out == reference.expected_observations(target_pair)
                    total += 1
                    exact += exact_case and output_case
                    correct_out += output_case
                    counts = action_counts[str((symbol, nuisance))]
                    counts["total"] += 1
                    counts["exact_canonical_correct"] += exact_case and output_case
                    counts["output_correct"] += output_case
                    if not (exact_case and output_case):
                        failure = {
                            "faithful_reproduction": {
                                "initial_observation": list(pair),
                                "initial_nuisance": 0,
                                "then_xor_memory_mask": mask,
                                "then_command": symbol,
                                "then_nuisance": nuisance,
                            },
                            "initial_canonical_code": canonical,
                            "flipped_neurons": [
                                i for i in range(runtime.n) if mask >> i & 1
                            ],
                            "corrupted_code": damaged,
                            "pre_refresh_observed_fingerprint": list(pre),
                            "event_vector": event,
                            "expected_behavior_after_command": list(target_pair),
                            "expected_exact_canonical_code": expected_code,
                            "actual_next_neural_code": actual,
                            "actual_next_observed_fingerprint": list(out),
                            "expected_next_fingerprint": list(
                                reference.expected_observations(target_pair)
                            ),
                            "next_readout_correct_but_code_not_repaired": output_case
                            and not exact_case,
                            "remaining_hamming_distance_to_target_code": (
                                actual ^ expected_code
                            ).bit_count(),
                        }
                        failures.append(failure)
        row = {
            "exact_corruption_weight": weight,
            "masks_per_canonical_code": len(masks),
            "corrupted_configurations": corrupt_configurations,
            "action_nuisance_cases": total,
            "exact_action_and_canonical_repair_cases": exact,
            "correct_output_cases_weaker_criterion": correct_out,
            "passed_all_exact_repair_cases": exact == total,
            "pre_refresh_wrong_current_observation_configurations": pre_wrong_current,
            "pre_refresh_wrong_probe_fingerprint_configurations": pre_wrong_probe,
            "by_command_nuisance": action_counts,
            "failure_count": len(failures),
            "actual_counterexamples": failures,
        }
        by_weight.append(row)
        all_failures.extend(failures)
    radii = []
    for radius in range(maximum + 1):
        rows = by_weight[: radius + 1]
        total = sum(v["action_nuisance_cases"] for v in rows)
        repaired = sum(v["exact_action_and_canonical_repair_cases"] for v in rows)
        radii.append(
            {
                "max_corrupted_spike_bits_before_each_action": radius,
                "passed": repaired == total,
                "finite_cases_checked": total,
                "exact_repair_cases": repaired,
                "correct_readout_only_cases": sum(
                    v["correct_output_cases_weaker_criterion"] for v in rows
                ),
                "arbitrarily_repeated_corruptions_certified": repaired == total,
                "proof_if_passed": "The initial code is canonical. Every <=k corruption followed by every legal action/nuisance maps exactly to the updated canonical code, so induction permits a new adversarial <=k corruption before every subsequent action.",
            }
        )
    distances = [
        {
            "behavior_a": list(a),
            "behavior_b": list(b),
            "hamming_distance": (codes[a] ^ codes[b]).bit_count(),
        }
        for i, a in enumerate(reference.PAIRS)
        for b in reference.PAIRS[i + 1 :]
    ]
    overlaps = []
    for radius in range(maximum + 1):
        masks = [m for w in range(radius + 1) for m in mask_list(runtime.n, w)]
        regions = {
            pair: {code ^ mask for mask in masks} for pair, code in codes.items()
        }
        pairs = []
        for i, a in enumerate(reference.PAIRS):
            for b in reference.PAIRS[i + 1 :]:
                overlap = regions[a] & regions[b]
                if overlap:
                    pairs.append(
                        {
                            "behavior_a": list(a),
                            "behavior_b": list(b),
                            "intersection_count": len(overlap),
                            "example_corrupted_code": min(overlap),
                        }
                    )
        overlaps.append(
            {
                "radius": radius,
                "all_behavior_regions_disjoint": not pairs,
                "overlap_counterexamples": pairs,
            }
        )
    return {
        "by_exact_corruption_weight": by_weight,
        "radius_certificates": radii,
        "pre_refresh_wrong_readout_examples": pre_wrong_examples,
        "canonical_hamming_distances_descriptive_only": distances,
        "corruption_region_overlap_checks": overlaps,
        "first_actual_counterexample": all_failures[0] if all_failures else None,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--network", type=Path, required=True)
    p.add_argument("--max-bits", type=int, default=2)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    started = time.monotonic()
    raw = args.network.read_bytes()
    spec = json.loads(raw)
    runtime = reference.NeuralRuntime(spec)
    if not 0 <= args.max_bits <= runtime.n:
        raise ValueError("max-bits outside neural state dimensions")
    codes, initial = canonical_codes(runtime)
    certificate = (
        certify(runtime, codes, args.max_bits)
        if initial["passed"]
        else {
            "performed": False,
            "reason": "four unique nuisance-invariant correct canonical initial codes were not established",
        }
    )
    report = {
        "network_path": str(args.network),
        "network_file_sha256": hashlib.sha256(raw).hexdigest(),
        "network_canonical_json_sha256": hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "verifier_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "independent_integer_runtime_source_sha256": hashlib.sha256(
            RUNTIME_SOURCE_BYTES
        ).hexdigest(),
        "runtime": "Pure-Python learned integer synaptic sums, hard thresholds and complete binary memory state; no NumPy/Torch training imports, symbolic decoder, compiled teacher transitions, or latent behavior IDs in recurrence",
        "corruption_model": "XOR up to k distinct memory-spike bits at a declared canonical inter-symbol boundary, immediately BEFORE every noninitial command; arbitrary nuisance may change at every action",
        "strict_success_criterion": "Correct next observed behavior AND exact next canonical neural code after one ordinary neural action tick; a correct current/readout fingerprint alone is a failure if the neural code is not repaired",
        "initial_canonical_code_check": initial,
        "dynamic_repair": certificate,
        "goal_policy_robustness_certified": False,
        "policy_scope": "This certifies memory dynamics only. Before refresh, corrupted memory can produce wrong observations; no goal-selection policy is certified by this check.",
        "scope_limits": [
            "Exact serialized network, declared clock boundaries/alphabet and stated corruption budget only.",
            "A clocked binary threshold/reset prototype, not production Nuria Brian2 LIF or endogenous local plasticity.",
            "Neither Hamming distance nor current readout accuracy is substituted for dynamic canonical restoration.",
            "No novelty or consciousness claim follows.",
        ],
        "elapsed_seconds": time.monotonic() - started,
    }
    out = args.out or args.network.with_name(args.network.stem + "-robust-check.json")
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(out),
                "network_sha256": report["network_file_sha256"],
                "canonical_codes_pass": initial["passed"],
                "radius_results": certificate.get("radius_certificates", []),
                "first_counterexample": certificate.get("first_actual_counterexample"),
                "elapsed_seconds": report["elapsed_seconds"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

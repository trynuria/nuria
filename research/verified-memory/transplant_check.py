"""Independent integer-only verifier for learned binary spike memory.

No imports from experiment.py, NumPy, Torch, or a teacher-transition compiler.
NeuralRuntime takes only serialized learned weights, a 12-bit spike state, and
an input event. Task variables exist exclusively in the evaluator below it.

Search exhausts every coordinate mask and every reachable donor/recipient code.
Future correctness is certified by the greatest closed safe subset of ALL4096
possible neural codes, under all commands and both nuisance values.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import time
from collections import deque
from itertools import combinations
from pathlib import Path


class NeuralRuntime:
    """Two threshold/reset phases; complete retained state = queued spikes."""

    def __init__(self, spec):
        if spec["format"] != "clocked-binary-spikes-v1":
            raise ValueError("Unexpected network format")
        if spec.get("alphabet") != ["flip", "swap", "noop"]:
            raise ValueError("Unsupported command alphabet or event binding")
        if (
            spec.get("phase")
            != "canonical memory-spike boundary; transition membrane reset"
        ):
            raise ValueError("Unsupported phase/checkpoint semantics")
        if spec.get("tick_order") != [
            "transition population fires from input and queued memory spikes",
            "memory population fires from transition spikes",
            "output projection thresholded at zero",
        ]:
            raise ValueError("Unsupported threshold/reset execution order")
        if type(spec.get("scale")) is not int or spec["scale"] <= 0:
            raise ValueError("Invalid positive integer weight scale")
        self.n = int(spec["state_neurons"])
        self.hidden = int(spec["transition_neurons"])
        self.arrays = spec["arrays"]
        a = self.arrays
        required = {
            "transition.weight": (self.hidden, 9 + self.n),
            "memory.weight": (self.n, self.hidden),
            "output.weight": (6, self.n),
        }
        for key, (rows, columns) in required.items():
            if len(a[key]) != rows or any(len(row) != columns for row in a[key]):
                raise ValueError(f"Wrong shape: {key}")
            if any(type(w) is not int for row in a[key] for w in row):
                raise ValueError(f"Noninteger weights: {key}")
        for key, size in (
            ("transition.bias", self.hidden),
            ("memory.bias", self.n),
            ("output.bias", 6),
        ):
            if len(a[key]) != size or any(type(w) is not int for w in a[key]):
                raise ValueError(f"Wrong/noninteger bias: {key}")
        for layer in ("transition", "memory", "output"):
            for weights, bias in zip(a[layer + ".weight"], a[layer + ".bias"]):
                if abs(bias) + sum(abs(w) for w in weights) >= 2**60:
                    raise ValueError(
                        "Accumulator bound exceeds the checked integer export contract"
                    )
        self.limit = 1 << self.n
        silenced = spec.get("silenced_memory_neurons", [])
        if any(type(i) is not int or not 0 <= i < self.n for i in silenced):
            raise ValueError("Invalid permanently silenced neuron")
        self.silenced_mask = sum(1 << i for i in set(silenced))
        self.active_state_bits = [
            tuple(i for i in range(self.n) if state >> i & 1)
            for state in range(self.limit)
        ]

    def output(self, state):
        state &= (self.limit - 1) ^ self.silenced_mask
        active = self.active_state_bits[state]
        a = self.arrays
        return tuple(
            int(
                a["output.bias"][j] + sum(a["output.weight"][j][i] for i in active) >= 0
            )
            for j in range(6)
        )

    def tick(self, state, event):
        # No task labels or state IDs enter this computation. Input/state bits
        # select active signed learned synapses, and Python integers cannot
        # silently overflow. Threshold equality is treated as a spike.
        if (
            not 0 <= state < self.limit
            or len(event) != 9
            or any(x not in (0, 1) for x in event)
        ):
            raise ValueError("Malformed binary neural state/event")
        state &= (self.limit - 1) ^ self.silenced_mask
        active = [i for i, bit in enumerate(event) if bit]
        active.extend(9 + i for i in self.active_state_bits[state])
        a = self.arrays
        transition_spikes = [
            j
            for j in range(self.hidden)
            if a["transition.bias"][j]
            + sum(a["transition.weight"][j][i] for i in active)
            >= 0
        ]
        next_state = 0
        for j in range(self.n):
            if (
                not (self.silenced_mask >> j & 1)
                and a["memory.bias"][j]
                + sum(a["memory.weight"][j][i] for i in transition_spikes)
                >= 0
            ):
                next_state |= 1 << j
        return next_state, self.output(next_state)


# Everything below is an independent task evaluator, never neural runtime input.
PAIRS = tuple((x, y) for x in (0, 1) for y in (0, 1))
COMMANDS = tuple((symbol, nuisance) for symbol in range(3) for nuisance in range(2))


def reference_action(pair, symbol):
    x, y = pair
    if symbol == 0:
        return 1 - x, y
    if symbol == 1:
        return y, x
    return pair


def expected_observations(pair):
    return tuple(pair) + reference_action(pair, 0) + reference_action(pair, 1)


def initial_event(pair, nuisance):
    event = [0] * 9
    event[pair[0]] = 1
    event[2 + pair[1]] = 1
    event[7 + nuisance] = 1
    return event


def command_event(symbol, nuisance):
    event = [0] * 9
    event[4 + symbol] = 1
    event[7 + nuisance] = 1
    return event


def describe(code, n):
    return {
        "integer_code": code,
        "spikes_neuron_order": [(code >> i) & 1 for i in range(n)],
    }


def reachable_check(runtime):
    queue, visited, mapping, witnesses = deque(), set(), {}, {}
    initial_checks = []
    edges = 0
    for pair in PAIRS:
        for nuisance in range(2):
            state, out = runtime.tick(0, initial_event(pair, nuisance))
            passed = out == expected_observations(pair)
            initial_checks.append(
                {
                    "initial_observation": list(pair),
                    "nuisance": nuisance,
                    "neural_code": state,
                    "output": list(out),
                    "passed": passed,
                }
            )
            if not passed:
                return (
                    {
                        "passed": False,
                        "reason": "initial observation mismatch",
                        "initial_checks": initial_checks,
                    },
                    {},
                    {},
                )
            queue.append((state, pair, nuisance, [], pair, nuisance))
    while queue:
        state, pair, nuisance, path, initial_pair, initial_nuisance = queue.popleft()
        key = state, pair, nuisance
        if key in visited:
            continue
        visited.add(key)
        mapping.setdefault(state, set()).add(pair)
        witnesses.setdefault(
            state,
            {
                "initial_observation": list(initial_pair),
                "initial_nuisance": initial_nuisance,
                "current_observed_pair": list(pair),
                "commands_and_nuisance": path,
            },
        )
        for symbol, next_nuisance in COMMANDS:
            next_state, out = runtime.tick(state, command_event(symbol, next_nuisance))
            target = reference_action(pair, symbol)
            edges += 1
            if out != expected_observations(target):
                return (
                    {
                        "passed": False,
                        "reason": "reachable composition mismatch",
                        "counterexample": {
                            "initial_observation": list(initial_pair),
                            "initial_nuisance": initial_nuisance,
                            "commands_and_nuisance": path + [[symbol, next_nuisance]],
                            "start_code": state,
                            "reference_pair": list(pair),
                            "command": symbol,
                            "nuisance": next_nuisance,
                            "output": list(out),
                            "expected": list(expected_observations(target)),
                        },
                        "reachable_product_states": len(visited),
                        "checked_edges": edges,
                    },
                    mapping,
                    witnesses,
                )
            queue.append(
                (
                    next_state,
                    target,
                    next_nuisance,
                    path + [[symbol, next_nuisance]],
                    initial_pair,
                    initial_nuisance,
                )
            )
    return (
        {
            "passed": True,
            "initial_checks": initial_checks,
            "reachable_product_states": len(visited),
            "checked_edges": edges,
            "distinct_neural_codes": len(mapping),
            "ambiguous_behavior_codes": [
                code for code, labels in mapping.items() if len(labels) != 1
            ],
        },
        mapping,
        witnesses,
    )


def global_safety(runtime):
    """Exact finite greatest fixed point, including off-manifold patched codes."""
    fingerprints = {expected_observations(pair): pair for pair in PAIRS}
    labels = [fingerprints.get(runtime.output(state)) for state in range(runtime.limit)]
    successors = []
    reverse = [[] for _ in range(runtime.limit)]
    for state in range(runtime.limit):
        next_codes = []
        for event_index, (symbol, nuisance) in enumerate(COMMANDS):
            next_state, _ = runtime.tick(state, command_event(symbol, nuisance))
            next_codes.append(next_state)
            reverse[next_state].append(state)
        successors.append(tuple(next_codes))
    bad = set()
    for state, label in enumerate(labels):
        if (
            state & runtime.silenced_mask
            or label is None
            or any(
                labels[next_state] != reference_action(label, COMMANDS[k][0])
                for k, next_state in enumerate(successors[state])
            )
        ):
            bad.add(state)
    queue = deque(bad)
    while queue:
        failed = queue.popleft()
        for predecessor in reverse[failed]:
            if predecessor not in bad:
                bad.add(predecessor)
                queue.append(predecessor)
    good = set(range(runtime.limit)) - bad
    return labels, successors, good


def counterexample(runtime, successors, start, expected_pair):
    """Shortest bad future from a particular complete state; includes empty word."""
    queue, seen = deque([(start, expected_pair, [])]), set()
    while queue:
        state, pair, path = queue.popleft()
        if (state, pair) in seen:
            continue
        seen.add((state, pair))
        out = runtime.output(state)
        if out != expected_observations(pair):
            return {
                "initial_neural_code": start,
                "expected_initial_pair": list(expected_pair),
                "commands_and_nuisance": path,
                "failure_neural_code": state,
                "observed_fingerprint": list(out),
                "expected_fingerprint": list(expected_observations(pair)),
                "future_command_count": len(path),
            }
        for k, (symbol, nuisance) in enumerate(COMMANDS):
            queue.append(
                (
                    successors[state][k],
                    reference_action(pair, symbol),
                    path + [[symbol, nuisance]],
                )
            )
    return None


def patch(recipient, donor, mask, all_mask):
    return (recipient & (all_mask ^ mask)) | (donor & mask)


def mask_score(mask, cases, labels, good, all_mask, factor):
    immediate, closed, diagnostic_closed, diagnostic_n = 0, 0, 0, 0
    failure = None
    for recipient, donor, rpair, dpair in cases:
        target = (dpair[0], rpair[1]) if factor == 0 else (rpair[0], dpair[1])
        state = patch(recipient, donor, mask, all_mask)
        now = labels[state] == target
        future = now and state in good
        immediate += now
        closed += future
        diagnostic = rpair[0] != dpair[0] and rpair[1] != dpair[1]
        diagnostic_n += diagnostic
        diagnostic_closed += diagnostic and future
        if not future and failure is None:
            failure = {
                "recipient": recipient,
                "donor": donor,
                "recipient_pair": list(rpair),
                "donor_pair": list(dpair),
                "patched_state": state,
                "expected_hybrid_pair": list(target),
                "immediate_output_matches": now,
            }
    return {
        "mask": mask,
        "neurons": [i for i in range(all_mask.bit_length()) if mask >> i & 1],
        "size": mask.bit_count(),
        "immediate_correct_cases": immediate,
        "future_closed_correct_cases": closed,
        "total_cases": len(cases),
        "diagnostic_future_closed_correct_cases": diagnostic_closed,
        "diagnostic_cases": diagnostic_n,
        "all_cases_pass": closed == len(cases),
        "first_failure": failure,
    }


def transfer_search(runtime, mapping, labels, successors, good):
    if any(len(v) != 1 for v in mapping.values()):
        return {
            "search_performed": False,
            "reason": "a reachable code has multiple task behaviors",
        }
    codes = sorted(mapping)
    behavior = {code: next(iter(mapping[code])) for code in codes}
    cases = [(r, d, behavior[r], behavior[d]) for r in codes for d in codes]
    all_mask = runtime.limit - 1
    result = {
        "all_masks_per_factor": runtime.limit,
        "donor_recipient_cases_per_mask": len(cases),
        "mask_bit_convention": "bit i selects donor neuron i; all other neurons retain recipient spikes",
        "future_criterion": "immediate observed fingerprint matches hybrid AND patched code belongs to greatest safe set under all six command/nuisance events",
        "factors": {},
    }
    for factor in (0, 1):
        scores = [
            mask_score(mask, cases, labels, good, all_mask, factor)
            for mask in range(runtime.limit)
        ]
        passing = [s for s in scores if s["all_cases_pass"]]
        selected = (
            min(passing, key=lambda s: (s["size"], s["mask"]))
            if passing
            else max(
                scores,
                key=lambda s: (
                    s["future_closed_correct_cases"],
                    s["diagnostic_future_closed_correct_cases"],
                    -s["size"],
                    -s["mask"],
                ),
            )
        )
        same_size = [s for s in scores if s["size"] == selected["size"]]
        rng = random.Random(20261024 + factor)
        controls = rng.sample(same_size, min(32, len(same_size)))
        chosen = dict(selected)
        if chosen["first_failure"]:
            first = chosen["first_failure"]
            chosen["shortest_future_counterexample"] = counterexample(
                runtime,
                successors,
                first["patched_state"],
                tuple(first["expected_hybrid_pair"]),
            )
        # Find a mask that gets every immediate output right yet fails later:
        # this directly exposes why an immediate-only transplant is insufficient.
        deceptively_good = [
            s
            for s in scores
            if s["immediate_correct_cases"] == len(cases) and not s["all_cases_pass"]
        ]
        deceptive_example = None
        if deceptively_good:
            deceptive_example = min(
                deceptively_good, key=lambda s: (s["size"], s["mask"])
            )
            f = deceptive_example["first_failure"]
            deceptive_example = dict(deceptive_example)
            deceptive_example["shortest_future_counterexample"] = counterexample(
                runtime,
                successors,
                f["patched_state"],
                tuple(f["expected_hybrid_pair"]),
            )
        patched_codes = {
            patch(r, d, selected["mask"], all_mask) for r, d, _, _ in cases
        }
        result["factors"][str(factor)] = {
            "future_safe_coordinate_patch_exists": bool(passing),
            "passing_mask_count": len(passing),
            "passing_masks": [s["mask"] for s in passing],
            "selected_minimal_exact_or_best_failed": chosen,
            "selected_patch_distinct_codes": sorted(patched_codes),
            "selected_patch_off_manifold_codes": sorted(patched_codes - set(codes)),
            "selected_patch_enters_original_reachable_code_after_every_single_command": all(
                n in mapping for code in patched_codes for n in successors[code]
            ),
            "immediate_only_false_positive_mask_count": len(deceptively_good),
            "immediate_only_false_positive_example": deceptive_example,
            "random_same_size_controls": {
                "seed": 20261024 + factor,
                "sample_count": len(controls),
                "sample_masks": [
                    {
                        k: s[k]
                        for k in (
                            "mask",
                            "size",
                            "future_closed_correct_cases",
                            "diagnostic_future_closed_correct_cases",
                            "all_cases_pass",
                        )
                    }
                    for s in controls
                ],
                "sample_mean_all_pairs_accuracy": sum(
                    s["future_closed_correct_cases"] / len(cases) for s in controls
                )
                / len(controls),
                "sample_mean_diagnostic_accuracy": sum(
                    s["diagnostic_future_closed_correct_cases"] / s["diagnostic_cases"]
                    for s in controls
                )
                / len(controls),
                "exact_same_size_population_count": len(same_size),
                "exact_same_size_population_passing_count": sum(
                    s["all_cases_pass"] for s in same_size
                ),
                "exact_population_mean_all_pairs_accuracy": sum(
                    s["future_closed_correct_cases"] / len(cases) for s in same_size
                )
                / len(same_size),
                "exact_population_mean_diagnostic_accuracy": sum(
                    s["diagnostic_future_closed_correct_cases"] / s["diagnostic_cases"]
                    for s in same_size
                )
                / len(same_size),
            },
            "all_mask_scores": [
                {
                    k: s[k]
                    for k in (
                        "mask",
                        "size",
                        "immediate_correct_cases",
                        "future_closed_correct_cases",
                        "diagnostic_future_closed_correct_cases",
                    )
                }
                for s in scores
            ],
        }
    whole_ok = all(
        d in good and labels[d] == dpair and patch(r, d, all_mask, all_mask) == d
        for r, d, rpair, dpair in cases
    )
    result["whole_state_donor_control"] = {
        "passed": whole_ok,
        "checked_pairs": len(cases),
        "meaning": "all donor outputs and every future composition preserved; not a selective hybrid transfer",
    }
    zero_cases = []
    for code, pair in behavior.items():
        zero_cases.append(
            {
                "original_code": code,
                "expected_pair": list(pair),
                "all_future_correct_after_zero_reset": 0 in good and labels[0] == pair,
                "counterexample": None
                if 0 in good and labels[0] == pair
                else counterexample(runtime, successors, 0, pair),
            }
        )
    result["zero_state_control"] = {
        "zero_output": list(runtime.output(0)),
        "zero_is_globally_safe": 0 in good,
        "zero_behavior": None if labels[0] is None else list(labels[0]),
        "cases": zero_cases,
    }
    lesions = []
    lesion_masks = set(1 << i for i in range(runtime.n))
    lesion_masks.update(
        result["factors"][str(f)]["selected_minimal_exact_or_best_failed"]["mask"]
        for f in (0, 1)
    )
    for mask in sorted(lesion_masks):
        failed = []
        for code, pair in behavior.items():
            lesion = code & (all_mask ^ mask)
            if lesion not in good or labels[lesion] != pair:
                failed.append(
                    {
                        "original_code": code,
                        "expected_pair": list(pair),
                        "lesioned_code": lesion,
                        "counterexample": counterexample(
                            runtime, successors, lesion, pair
                        ),
                    }
                )
        lesions.append(
            {
                "zeroed_mask": mask,
                "zeroed_neurons": [i for i in range(runtime.n) if mask >> i & 1],
                "failed_reachable_codes": len(failed),
                "total_reachable_codes": len(codes),
                "counterexamples": failed,
            }
        )
    result["lesion_controls"] = lesions
    result["lesion_controls_semantics"] = (
        "One-time zeroing at a complete memory-spike boundary, followed by the original network. Permanent lesions are separately checked below."
    )
    # Descriptive code geometry plus actual neural fault tests. Hamming distance
    # alone is never treated as proof that the learned dynamics correct faults.
    unit_tables = []
    for unit in range(runtime.n):
        table = {
            pair: {
                (code >> unit) & 1 for code, label in behavior.items() if label == pair
            }
            for pair in PAIRS
        }
        depends_x = all(table[(x, 0)] == table[(x, 1)] for x in (0, 1))
        depends_y = all(table[(0, y)] == table[(1, y)] for y in (0, 1))
        kind = (
            "history_dependent"
            if any(len(v) != 1 for v in table.values())
            else "constant"
            if depends_x and depends_y
            else "X_only"
            if depends_x
            else "Y_only"
            if depends_y
            else "mixed"
        )
        unit_tables.append(
            {
                "neuron": unit,
                "possible_values_in_pair_order00_01_10_11": [
                    sorted(table[pair]) for pair in PAIRS
                ],
                "evaluator_description": kind,
            }
        )
    hamming = [
        {"code_a": a, "code_b": b, "distance": (a ^ b).bit_count()}
        for i, a in enumerate(codes)
        for b in codes[i + 1 :]
    ]
    perturbations = []
    for size in (1, 2, 3):
        total, passed, first_failure = 0, 0, None
        for units in combinations(range(runtime.n), size):
            mask = sum(1 << i for i in units)
            for code, pair in behavior.items():
                changed = code ^ mask
                good_case = changed in good and labels[changed] == pair
                total += 1
                passed += good_case
                if not good_case and first_failure is None:
                    first_failure = {
                        "original_code": code,
                        "flipped_neurons": list(units),
                        "changed_code": changed,
                        "counterexample": counterexample(
                            runtime, successors, changed, pair
                        ),
                    }
        perturbations.append(
            {
                "flipped_spike_bits": size,
                "all_future_correct_cases": passed,
                "total_cases": total,
                "all_cases_pass": passed == total,
                "first_actual_counterexample": first_failure,
            }
        )
    result["learned_code_geometry"] = {
        "descriptive_only_unit_dependencies": unit_tables,
        "pairwise_hamming_distances": hamming,
        "minimum_hamming_distance": min(v["distance"] for v in hamming),
        "minimum_hamming_distance_between_distinct_behaviors": min(
            (a ^ b).bit_count()
            for a in codes
            for b in codes
            if behavior[a] != behavior[b]
        ),
        "actual_transient_bit_flip_tests": perturbations,
    }
    return result


def permanent_lesion_search(runtime):
    """Find minimal damaging permanent subsets, testing every subset through3."""
    rows, minimum, witnesses = [], None, []
    available = [i for i in range(runtime.n) if not runtime.silenced_mask >> i & 1]
    for size in range(1, min(3, len(available)) + 1):
        tested, failed, passing = 0, 0, 0
        masks = []
        for neurons in combinations(available, size):
            mask = sum(1 << i for i in neurons)
            lesioned = copy.copy(runtime)
            lesioned.silenced_mask = runtime.silenced_mask | mask
            check, _, _ = reachable_check(lesioned)
            tested += 1
            if not check["passed"]:
                failed += 1
                masks.append(mask)
                witnesses.append(
                    {
                        "additional_silenced_neurons": list(neurons),
                        "additional_mask": mask,
                        "actual_failure": check,
                    }
                )
            else:
                passing += 1
        rows.append(
            {
                "additional_lesion_size": size,
                "tested_masks": tested,
                "damaging_masks": failed,
                "closure_preserving_masks": passing,
                "all_damaging_masks": masks,
            }
        )
        if failed:
            minimum = size
            break
    return {
        "semantics": "Permanently clamp selected memory neurons to0 after each threshold/reset phase, before output and subsequent recurrence; existing spec lesions retained",
        "minimal_additional_damaging_subset_size_up_to3": minimum,
        "levels": rows,
        "actual_counterexamples": witnesses,
        "larger_sizes_skipped_after_minimality_established": minimum is not None,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--network",
        type=Path,
        required=True,
    )
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    started = time.monotonic()
    raw = args.network.read_bytes()
    spec = json.loads(raw)
    runtime = NeuralRuntime(spec)
    if runtime.n != 12:
        raise ValueError(
            "This bounded exhaustive verifier is restricted to12 memory neurons"
        )
    reach, mapping, witnesses = reachable_check(runtime)
    labels, successors, good = global_safety(runtime)
    search = (
        transfer_search(runtime, mapping, labels, successors, good)
        if reach["passed"]
        else {"search_performed": False, "reason": "base closure failed"}
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
        "runtime": "Independent pure-Python arbitrary-precision integer sums; two threshold/reset populations; no NumPy, Torch or training-module imports; no task labels in NeuralRuntime.tick/output",
        "scope": "Exact serialized quantized network only, at declared synchronous spike boundaries; not continuous Brian2 LIF, consciousness, arbitrary tasks, or other training seeds",
        "independent_reachable_closure": reach,
        "reachable_neural_codes": [
            {
                **describe(code, runtime.n),
                "evaluator_only_behaviors": [list(v) for v in sorted(mapping[code])],
                "first_witness": witnesses[code],
            }
            for code in sorted(mapping)
        ],
        "all_binary_state_space_analysis": {
            "neural_codes_examined": runtime.limit,
            "transition_events_per_code": len(COMMANDS),
            "integer_ticks_checked": runtime.limit * len(COMMANDS),
            "codes_with_valid_current_fingerprint": sum(v is not None for v in labels),
            "codes_safe_for_every_future_composition": len(good),
            "safe_code_counts_by_behavior": {
                str(pair): sum(labels[s] == pair for s in good) for pair in PAIRS
            },
            "fixed_point_method": "Start from valid current/one-step outputs; propagate failures through every predecessor to obtain greatest safe closed subset",
        },
        "selective_transfer": search,
        "permanent_lesion_search": permanent_lesion_search(runtime)
        if reach["passed"]
        else {"performed": False, "reason": "base closure already fails"},
        "spec_permanently_silenced_memory_neurons": [
            i for i in range(runtime.n) if runtime.silenced_mask >> i & 1
        ],
        "elapsed_seconds": time.monotonic() - started,
    }
    out = args.out or args.network.parent / "transplant-check.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    summary = {
        "report": str(out),
        "network_sha256": report["network_file_sha256"],
        "base_closure": reach["passed"],
        "reachable_codes": len(mapping),
        "globally_safe_codes": len(good),
        "elapsed_seconds": report["elapsed_seconds"],
    }
    if "factors" in search:
        summary["selective_transfer"] = {
            f: {
                "exact_mask_exists": r["future_safe_coordinate_patch_exists"],
                "passing_masks": r["passing_mask_count"],
                "selected_neurons": r["selected_minimal_exact_or_best_failed"][
                    "neurons"
                ],
                "best_all_pair_cases": r["selected_minimal_exact_or_best_failed"][
                    "future_closed_correct_cases"
                ],
                "total_pairs": r["selected_minimal_exact_or_best_failed"][
                    "total_cases"
                ],
            }
            for f, r in search["factors"].items()
        }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

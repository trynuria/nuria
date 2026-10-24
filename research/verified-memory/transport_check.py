"""Check behavioral memory transport between independently learned spike codes.

The migration map is used once when replacing a frozen skill module. Neither
network consults it during subsequent neural execution. This preserves the
declared finite behavioral contract, not consciousness or an entire identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from pathlib import Path

from transplant_check import COMMANDS, NeuralRuntime, command_event, reachable_check


def equivalent_future(left, right, source, target):
    queue, seen, edges = deque([(source, target, [])]), set(), 0
    while queue:
        a, b, path = queue.popleft()
        if (a, b) in seen:
            continue
        seen.add((a, b))
        if left.output(a) != right.output(b):
            return {
                "passed": False,
                "counterexample": path,
                "source_output": list(left.output(a)),
                "target_output": list(right.output(b)),
            }
        for symbol, nuisance in COMMANDS:
            x, _ = left.tick(a, command_event(symbol, nuisance))
            y, _ = right.tick(b, command_event(symbol, nuisance))
            edges += 1
            queue.append((x, y, path + [[symbol, nuisance]]))
    return {"passed": True, "checked_product_states": len(seen), "checked_edges": edges}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raw_a, raw_b = args.source.read_bytes(), args.target.read_bytes()
    a, b = json.loads(raw_a), json.loads(raw_b)
    # These are explicit protocol bindings, not identities inferred from tick
    # shape. Both world-conformance gates must pass before transport is issued.
    for field in ("format", "alphabet", "tick_order", "phase", "state_neurons"):
        if a[field] != b[field]:
            raise ValueError("Incompatible module interaction contracts")
    left, right = NeuralRuntime(a), NeuralRuntime(b)
    check_a, reachable_a, _ = reachable_check(left)
    check_b, reachable_b, _ = reachable_check(right)
    if not check_a["passed"] or not check_b["passed"]:
        raise ValueError("World conformance failed; cannot issue transport")
    by_fingerprint = {}
    for code in sorted(reachable_b):
        by_fingerprint.setdefault(right.output(code), []).append(code)
    rows = []
    for code in sorted(reachable_a):
        # No latent task pair enters this choice. Match observable output
        # fingerprints, then independently check the ENTIRE future product.
        candidates = by_fingerprint.get(left.output(code), [])
        selected, verified = None, None
        for candidate in candidates:
            result = equivalent_future(left, right, code, candidate)
            if result["passed"]:
                selected, verified = candidate, result
                break
        if selected is None:
            raise ValueError("No behaviorally equivalent target checkpoint")
        rows.append(
            {
                "source_spike_code": code,
                "target_spike_code": selected,
                "observable_fingerprint": list(left.output(code)),
                "transport_certificate": verified,
                "raw_spike_copy_control": equivalent_future(left, right, code, code),
            }
        )
    result = {
        "source_network_sha256": hashlib.sha256(raw_a).hexdigest(),
        "target_network_sha256": hashlib.sha256(raw_b).hexdigest(),
        "verifier_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "source_conformance": check_a,
        "target_conformance": check_b,
        "all_reachable_source_states_transportable": all(
            row["transport_certificate"]["passed"] for row in rows
        ),
        "raw_copy_failing_states": sum(
            not row["raw_spike_copy_control"]["passed"] for row in rows
        ),
        "transport_map": rows,
        "scope": "Exact frozen finite-task spike modules at synchronous command boundaries. Mapping is a one-time checkpoint migration, not evidence of substrate-independent consciousness or arbitrary neural transfer.",
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "all_reachable_source_states_transportable",
                    "raw_copy_failing_states",
                    "source_network_sha256",
                    "target_network_sha256",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

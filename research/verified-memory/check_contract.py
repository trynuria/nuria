"""Bounded mutation checks of the independent goal-loop verifier contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--network", type=Path, required=True)
    p.add_argument("--policy", type=Path, required=True)
    p.add_argument("--verifier", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    spec = importlib.util.spec_from_file_location(
        "independent_goal_checker", args.verifier
    )
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    raw_n, raw_p = args.network.read_bytes(), args.policy.read_bytes()
    network, policy = json.loads(raw_n), json.loads(raw_p)
    cases = []

    def check(name, mutate, expected):
        n, p = copy.deepcopy(network), copy.deepcopy(policy)
        mutate(n, p)
        try:
            memory = verifier.Memory(n)
            runtime_policy = verifier.Policy(p, memory.n)
            codes, goals, closure = verifier.establish_closure(memory, runtime_policy)
            if not closure["passed"]:
                actual, detail = "semantic_failure", closure["failures"][0]
            else:
                result = verifier.evaluate(
                    memory, runtime_policy, codes, goals, 0, True
                )
                actual = "pass" if result["passed"] else "semantic_failure"
                detail = result["first_actual_counterexample"]
        except (ValueError, KeyError, TypeError) as error:
            actual, detail = (
                "contract_rejected",
                {"exception": type(error).__name__, "message": str(error)},
            )
        cases.append(
            {
                "mutation": name,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
                "detail": detail,
            }
        )

    check("unchanged baseline", lambda n, p: None, "pass")
    check(
        "memory phase order reversed",
        lambda n, p: n.__setitem__("tick_order", list(reversed(n["tick_order"]))),
        "contract_rejected",
    )
    check(
        "memory canonical boundary changed",
        lambda n, p: n.__setitem__("phase", "continuous membrane state"),
        "contract_rejected",
    )
    check(
        "memory command meanings reordered",
        lambda n, p: n.__setitem__("alphabet", ["swap", "flip", "noop"]),
        "contract_rejected",
    )
    check(
        "memory input shape changed",
        lambda n, p: n.__setitem__("state_neurons", 13),
        "contract_rejected",
    )
    check(
        "memory floating synaptic weight",
        lambda n, p: n["arrays"]["transition.weight"][0].__setitem__(0, 1.0),
        "contract_rejected",
    )
    check(
        "memory accumulator contract exceeded",
        lambda n, p: n["arrays"]["transition.weight"][0].__setitem__(0, 2**60),
        "contract_rejected",
    )
    check(
        "memory zero scale", lambda n, p: n.__setitem__("scale", 0), "contract_rejected"
    )
    check(
        "policy action meanings reordered",
        lambda n, p: p.__setitem__("actions", ["swap", "flip", "noop", "STOP"]),
        "contract_rejected",
    )
    check(
        "policy threshold equality changed",
        lambda n, p: p.__setitem__("runtime", p["runtime"].replace(">=0", ">0")),
        "contract_rejected",
    )
    check(
        "policy duplicate goal channels",
        lambda n, p: p["goal_channels"].__setitem__(1, list(p["goal_channels"][0])),
        "contract_rejected",
    )
    check(
        "policy floating synaptic weight",
        lambda n, p: p["arrays"]["hidden.weight"][0].__setitem__(0, 1.0),
        "contract_rejected",
    )
    check(
        "policy action output shape changed",
        lambda n, p: p["arrays"]["action.bias"].append(0),
        "contract_rejected",
    )
    check(
        "policy all actions suppressed with valid integers",
        lambda n, p: p["arrays"].__setitem__("action.bias", [-(10**9)] * 4),
        "semantic_failure",
    )
    check(
        "memory all queries forced on with valid integers",
        lambda n, p: n["arrays"].__setitem__("output.bias", [10**9] * 6),
        "semantic_failure",
    )
    check(
        "policy physically impossible goal fingerprint",
        lambda n, p: p["goal_channels"].__setitem__(0, [0, 0, 0, 0, 0, 0]),
        "semantic_failure",
    )

    report = {
        "format": "independent-goal-loop-contract-mutations-v1",
        "network_sha256": digest(raw_n),
        "policy_sha256": digest(raw_p),
        "verifier_source_sha256": digest(args.verifier.read_bytes()),
        "audit_source_sha256": digest(Path(__file__).read_bytes()),
        "passed": all(case["passed"] for case in cases),
        "case_count": len(cases),
        "cases": cases,
        "formal_induction_review": {
            "initial_case": "Every legal initial observation and nuisance initializes the exact canonical body-bound memory code.",
            "inductive_invariant": "Before the adversarial fault at a new cycle, neural memory is the exact canonical code for the current evaluator body state.",
            "local_step": "Every <=k-bit XOR fault followed by either-nuisance NOOP refresh returns that same canonical code; same learned policy selects one action. For either action nuisance, a non-STOP action advances both body and memory to the corresponding canonical state and strictly reduces fixed-goal rank.",
            "terminal_case": "STOP holds exactly at the true body goal. The refreshed memory is canonical at STOP; no body or memory action tick follows STOP.",
            "bound": "For a goal held FIXED throughout the route, rank starts <=3 and decreases on every non-STOP cycle: <=3 body actions, <=4 cycles including terminal STOP, <=7 memory ticks including refreshes.",
            "changing_goal_limit": "Goal input may be externally selected, but if the goal changes adversarially between decisions, the three-action convergence bound does NOT apply. Local action correctness can still be checked for each presented goal.",
            "fault_schedule_limit": "No extra fault may occur between refresh, policy inference and the action memory tick. Direct damaged-policy failures refute that stronger unrestricted schedule.",
            "finite_completeness": "The checked model has exactly four reachable canonical body/memory pairs, four unique goal bindings, all three command meanings, both nuisance values, and all distinct XOR masks of weights 0 through k.",
            "proof_kind": "Exhaustive exact-integer finite verification plus induction over declared cycles; ordinary source/file hashes bind artifacts. This is NOT a cryptographic proof of arbitrary execution or independent verification of the real world.",
        },
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(args.out),
                "passed": report["passed"],
                "cases": report["case_count"],
                "failed_cases": [c for c in cases if not c["passed"]],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

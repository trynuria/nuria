"""Run the declared small training procedures into a new evidence directory."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent


def run(script, *args):
    subprocess.run([sys.executable, str(SOURCE / script), *map(str, args)], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    evidence = args.out / "evidence"
    for seed in (9102, 9103, 9104):
        run(
            "train_initial.py",
            "--out",
            evidence / f"initial-{seed}",
            "--seed",
            seed,
            "--rounds",
            4,
            "--updates",
            1000,
        )
    parent = evidence / "initial-9102/network-00.json"
    run(
        "train_memory.py",
        "--out",
        evidence / "factorized",
        "--seed",
        9301,
        "--rounds",
        8,
        "--updates",
        2000,
        "--parent",
        parent,
        "--noise-bits",
        2,
    )
    run(
        "train_separated.py",
        "--out",
        evidence / "mixed",
        "--seed",
        9501,
        "--rounds",
        1,
        "--updates",
        8000,
        "--noise-bits",
        3,
        "--compression",
        0.15,
    )
    run(
        "train_initial.py",
        "--out",
        evidence / "repair",
        "--seed",
        9201,
        "--rounds",
        6,
        "--updates",
        500,
        "--parent",
        parent,
        "--silence",
        2,
    )
    for seed in (9102, 9103, 9104):
        run(
            "transplant_check.py",
            "--network",
            evidence / f"initial-{seed}/network-00.json",
            "--out",
            evidence / f"initial-{seed}/transplant-check.json",
        )
    for name, radius in (("factorized", 2), ("mixed", 3)):
        network = evidence / name / "network-00.json"
        policy_dir = evidence / f"{name}-goal"
        run(
            "robust_check.py",
            "--network",
            network,
            "--max-bits",
            radius + 1,
            "--out",
            evidence / name / "robust-check.json",
        )
        run("goal_policy.py", "--network", network, "--out", policy_dir)
        run(
            "goal_robust_check.py",
            "--network",
            network,
            "--policy",
            policy_dir / "goal-policy.json",
            "--max-bits",
            radius,
            "--out",
            evidence / name / "goal-robustness.json",
        )
        result = json.loads((evidence / name / "goal-robustness.json").read_text())
        if (
            not result["initial_and_command_world_closure"]["passed"]
            or not result["neural_refresh_then_same_learned_policy"]["passed"]
        ):
            raise ValueError(
                f"Reproduced network did not earn the declared goal certificate: {name}"
            )
    run("check_tradeoff.py", "--root", args.out, "--out", evidence / "tradeoff.json")
    run(
        "transport_check.py",
        "--source",
        parent,
        "--target",
        evidence / "initial-9103/network-00.json",
        "--out",
        evidence / "transport.json",
    )
    print(
        json.dumps(
            {
                "completed": True,
                "evidence_directory": str(evidence),
                "scope": "Certificates apply to the actual exported weights. Floating-point training may differ across platforms; no byte-identical training guarantee is assumed.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

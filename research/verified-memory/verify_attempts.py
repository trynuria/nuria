"""Recompute retained failed-LIF held-out scores from numeric evidence."""

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent / "attempts"


def main():
    findings = json.loads((ROOT / "recurrent-lif-findings.json").read_text())
    rows = []
    for result in findings["per_seed"]:
        name = f"{result['model']}_full_closure_seed{result['seed']}_steps2000_raw.npz"
        with np.load(ROOT / "raw" / name, allow_pickle=False) as data:
            predicted = data["predicted_probe_logits_each_prefix"][-1, :, :2] > 0
            expected = data["observed_probe_outcomes_each_prefix"][-1, :, :2] > 0.5
            accuracy = float(np.all(predicted == expected, axis=1).mean())
            if abs(accuracy - result["fresh_raw_joint_length48"]) > 1e-15:
                raise ValueError(
                    "Held-out prediction evidence differs from retained score"
                )
            diagnostic = data["transfer_diagnostic_mask_both_factors_differ"]
            for factor in (0, 1):
                predicted = (
                    data[f"factor_{factor}_predicted_hybrid_future_logits"][
                        diagnostic, :2
                    ]
                    > 0
                )
                expected = (
                    data[f"factor_{factor}_expected_hybrid_future_observation"][
                        diagnostic
                    ]
                    > 0.5
                )
                measured = float(np.all(predicted == expected, axis=1).mean())
                if abs(measured - result[f"factor{factor}_transfer_accuracy"]) > 1e-15:
                    raise ValueError(
                        "Held-out intervention evidence differs from retained score"
                    )
            rows.append(
                {
                    "model": result["model"],
                    "seed": result["seed"],
                    "fresh_length48_accuracy": accuracy,
                }
            )
    print(
        json.dumps(
            {
                "passed": True,
                "numeric_archives_checked": len(rows),
                "independently_recomputed_fresh_scores": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Retrain every retained semantic-priority development checkpoint exactly.

This uses the fitted model implementation, not an independent gradient checker.
It is separate from independent final-output and acceptance reconstruction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from cognition.semantic_guidance import SemanticGuidance
from scripts.check_neural_routing_results import _data, check_archives, require


def reproduce(development):
    development = Path(development)
    check_archives(development)
    rows = []
    began = time.process_time()
    for attempt in range(1, 5):
        for fold in (0, 1):
            prefix = f"semantic-priority-dev-{attempt:02}"
            dataset = _data(development, f"{prefix}/training-{fold}.json")
            summary = _data(development, f"{prefix}/summary-{fold}.json")
            for method, fitted in summary["fits"].items():
                expected = fitted["evidence"]
                model = SemanticGuidance(**expected["configuration"])
                model.fit(dataset["records"])
                actual = model.evidence()
                require(
                    actual == expected,
                    "A deterministic development fit differs from its complete retained checkpoint",
                )
                rows.append(
                    {
                        "attempt": attempt,
                        "fold": fold,
                        "method": method,
                        "training_sha256": actual["training_sha256"],
                        "weights_sha256": hashlib.sha256(
                            json.dumps(
                                actual["weights"], sort_keys=True, separators=(",", ":")
                            ).encode()
                        ).hexdigest(),
                        "exact_inference_checkpoint_match": True,
                    }
                )
    return {
        "schema": "nuria.semantic-guidance-training-reproduction.v1",
        "verified": True,
        "fits": rows,
        "cpu_seconds": time.process_time() - began,
        "source_sha256": hashlib.sha256(
            (
                Path(__file__).resolve().parents[1] / "cognition/semantic_guidance.py"
            ).read_bytes()
        ).hexdigest(),
        "scope": "Deterministic full retraining on retained development records reproduces every complete inference checkpoint. Uses the fitted numerical implementation; not an independent gradient proof, external receipt authentication, fresh qualification or consciousness evidence.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("development", type=Path)
    parser.add_argument("new_receipt", type=Path)
    args = parser.parse_args()
    result = reproduce(args.development)
    with args.new_receipt.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "verified": result["verified"],
                "fits": len(result["fits"]),
                "cpu_seconds": result["cpu_seconds"],
            }
        )
    )


if __name__ == "__main__":
    main()

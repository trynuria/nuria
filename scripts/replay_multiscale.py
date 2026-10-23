"""Replay supplied inputs through an isolated durable TVB/Brian2 experiment."""

import argparse
import json
from pathlib import Path

from cognition.multiscale_loop import MultiscaleExperiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", type=Path, help="JSONL input records; no RPC is called"
    )
    parser.add_argument("state", type=Path, help="New, empty experiment directory")
    args = parser.parse_args()
    if args.inputs.stat().st_size > 4_000_000:
        raise ValueError("Replay exceeds the four-megabyte bound")
    events = [
        json.loads(line)
        for line in args.inputs.read_text().splitlines()
        if line.strip()
    ]
    if not events or len(events) > 10000:
        raise ValueError("Replay needs 1–10000 inputs")
    experiment = MultiscaleExperiment(args.state)
    try:
        for order, event in enumerate(events):
            experiment.observe(order, event)
        print(
            json.dumps(
                {
                    "phase": "isolated_replay",
                    "events": experiment.path.events,
                    "integrity": experiment.journal.verify(full=True),
                    "model_identity": experiment.path.model.identity,
                    "input_scope": "Supplied file; chain provenance is not independently verified by this command",
                    "financial_execution": False,
                },
                indent=2,
            )
        )
    finally:
        experiment.close()


if __name__ == "__main__":
    main()

"""Capture exact prior-code continuation from a trusted private journal copy.

Run this file with PYTHONPATH pointing to the previous code checkout. Keep the
private-copy marker and never supply the active worker's directory.
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from cognition.brain import AdaptiveBrain


def capture(directory: Path) -> dict:
    marker = directory / "PRIVATE-UPGRADE-DRILL.json"
    if (
        not marker.exists()
        or json.loads(marker.read_text()).get("scope")
        != "private copied checkpoint; no public projection"
    ):
        raise ValueError("A marked private copied journal is required")
    db = sqlite3.connect(f"file:{directory / 'cognition.sqlite3'}?mode=ro", uri=True)
    try:
        metadata, raw = db.execute(
            "SELECT metadata,network FROM checkpoint WHERE id=1"
        ).fetchone()
    finally:
        db.close()
    metadata = json.loads(metadata)
    neural_directory = directory / "baseline-neural"
    neural_directory.mkdir(exist_ok=False)
    brain = AdaptiveBrain(neural_directory)
    brain.restore(raw, metadata["brain"])
    result = {
        key: metadata[key]
        for key in ("tick", "cursor", "genesis_utc", "record_seq", "record_head")
    }
    result.update(
        checkpoint_sha256=hashlib.sha256(raw).hexdigest(),
        neural_state_sha256=brain.state_hash(),
    )
    frame = brain.advance(duration_ms=20)
    result.update(
        next_spikes_sha256=frame["spikes_sha256"], next_state_sha256=brain.state_hash()
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve the previous baseline receipt")
    args.output.write_text(json.dumps(capture(args.directory), indent=2) + "\n")


if __name__ == "__main__":
    main()

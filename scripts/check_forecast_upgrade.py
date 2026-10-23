"""Exercise a trusted private COPY of a cognitive checkpoint without RPC.

Never pass the active worker directory. The caller must supply a copied journal
and a baseline receipt made from that same checkpoint with the previous code.
This command writes clearly labelled fixtures only into the private copy.
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from cognition.adaptive_forecast import AdaptiveForecast
from cognition.journal import canonical
from cognition.worker import Organism


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def check(directory: Path, expected: dict, source_sha256: str) -> dict:
    marker = directory / "PRIVATE-UPGRADE-DRILL.json"
    if (
        not marker.exists()
        or json.loads(marker.read_text()).get("scope")
        != "private copied checkpoint; no public projection"
    ):
        raise ValueError("An explicit private-copy marker is required")
    with sqlite3.connect(
        f"file:{directory / 'cognition.sqlite3'}?mode=ro", uri=True
    ) as db:
        metadata_raw, raw_network = db.execute(
            "SELECT metadata,network FROM checkpoint WHERE id=1"
        ).fetchone()
    metadata = json.loads(metadata_raw)
    if hashlib.sha256(raw_network).hexdigest() != expected["checkpoint_sha256"]:
        raise ValueError("Baseline receipt belongs to a different checkpoint")
    for key in ("tick", "cursor", "genesis_utc", "record_seq", "record_head"):
        if metadata[key] != expected[key]:
            raise ValueError("Baseline history differs: " + key)
    original_states = metadata.get("forecasts", {})
    organism = Organism(directory, source_sha256)
    try:
        if organism.brain.state_hash() != expected["neural_state_sha256"]:
            raise RuntimeError("Upgrade changed the restored neural state")
        if (organism.tick, organism.cursor, organism.genesis) != (
            expected["tick"],
            expected["cursor"],
            expected["genesis_utc"],
        ):
            raise RuntimeError("Upgrade changed neural history identity")
        for source, state in original_states.items():
            model = organism.forecasts[source]
            if (
                not isinstance(model, AdaptiveForecast)
                or digest(model.base.state()) != digest(state)
                or model.stats["n"] != 0
            ):
                raise RuntimeError("Legacy learning was not preserved")
        frame = organism.brain.advance(duration_ms=20)
        if (
            frame["spikes_sha256"] != expected["next_spikes_sha256"]
            or organism.brain.state_hash() != expected["next_state_sha256"]
        ):
            raise RuntimeError("Next neural frame differs from the previous code")
    finally:
        # Deliberately leave the simulated frame and upgrade uncommitted.
        organism.journal.db.close()
    recovered = Organism(directory, source_sha256)
    try:
        if (
            recovered.brain.state_hash() != expected["neural_state_sha256"]
            or recovered.cursor != expected["cursor"]
        ):
            raise RuntimeError("Uncommitted state did not roll back")
        fixture_source = "fixture:private-upgrade-drill"
        for offset, side in ((1, "buy"), (2, "sell")):
            recovered.perceive(
                expected["cursor"] + offset,
                {
                    "id": f"private-upgrade-{offset}",
                    "source": fixture_source,
                    "side": side,
                    "quote_amount": 0.5,
                    "creator_fee": 0.001,
                },
            )
        recovered.save()
        checkpoint_hash = recovered.brain.state_hash()
        saved_forecast = digest(recovered.forecasts[fixture_source].state())
        committed_head = recovered.journal.head
    finally:
        recovered.journal.db.close()
    resumed = Organism(directory, source_sha256)
    try:
        if (
            resumed.brain.state_hash() != checkpoint_hash
            or digest(resumed.forecasts[fixture_source].state()) != saved_forecast
            or resumed.journal.head != committed_head
        ):
            raise RuntimeError("Committed neural and forecast continuation differs")
        integrity = resumed.journal.verify(full=True)
        if not integrity["valid"] or resumed.forecasts[fixture_source].stats["n"] != 1:
            raise RuntimeError("Copied journal continuity failed")
        return {
            "verified": True,
            "scope": "Trusted private copied checkpoint and isolated fixtures; no live input, public cache, RPC or payment modified",
            "original_genesis_utc": resumed.genesis,
            "original_tick": expected["tick"],
            "original_cursor": expected["cursor"],
            "original_record_seq": expected["record_seq"],
            "original_record_head": expected["record_head"],
            "checkpoint_sha256": expected["checkpoint_sha256"],
            "restored_neural_state_identical": True,
            "next_spikes_and_state_identical_to_previous_code": True,
            "legacy_sources_preserved": len(original_states),
            "uncommitted_state_rollback_verified": True,
            "joint_forecast_and_neural_recovery_verified": True,
            "copied_journal_integrity": integrity,
            "financial_transactions": 0,
        }
    finally:
        resumed.journal.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("baseline_receipt", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve the previous drill receipt")
    result = check(
        args.directory,
        json.loads(args.baseline_receipt.read_text()),
        args.source_sha256,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

"""Persistent experimental perception path, separate from production authority."""

from __future__ import annotations

import fcntl
import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from cognition.brain import MODEL_VERSION, AdaptiveBrain, sha
from cognition.forecast import ForecastCouncil
from cognition.journal import Journal, canonical
from cognition.learning import OnlineReadout
from cognition.multiscale import VERSION, CoupledBrain, CouplingConfig


class PerceptionPath:
    """Use the existing readout and council, without adding regional features.

    Memory recall, workspace and the autonomous action/habitat loop are outside
    this controlled comparison. Energy is held at 1 and recall/salience at zero.
    """

    def __init__(self, directory: Path, config: CouplingConfig | None):
        self.model = (
            AdaptiveBrain(directory)
            if config is None
            else CoupledBrain(directory, config)
        )
        self.readouts: dict[str, OnlineReadout] = {}
        self.councils: dict[str, ForecastCouncil] = {}
        self.events = 0

    def observe(self, event: dict) -> dict:
        started = time.perf_counter()
        source = event.get("source", "unknown")
        readout = self.readouts.setdefault(source, OnlineReadout())
        council = self.councils.setdefault(source, ForecastCouncil())
        frame = self.model.advance(event, duration_ms=20, context={"energy": 1})
        uncertainty = 1.0
        if council.pending:
            p = council.pending["probability"]
            uncertainty = float(-p * np.log2(p) - (1 - p) * np.log2(1 - p))
        x = readout.features(event, frame["features"], 1.0, uncertainty)
        neural = readout.observe(event, x)
        forecast = council.observe(event, neural["prediction_buy"])
        feedback = forecast["feedback"]
        self.model.reward(float(feedback["improvement"]) if feedback else 0.0)
        self.events += 1
        return {
            "event_id": event["id"],
            "source": source,
            "event_sha256": sha(canonical(event)),
            "spikes_sha256": frame["spikes_sha256"],
            "features_sha256": sha(
                np.asarray(frame["features"], dtype="<f8").tobytes()
            ),
            "neural_state_sha256": (
                self.model.state_hash()
                if isinstance(self.model, AdaptiveBrain)
                else self.model.brain.state_hash()
            ),
            "action_spike_scores": frame["action_spike_scores"],
            "forecast": forecast,
            "neural": neural,
            "multiscale": frame.get("multiscale"),
            "wall_seconds": time.perf_counter() - started,
        }

    def learning_state(self) -> dict:
        return {
            "events": self.events,
            "readouts": {
                source: model.state() for source, model in self.readouts.items()
            },
            "councils": {
                source: model.state() for source, model in self.councils.items()
            },
        }

    def restore_learning(self, state: dict) -> None:
        self.events = state["events"]
        self.readouts = {
            source: OnlineReadout(value) for source, value in state["readouts"].items()
        }
        self.councils = {
            source: ForecastCouncil(value)
            for source, value in state["councils"].items()
        }


class MultiscaleExperiment:
    """Joint neural, regional, learning and input-cursor commits in one journal."""

    def __init__(self, directory: Path, config: CouplingConfig | None = None):
        self.config = config or CouplingConfig()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        marker = self.directory / "MULTISCALE-EXPERIMENT.json"
        if not marker.exists():
            if any(self.directory.iterdir()):
                raise ValueError(
                    "An existing unmarked history cannot become a multiscale experiment"
                )
            marker.write_bytes(
                canonical({"model": VERSION, "config": asdict(self.config)})
            )
        if json.loads(marker.read_text()) != json.loads(
            canonical({"model": VERSION, "config": asdict(self.config)})
        ):
            raise ValueError("Experiment configuration changed")
        self.lock = (self.directory / "writer.lock").open("a")
        try:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("Another writer owns this experiment") from None
        self.journal = Journal(self.directory)
        if not self.journal.verify(full=True)["valid"]:
            raise RuntimeError("Experiment journal integrity failed")
        self.path = PerceptionPath(self.directory / "neural", self.config)
        saved = self.journal.restore()
        if saved:
            metadata, raw = saved
            self.path.model.restore(raw, metadata["checkpoint_sha256"])
            self.path.restore_learning(metadata["learning"])
        else:
            self.save()
        self.failed = False

    def observe(self, order: int, event: dict) -> dict:
        if self.failed:
            raise RuntimeError(
                "An interrupted experiment must be reopened from its checkpoint"
            )
        if type(order) is not int or order < 0:
            raise ValueError("Invalid input order")
        stored = self.journal.db.execute(
            "SELECT event_order,payload FROM effects WHERE input_id=?", (event["id"],)
        ).fetchone()
        if stored:
            previous = json.loads(stored[1])
            if stored[0] != order or previous["event_sha256"] != sha(canonical(event)):
                raise ValueError("Previously committed input changed")
            return previous
        maximum = self.journal.db.execute(
            "SELECT max(event_order) FROM effects"
        ).fetchone()[0]
        if maximum is not None and order <= maximum:
            raise ValueError("Input ordering changed")
        try:
            row = self.path.observe(event)
            # Raw stimulus and parameter hashes are retained; no wallet authority.
            record = self.journal.append("multiscale_perception", row)
            self.journal.db.execute(
                "INSERT INTO effects VALUES(?,?,?)",
                (event["id"], order, canonical(record).decode()),
            )
            self.save()
        except Exception:
            self.journal.db.rollback()
            self.failed = True
            raise
        return record

    def save(self) -> None:
        raw = self.path.model.checkpoint()
        self.journal.save(
            {
                "model": VERSION,
                "base_model": MODEL_VERSION,
                "record_seq": self.journal.seq,
                "record_head": self.journal.head,
                "checkpoint_sha256": sha(raw),
                "learning": self.path.learning_state(),
            },
            raw,
        )

    def close(self) -> None:
        self.journal.db.close()
        self.lock.close()

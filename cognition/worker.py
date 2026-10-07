"""Single cognitive writer: perception, learning, memory, actions and evidence."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from cognition.brain import MODEL_VERSION, AdaptiveBrain, sha
from cognition.forecast import ForecastCouncil
from cognition.jobs import Experiments
from cognition.journal import Journal, canonical
from cognition.learning import OnlineReadout
from cognition.memory import EpisodicMemory, Workspace
from cognition.policy import ResourceState
from cognition.treasury import public_treasury
from cognition.world import Habitat
from publish import publish
from store import connect


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class Organism:
    def __init__(self, directory: Path, source_sha256: str):
        self.directory = Path(directory)
        self.source_sha256 = source_sha256
        self.journal = Journal(directory)
        self.brain = AdaptiveBrain(directory)
        self.memory = EpisodicMemory(self.journal.db)
        saved = self.journal.restore()
        metadata = saved[0] if saved else {}
        if saved:
            self.brain.restore(saved[1], metadata["brain"])
        self.tick = int(metadata.get("tick", 0))
        self.cursor = int(metadata.get("cursor", 0))
        self.genesis = metadata.get("genesis_utc", utc())
        self.readouts = {
            source: OnlineReadout(state)
            for source, state in metadata.get("readouts", {}).items()
        }
        self.forecasts = {
            source: ForecastCouncil(state)
            for source, state in metadata.get("forecasts", {}).items()
        }
        self.forecast_genesis = metadata.get("forecast_genesis_utc", utc())
        self.committed_tick = int(metadata.get("tick", 0))
        self.committed_cursor = int(metadata.get("cursor", 0))
        self.workspace = Workspace(metadata.get("workspace"))
        self.resources = ResourceState(metadata.get("resources"))
        self.habitat = Habitat(metadata.get("habitat"))
        self.experiments = Experiments(metadata.get("experiments"))
        self.last_decision = metadata.get("last_decision")
        self.commerce_cursor = int(metadata.get("commerce_cursor", 0))
        self.commerce_outcomes = int(metadata.get("commerce_outcomes", 0))
        self.last_effects = []
        self.features = metadata.get("features", [0.0] * 64)
        self.memory_summary = self.memory.summary()
        self.last_refresh = 0.0
        self.ledger_check = self.journal.verify(full=True)
        if not self.ledger_check["valid"]:
            raise RuntimeError("Cognitive integrity check failed; continuation refused")
        if not saved:
            self.experiments.run("experiment", self.brain, self.memory, 0)
            self.save()

    def perceive(self, order: int, event: dict) -> dict:
        existing = self.journal.db.execute(
            "SELECT payload FROM effects WHERE input_id=?", (event["id"],)
        ).fetchone()
        if existing:
            stored = json.loads(existing[0])
            if (
                stored["source_evidence_sha256"] != sha(canonical(event))
                or stored["event_order"] != order
            ):
                raise ValueError(
                    "Previously recorded input changed; continuation refused"
                )
            self.cursor = max(self.cursor, order)
            return json.loads(existing[0])
        if (
            event["side"] not in ("buy", "sell")
            or type(event["quote_amount"]) not in (int, float)
            or not np.isfinite(event["quote_amount"])
            or not 0 < event["quote_amount"] <= 1e9
        ):
            raise ValueError("Source input is invalid; cursor was not advanced")
        context = self.memory.context(event)
        novelty = self.workspace.novelty(context)
        recalled = self.memory.recall(
            self.features, context, source=event.get("source", "unknown")
        )
        broadcast = recalled[0]["features"] if recalled else [0.0] * 64
        frame = self.brain.advance(
            event,
            duration_ms=20,
            context={
                "memory_features": broadcast,
                "salience": novelty,
                "energy": self.resources.energy,
            },
        )
        self.features = frame["features"]
        readout = self.readouts.setdefault(
            event.get("source", "unknown"), OnlineReadout()
        )
        features = readout.features(
            event, self.features, self.resources.energy, self.workspace.uncertainty
        )
        neural_learning = readout.observe(event, features)
        council = self.forecasts.setdefault(
            event.get("source", "unknown"), ForecastCouncil()
        )
        learning = council.observe(event, neural_learning["prediction_buy"])
        feedback = learning["feedback"]
        improvement = float(feedback["improvement"]) if feedback else 0.0
        plasticity = self.brain.reward(improvement)
        surprise = abs(float(feedback["error"])) if feedback else 0.0
        self.workspace.uncertainty = learning["uncertainty"]
        self.workspace.last_surprise = surprise
        self.memory.add(
            order, event, self.features, min(1, 0.6 * surprise + 0.4 * novelty)
        )
        self.habitat.signal(event)
        effect = {
            "input_id": event["id"],
            "event_order": order,
            "source": event.get("source"),
            "signature": event.get("signature"),
            "side": event["side"],
            "quote_amount": event["quote_amount"],
            "source_evidence_sha256": sha(canonical(event)),
            "source_payload": event,
            "sensory_encoding_sha256": sha(
                AdaptiveBrain.encode(event).astype("<f8").tobytes()
            ),
            "before_state_sha256": frame["before_state_sha256"],
            "after_state_sha256": self.brain.state_hash(),
            "spike_count": frame["spike_count"],
            "spikes_sha256": frame["spikes_sha256"],
            "next_buy_probability": learning["prediction_buy"],
            "forecast_method": "forecast-council-v1",
            "forecast_experts": council.pending["experts"],
            "forecast_attention": learning["attention"],
            "neural_readout_probability": neural_learning["prediction_buy"],
            "prediction_uncertainty": learning["uncertainty"],
            "reward_modulation": plasticity,
            "processing_utc": utc(),
            "scope": "Individually encoded recorded input; causal isolation requires a matched replay probe",
        }
        effect = self.journal.append("input", effect, frame["spikes"])
        self.journal.db.execute(
            "INSERT INTO effects VALUES(?,?,?)",
            (event["id"], order, canonical(effect).decode()),
        )
        self.cursor = order
        return effect

    def commercial_feedback(self, records: list[dict]) -> None:
        """Credit a paid action once, in the same transaction as its checkpoint.

        Delayed payments update goal values rather than unrelated spike eligibility.
        """
        for record in sorted(records, key=lambda row: row.get("seq", 0)):
            seq, reward = record.get("seq"), record.get("reward")
            action = record.get("action")
            if type(seq) is not int or seq <= self.commerce_cursor:
                continue
            if (
                action not in self.resources.action_values
                or type(reward) not in (int, float)
                or not np.isfinite(reward)
                or not -1 <= reward <= 1
                or not isinstance(record.get("job_id"), str)
                or len(record.get("result_sha256", "")) != 64
                or type(record.get("amount_micro_usdc")) is not int
                or record["amount_micro_usdc"] <= 0
            ):
                raise RuntimeError("Paid outcome evidence is malformed")
            self.resources.outcome(action, reward, 0)
            self.journal.append(
                "paid_outcome",
                {
                    **record,
                    "utc": utc(),
                    "tick": self.tick,
                    "scope": "Verified paid forecast outcome credits the purchasing action; no delayed synaptic-credit claim",
                },
            )
            self.commerce_cursor = seq
            self.commerce_outcomes += 1

    def cycle(
        self,
        rows: list[tuple[int, dict]],
        treasury: dict | None = None,
        upstream: dict | None = None,
        durable: bool = True,
    ) -> dict:
        start = time.monotonic()
        treasury = public_treasury(treasury)
        self.last_effects = [self.perceive(order, event) for order, event in rows]
        self.tick += 1
        novelty = max(
            [
                1
                / (1 + self.workspace.visits.get(self.memory.context(event), 1)) ** 0.5
                for _, event in rows
            ],
            default=0.1,
        )
        candidates = [
            {
                "kind": "prediction",
                "salience": self.workspace.uncertainty,
                "detail": "Next-side forecast uncertainty",
            },
            {
                "kind": "memory",
                "salience": self.workspace.last_surprise,
                "detail": "Retrieve and replay surprising episodes",
            },
            {
                "kind": "resources",
                "salience": self.resources.fatigue
                + (0.2 if treasury.get("balance_lamports") is None else 0),
                "detail": "Compute pressure and treasury evidence",
            },
            {
                "kind": "exploration",
                "salience": novelty,
                "detail": "Coverage of unfamiliar contexts",
            },
        ]
        attention = self.workspace.broadcast(candidates)
        utilities = self.resources.candidates(
            self.workspace.uncertainty,
            self.workspace.last_surprise,
            novelty,
            treasury,
            self.memory_summary["episodes"] + len(rows),
        )
        recalled = (
            self.memory.recent[0]["features"] if self.memory.recent else [0.0] * 64
        )
        frame = self.brain.advance(
            utilities=utilities,
            context={
                "memory_features": recalled,
                "salience": attention[0]["salience"] if attention else 0,
                "energy": self.resources.energy,
            },
        )
        if self.tick % 5 == 0:
            decision = self.resources.choose(
                utilities, frame["action_spike_scores"], self.tick
            )
            action = decision["action"]
            habitat = self.habitat.act(action)
            job = self.experiments.run(action, self.brain, self.memory, self.tick)
            reward = float(np.clip(habitat["reward"] + job.get("reward", 0), -1, 1))
            self.brain.reward(reward)
            elapsed = time.monotonic() - start
            self.resources.outcome(action, reward, elapsed)
            payload = {
                "utc": utc(),
                "tick": self.tick,
                "source_sha256": self.source_sha256,
                "upstream_receipt_head": (upstream or {}).get("receipt_head"),
                "workspace": attention,
                "decision": decision,
                "outcome": {"habitat": habitat, "job": job, "reward": round(reward, 6)},
                "model_state_sha256": self.brain.state_hash(),
                "spikes_sha256": sha(canonical(frame["spikes"])),
                "funds_spent_lamports": 0,
                "explanation": f"{action.title()} selected by published goal and neural scores; outcome reward {reward:.4f}. Local execution used included server capacity.",
            }
            self.last_decision = self.journal.append(
                "decision", payload, frame["spikes"]
            )
        if time.monotonic() - self.last_refresh > 30:
            self.memory_summary = self.memory.summary()
            self.ledger_check = self.journal.verify()
            if not self.ledger_check["valid"]:
                raise RuntimeError(
                    "Cognitive journal integrity failed; continuation refused"
                )
            self.last_refresh = time.monotonic()
        if durable:
            self.save()
        return self.snapshot(treasury, upstream, time.monotonic() - start)

    def save(self) -> None:
        raw = self.brain.checkpoint()
        metadata = {
            "model": MODEL_VERSION,
            "tick": self.tick,
            "cursor": self.cursor,
            "genesis_utc": self.genesis,
            "source_sha256": self.source_sha256,
            "record_seq": self.journal.seq,
            "record_head": self.journal.head,
            "brain": {
                "model": MODEL_VERSION,
                "network_sha256": sha(raw),
                "topology_sha256": self.brain.topology["sha256"],
                "offset": self.brain.offset,
                "frames": self.brain.frames,
                "total_spikes": self.brain.total_spikes,
                "rate_history": self.brain.rate_history,
            },
            "readouts": {
                source: readout.state() for source, readout in self.readouts.items()
            },
            "forecasts": {
                source: forecast.state() for source, forecast in self.forecasts.items()
            },
            "forecast_genesis_utc": self.forecast_genesis,
            "workspace": self.workspace.state(),
            "resources": self.resources.state(),
            "habitat": self.habitat.state(),
            "experiments": self.experiments.state(),
            "last_decision": self.last_decision,
            "commerce_cursor": self.commerce_cursor,
            "commerce_outcomes": self.commerce_outcomes,
            "features": self.features,
        }
        self.journal.save(metadata, raw)
        self.committed_tick = self.tick
        self.committed_cursor = self.cursor

    def snapshot(
        self,
        treasury: dict | None = None,
        upstream: dict | None = None,
        cycle_seconds: float = 0,
    ) -> dict:
        frame = {k: v for k, v in self.brain.last.items() if k != "features"}
        return {
            "phase": "running",
            "updated_utc": utc(),
            "genesis_utc": self.genesis,
            "tick": self.tick,
            "source_cursor": self.cursor,
            "committed_tick": self.committed_tick,
            "committed_cursor": self.committed_cursor,
            "pending_ticks": self.tick - self.committed_tick,
            "spikes_total": self.brain.total_spikes,
            "history": self.brain.rate_history,
            "model": MODEL_VERSION,
            "source_sha256": self.source_sha256,
            "neurons": 1024,
            "legacy_neurons": (upstream or {}).get("neurons"),
            "synapses": len(self.brain.exc) + len(self.brain.inh),
            "plastic_synapses": len(self.brain.exc),
            "cycle_wall_seconds": round(cycle_seconds, 6),
            "neural": frame,
            "workspace": self.workspace.state(),
            "learning": {
                "method": "forecast-council-v1",
                "genesis_utc": self.forecast_genesis,
                "scope": "Outcome memory and learned forecast attention alongside a separate neural specialist; forecast gains do not establish a synaptic advantage",
                "sources": {
                    source: {
                        "samples": forecast.stats["n"],
                        "prediction": {
                            k: v
                            for k, v in (forecast.pending or {}).items()
                            if k != "context"
                        },
                        "metrics": {source: forecast.metrics()},
                        "history": list(forecast.history)[-60:],
                    }
                    for source, forecast in self.forecasts.items()
                },
                "neural_readouts": {
                    source: readout.metrics()
                    for source, readout in self.readouts.items()
                },
            },
            "memory": self.memory_summary,
            "resources": self.resources.public(),
            "habitat": self.habitat.state(),
            "experiments": self.experiments.state(),
            "last_decision": self.last_decision,
            "recent_effects": self.last_effects[-12:],
            "record_head": self.journal.head,
            "record_seq": self.journal.seq,
            "integrity": self.ledger_check,
            "treasury": treasury or public_treasury(),
            "paid_outcomes": self.commerce_outcomes,
            "upstream": {
                "tick": (upstream or {}).get("tick"),
                "genesis_utc": (upstream or {}).get("genesis_utc"),
                "receipt_head": (upstream or {}).get("receipt_head"),
                "feed": (upstream or {}).get("feed"),
            },
            "scope": "Persistent hybrid spiking and adaptive learning experiment; metrics establish implemented behavior, not subjective experience",
        }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    data = Path(os.environ["NURIA_COGNITION_DATA"])
    public = Path(os.environ["NURIA_COGNITION_PUBLIC"])
    public.mkdir(parents=True, exist_ok=True)
    source = hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted((root / "cognition").glob("*.py")))
    ).hexdigest()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    data.mkdir(parents=True, exist_ok=True)
    writer_lock = (data / "writer.lock").open("a")
    try:
        fcntl.flock(writer_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Cognitive writer already active")
    organism = None
    try:
        organism = Organism(data, source)
        publish(public, "topology.json", organism.brain.topology)
        while not stop.is_set():
            begin = time.monotonic()
            if shutil.disk_usage(data).free < 2 * 1024**3:
                raise RuntimeError(
                    "Cognitive capacity threshold reached; evidence retained"
                )
            with connect(None) as db:
                rows = db.execute(
                    "SELECT event_order,payload FROM inputs WHERE event_order>? ORDER BY event_order LIMIT 4",
                    (organism.cursor,),
                ).fetchall()
                remaining = db.execute(
                    "SELECT count(*) FROM inputs WHERE event_order>?",
                    (organism.cursor,),
                ).fetchone()[0]
            try:
                upstream = json.loads(
                    Path(os.environ["NURIA_UPSTREAM_STATUS"]).read_text()
                )
            except (OSError, ValueError):
                upstream = {}
            try:
                treasury = json.loads(
                    Path(
                        os.environ.get("NURIA_TREASURY_STATUS", "/nonexistent")
                    ).read_text()
                )
            except (OSError, ValueError):
                treasury = public_treasury()
            commerce_path = Path(
                os.environ.get("NURIA_COMMERCE_STATUS", "/nonexistent")
            )
            if (
                commerce_path.exists()
                and 0 <= time.time() - commerce_path.stat().st_mtime < 30
            ):
                commerce = json.loads(commerce_path.read_text())
                organism.commercial_feedback(commerce.get("feedback", []))
            result = organism.cycle(
                [(order, json.loads(raw)) for order, raw in rows],
                treasury,
                upstream,
                durable=(organism.tick + 1) % 5 == 0,
            )
            result["queued_source_inputs"] = max(0, remaining - len(rows))
            result["processing_capacity_per_cycle"] = 4
            publish(public, "status.json", result)
            publish(public, "decisions.json", organism.journal.recent("decision"))
            publish(
                public,
                "effects.json",
                [
                    json.loads(r[0])
                    for r in organism.journal.db.execute(
                        "SELECT payload FROM effects ORDER BY event_order DESC LIMIT 40"
                    )
                ],
            )
            stop.wait(max(0.1, 1 - (time.monotonic() - begin)))
        organism.save()
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, RuntimeError)
            else "Cognitive worker failed; diagnostics retained privately"
        )
        publish(
            public,
            "status.json",
            {"phase": "error", "updated_utc": utc(), "error": message},
        )
        raise


if __name__ == "__main__":
    main()

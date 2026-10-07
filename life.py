"""Continuous Brian2 dynamics, durable input queue and local integrity receipts.

No wallets are signed and no funds are spent. Test inputs are explicitly tagged.
"""

import fcntl
import hashlib
import json
import math
import os
import shutil
import threading
import time
import uuid
import zipfile
import zlib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import brian2 as b
import numpy as np

from store import connect

REGIONS = [
    ("buy", "Buy sensory", 0, 32, "#b9f5cf"),
    ("sell", "Sell sensory", 32, 64, "#f19cba"),
    ("association", "Association", 64, 128, "#99bdf8"),
    ("memory", "Recurrent memory", 128, 192, "#b5a5f8"),
    ("policy", "Resource policy", 192, 208, "#f1dba2"),
    ("inhibition", "Inhibitory control", 208, 256, "#64ceca"),
]
POLICY = ["compute", "experiments", "reserve", "observe"]
MODEL_VERSION = "nuria-recurrent-v1"


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


class Life:
    def __init__(self, directory, source_hash):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "checkpoints").mkdir(exist_ok=True)
        self.source_hash = source_hash
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.state = {
            "phase": "starting",
            "source": "brian2",
            "mode": "test",
            "error": None,
            "updated_utc": None,
        }
        self.topology = None
        self.history = deque(maxlen=120)
        self.feed_state = {
            "phase": "not_launched",
            "mint": None,
            "coverage": "No token connected. Inputs are simulated.",
        }
        self.dbpath = self.directory / "life.sqlite3"
        if not os.environ.get("NURIA_DATABASE"):
            with self.db() as db:
                db.executescript("""
              CREATE TABLE IF NOT EXISTS inputs(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                created_utc TEXT NOT NULL, receipt INTEGER);
              CREATE TABLE IF NOT EXISTS receipts(
                seq INTEGER PRIMARY KEY, previous_hash TEXT NOT NULL,
                hash TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS spike_windows(
                seq INTEGER PRIMARY KEY, data BLOB NOT NULL);
              CREATE TABLE IF NOT EXISTS source_cursors(
                address TEXT PRIMARY KEY, signature TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS scan_progress(
                address TEXT PRIMARY KEY, newest TEXT NOT NULL, before_signature TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS transactions(
                signature TEXT PRIMARY KEY, slot INTEGER, status TEXT NOT NULL,
                detail TEXT, attempts INTEGER DEFAULT 0);
            """)
        self.thread = threading.Thread(target=self.run, daemon=True, name="nuria-life")

    def db(self):
        return connect(self.dbpath)

    def start(self):
        self.thread.start()

    def enqueue(self, event):
        if event["side"] not in ("buy", "sell"):
            raise ValueError("Invalid side")
        amount = event["quote_amount"]
        if (
            type(amount) not in (int, float)
            or not math.isfinite(amount)
            or not 0 < amount <= 1e9
        ):
            raise ValueError("Invalid amount")
        with self.db() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO inputs VALUES(?,?,?,?,NULL)",
                (event["id"], event["source"], canonical(event).decode(), utc()),
            ).rowcount
        return bool(inserted)

    def demo(self, side, amount):
        with self.lock:
            if self.feed_state.get("mint"):
                raise ValueError("Test inputs disabled when a real mint is configured")
        event = {
            "id": "test-" + uuid.uuid4().hex,
            "source": "test",
            "side": side,
            "quote_amount": amount,
            "quote_unit": "SOL",
            "creator_fee": round(amount * 0.003, 9),
            "fee_basis": "Illustrative 0.3%; not a Pump.fun fee quote",
            "signature": None,
            "slot": None,
            "created_utc": utc(),
        }
        self.enqueue(event)
        return event

    def snapshot(self):
        with self.lock:
            out = dict(self.state)
            out["history"] = list(self.history)
            out["feed"] = dict(self.feed_state)
        with self.db() as db:
            out["queued_inputs"] = db.execute(
                "SELECT count(*) FROM inputs WHERE receipt IS NULL"
            ).fetchone()[0]
            out["durable_receipts"] = db.execute(
                "SELECT coalesce(max(seq),0) FROM receipts"
            ).fetchone()[0]
            out["unresolved_transactions"] = db.execute(
                "SELECT count(*) FROM transactions WHERE status='pending'"
            ).fetchone()[0]
        return out

    def events(self, limit=30):
        with self.db() as db:
            rows = db.execute(
                "SELECT payload,receipt FROM inputs ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(json.loads(row[0]), receipt=row[1]) for row in rows]

    def receipts(self, limit=30):
        with self.db() as db:
            rows = db.execute(
                "SELECT seq,previous_hash,hash,payload FROM receipts ORDER BY seq DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            dict(json.loads(row[3]), seq=row[0], previous_hash=row[1], hash=row[2])
            for row in rows
        ]

    def verify(self):
        previous = "0" * 64
        checked = 0
        with self.db() as db:
            for seq, parent, current, payload in db.execute(
                "SELECT seq,previous_hash,hash,payload FROM receipts ORDER BY seq"
            ):
                checked += 1
                if (
                    seq != checked
                    or parent != previous
                    or digest(parent.encode() + payload.encode()) != current
                ):
                    return {"valid": False, "checked": checked, "first_failure": seq}
                row = json.loads(payload)
                raw = db.execute(
                    "SELECT data FROM spike_windows WHERE seq=?", (seq,)
                ).fetchone()
                if not raw or digest(zlib.decompress(raw[0])) != row["spikes_sha256"]:
                    return {
                        "valid": False,
                        "checked": checked,
                        "first_failure": seq,
                        "reason": "Spike evidence differs",
                    }
                if len(row["input_ids"]) != len(row["input_evidence_sha256"]):
                    return {
                        "valid": False,
                        "checked": checked,
                        "first_failure": seq,
                        "reason": "Input evidence length differs",
                    }
                for ident, expected in zip(
                    row["input_ids"], row["input_evidence_sha256"]
                ):
                    event = db.execute(
                        "SELECT payload,receipt FROM inputs WHERE id=?", (ident,)
                    ).fetchone()
                    if (
                        not event
                        or event[1] != seq
                        or digest(canonical(json.loads(event[0]))) != expected
                    ):
                        return {
                            "valid": False,
                            "checked": checked,
                            "first_failure": seq,
                            "reason": "Input evidence differs",
                        }
                previous = current
        return {
            "valid": True,
            "checked": checked,
            "head": previous,
            "scope": "Local hash chain integrity only. No independent witness or Solana anchoring.",
        }

    def build(self):
        b.start_scope()
        b.seed(71402)
        b.prefs.codegen.target = "numpy"
        b.prefs.logging.delete_log_on_exit = False
        b.defaultclock.dt = 1 * b.ms
        self.neurons = b.NeuronGroup(
            256,
            """
            dv/dt = (bias + drive + ge - gi - adaptation - v)/(20*ms) : 1 (unless refractory)
            dge/dt = -ge/(8*ms) : 1
            dgi/dt = -gi/(12*ms) : 1
            dadaptation/dt = -adaptation/(300*ms) : 1
            bias : 1
            drive : 1
            """,
            threshold="v > 1",
            reset="v=0; adaptation += 0.04",
            refractory=4 * b.ms,
            method="euler",
            name="neurons",
        )
        rng = np.random.default_rng(71402)
        self.neurons.v = rng.uniform(0, 0.8, 256)
        self.neurons.bias = rng.uniform(0.84, 1.02, 256)
        self.base_bias = np.array(self.neurons.bias[:])
        self.exc = b.Synapses(
            self.neurons,
            self.neurons,
            """
            w : 1
            dpre/dt = -pre/(20*ms) : 1 (event-driven)
            dpost/dt = -post/(20*ms) : 1 (event-driven)
            """,
            on_pre="ge_post += w; pre += 0.001; w=clip(w+post,0.005,0.25)",
            on_post="post -= 0.00105; w=clip(w+pre,0.005,0.25)",
            name="excitatory",
        )
        self.exc.connect(condition="i < 208 and i != j", p=0.075)
        self.exc.w = "0.025+rand()*0.065"
        self.initial_weights = np.array(self.exc.w[:])
        self.exc.delay = "1*ms+rand()*7*ms"
        self.inh = b.Synapses(
            self.neurons, self.neurons, on_pre="gi_post += 0.24", name="inhibitory"
        )
        self.inh.connect(condition="i >= 208 and i != j", p=0.12)
        self.inh.delay = 2 * b.ms
        self.noise = b.PoissonGroup(256, rates=18 * b.Hz, name="background")
        self.noise_syn = b.Synapses(
            self.noise,
            self.neurons,
            on_pre="ge_post += 0.32",
            name="background_connections",
        )
        self.noise_syn.connect(j="i")
        self.net = b.Network(
            self.neurons, self.exc, self.inh, self.noise, self.noise_syn
        )
        nodes = []
        for key, title, start, end, color in REGIONS:
            for n in range(start, end):
                nodes.append({"id": n, "region": key, "color": color})
        edges = [
            [int(i), int(j), "exc", round(float(d / b.ms), 2)]
            for i, j, d in zip(self.exc.i[:], self.exc.j[:], self.exc.delay[:])
        ]
        edges += [
            [int(i), int(j), "inh", 2] for i, j in zip(self.inh.i[:], self.inh.j[:])
        ]
        self.topology = {
            "nodes": nodes,
            "edges": edges,
            "regions": [
                {"id": r[0], "name": r[1], "start": r[2], "end": r[3], "color": r[4]}
                for r in REGIONS
            ],
            "plastic_synapses": len(self.exc),
            "inhibitory_synapses": len(self.inh),
            "model": MODEL_VERSION,
            "seed": 71402,
            "dt_ms": 1,
        }
        self.topology["sha256"] = digest(canonical(self.topology))
        self.tick = 0
        self.offset = 0
        self.fee_demo = 0
        self.accrued_live = {}
        self.pending = []
        self.inflight = set()
        self.spike_total = 0
        self.policy = "observe"
        self.genesis_utc = utc()
        self.restore()
        if self.tick == 0:
            self.checkpoint()

    def restore(self):
        path = self.directory / "resume.zip"
        if not path.exists():
            with self.db() as db:
                if db.execute("SELECT count(*) FROM receipts").fetchone()[0]:
                    raise RuntimeError(
                        "Receipts exist but checkpoint is missing; automatic reset refused"
                    )
            return
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if (
                manifest["model"] != MODEL_VERSION
                or manifest["topology_hash"] != self.topology["sha256"]
            ):
                raise RuntimeError(
                    "Saved network differs from source; automatic reset refused"
                )
            raw = archive.read("network.pkl")
            if digest(raw) != manifest["network_hash"]:
                raise RuntimeError("Checkpoint integrity failed")
            restore_path = self.directory / "restore.pkl"
            restore_path.write_bytes(raw)
        self.net.restore("life", filename=str(restore_path), restore_random_state=True)
        self.tick = manifest["tick"]
        self.offset = manifest["offset"]
        self.fee_demo = manifest["fee_demo"]
        self.accrued_live = manifest["accrued_live"]
        self.spike_total = manifest["spike_total"]
        self.genesis_utc = manifest["genesis_utc"]
        self.policy = manifest["policy"]
        self.commit(manifest["receipts"])
        with self.lock:
            self.state["restored_tick"] = self.tick

    def commit(self, receipts):
        with self.db() as db:
            for row in receipts:
                exists = db.execute(
                    "SELECT hash FROM receipts WHERE seq=?", (row["seq"],)
                ).fetchone()
                if exists:
                    if exists[0] != row["hash"]:
                        raise RuntimeError("Checkpoint conflicts with durable receipt")
                    continue
                head = db.execute(
                    "SELECT seq,hash FROM receipts ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                if row["previous_hash"] != (head[1] if head else "0" * 64) or row[
                    "seq"
                ] != (head[0] + 1 if head else 1):
                    raise RuntimeError("Receipt gap; automatic continuation refused")
                db.execute(
                    "INSERT INTO receipts VALUES(?,?,?,?)",
                    (row["seq"], row["previous_hash"], row["hash"], row["payload"]),
                )
                db.execute(
                    "INSERT INTO spike_windows VALUES(?,?)",
                    (row["seq"], zlib.compress(canonical(row["spikes"]), 6)),
                )
                payload = json.loads(row["payload"])
                for ident in payload["input_ids"]:
                    db.execute(
                        "UPDATE inputs SET receipt=? WHERE id=?", (row["seq"], ident)
                    )

    def checkpoint(self):
        with (self.directory / "checkpoint.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._checkpoint()

    def _checkpoint(self):
        pkl = self.directory / "engine-state.next.pkl"
        self.net.store("life", filename=str(pkl))
        raw = pkl.read_bytes()
        manifest = {
            "model": MODEL_VERSION,
            "topology_hash": self.topology["sha256"],
            "network_hash": digest(raw),
            "tick": self.tick,
            "offset": self.offset,
            "fee_demo": self.fee_demo,
            "accrued_live": self.accrued_live,
            "spike_total": self.spike_total,
            "policy": self.policy,
            "genesis_utc": self.genesis_utc,
            "saved_utc": utc(),
            "receipts": self.pending,
        }
        nextfile = self.directory / "resume.next.zip"
        with zipfile.ZipFile(
            nextfile, "w", zipfile.ZIP_DEFLATED, compresslevel=1
        ) as archive:
            archive.writestr("network.pkl", raw)
            archive.writestr("manifest.json", canonical(manifest))
        with nextfile.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(nextfile, self.directory / "resume.zip")
        self.commit(self.pending)
        self.pending = []
        self.inflight.clear()
        if self.tick % 600 == 0:
            shutil.copyfile(
                self.directory / "resume.zip",
                self.directory / "checkpoints" / f"{self.tick:012d}.zip",
            )

    def run(self):
        try:
            self.build()
            with self.db() as db:
                head = db.execute(
                    "SELECT seq,hash FROM receipts ORDER BY seq DESC LIMIT 1"
                ).fetchone()
            seq, previous = head if head else (0, "0" * 64)
            last_demo = time.monotonic()
            while not self.stop.is_set():
                start = time.monotonic()
                if shutil.disk_usage(self.directory).free < 2 * 1024**3:
                    raise RuntimeError(
                        "Less than 2 GiB free; engine halted without deleting evidence"
                    )
                feed_file = os.environ.get("NURIA_FEED_STATUS")
                if feed_file:
                    try:
                        with self.lock:
                            self.feed_state = json.loads(Path(feed_file).read_text())
                    except (OSError, ValueError):
                        pass
                if not self.feed_state.get("mint") and start - last_demo >= 20:
                    side = "buy" if (self.tick // 20) % 2 == 0 else "sell"
                    self.demo(side, [0.2, 0.8, 1.5][(self.tick // 20) % 3])
                    last_demo = start
                with self.db() as db:
                    rows = db.execute(
                        "SELECT payload FROM inputs WHERE receipt IS NULL ORDER BY rowid LIMIT 1000"
                    ).fetchall()
                inputs = [
                    json.loads(r[0])
                    for r in rows
                    if json.loads(r[0])["id"] not in self.inflight
                ][:100]
                before_weights = digest(
                    np.asarray(self.exc.w[:], dtype="<f8").tobytes()
                )
                before_state = digest(
                    np.asarray(self.neurons.v[:], dtype="<f8").tobytes()
                )
                self.neurons.drive = 0
                sensory = [0.0, 0.0]
                for event in inputs:
                    gain = min(1.5, 0.25 + 0.45 * math.log1p(event["quote_amount"]))
                    sensory[0 if event["side"] == "buy" else 1] += gain
                    if event["source"] == "test":
                        self.fee_demo += event.get("creator_fee", 0)
                    else:
                        unit = event["quote_unit"]
                        self.accrued_live[unit] = self.accrued_live.get(
                            unit, 0
                        ) + event.get("creator_fee", 0)
                    self.inflight.add(event["id"])
                sensory = [min(3, x) for x in sensory]
                self.neurons.drive[:32] = sensory[0]
                self.neurons.drive[32:64] = sensory[1]
                self.neurons.bias = self.base_bias + self.offset
                monitor = b.SpikeMonitor(self.neurons, name="frame_spikes")
                self.net.add(monitor)
                sim_start = float(self.net.t / b.second)
                self.net.run(200 * b.ms, namespace={})
                counts = np.asarray(monitor.count[:], dtype=int)
                spikes = [
                    [round(float(t / b.second) - sim_start, 4), int(i)]
                    for t, i in zip(monitor.t[:], monitor.i[:])
                ]
                self.net.remove(monitor)
                self.tick += 1
                self.spike_total += len(spikes)
                rate = len(spikes) / (256 * 0.2)
                self.offset = float(
                    np.clip(self.offset + 0.002 * (8 - rate), -0.2, 0.25)
                )
                region_rates = [
                    float(counts[a:z].sum() / ((z - a) * 0.2))
                    for _, _, a, z, _ in REGIONS
                ]
                probability = np.array(region_rates) + 1e-9
                probability /= probability.sum()
                entropy = float(
                    -np.sum(probability * np.log2(probability)) / np.log2(6)
                )
                output_scores = [
                    int(counts[192 + 4 * k : 196 + 4 * k].sum()) for k in range(4)
                ]
                choice = int(np.argmax(output_scores)) if max(output_scores) > 0 else 3
                self.policy = POLICY[choice]
                fee_available = self.fee_demo
                allocations = {
                    "compute": 0.0,
                    "experiments": 0.0,
                    "reserve": fee_available,
                }
                if self.policy in ("compute", "experiments"):
                    allocations[self.policy] = round(fee_available * 0.25, 9)
                    allocations["reserve"] = fee_available - allocations[self.policy]
                weights = np.asarray(self.exc.w[:], dtype="<f8")
                weight_hash = digest(weights.tobytes())
                metrics = {
                    "rate_hz": round(rate, 3),
                    "regional_rates": [round(r, 3) for r in region_rates],
                    "activity_entropy": round(entropy, 4),
                    "mean_weight": round(float(weights.mean()), 6),
                    "changed_synapses": int(
                        np.sum(np.abs(weights - self.initial_weights) > 1e-6)
                    ),
                    "spikes": len(spikes),
                }
                payload = {
                    "schema": 1,
                    "tick": self.tick,
                    "utc": utc(),
                    "sim_seconds": round(float(self.net.t / b.second), 3),
                    "model": MODEL_VERSION,
                    "source_sha256": self.source_hash,
                    "input_ids": [e["id"] for e in inputs],
                    "input_sources": [e["source"] for e in inputs],
                    "input_evidence_sha256": [digest(canonical(e)) for e in inputs],
                    "sensory_drive": sensory,
                    "before_membrane_sha256": before_state,
                    "after_membrane_sha256": digest(
                        np.asarray(self.neurons.v[:], dtype="<f8").tobytes()
                    ),
                    "before_weights_sha256": before_weights,
                    "after_weights_sha256": weight_hash,
                    "spikes_sha256": digest(canonical(spikes)),
                    "metrics": metrics,
                    "allocation_proposal": self.policy,
                    "policy_spike_scores": output_scores,
                    "funds_spent": 0,
                    "signature": None,
                }
                seq += 1
                encoded = canonical(payload).decode()
                current = digest(previous.encode() + encoded.encode())
                self.pending.append(
                    {
                        "seq": seq,
                        "previous_hash": previous,
                        "hash": current,
                        "payload": encoded,
                        "spikes": spikes,
                    }
                )
                previous = current
                if self.tick % 5 == 0 or self.stop.is_set():
                    self.checkpoint()
                frame = {
                    "tick": self.tick,
                    "rate": round(rate, 3),
                    "entropy": round(entropy, 3),
                    "mean_weight": metrics["mean_weight"],
                    "regions": metrics["regional_rates"],
                }
                with self.lock:
                    self.history.append(frame)
                    self.state.update(
                        phase="running",
                        updated_utc=payload["utc"],
                        genesis_utc=self.genesis_utc,
                        tick=self.tick,
                        sim_seconds=payload["sim_seconds"],
                        frame_sim_ms=200,
                        frame_wall_seconds=round(time.monotonic() - start, 3),
                        neurons=256,
                        synapses=len(self.exc) + len(self.inh),
                        plastic_synapses=len(self.exc),
                        brian2_version=b.__version__,
                        topology_sha256=self.topology["sha256"],
                        source_sha256=self.source_hash,
                        metrics=metrics,
                        spikes=spikes,
                        voltages=np.asarray(self.neurons.v[:]).round(4).tolist(),
                        counts=counts.tolist(),
                        weights=weights.round(5).tolist(),
                        receipt_head=current,
                        receipt_pending=len(self.pending),
                        spikes_total=self.spike_total,
                        policy={
                            "choice": self.policy,
                            "scores": output_scores,
                            "mode": "proposal_only",
                            "allocations_demo_sol": allocations,
                            "spent_sol": 0,
                            "explanation": "Highest-spiking output population chooses a bounded proposal; max 25% earmarked, remainder reserved.",
                        },
                        treasury={
                            "demo_accrued_sol": round(self.fee_demo, 9),
                            "live_observed_accrual": dict(self.accrued_live),
                            "actual_received_sol": None,
                            "creator_wallet": None,
                            "spend_enabled": False,
                        },
                    )
                self.stop.wait(max(0.1, 1 - (time.monotonic() - start)))
            if self.pending:
                self.checkpoint()
        except Exception as exc:
            # These messages contain no RPC URL or provider exception text.
            message = (
                str(exc)
                if isinstance(exc, RuntimeError)
                else "Neural engine failed; server diagnostics retained"
            )
            with self.lock:
                self.state.update(phase="error", error=message, updated_utc=utc())
            import traceback

            traceback.print_exc()

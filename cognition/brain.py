"""Reward-modulated recurrent spiking circuit with persistent Brian2 state."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import brian2 as b
import numpy as np

MODEL_VERSION = "nuria-cognition-v1"
SEED = 5107
POPULATIONS = (
    ("sensory", "Sensory encoding", 0, 128, "#c5fba4"),
    ("association", "Association", 128, 512, "#a6c5ec"),
    ("memory", "Recurrent memory", 512, 640, "#beaacf"),
    ("workspace", "Global workspace", 640, 768, "#e9cf99"),
    ("policy", "Action readout", 768, 896, "#dba7b4"),
    ("inhibition", "Inhibitory control", 896, 1024, "#89c8ba"),
)
ACTIONS = (
    "explore",
    "forage",
    "predict",
    "replay",
    "experiment",
    "rest",
    "compare",
    "reserve",
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class AdaptiveBrain:
    """A software neural model; plasticity and readouts are measured explicitly."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        b.start_scope()
        b.seed(SEED)
        b.prefs.codegen.target = "numpy"
        b.prefs.logging.delete_log_on_exit = False
        b.defaultclock.dt = b.ms
        self.neurons = b.NeuronGroup(
            1024,
            """
            dv/dt = (bias + drive + ge - gi - adaptation - v)/(tau*ms) : 1 (unless refractory)
            dge/dt = -ge/(8*ms) : 1
            dgi/dt = -gi/(12*ms) : 1
            dadaptation/dt = -adaptation/(350*ms) : 1
            bias : 1
            drive : 1
            tau : 1
            """,
            threshold="v > 1",
            reset="v=0; adaptation += 0.035",
            refractory=4 * b.ms,
            method="euler",
            name="cognitive_neurons",
        )
        rng = np.random.default_rng(SEED)
        self.neurons.v = rng.uniform(0, 0.8, 1024)
        self.base_bias = rng.uniform(0.87, 1.0, 1024)
        self.neurons.bias = self.base_bias
        self.neurons.tau = 20
        self.neurons.tau[:128] = 12
        self.neurons.tau[512:640] = 45
        self.exc = b.Synapses(
            self.neurons,
            self.neurons,
            """
            w : 1
            plastic_gain : 1 (shared)
            dpre/dt = -pre/(20*ms) : 1 (event-driven)
            dpost/dt = -post/(20*ms) : 1 (event-driven)
            eligibility : 1
            """,
            on_pre="ge_post += w; pre += 0.003; eligibility=clip(eligibility+post,-1,1); w=clip(w+plastic_gain*0.015*post,0.002,0.18)",
            on_post="post -= 0.00315; eligibility=clip(eligibility+pre,-1,1); w=clip(w+plastic_gain*0.015*pre,0.002,0.18)",
            name="cognitive_plastic",
        )
        self.exc.connect(condition="i < 896 and i != j", p=0.025)
        self.exc.w = "0.025 + rand()*0.025"
        self.exc.plastic_gain = 1
        self.exc.delay = "1*ms + rand()*11*ms"
        self.inh = b.Synapses(
            self.neurons,
            self.neurons,
            on_pre="gi_post += 0.18",
            name="cognitive_inhibition",
        )
        self.inh.connect(condition="i >= 896 and i != j", p=0.05)
        self.inh.delay = 2 * b.ms
        self.noise = b.PoissonGroup(1024, rates=18 * b.Hz, name="cognitive_background")
        self.noise_syn = b.Synapses(
            self.noise,
            self.neurons,
            on_pre="ge_post += 0.28",
            name="cognitive_noise_connections",
        )
        self.noise_syn.connect(j="i")
        self.net = b.Network(
            self.neurons, self.exc, self.inh, self.noise, self.noise_syn
        )
        self.offset = 0.0
        self.frames = 0
        self.last = {}
        self.total_spikes = 0
        self.rate_history = []
        self.sample_indices = np.linspace(
            0, len(self.exc) - 1, min(1600, len(self.exc)), dtype=int
        )
        self.topology = self._topology()

    def _topology(self) -> dict:
        nodes = [
            {"id": i, "region": key, "color": color}
            for key, _, start, end, color in POPULATIONS
            for i in range(start, end)
        ]
        edges = [
            [
                int(self.exc.i[k]),
                int(self.exc.j[k]),
                "exc",
                round(float(self.exc.delay[k] / b.ms), 2),
            ]
            for k in self.sample_indices
        ]
        count = min(256, len(self.inh))
        for k in np.linspace(0, len(self.inh) - 1, count, dtype=int):
            edges.append([int(self.inh.i[k]), int(self.inh.j[k]), "inh", 2])
        value = {
            "model": MODEL_VERSION,
            "seed": SEED,
            "nodes": nodes,
            "edges": edges,
            "regions": [
                {"id": key, "name": name, "start": start, "end": end, "color": color}
                for key, name, start, end, color in POPULATIONS
            ],
            "plastic_synapses": len(self.exc),
            "inhibitory_synapses": len(self.inh),
            "rendered_edges": len(edges),
            "edge_sampling": "deterministic evenly spaced subset; complete connectivity retained by the worker",
        }
        value["sha256"] = sha(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        )
        return value

    def state_hash(self) -> str:
        """Projection of neural variables; delayed queues and RNG live in checkpoints."""
        arrays = [
            np.asarray(getattr(self.neurons, key)[:], dtype="<f8").tobytes()
            for key in ("v", "ge", "gi", "adaptation", "bias", "drive", "tau")
        ]
        arrays.extend(
            np.asarray(self.exc.variables[key].get_value(), dtype="<f8").tobytes()
            for key in ("w", "pre", "post", "eligibility", "plastic_gain")
        )
        arrays.append(
            np.asarray(
                [float(self.net.t / b.second), self.offset], dtype="<f8"
            ).tobytes()
        )
        return sha(b"".join(arrays))

    @staticmethod
    def encode(event: dict) -> np.ndarray:
        """Encode one event independently; event IDs also select a sensory texture."""
        values = np.zeros(128)
        gain = min(1.5, 0.25 + 0.45 * math.log1p(float(event["quote_amount"])))
        start = 0 if event["side"] == "buy" else 32
        values[start : start + 32] = gain
        texture = np.frombuffer(
            hashlib.sha256(event["id"].encode()).digest(), dtype=np.uint8
        )
        values[64:96] = texture.astype(float) / 255 * 0.24
        values[96:112] = min(1, float(event["quote_amount"]) / 10) * 0.35
        values[112:128] = min(1, float(event.get("creator_fee", 0)) * 100) * 0.2
        return values

    def advance(
        self,
        event: dict | None = None,
        utilities: dict | None = None,
        duration_ms: int = 100,
        context: dict | None = None,
    ) -> dict:
        before = self.state_hash()
        before_weights = np.asarray(self.exc.w[:]).copy()
        self.neurons.drive = 0
        if event is not None:
            self.neurons.drive[:128] = self.encode(event)
        context = context or {}
        recalled = np.asarray(context.get("memory_features", [0.0] * 64), dtype=float)
        if recalled.shape != (64,):
            raise ValueError("Memory broadcast shape is invalid")
        self.neurons.drive[512:640] = np.tile(np.clip(recalled, 0, 1), 2) * 0.2
        focus = float(np.clip(context.get("salience", 0), 0, 1))
        self.neurons.drive[640:768] = focus * 0.18
        self.noise.rates = (
            12 + 6 * float(np.clip(context.get("energy", 1), 0, 1))
        ) * b.Hz
        for index, action in enumerate(ACTIONS):
            self.neurons.drive[768 + index * 16 : 784 + index * 16] = (
                max(0, min(1, (utilities or {}).get(action, 0))) * 0.35
            )
        self.neurons.bias = self.base_bias + self.offset
        monitor = b.SpikeMonitor(self.neurons, name="cognitive_window")
        self.net.add(monitor)
        sim_start = float(self.net.t / b.second)
        try:
            self.net.run(duration_ms * b.ms, namespace={})
            counts = np.asarray(monitor.count[:], dtype=int)
            spikes = [
                [round(float(t / b.second) - sim_start, 4), int(i)]
                for t, i in zip(monitor.t[:], monitor.i[:])
            ]
        finally:
            self.net.remove(monitor)
        rate = len(spikes) / (1024 * duration_ms / 1000)
        self.offset = float(np.clip(self.offset + 0.0004 * (8 - rate), -0.15, 0.2))
        self.exc.eligibility = np.asarray(self.exc.eligibility[:]) * math.exp(
            -duration_ms / 500
        )
        self.frames += 1
        self.total_spikes += len(spikes)
        self.rate_history = (
            self.rate_history + [{"frame": self.frames, "rate": round(rate, 3)}]
        )[-120:]
        regional = [
            round(float(counts[a:z].sum() / ((z - a) * duration_ms / 1000)), 3)
            for _, _, a, z, _ in POPULATIONS
        ]
        features = (
            np.clip(
                counts.reshape(64, 16).mean(axis=1) / max(1, duration_ms / 50), 0, 4
            )
            / 4
        )
        self.last = {
            "frame": self.frames,
            "sim_seconds": round(float(self.net.t / b.second), 4),
            "window_ms": duration_ms,
            "spikes": spikes,
            "spike_count": len(spikes),
            "counts": counts.tolist(),
            "voltages": np.asarray(self.neurons.v[:]).round(4).tolist(),
            "sampled_weights": np.asarray(self.exc.w[:])[self.sample_indices]
            .round(6)
            .tolist(),
            "regional_rates": regional,
            "rate_hz": round(rate, 3),
            "changed_synapses": int(
                np.count_nonzero(
                    np.abs(np.asarray(self.exc.w[:]) - before_weights) > 1e-12
                )
            ),
            "mean_weight": round(float(np.asarray(self.exc.w[:]).mean()), 6),
            "before_state_sha256": before,
            "after_state_sha256": self.state_hash(),
            "action_spike_scores": {
                name: int(counts[768 + k * 16 : 784 + k * 16].sum())
                for k, name in enumerate(ACTIONS)
            },
            "spikes_sha256": sha(
                json.dumps(spikes, sort_keys=True, separators=(",", ":")).encode()
            ),
            "features": features.tolist(),
        }
        return self.last

    def reward(self, prediction_error: float) -> dict:
        """Apply bounded outcome feedback to synaptic eligibility traces."""
        signal = float(np.clip(prediction_error, -1, 1))
        before = np.asarray(self.exc.w[:]).copy()
        after = np.clip(
            before + 0.01 * signal * np.asarray(self.exc.eligibility[:]), 0.002, 0.18
        )
        self.exc.w = after
        return {
            "signal": round(signal, 6),
            "changed_synapses": int(np.count_nonzero(abs(after - before) > 1e-12)),
            "before_weights_sha256": sha(before.astype("<f8").tobytes()),
            "after_weights_sha256": sha(after.astype("<f8").tobytes()),
        }

    def checkpoint(self) -> bytes:
        path = self.directory / "network.current.pkl"
        self.net.store("cognition", filename=str(path))
        return path.read_bytes()

    def restore(self, raw: bytes, metadata: dict) -> None:
        if (
            metadata["model"] != MODEL_VERSION
            or metadata["topology_sha256"] != self.topology["sha256"]
        ):
            raise RuntimeError("Cognitive checkpoint model differs; reset refused")
        if sha(raw) != metadata["network_sha256"]:
            raise RuntimeError("Cognitive checkpoint integrity failed")
        path = self.directory / "network.restore.pkl"
        path.write_bytes(raw)
        self.net.restore("cognition", filename=str(path), restore_random_state=True)
        self.offset = metadata["offset"]
        self.frames = metadata["frames"]
        self.total_spikes = metadata.get("total_spikes", 0)
        self.rate_history = metadata.get("rate_history", [])

    def counterfactual(self, event: dict) -> dict:
        """Matched replay with and without an input from the same state and RNG."""
        before_meta = (
            self.offset,
            self.frames,
            self.last,
            self.total_spikes,
            list(self.rate_history),
        )
        self.net.store("probe_before")
        actual = self.advance(event, duration_ms=20)
        actual_meta = (
            self.offset,
            self.frames,
            self.last,
            self.total_spikes,
            list(self.rate_history),
        )
        self.net.store("probe_actual")
        self.net.restore("probe_before", restore_random_state=True)
        self.offset, self.frames, self.last, self.total_spikes, self.rate_history = (
            before_meta
        )
        control = self.advance(None, duration_ms=20)
        self.net.restore("probe_actual", restore_random_state=True)
        self.offset, self.frames, self.last, self.total_spikes, self.rate_history = (
            actual_meta
        )
        if actual["before_state_sha256"] != control["before_state_sha256"]:
            raise RuntimeError("Matched replay initial state differs")
        return {
            "input_id": event["id"],
            "same_initial_state_sha256": actual["before_state_sha256"],
            "same_random_state": True,
            "actual_spikes": actual["spike_count"],
            "control_spikes": control["spike_count"],
            "spike_difference": actual["spike_count"] - control["spike_count"],
            "state_changed_by_input": actual["after_state_sha256"]
            != control["after_state_sha256"],
            "actual_state_sha256": actual["after_state_sha256"],
            "control_state_sha256": control["after_state_sha256"],
            "scope": "Controlled replay perturbation from current neural state; does not isolate the original historical transaction or demonstrate consciousness",
        }

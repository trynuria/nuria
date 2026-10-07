"""Independent Brian2 sensory circuit for the decision lab, with no utility drive."""

from __future__ import annotations

import hashlib
from pathlib import Path

import brian2 as b
import numpy as np

SENSOR_VERSION = "discovery-sensor-v1"


class SpikeSensor:
    def __init__(self, directory: Path, seed: int = 9021):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        b.start_scope()
        b.seed(seed)
        b.prefs.codegen.target = "numpy"
        b.prefs.logging.delete_log_on_exit = False
        b.defaultclock.dt = b.ms
        self.cells = b.NeuronGroup(
            256,
            """
            dv/dt = (0.62 + drive + ge - gi - v)/(18*ms) : 1 (unless refractory)
            dge/dt = -ge/(7*ms) : 1
            dgi/dt = -gi/(11*ms) : 1
            drive : 1
            """,
            threshold="v > 1",
            reset="v=0",
            refractory=3 * b.ms,
            method="euler",
            name="discovery_cells",
        )
        self.cells.v = np.random.default_rng(seed).uniform(0, 0.7, 256)
        self.synapses = b.Synapses(
            self.cells, self.cells, "w : 1", on_pre="ge_post += w", name="discovery_exc"
        )
        self.synapses.connect(condition="i < 224 and i != j", p=0.045)
        self.synapses.w = "0.02 + rand()*0.03"
        self.synapses.delay = "1*ms + rand()*5*ms"
        self.inhibition = b.Synapses(
            self.cells, self.cells, on_pre="gi_post += 0.12", name="discovery_inh"
        )
        self.inhibition.connect(condition="i >= 224 and i != j", p=0.08)
        self.noise = b.PoissonGroup(256, rates=10 * b.Hz, name="discovery_noise")
        self.background = b.Synapses(
            self.noise, self.cells, on_pre="ge_post += 0.2", name="discovery_background"
        )
        self.background.connect(j="i")
        self.network = b.Network(
            self.cells, self.synapses, self.inhibition, self.noise, self.background
        )
        self.windows = 0

    def __call__(self, cue: int | None) -> dict:
        if cue is not None and (type(cue) is not int or not 0 <= cue < 8):
            raise ValueError("Invalid sensory cue")
        self.cells.drive = 0
        if cue is not None:
            self.cells.drive[cue * 16 : (cue + 1) * 16] = 1.5
        monitor = b.SpikeMonitor(self.cells, name="discovery_window")
        self.network.add(monitor)
        start = float(self.network.t / b.second)
        try:
            self.network.run(40 * b.ms, namespace={})
            counts = np.asarray(monitor.count[:], float)
            features = np.concatenate(
                (
                    counts.reshape(16, 16).mean(axis=1) / 4,
                    np.asarray(self.cells.v[:]).reshape(16, 16).mean(axis=1),
                )
            )
            spikes = len(monitor.i)
            spike_times = [
                [round(float(t / b.second) - start, 6), int(i)]
                for t, i in zip(monitor.t[:], monitor.i[:])
            ]
            pool_voltages = (
                np.asarray(self.cells.v[:]).reshape(16, 16).mean(axis=1).tolist()
            )
        finally:
            self.network.remove(monitor)
        self.windows += 1
        return {
            "features": features.tolist(),
            "spike_count": spikes,
            "window": self.windows,
            "window_ms": 40,
            "spikes": spike_times,
            "counts": counts.astype(int).tolist(),
            "pool_voltages": pool_voltages,
        }

    def checkpoint(self) -> tuple[dict, bytes]:
        path = self.directory / "sensor.current.pkl"
        self.network.store("discovery", filename=str(path))
        raw = path.read_bytes()
        return {
            "model": SENSOR_VERSION,
            "seed": self.seed,
            "windows": self.windows,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }, raw

    def restore(self, metadata: dict, raw: bytes) -> None:
        if (
            metadata["model"] != SENSOR_VERSION
            or metadata["seed"] != self.seed
            or hashlib.sha256(raw).hexdigest() != metadata["sha256"]
        ):
            raise RuntimeError("Discovery sensor checkpoint differs; reset refused")
        path = self.directory / "sensor.restore.pkl"
        path.write_bytes(raw)
        self.network.restore("discovery", filename=str(path), restore_random_state=True)
        self.windows = metadata["windows"]

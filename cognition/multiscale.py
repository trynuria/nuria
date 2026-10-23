"""Causal, windowed TVB/Brian2 coupling with a joint trusted checkpoint.

TVB integrates Wilson–Cowan regional populations; Brian2 retains Nuria's
existing 1024-neuron circuit. Both exchange *previous-window* activity. The
engineering ports below are not assertions of human functional homology.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import asdict, dataclass
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import numpy as np

from cognition.brain import MODEL_VERSION, POPULATIONS, AdaptiveBrain, sha
from cognition.journal import canonical

VERSION = "nuria-multiscale-v1"
DATASET = "connectivity_76.zip"
DATASET_SHA256 = "8b856d5fa80a8593e01dc18b95efff829ca98d2daf43cea38fd3309fed726b2e"
PORTS = (
    ("lV1", "rV1", "lA1", "rA1"),
    ("lPFCDL", "rPFCDL"),
    ("lHC", "rHC"),
    ("lCCA", "rCCA"),
    ("lM1", "rM1"),
    ("lG", "rG"),
)


@dataclass(frozen=True)
class CouplingConfig:
    window_ms: int = 20
    dt_ms: float = 1.0
    speed_m_per_s: float = 4.0
    global_gain: float = 0.1
    spike_to_region_gain: float = 0.08
    region_to_spike_gain: float = 0.12
    input_gain: float = 0.3
    washout_ms: int = 1000
    shuffle_seed: int | None = None

    def __post_init__(self):
        if self.window_ms != 20 or self.dt_ms != 1.0 or self.washout_ms != 1000:
            raise ValueError("This model version fixes its clocks and washout")
        for name, ceiling in (
            ("global_gain", 1.0),
            ("spike_to_region_gain", 0.2),
            ("region_to_spike_gain", 0.2),
            ("input_gain", 0.5),
        ):
            value = getattr(self, name)
            if not np.isfinite(value) or not 0 <= value <= ceiling:
                raise ValueError("Invalid coupling parameter: " + name)
        if self.speed_m_per_s != 4.0:
            raise ValueError("This model version fixes conduction speed")
        if self.shuffle_seed is not None and not isinstance(self.shuffle_seed, int):
            raise ValueError("Shuffle seed must be an integer")


class RegionalNetwork:
    """Use TVB's simulator and delay history, not a reimplementation of its ODE."""

    def __init__(self, config: CouplingConfig):
        # Optional dependencies never enter the active cognitive worker's imports.
        from tvb.datatypes.connectivity import Connectivity
        from tvb.simulator import coupling, integrators, models, monitors, simulator

        class DeterministicLogFilter(logging.Filter):
            def filter(self, record):
                return record.getMessage() != (
                    "random_state supplied for non-stochastic integration"
                )

        logger = logging.getLogger("tvb.simulator.integrators")
        if not any(
            type(item).__name__ == "DeterministicLogFilter" for item in logger.filters
        ):
            logger.addFilter(DeterministicLogFilter())

        self.config = config
        raw = files("tvb_data").joinpath("connectivity", DATASET).read_bytes()
        if sha(raw) != DATASET_SHA256:
            raise RuntimeError("TVB dataset changed; unreviewed connectivity refused")
        conn = Connectivity.from_file(DATASET)
        conn.configure()
        self.labels = conn.region_labels.tolist()
        if len(self.labels) != 76:
            raise RuntimeError("Unexpected region coverage")
        self.ports = [[self.labels.index(label) for label in group] for group in PORTS]
        weights, lengths = conn.weights.copy(), conn.tract_lengths.copy()
        if config.shuffle_seed is not None:
            # Preserve density and the joint nonzero weight/length distribution.
            # Degree sequence and row strengths are NOT preserved by this null.
            active = np.argwhere(weights > 0)
            candidates = np.argwhere(~np.eye(len(weights), dtype=bool))
            rng = np.random.default_rng(config.shuffle_seed)
            destinations = candidates[rng.permutation(len(candidates))[: len(active)]]
            old_weights = weights[active[:, 0], active[:, 1]].copy()
            old_lengths = lengths[active[:, 0], active[:, 1]].copy()
            weights[:] = 0
            lengths[:] = 0
            weights[destinations[:, 0], destinations[:, 1]] = old_weights
            lengths[destinations[:, 0], destinations[:, 1]] = old_lengths
        weights /= max(float(np.max(weights.sum(axis=1))), 1.0)
        conn.weights, conn.tract_lengths = weights, lengths
        conn.speed = np.array([config.speed_m_per_s])
        self.connectivity_sha256 = sha(
            weights.astype("<f8").tobytes() + lengths.astype("<f8").tobytes()
        )
        self.sim = simulator.Simulator(
            model=models.WilsonCowan(P=np.full(76, 0.25)),
            connectivity=conn,
            coupling=coupling.Linear(a=np.array([config.global_gain])),
            integrator=integrators.HeunDeterministic(dt=config.dt_ms),
            monitors=(monitors.Raw(),),
            initial_conditions=np.full((1, 2, 76, 1), 0.1),
        ).configure()
        self._integrate(config.washout_ms)
        self.reference = self.excitation.copy()
        self.previous_rates = np.zeros(6)

    def _integrate(self, steps: int) -> None:
        # Exhaustion is essential: TVB commits current_state/current_step at exit.
        for _ in self.sim(n_steps=steps):
            pass
        if not np.isfinite(self.sim.current_state).all():
            raise RuntimeError("Nonfinite regional state; continuation refused")

    @property
    def excitation(self) -> np.ndarray:
        return self.sim.current_state[0, :, 0].copy()

    def drive(self) -> np.ndarray:
        activity = np.clip((self.excitation - self.reference) / 0.2, -1, 1)
        drive = np.zeros(1024)
        for (_, _, start, end, _), region_ids in zip(POPULATIONS, self.ports):
            drive[start:end] = (
                float(activity[region_ids].mean()) * self.config.region_to_spike_gain
            )
        return drive

    def advance(self, event: dict | None, new_rates: list[float]) -> dict:
        rates = np.asarray(new_rates, dtype=float)
        if rates.shape != (6,) or not np.isfinite(rates).all() or np.any(rates < 0):
            raise ValueError("Invalid spike feedback")
        old_rates = self.previous_rates.copy()
        incoming = np.zeros(76)
        for ids, rate in zip(self.ports, old_rates):
            incoming[ids] += self.config.spike_to_region_gain * np.clip(
                (rate - 8) / 25, -1, 1
            )
        if event is not None:
            side = event["side"]
            if side not in ("buy", "sell"):
                raise ValueError("Unknown input side")
            amount = float(event["quote_amount"])
            if not np.isfinite(amount) or not 0 < amount <= 1e9:
                raise ValueError("Invalid input amount")
            label = "lV1" if side == "buy" else "rV1"
            incoming[self.labels.index(label)] += self.config.input_gain * min(
                1.0, 0.25 + np.log1p(amount) / 2
            )
        self.sim.model.P = np.clip(0.25 + incoming, 0, 1)
        before = self.state_hash()
        self._integrate(self.config.window_ms)
        self.previous_rates = rates.copy()
        return {
            "before_sha256": before,
            "after_sha256": self.state_hash(),
            "input_drive_sha256": sha(incoming.astype("<f8").tobytes()),
            "used_previous_spike_rates": old_rates.tolist(),
            "excitation": self.excitation.tolist(),
            "inhibition": self.sim.current_state[1, :, 0].tolist(),
            "model_time_ms": self.sim.current_step * self.config.dt_ms,
        }

    def state_hash(self) -> str:
        return sha(
            b"".join(
                np.asarray(value, dtype="<f8").tobytes()
                for value in (
                    self.sim.current_state,
                    self.sim.history.buffer,
                    self.sim.model.P,
                    self.reference,
                    self.previous_rates,
                    [self.sim.current_step],
                )
            )
        )

    def checkpoint(self) -> bytes:
        buffer = io.BytesIO()
        np.savez_compressed(
            buffer,
            current_state=self.sim.current_state,
            history=self.sim.history.buffer,
            drive=self.sim.model.P,
            reference=self.reference,
            previous_rates=self.previous_rates,
            step=np.array([self.sim.current_step], dtype=np.int64),
        )
        return buffer.getvalue()

    def restore(self, raw: bytes) -> None:
        with np.load(io.BytesIO(raw), allow_pickle=False) as value:
            expected = {
                "current_state": self.sim.current_state.shape,
                "history": self.sim.history.buffer.shape,
                "drive": (76,),
                "reference": (76,),
                "previous_rates": (6,),
                "step": (1,),
            }
            if set(value.files) != set(expected):
                raise ValueError("Incomplete regional checkpoint")
            for key, shape in expected.items():
                if value[key].shape != shape or not np.isfinite(value[key]).all():
                    raise ValueError("Invalid regional checkpoint: " + key)
            step = int(value["step"][0])
            if step < self.config.washout_ms or step % self.config.window_ms:
                raise ValueError("Invalid regional clock")
            self.sim.current_state = value["current_state"].copy()
            self.sim.history.buffer[:] = value["history"]
            self.sim.model.P = value["drive"].copy()
            self.reference = value["reference"].copy()
            self.previous_rates = value["previous_rates"].copy()
            self.sim.current_step = step


class CoupledBrain:
    """A new circuit history; never load or rewrite the live circuit's checkpoint."""

    def __init__(self, directory: Path, config: CouplingConfig | None = None):
        self.config = config or CouplingConfig()
        self.regional = RegionalNetwork(self.config)
        # Construct Brian last, so TVB setup cannot perturb its global RNG stream.
        self.brain = AdaptiveBrain(directory)
        self.identity = {
            "version": VERSION,
            "config": asdict(self.config),
            "dataset": DATASET,
            "dataset_sha256": DATASET_SHA256,
            "connectivity_sha256": self.regional.connectivity_sha256,
            "ports": PORTS,
            "dependencies": {
                name: version(name)
                for name in ("brian2", "tvb-library", "tvb-data", "numpy")
            },
        }
        self.identity_sha256 = sha(canonical(self.identity))
        self.failed = False

    def advance(self, event: dict | None = None, **kwargs) -> dict:
        if self.failed:
            raise RuntimeError("An incomplete coupled step requires trusted recovery")
        if "duration_ms" in kwargs and kwargs["duration_ms"] != self.config.window_ms:
            raise ValueError("Coupling requires fixed 20 ms windows")
        if event is not None:
            amount, fee = (
                float(event["quote_amount"]),
                float(event.get("creator_fee", 0)),
            )
            if (
                not isinstance(event.get("id"), str)
                or event["side"] not in ("buy", "sell")
                or not np.isfinite([amount, fee]).all()
                or not 0 < amount <= 1e9
                or fee < 0
            ):
                raise ValueError("Invalid coupled input")
        kwargs["duration_ms"] = self.config.window_ms
        drive = self.regional.drive()
        before = self.state_hash()
        try:
            frame = self.brain.advance(event, regional_drive=drive, **kwargs)
            regional = self.regional.advance(event, frame["regional_rates"])
        except Exception:
            self.failed = True
            raise
        return {
            **frame,
            "multiscale": {
                "identity_sha256": self.identity_sha256,
                "before_sha256": before,
                "after_sha256": self.state_hash(),
                "regional_drive_sha256": sha(drive.astype("<f8").tobytes()),
                "regional_drive_max_abs": float(np.max(np.abs(drive))),
                "exchange_lag_ms": self.config.window_ms,
                "regional": regional,
            },
        }

    def reward(self, signal: float) -> dict:
        return self.brain.reward(signal)

    def state_hash(self) -> str:
        return sha((self.brain.state_hash() + self.regional.state_hash()).encode())

    def checkpoint(self) -> bytes:
        if self.failed:
            raise RuntimeError("An incomplete coupled step cannot be checkpointed")
        brian = self.brain.checkpoint()
        regional = self.regional.checkpoint()
        metadata = {
            "identity_sha256": self.identity_sha256,
            "brain": {
                "model": MODEL_VERSION,
                "topology_sha256": self.brain.topology["sha256"],
                "network_sha256": sha(brian),
                "offset": self.brain.offset,
                "frames": self.brain.frames,
                "total_spikes": self.brain.total_spikes,
                "rate_history": self.brain.rate_history,
            },
            "regional_sha256": sha(regional),
            "joint_state_sha256": self.state_hash(),
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", canonical(metadata))
            archive.writestr("brian.pkl", brian)
            archive.writestr("regional.npz", regional)
        return buffer.getvalue()

    def restore(self, raw: bytes, expected_sha256: str) -> None:
        # A hash is integrity, not authenticity. Only trusted local journals may
        # supply this bundle: the Brian member is a pickle, as in the base model.
        if len(raw) > 32_000_000 or sha(raw) != expected_sha256:
            raise ValueError("Joint checkpoint integrity failed")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sorted(archive.namelist()) != [
                "brian.pkl",
                "metadata.json",
                "regional.npz",
            ]:
                raise ValueError("Unexpected checkpoint members")
            if sum(item.file_size for item in archive.infolist()) > 64_000_000:
                raise ValueError("Expanded checkpoint exceeds its limit")
            metadata = json.loads(archive.read("metadata.json"))
            if metadata["identity_sha256"] != self.identity_sha256:
                raise ValueError("Model, parameters or dependency versions changed")
            regional = archive.read("regional.npz")
            brian = archive.read("brian.pkl")
        if sha(regional) != metadata["regional_sha256"]:
            raise ValueError("Regional checkpoint integrity failed")
        if sha(brian) != metadata["brain"]["network_sha256"]:
            raise ValueError("Brian checkpoint integrity failed")
        self.regional.restore(regional)
        self.brain.restore(brian, metadata["brain"])
        expected_step = (
            self.config.washout_ms + self.brain.frames * self.config.window_ms
        )
        if self.regional.sim.current_step != expected_step:
            raise ValueError("Regional and spiking clocks disagree")
        if self.state_hash() != metadata["joint_state_sha256"]:
            raise ValueError("Restored joint state differs")
        self.failed = False

"""One isolated discovery writer, independently checkpointed from both organisms."""

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

from cognition.discovery import VERSION, DiscoveryLab
from cognition.journal import Journal
from cognition.spike_sensor import SpikeSensor
from publish import publish


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class LabRuntime:
    def __init__(self, directory: Path, source_sha256: str, seed: int = 81001):
        self.journal = Journal(directory)
        self.source_sha256 = source_sha256
        if not self.journal.verify(full=True)["valid"]:
            raise RuntimeError("Discovery journal integrity failed")
        saved = self.journal.restore()
        metadata = saved[0] if saved else {}
        if saved and metadata["model"] != VERSION:
            raise RuntimeError("Discovery model differs; reset refused")
        self.genesis = metadata.get("genesis_utc", utc())
        self.lab = DiscoveryLab(metadata.get("seed", seed), metadata.get("lab"))
        self.sensor = SpikeSensor(
            directory, metadata.get("sensor", {}).get("seed", 9021)
        )
        if saved:
            self.sensor.restore(metadata["sensor"], saved[1])

    def save(self) -> None:
        sensor, raw = self.sensor.checkpoint()
        self.journal.save(
            {
                "model": VERSION,
                "tick": self.lab.trials,
                "cursor": self.lab.trials,
                "seed": self.lab.seed,
                "genesis_utc": self.genesis,
                "source_sha256": self.source_sha256,
                "lab": self.lab.state(),
                "sensor": sensor,
                "record_seq": self.journal.seq,
                "record_head": self.journal.head,
            },
            raw,
        )

    def step(self, coupling: dict | None = None) -> dict:
        result = self.lab.step(self.sensor, coupling=coupling)
        self.journal.append("discovery_trial", result)
        self.save()
        return self.snapshot()

    def snapshot(self) -> dict:
        return {
            "phase": "running",
            "updated_utc": utc(),
            "genesis_utc": self.genesis,
            "source_sha256": self.source_sha256,
            **self.lab.public(),
            "sensor": {
                "model": "discovery-sensor-v1",
                "neurons": 256,
                "windows": self.sensor.windows,
                "plasticity": "fixed recurrent synapses; decision and cue readouts learn separately",
            },
            "record_head": self.journal.head,
            "record_seq": self.journal.seq,
            "integrity": self.journal.verify(),
            "records": self.journal.recent("discovery_trial", 3),
            "financial_execution": "None; all budget units are virtual",
        }


def main() -> None:
    directory = Path(os.environ["NURIA_DISCOVERY_DATA"])
    public = Path(os.environ["NURIA_DISCOVERY_PUBLIC"])
    directory.mkdir(parents=True, exist_ok=True)
    public.mkdir(parents=True, exist_ok=True)
    lock = (directory / "writer.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Discovery writer already active")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    root = Path(__file__).resolve().parent
    source = hashlib.sha256(
        b"".join(
            (root / name).read_bytes()
            for name in (
                "discovery.py",
                "spike_sensor.py",
                "lab_worker.py",
                "journal.py",
            )
        )
    ).hexdigest()
    runtime = LabRuntime(directory, source)
    try:
        while not stop.is_set():
            if shutil.disk_usage(directory).free < 2 * 1024**3:
                raise RuntimeError(
                    "Discovery capacity threshold reached; evidence retained"
                )
            begin = time.monotonic()
            coupling = {}
            path = Path(os.environ["NURIA_DISCOVERY_UPSTREAM"])
            try:
                if time.time() - path.stat().st_mtime < 15:
                    upstream = json.loads(path.read_text())
                    coupling = {
                        "tick": upstream["tick"],
                        "source_cursor": upstream["source_cursor"],
                        "record_head": upstream["record_head"],
                        "rate_hz": upstream.get("neural", {}).get("rate_hz", 0),
                        "input_source": upstream.get("upstream", {}).get("feed", {}),
                    }
            except (OSError, ValueError, KeyError):
                pass
            snapshot = runtime.step(coupling)
            snapshot["cycle_wall_seconds"] = time.monotonic() - begin
            publish(public, "status.json", snapshot)
            stop.wait(max(0.1, 5 - (time.monotonic() - begin)))
        runtime.save()
    except Exception:
        publish(
            public,
            "status.json",
            {
                "phase": "error",
                "updated_utc": utc(),
                "error": "Discovery worker failed; evidence retained",
            },
        )
        raise


if __name__ == "__main__":
    main()

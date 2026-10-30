"""Source-labelled extracellular recordings and an isolated Brian2 input bridge.

Waveform statistics are not spike sorting, a biological identity check or a
consciousness measure. No lab control or production state is imported here.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

MODES = {
    "public_observed",
    "recorded_biological",
    "controlled_biological",
    "synthetic_fixture",
}
MAX_BYTES = 16 * 1024 * 1024


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("UTC timestamp required")
    return parsed


def validate_recording(value):
    """Validate a portable channel-major window without upgrading its provenance."""
    if value.get("schema") != 1 or value.get("mode") not in MODES:
        raise ValueError("Unsupported recording schema or source mode")
    for key in ("provider", "recording_id", "units", "provenance"):
        if not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 512:
            raise ValueError("Missing bounded source metadata: " + key)
    channels = value.get("channels")
    if (
        not isinstance(channels, list)
        or not 1 <= len(channels) <= 32
        or any(type(c) is not int or not 0 <= c < 128 for c in channels)
        or len(set(channels)) != len(channels)
    ):
        raise ValueError("Distinct electrode channel IDs required")
    samples = value.get("samples")
    if (
        not isinstance(samples, list)
        or len(samples) != len(channels)
        or any(
            not isinstance(row, list) or not 8 <= len(row) <= 16384 for row in samples
        )
        or len({len(row) for row in samples}) != 1
        or any(type(v) not in (int, float) for row in samples for v in row)
    ):
        raise ValueError("Expected rectangular, channel-major numeric samples")
    matrix = np.asarray(samples, dtype=float)
    if not np.isfinite(matrix).all() or np.max(np.abs(matrix)) > 1e12:
        raise ValueError("Nonfinite or out-of-range samples")
    rate = value.get("sample_rate_hz")
    if rate is not None and (
        type(rate) not in (int, float) or not math.isfinite(rate) or not 0 < rate <= 1e6
    ):
        raise ValueError("Invalid sample rate")
    for key in ("received_utc", "acquired_utc"):
        if value.get(key) is not None:
            utc(value[key])
    if value["mode"] == "public_observed":
        if (
            value.get("acquired_utc") is not None
            or value.get("sample_rate_hz") is not None
        ):
            raise ValueError(
                "The public protocol does not establish acquisition time or rate"
            )
        if (
            value["units"] != "unknown"
            or value["provenance"] != "public_endpoint_unattested"
        ):
            raise ValueError("Public-view provenance cannot be upgraded")
        if value.get("received_utc") is None:
            raise ValueError("Public reception time required")
    elif value["mode"] != "synthetic_fixture":
        if value.get("acquired_utc") is None or rate is None:
            raise ValueError(
                "Provider recording metadata must include acquisition time and rate"
            )
        if value["provenance"] != "provider_metadata_unverified":
            raise ValueError(
                "Provider metadata is an attribution, not independent verification"
            )
    return matrix


def read_recording(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Recording byte limit")
    value = json.loads(path.read_bytes())
    validate_recording(value)
    return value


def write_new(path, value):
    """Never replace an earlier recording, result or checkpoint."""
    raw = canonical(value) + b"\n"
    if len(raw) > MAX_BYTES:
        raise ValueError("Artifact byte limit")
    with Path(path).open("xb") as stream:
        Path(path).chmod(0o600)
        stream.write(raw)


def waveform_features(recording):
    matrix = validate_recording(recording)
    centered = matrix - matrix.mean(axis=1, keepdims=True)
    std = np.std(matrix, axis=1)
    line = np.mean(np.abs(np.diff(matrix, axis=1)), axis=1)
    spectrum = np.abs(np.fft.rfft(centered, axis=1)) ** 2
    frequencies = np.fft.rfftfreq(matrix.shape[1])
    power = spectrum.sum(axis=1)
    centroid = np.divide(
        spectrum @ frequencies, power, out=np.zeros_like(power), where=power > 0
    )
    rms = np.sqrt(np.mean(matrix * matrix, axis=1))
    return {
        "channels": recording["channels"],
        "rms": rms.tolist(),
        "standard_deviation": std.tolist(),
        "line_length_per_sample": line.tolist(),
        "spectral_centroid_cycles_per_sample": centroid.tolist(),
        "spike_rate_hz": None,
        "scope": "Descriptive waveform statistics; no event detection or neural identity inference",
    }


def sensory_drive(recording):
    """Fixed bounded encoding; no thresholds fitted on a test response."""
    features = waveform_features(recording)
    drive = np.zeros(1024)
    groups = np.array_split(np.arange(128), len(recording["channels"]))
    for indices, std, line in zip(
        groups, features["standard_deviation"], features["line_length_per_sample"]
    ):
        drive[indices] = (
            0.18 * math.tanh(math.log1p(std)) * math.tanh(line / max(std, 1e-12))
        )
    return drive


def bridge(recordings, directory, *, duration_ms=100):
    """Replay both matched arms in new circuits; never open a production checkpoint."""
    from cognition.brain import AdaptiveBrain

    if (
        not 1 <= len(recordings) <= 32
        or type(duration_ms) is not int
        or not 20 <= duration_ms <= 200
    ):
        raise ValueError(
            "Bridge limited to 32 windows of 20–200 simulated milliseconds"
        )
    for recording in recordings:
        validate_recording(recording)
    identities = {(r["mode"], r["provider"], tuple(r["channels"])) for r in recordings}
    if len(identities) != 1:
        raise ValueError("Do not mix source types or electrode layouts in one replay")
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = {
        "schema": 1,
        "scope": "Matched-seed causal wiring check; not a learning or intelligence comparison",
        "mode": recordings[0]["mode"],
        "input_sha256": [digest(r) for r in recordings],
        "biological_origin_independently_verified": False,
        "acquisition_continuity_verified": False,
        "arms": {},
    }
    for arm in ("zero_drive", "waveform_drive"):
        brain = AdaptiveBrain(directory / arm)
        start = brain.state_hash()
        windows = []
        for recording in recordings:
            drive = (
                sensory_drive(recording) if arm == "waveform_drive" else np.zeros(1024)
            )
            observed = brain.advance(duration_ms=duration_ms, regional_drive=drive)
            windows.append(
                {
                    key: observed[key]
                    for key in (
                        "before_state_sha256",
                        "after_state_sha256",
                        "spikes_sha256",
                        "spike_count",
                        "regional_rates",
                        "features",
                    )
                }
            )
        result["arms"][arm] = {"initial_state_sha256": start, "windows": windows}
    result["matched_initial_state"] = (
        len({r["initial_state_sha256"] for r in result["arms"].values()}) == 1
    )
    result["changed_windows"] = sum(
        a["spikes_sha256"] != b["spikes_sha256"]
        for a, b in zip(
            result["arms"]["zero_drive"]["windows"],
            result["arms"]["waveform_drive"]["windows"],
        )
    )
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    write_new(directory / "result.json", result)
    return result

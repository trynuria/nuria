"""Bounded read-only client for the current FinalSpark public viewer protocol.

This is independent protocol integration, not a copy of the legacy LiveMEA SDK.
No stimulation, cameras, pumps, lab secrets or private endpoints are available.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from cognition.biology import digest, validate_recording, waveform_features, write_new

ORIGIN = "https://livenp.finalspark.com"


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if (parsed.scheme, parsed.netloc) != ("https", "livenp.finalspark.com"):
            raise ValueError("Cross-origin public-view redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicReader:
    def __init__(
        self, *, max_frames=3, seconds=25, max_bytes=4 * 1024 * 1024, open_request=None
    ):
        if type(max_frames) is not int or not 1 <= max_frames <= 32:
            raise ValueError("Frame bound must be 1–32")
        if (
            type(seconds) not in (int, float)
            or not math.isfinite(seconds)
            or not 2 <= seconds <= 60
            or type(max_bytes) is not int
            or not 4096 <= max_bytes <= 16 * 1024 * 1024
        ):
            raise ValueError("Invalid time or byte bound")
        self.max_frames, self.seconds, self.max_bytes = max_frames, seconds, max_bytes
        self.open = (
            open_request or urllib.request.build_opener(SameOriginRedirect()).open
        )
        self.sid = None
        self.used = 0
        self.requests = []
        self.started = None

    def _request(self, path, body=None, *, closing=False):
        remaining = self.seconds - (time.monotonic() - self.started)
        allowance = self.max_bytes - self.used
        if closing:
            # Fixed extra close allowance: never another data poll after expiry.
            remaining, allowance = 2, 4096
        elif remaining <= 0 or allowance <= 0:
            raise TimeoutError("Public subscription limit reached")
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Only fixed-origin relative paths allowed")
        request = urllib.request.Request(
            ORIGIN + path,
            data=None if body is None else body.encode(),
            headers={
                "User-Agent": "Nuria-read-only-observation/1",
                "Content-Type": "text/plain;charset=UTF-8",
            },
        )
        with self.open(request, timeout=min(8, max(0.1, remaining))) as response:
            raw = response.read(allowance + 1)
            self.used += len(raw)
            self.requests.append(
                {
                    "method": request.get_method(),
                    "status": response.status,
                    "bytes": len(raw),
                    "closing": closing,
                }
            )
        if len(raw) > allowance:
            raise ValueError("Public subscription byte limit reached")
        return raw.decode("utf-8")

    def _socket(self, body=None, *, closing=False):
        query = {"EIO": "4", "transport": "polling", "t": str(time.monotonic_ns())}
        if self.sid:
            query["sid"] = self.sid
        return self._request(
            "/socket.io/?" + urllib.parse.urlencode(query), body, closing=closing
        )

    def capture(self, directory, *, channels=(0, 1, 2, 3)):
        if self.started is not None:
            raise ValueError("Reader instances are single-use")
        if (
            not 1 <= len(channels) <= 8
            or len(set(channels)) != len(channels)
            or any(type(c) is not int or not 0 <= c < 128 for c in channels)
        ):
            raise ValueError("Select 1–8 distinct public channels")
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.started = time.monotonic()
        receipt = {
            "schema": 1,
            "mode": "public_observed",
            "endpoint": ORIGIN,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "frames": [],
            "completed": False,
            "disconnected": False,
            "biological_origin_independently_verified": False,
            "acquisition_continuity_verified": False,
        }
        try:
            controllers = json.loads(self._request("/controllers"))["controllers"]
            controller = next(
                c
                for c in controllers
                if c.get("default_controller")
                and c.get("online")
                and not c.get("maintenance")
            )
            mea = controller.get("default_mea", 0)
            available = {
                c["channel_index"]
                for c in controller["electrode_layout"]
                if c.get("mea_index") == mea
            }
            if not set(channels) <= available:
                raise ValueError("Requested channels absent from public layout")
            count = json.loads(self._request("/sample"))["numbersample"]
            if type(count) is not int or not 8 <= count <= 16384:
                raise ValueError("Unsupported public sample shape")
            receipt["selection"] = {
                "controller_id": controller["_id"],
                "mea_index": mea,
                "channels": list(channels),
                "samples_per_channel": count,
            }
            handshake = self._socket()
            if not handshake.startswith("0"):
                raise ValueError("Expected Engine.IO opening packet")
            self.sid = json.loads(handshake[1:])["sid"]
            self._socket("40")
            acknowledged = False
            for _ in range(3):
                packets = self._socket().split("\x1e")
                for packet in packets:
                    if packet == "2":
                        self._socket("3")
                    elif packet.startswith("40"):
                        acknowledged = True
                if acknowledged:
                    break
            if not acknowledged:
                raise ValueError("Missing Socket.IO connection acknowledgment")
            subscription = {
                "id_intan": controller["_id"],
                "mea_index": mea,
                "channel_index": list(channels),
            }
            self._socket(
                "42" + json.dumps(["start", subscription], separators=(",", ":"))
            )
            while len(receipt["frames"]) < self.max_frames:
                for packet in self._socket().split("\x1e"):
                    if packet == "2":
                        self._socket("3")
                        continue
                    if packet.startswith(("1", "41", "44")):
                        raise ConnectionError("Public subscription disconnected")
                    if not packet.startswith("42"):
                        continue
                    event = json.loads(packet[2:])
                    if not isinstance(event, list) or len(event) != 2:
                        raise ValueError("Malformed public event")
                    if event[0] == "maintenance":
                        payload = event[1]
                        if payload.get("id_intan") == controller["_id"] and payload.get(
                            "maintenance"
                        ):
                            raise ConnectionError(
                                "Selected public controller entered maintenance"
                            )
                    if event[0] != "data":
                        continue
                    flat = event[1]
                    if not isinstance(flat, list) or len(flat) != count * len(channels):
                        raise ValueError("Public event changed channel-major shape")
                    frame = {
                        "schema": 1,
                        "mode": "public_observed",
                        "provider": "FinalSpark public viewer",
                        "recording_id": controller["_id"] + ":" + str(mea),
                        "provenance": "public_endpoint_unattested",
                        "channels": list(channels),
                        "samples": [
                            flat[i * count : (i + 1) * count]
                            for i in range(len(channels))
                        ],
                        "units": "unknown",
                        "sample_rate_hz": None,
                        "acquired_utc": None,
                        "received_utc": datetime.now(timezone.utc).isoformat(),
                        "sequence": None,
                    }
                    validate_recording(frame)
                    filename = f"frame-{len(receipt['frames']) + 1:03}.json"
                    write_new(directory / filename, frame)
                    receipt["frames"].append(
                        {
                            "file": filename,
                            "sha256": digest(frame),
                            "received_utc": frame["received_utc"],
                            "features": waveform_features(frame),
                        }
                    )
                    if len(receipt["frames"]) == self.max_frames:
                        break
            receipt["completed"] = True
        except Exception as error:
            # Error strings can contain transport session identifiers; retain type only.
            receipt["error_type"] = type(error).__name__
        finally:
            if self.sid:
                try:
                    self._socket("41\x1e1", closing=True)
                    receipt["disconnected"] = True
                except Exception as error:
                    receipt["close_error_type"] = type(error).__name__
            receipt.update(
                received_bytes=self.used,
                wall_seconds=time.monotonic() - self.started,
                requests=self.requests,
            )
            write_new(directory / "receipt.json", receipt)
        return receipt

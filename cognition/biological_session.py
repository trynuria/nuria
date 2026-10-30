"""Read-only NeuroPlatform v2 boundary and lab-reviewable stimulation plans.

The optional vendor SDK belongs in its supported Python 3.11/3.12 environment.
There is deliberately no trigger/upload/pump/camera execution method here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from cognition.biology import digest, utc


@dataclass(frozen=True)
class ReadWindow:
    dataset: str
    start_utc: str
    stop_utc: str

    def __post_init__(self):
        start, stop = utc(self.start_utc), utc(self.stop_utc)
        if not isinstance(self.dataset, str) or not 1 <= len(self.dataset) <= 96:
            raise ValueError("Assigned dataset identifier required")
        if not timedelta(0) < stop - start <= timedelta(seconds=60):
            raise ValueError("Read windows must be positive and at most 60 seconds")


class RecordedSession:
    """Injectable query boundary; no vendor import or connection on construction."""

    def __init__(self, controller, query_type):
        self.controller, self.query_type = controller, query_type

    @classmethod
    def from_vendor_sdk(cls):
        import sys

        if sys.version_info[:2] not in ((3, 11), (3, 12)):
            raise RuntimeError(
                "NeuroPlatform v2 requires a separate Python 3.11/3.12 environment"
            )
        from neuroplatformv2.core.database import DatabaseController
        from neuroplatformv2.utils.schemas import SpikeEventQuery

        return cls(DatabaseController, SpikeEventQuery)

    async def read_events(self, window: ReadWindow):
        start, stop = utc(window.start_utc), utc(window.stop_utc)
        query = self.query_type(start=start, stop=stop, fsname=window.dataset)
        data = await asyncio.wait_for(
            self.controller.get_spike_event(query), timeout=15
        )
        if data is None:
            raise ValueError("Missing data is unknown, not zero activity")
        if not {"Time", "channel"} <= set(data.columns) or len(data) > 100000:
            raise ValueError("Unsupported or oversized vendor event table")
        events = []
        for row in data[["Time", "channel"]].to_dict(orient="records"):
            timestamp = row["Time"]
            timestamp = (
                timestamp.isoformat()
                if hasattr(timestamp, "isoformat")
                else str(timestamp)
            )
            instant = utc(timestamp)
            channel = row["channel"]
            if (
                type(channel) is not int
                or not 0 <= channel < 128
                or not start <= instant < stop
            ):
                raise ValueError("Event outside requested interval or electrode range")
            events.append({"acquired_utc": timestamp, "channel": channel})
        events.sort(key=lambda e: (e["acquired_utc"], e["channel"]))
        return {
            "schema": 1,
            "mode": "recorded_biological",
            "provenance": "provider_metadata_unverified",
            "dataset": window.dataset,
            "start_utc": window.start_utc,
            "stop_utc": window.stop_utc,
            "events": events,
            "event_sha256": digest(events),
            "biological_origin_independently_verified": False,
            "scope": "Provider threshold-crossing events; not identified single neurons",
        }


def stimulation_review_plan(
    *,
    session_id,
    source_digest,
    pattern_key,
    event_count,
    window_seconds,
    cooldown_seconds,
):
    """Prepare one aggregate event-window proposal, never a per-trade pulse train.

    Pattern keys name a lab-configured pattern; they do not specify electrical
    amplitudes or imply that a particular pattern is authorized or beneficial.
    """
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 96:
        raise ValueError("Assigned session identifier required")
    if (
        not isinstance(source_digest, str)
        or len(source_digest) != 64
        or any(c not in "0123456789abcdef" for c in source_digest)
    ):
        raise ValueError("Exact input-window digest required")
    if type(pattern_key) is not int or not 0 <= pattern_key < 16:
        raise ValueError("Pattern key must be in the documented 16-key mapping")
    if type(event_count) is not int or not 0 <= event_count <= 100000:
        raise ValueError("Bounded event count required")
    if type(window_seconds) not in (int, float) or not 1 <= window_seconds <= 300:
        raise ValueError("Aggregate window must be 1–300 seconds")
    if type(cooldown_seconds) not in (int, float) or not 1 <= cooldown_seconds <= 60:
        raise ValueError("Explicit lab-reviewable cooldown required")
    return {
        "schema": 1,
        "session_id": session_id,
        "source_sha256": source_digest,
        "observed_event_count": event_count,
        "window_seconds": window_seconds,
        "proposed_pattern_key": pattern_key if event_count else None,
        "proposed_trigger_count": int(event_count > 0),
        "cooldown_seconds": cooldown_seconds,
        "execution_enabled": False,
        "requires": [
            "Assigned and currently booked hardware",
            "Operator-reviewed electrical envelope, electrode mapping and artifact exclusion",
            "Confirmed SDK version and scoped cleanup procedure",
            "Separate durable execution limits and activation review",
        ],
        "scope": "Non-executable review artifact; token trades are not biological rewards",
    }

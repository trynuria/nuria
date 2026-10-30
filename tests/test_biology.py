"""Provenance, bounded protocol, held-out comparison and vendor query contracts."""

import asyncio
import copy
import json
import unittest
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from cognition.biological_session import (
    ReadWindow,
    RecordedSession,
    stimulation_review_plan,
)
from cognition.biology import (
    bridge,
    digest,
    sensory_drive,
    validate_recording,
    waveform_features,
    write_new,
)
from cognition.biology_comparison import load_course, ridge_readout, verify_comparison
from cognition.finalspark import PublicReader, SameOriginRedirect
from scripts.finalspark import capture_frames, import_recording

ROOT = Path(__file__).resolve().parents[1]


def fresh():
    path = ROOT / ".test-state" / ("biology-" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    return path


def recording(mode="synthetic_fixture", number=0):
    return {
        "schema": 1,
        "mode": mode,
        "provider": "test-fixture",
        "recording_id": "declared-fixture",
        "units": "fixture units",
        "provenance": "synthetic_fixture",
        "sample_rate_hz": 32,
        "channels": [0, 1],
        "samples": [
            [float(np.sin(i * 0.7 + number) * (number + 1)) for i in range(32)],
            [float(np.cos(i * 0.4 + number)) for i in range(32)],
        ],
        "received_utc": None,
        "acquired_utc": (
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=number)
        ).isoformat(),
    }


class Response:
    status = 200

    def __init__(self, body):
        self.body = body.encode()

    def read(self, count):
        return self.body[:count]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Transport:
    def __init__(self, bad=False):
        controller = {
            "_id": "public-test",
            "default_controller": True,
            "online": True,
            "maintenance": False,
            "electrode_layout": [
                {"mea_index": 0, "channel_index": c} for c in range(4)
            ],
        }
        self.responses = [
            json.dumps({"controllers": [controller]}),
            '{"numbersample":8}',
            '0{"sid":"private-transport-session"}',
            "ok",
            "40{}",
            "ok",
            '2\x1e42["data",' + json.dumps([1.0] * (31 if bad else 32)) + "]",
            "ok",
            "ok",
        ]
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append(req)
        return Response(self.responses.pop(0))


class RecordingTests(unittest.TestCase):
    def test_finite_channel_major_shape_and_identity(self):
        value = recording()
        self.assertEqual(validate_recording(value).shape, (2, 32))
        for mutate in (
            lambda r: r["samples"][0].pop(),
            lambda r: r["samples"][0].__setitem__(0, float("nan")),
            lambda r: r.__setitem__("channels", [0, 0]),
        ):
            broken = copy.deepcopy(value)
            mutate(broken)
            with self.assertRaises(ValueError):
                validate_recording(broken)

    def test_public_metadata_cannot_claim_rate_units_or_acquisition_time(self):
        value = recording("public_observed")
        value.update(
            sample_rate_hz=None,
            acquired_utc=None,
            units="unknown",
            provenance="public_endpoint_unattested",
            received_utc="2026-01-01T00:00:00+00:00",
        )
        validate_recording(value)
        for key, false_claim in (
            ("sample_rate_hz", 3750),
            ("acquired_utc", "2026-01-01T00:00:00Z"),
            ("units", "microvolts"),
            ("provenance", "verified"),
        ):
            with self.assertRaises(ValueError):
                validate_recording(dict(value, **{key: false_claim}))

    def test_source_features_do_not_become_spike_counts(self):
        value = recording()
        features = waveform_features(value)
        self.assertIsNone(features["spike_rate_hz"])
        drive = sensory_drive(value)
        self.assertLessEqual(np.max(np.abs(drive)), 0.18)
        self.assertTrue(np.any(drive[:128]))
        self.assertFalse(np.any(drive[128:]))

    def test_flat_signal_is_zero_drive_and_artifact_is_never_overwritten(self):
        value = recording()
        value["samples"] = [[0.0] * 32] * 2
        np.testing.assert_array_equal(sensory_drive(value), np.zeros(1024))
        path = fresh() / "recording.json"
        write_new(path, value)
        with self.assertRaises(FileExistsError):
            write_new(path, value)

    def test_npy_import_retains_declared_metadata(self):
        path, value = fresh(), recording()
        np.save(path / "array.npy", value.pop("samples"))
        write_new(path / "metadata.json", value)
        result = import_recording(
            path / "array.npy", path / "metadata.json", path / "recording.json"
        )
        self.assertEqual(result["mode"], "synthetic_fixture")
        self.assertFalse(result["biological_origin_independently_verified"])


class PublicProtocolTests(unittest.TestCase):
    def test_current_protocol_connect_ping_capture_close_and_replay_identity(self):
        transport, path = Transport(), fresh() / "capture"
        result = PublicReader(max_frames=1, open_request=transport).capture(path)
        self.assertTrue(result["completed"])
        self.assertTrue(result["disconnected"])
        self.assertEqual(len(capture_frames(path)), 1)
        posts = [r.data for r in transport.requests if r.data]
        self.assertIn(b"3", posts)
        self.assertEqual(posts[-1], b"41\x1e1")
        self.assertFalse(any(b"stim" in p or b"trigger" in p for p in posts))
        self.assertNotIn("private-transport-session", json.dumps(result))
        frame = path / "frame-001.json"
        value = json.loads(frame.read_bytes())
        value["samples"][0][0] = 999
        frame.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            capture_frames(path)

    def test_malformed_frame_retains_failure_and_disconnects(self):
        result = PublicReader(max_frames=1, open_request=Transport(bad=True)).capture(
            fresh() / "capture"
        )
        self.assertFalse(result["completed"])
        self.assertEqual(result["error_type"], "ValueError")
        self.assertTrue(result["disconnected"])

    def test_deadlines_and_budgets_are_checked_before_open(self):
        for kwargs in (
            {"max_frames": 0},
            {"seconds": 120},
            {"seconds": float("nan")},
            {"max_bytes": 2},
        ):
            with self.assertRaises(ValueError):
                PublicReader(**kwargs)
        transport = Transport()
        reader = PublicReader(open_request=transport)
        reader.started = 0
        with self.assertRaises(TimeoutError):
            reader._request("/sample")
        self.assertEqual(transport.requests, [])

    def test_cross_origin_redirect_is_refused(self):
        with self.assertRaises(ValueError):
            SameOriginRedirect().redirect_request(
                None, None, 302, "redirect", {}, "https://example.com/secret"
            )


class ComparisonTests(unittest.TestCase):
    def test_published_scores_replay_and_tampering_is_rejected(self):
        result_path = ROOT / "research/biology/synthetic-comparison-01.json"
        self.assertTrue(verify_comparison(result_path)["verified"])
        value = json.loads(result_path.read_bytes())
        value["methods"]["statistics_plus_brian2"]["test_mse"] += 0.01
        broken = fresh() / "tampered.json"
        write_new(broken, value)
        with self.assertRaises(ValueError):
            verify_comparison(broken)

    def course(self):
        rows = []
        for i in range(12):
            value = recording(number=i)
            start = datetime.fromisoformat(value["acquired_utc"])
            rows.append(
                {
                    "start_utc": start.isoformat(),
                    "stop_utc": (start + timedelta(seconds=1)).isoformat(),
                    "recording": value,
                    "target": float(i % 3),
                }
            )
        return {
            "schema": 1,
            "target_definition": "Synthetic software-test label; no biological outcome",
            "rows": rows,
        }

    def test_temporal_course_and_duplicates(self):
        path, value = fresh() / "course.json", self.course()
        write_new(path, value)
        self.assertEqual(len(load_course(path)["rows"]), 12)
        value["rows"][-1]["recording"]["samples"] = value["rows"][0]["recording"][
            "samples"
        ]
        second = path.with_name("duplicate.json")
        write_new(second, value)
        with self.assertRaises(ValueError):
            load_course(second)

    def test_test_targets_are_not_used_in_fit_and_normalization_is_train_only(self):
        train = np.arange(12, dtype=float).reshape(6, 2)
        validation, test = train[:3] + 50, train[:3] + 100
        a = ridge_readout(train, validation, test, np.arange(6), np.arange(3))
        b = ridge_readout(train, validation, test + 1000, np.arange(6), np.arange(3))
        self.assertEqual(a["weights"], b["weights"])
        self.assertEqual(a["train_mean"], train.mean(axis=0).tolist())

    def test_mixed_sources_and_overlap_are_refused(self):
        for key in ("recording_id", "overlap"):
            value, path = self.course(), fresh() / "course.json"
            if key == "overlap":
                value["rows"][1]["start_utc"] = value["rows"][0]["start_utc"]
            else:
                value["rows"][-1]["recording"][key] = "other-culture"
            write_new(path, value)
            with self.assertRaises(ValueError):
                load_course(path)


class SDKTests(unittest.TestCase):
    def test_provider_events_are_checked_against_requested_window(self):
        class Table:
            columns = ("Time", "channel")

            def __init__(self, channel):
                self.channel = channel

            def __len__(self):
                return 1

            def __getitem__(self, key):
                return self

            def to_dict(self, orient):
                return [{"Time": "2026-01-01T00:00:00.1Z", "channel": self.channel}]

        @dataclass
        class Query:
            start: datetime
            stop: datetime
            fsname: str

        class Controller:
            channel = 1

            @classmethod
            async def get_spike_event(cls, query):
                return Table(cls.channel)

        window = ReadWindow(
            "assigned-fixture", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z"
        )
        result = asyncio.run(RecordedSession(Controller, Query).read_events(window))
        self.assertEqual(result["events"][0]["channel"], 1)
        self.assertFalse(result["biological_origin_independently_verified"])
        Controller.channel = 128
        with self.assertRaises(ValueError):
            asyncio.run(RecordedSession(Controller, Query).read_events(window))

    def test_read_window_bounds_and_absent_data(self):
        with self.assertRaises(ValueError):
            ReadWindow("assigned", "2026-01-01T00:00:00Z", "2026-01-01T00:02:00Z")

        @dataclass
        class Query:
            start: datetime
            stop: datetime
            fsname: str

        class Controller:
            @staticmethod
            async def get_spike_event(query):
                self.assertEqual(query.fsname, "assigned")
                return None

        with self.assertRaises(ValueError):
            asyncio.run(
                RecordedSession(Controller, Query).read_events(
                    ReadWindow(
                        "assigned", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z"
                    )
                )
            )

    def test_stimulation_proposal_is_batched_and_cannot_execute(self):
        value = stimulation_review_plan(
            session_id="unassigned-fixture",
            source_digest=digest([1]),
            pattern_key=2,
            event_count=100000,
            window_seconds=60,
            cooldown_seconds=10,
        )
        self.assertEqual(value["proposed_trigger_count"], 1)
        self.assertFalse(value["execution_enabled"])
        self.assertFalse(hasattr(RecordedSession, "trigger"))


class BrianBridgeTests(unittest.TestCase):
    def test_isolated_matched_circuit_changes_from_real_measured_drive(self):
        result = bridge([recording()], fresh() / "bridge", duration_ms=50)
        self.assertTrue(result["matched_initial_state"])
        self.assertEqual(result["changed_windows"], 1)
        self.assertEqual(result["mode"], "synthetic_fixture")


if __name__ == "__main__":
    unittest.main()

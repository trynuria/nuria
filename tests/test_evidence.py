"""Evidence corruption and neural recovery regression checks."""

import json
import os
import time
import unittest
import uuid
import zlib
from pathlib import Path
from unittest.mock import patch

import numpy as np

from life import Life, canonical, digest

ROOT = Path(__file__).resolve().parents[1]


def fresh_directory():
    # Preserve test evidence for inspection; never reuse production directories.
    path = ROOT / ".test-state" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        os.environ.pop("NURIA_DATABASE", None)
        self.addCleanup(self.environment.stop)
        self.life = Life(fresh_directory(), "fixture-source")
        self.event = {
            "id": "fixture-input",
            "source": "test",
            "side": "buy",
            "quote_amount": 1.0,
        }
        self.life.enqueue(self.event)
        spikes = [[0.001, 7]]
        self.payload = {
            "input_ids": [self.event["id"]],
            "input_evidence_sha256": [digest(canonical(self.event))],
            "spikes_sha256": digest(canonical(spikes)),
        }
        self.write_receipt(self.payload, spikes)

    def write_receipt(self, payload, spikes):
        encoded = canonical(payload).decode()
        previous = "0" * 64
        current = digest(previous.encode() + encoded.encode())
        with self.life.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO receipts VALUES(?,?,?,?)",
                (1, previous, current, encoded),
            )
            db.execute(
                "INSERT OR REPLACE INTO spike_windows VALUES(?,?)",
                (1, zlib.compress(canonical(spikes))),
            )
            db.execute("UPDATE inputs SET receipt=1 WHERE id=?", (self.event["id"],))

    def test_valid_evidence_chain(self):
        self.assertTrue(self.life.verify()["valid"])

    def test_changed_input_is_detected(self):
        changed = dict(self.event, quote_amount=2.0)
        with self.life.db() as db:
            db.execute(
                "UPDATE inputs SET payload=? WHERE id=?",
                (canonical(changed).decode(), self.event["id"]),
            )
        self.assertFalse(self.life.verify()["valid"])

    def test_changed_spike_evidence_is_detected(self):
        with self.life.db() as db:
            db.execute(
                "UPDATE spike_windows SET data=? WHERE seq=1",
                (zlib.compress(canonical([[0.002, 8]])),),
            )
        self.assertEqual(self.life.verify()["reason"], "Spike evidence differs")

    def test_changed_parent_hash_is_detected(self):
        with self.life.db() as db:
            db.execute("UPDATE receipts SET previous_hash=? WHERE seq=1", ("f" * 64,))
        self.assertFalse(self.life.verify()["valid"])

    def test_missing_input_hash_is_detected(self):
        payload = dict(self.payload, input_evidence_sha256=[])
        self.write_receipt(payload, [[0.001, 7]])
        self.assertEqual(self.life.verify()["reason"], "Input evidence length differs")

    def test_invalid_amounts_are_rejected(self):
        for value in [0, -1, True, float("nan"), float("inf"), 1e10, "1"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.life.enqueue(dict(self.event, quote_amount=value))

    def test_inputs_are_idempotent(self):
        self.assertFalse(self.life.enqueue(self.event))


class ContinuityTests(unittest.TestCase):
    def test_saved_network_resumes_without_reset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NURIA_DATABASE", None)
            directory = fresh_directory()
            first = Life(directory, "fixture-source")
            first.start()
            try:
                deadline = time.monotonic() + 30
                while (
                    first.state.get("tick", 0) < 5
                    and first.state.get("phase") != "error"
                    and time.monotonic() < deadline
                ):
                    first.stop.wait(0.05)
                self.assertEqual(
                    first.state.get("phase"), "running", first.state.get("error")
                )
                self.assertGreaterEqual(first.state["tick"], 5)
            finally:
                first.stop.set()
                first.thread.join(10)
            self.assertFalse(first.thread.is_alive())
            before = (
                first.tick,
                float(first.net.t),
                first.genesis_utc,
                np.asarray(first.neurons.v[:]).copy(),
                np.asarray(first.exc.w[:]).copy(),
            )
            second = Life(directory, "fixture-source")
            second.build()
            self.assertEqual(second.tick, before[0])
            self.assertEqual(float(second.net.t), before[1])
            self.assertEqual(second.genesis_utc, before[2])
            np.testing.assert_array_equal(second.neurons.v[:], before[3])
            np.testing.assert_array_equal(second.exc.w[:], before[4])
            self.assertTrue(second.verify()["valid"])
            self.assertEqual(second.verify()["checked"], second.tick)

    def test_receipts_without_checkpoint_refuse_reset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NURIA_DATABASE", None)
            life = Life(fresh_directory(), "fixture-source")
            with life.db() as db:
                db.execute(
                    "INSERT INTO receipts VALUES(?,?,?,?)",
                    (1, "0" * 64, "f" * 64, json.dumps({})),
                )
            with self.assertRaisesRegex(RuntimeError, "automatic reset refused"):
                life.restore()

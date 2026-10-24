"""Bounded maintenance scans preserve integrity checks and honest progress."""

import json
import sqlite3
import subprocess
import sys
import unittest
import zlib
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from receipt_verifier import (
    ZERO,
    ChainChanged,
    Limits,
    ReceiptVerifier,
    VerificationError,
    VerificationUnavailable,
    canonical,
    digest,
)
from verify_worker import run

ROOT = Path(__file__).resolve().parents[1]


class Fixture:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            "CREATE TABLE receipts(seq INTEGER PRIMARY KEY,previous_hash TEXT,"
            "hash TEXT,payload TEXT);"
            "CREATE TABLE spike_windows(seq INTEGER PRIMARY KEY,data BLOB);"
            "CREATE TABLE inputs(id TEXT PRIMARY KEY,payload TEXT,receipt INTEGER);"
        )

    def add(self, *, body="sample", spikes=None):
        head = self.db.execute(
            "SELECT seq,hash FROM receipts ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        number, previous = (head[0] + 1, head[1]) if head else (1, ZERO)
        event = {"id": f"event-{number}", "body": body}
        spikes = [[0.001, number % 256]] if spikes is None else spikes
        raw_spikes = canonical(spikes)
        value = {
            "input_ids": [event["id"]],
            "input_evidence_sha256": [digest(canonical(event))],
            "spikes_sha256": digest(raw_spikes),
        }
        payload = canonical(value).decode()
        current = digest(previous.encode() + payload.encode())
        self.db.execute(
            "INSERT INTO receipts VALUES(?,?,?,?)", (number, previous, current, payload)
        )
        self.db.execute(
            "INSERT INTO spike_windows VALUES(?,?)",
            (number, zlib.compress(raw_spikes)),
        )
        self.db.execute(
            "INSERT INTO inputs VALUES(?,?,?)",
            (event["id"], canonical(event).decode(), number),
        )
        return number


class ReceiptVerifierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.db = self.fixture.db
        self.addCleanup(self.db.close)

    def fill(self, count, **kwargs):
        for _ in range(count):
            self.fixture.add(**kwargs)

    def test_multibatch_scan_has_fixed_target_and_no_early_success(self):
        self.fill(5)
        queries = []
        self.db.set_trace_callback(queries.append)
        verifier = ReceiptVerifier(batch_size=2)
        first = verifier.advance(self.db)
        self.assertEqual((first["checked"], first["target_seq"]), (2, 5))
        self.assertFalse(first["valid"])
        self.assertEqual(first["phase"], "catching_up")
        self.fixture.add()
        second = verifier.advance(self.db)
        self.assertEqual((second["checked"], second["target_seq"]), (4, 5))
        target_end = verifier.advance(self.db)
        self.assertEqual(target_end["checked"], 5)
        self.assertTrue(target_end["target_complete"])
        self.assertFalse(target_end["valid"])
        self.assertEqual(target_end["observed_head_seq"], 6)
        final = verifier.advance(self.db)
        self.assertEqual(final["phase"], "verified")
        self.assertTrue(final["valid"])
        self.assertEqual((final["checked"], final["target_seq"]), (6, 6))
        pages = [q for q in queries if "FROM receipts WHERE seq>" in q]
        self.assertEqual(len(pages), 4)
        self.assertTrue(all("LIMIT 2" in q for q in pages))

    def test_corrupt_parent_stops_at_last_good_receipt(self):
        self.fill(4)
        self.db.execute("UPDATE receipts SET previous_hash=? WHERE seq=3", ("f" * 64,))
        verifier = ReceiptVerifier(batch_size=4)
        with self.assertRaisesRegex(VerificationError, "chain mismatch") as caught:
            verifier.advance(self.db)
        status = verifier.failure(caught.exception)
        self.assertEqual((status["checked"], status["first_failure"]), (2, 3))
        self.assertFalse(status["valid"])
        self.assertEqual(status["phase"], "error")

    def test_changed_payload_and_missing_hash_are_rejected(self):
        self.fill(1)
        payload = json.loads(
            self.db.execute("SELECT payload FROM receipts WHERE seq=1").fetchone()[0]
        )
        payload["input_evidence_sha256"] = []
        encoded = canonical(payload).decode()
        self.db.execute("UPDATE receipts SET payload=? WHERE seq=1", (encoded,))
        with self.assertRaisesRegex(VerificationError, "chain mismatch"):
            ReceiptVerifier().advance(self.db)
        self.db.execute(
            "UPDATE receipts SET hash=? WHERE seq=1",
            (digest(ZERO.encode() + encoded.encode()),),
        )
        with self.assertRaisesRegex(VerificationError, "length mismatch"):
            ReceiptVerifier().advance(self.db)

    def test_corrupt_spikes_and_input_are_independently_rejected(self):
        self.fill(2)
        original = self.db.execute(
            "SELECT data FROM spike_windows WHERE seq=2"
        ).fetchone()[0]
        self.db.execute(
            "UPDATE spike_windows SET data=? WHERE seq=2",
            (zlib.compress(canonical([[0.002, 17]])),),
        )
        with self.assertRaisesRegex(VerificationError, "Spike evidence mismatch"):
            ReceiptVerifier().advance(self.db)
        self.db.execute("UPDATE spike_windows SET data=? WHERE seq=2", (original,))
        self.db.execute(
            "UPDATE inputs SET payload=? WHERE receipt=2", ('{"changed":1}',)
        )
        with self.assertRaisesRegex(VerificationError, "Input evidence mismatch"):
            ReceiptVerifier().advance(self.db)

    def test_missing_spikes_and_wrong_input_receipt_fail(self):
        self.fill(1)
        self.db.execute("UPDATE inputs SET receipt=2")
        with self.assertRaisesRegex(VerificationError, "Input evidence mismatch"):
            ReceiptVerifier().advance(self.db)
        self.db.execute("UPDATE inputs SET receipt=1")
        self.db.execute("DELETE FROM spike_windows")
        with self.assertRaisesRegex(VerificationError, "Spike evidence missing"):
            ReceiptVerifier().advance(self.db)

    def test_gap_cannot_skip_to_later_receipt(self):
        self.fill(3)
        self.db.execute("DELETE FROM receipts WHERE seq=2")
        verifier = ReceiptVerifier()
        with self.assertRaises(VerificationError) as caught:
            verifier.advance(self.db)
        self.assertEqual(verifier.failure(caught.exception)["first_failure"], 2)

    def test_head_regression_and_replacement_invalidate_cursor(self):
        self.fill(4)
        verifier = ReceiptVerifier(batch_size=2)
        verifier.advance(self.db)
        self.db.execute("DELETE FROM receipts WHERE seq>3")
        with self.assertRaisesRegex(ChainChanged, "regressed") as caught:
            verifier.advance(self.db)
        self.assertEqual(verifier.failure(caught.exception)["phase"], "unavailable")
        verifier.reset()
        verifier.advance(self.db)
        self.db.execute("UPDATE receipts SET hash=? WHERE seq=2", ("d" * 64,))
        with self.assertRaisesRegex(ChainChanged, "boundary changed"):
            verifier.advance(self.db)

    def test_restart_cannot_trust_public_or_unchecked_cursor(self):
        self.fill(4)
        first = ReceiptVerifier(batch_size=2)
        first.advance(self.db)
        second = ReceiptVerifier(batch_size=1)
        self.assertEqual(second.advance(self.db)["checked"], 1)

    def test_export_is_checked_prefix_and_tick_bounded(self):
        self.fill(8)
        verifier = ReceiptVerifier(
            batch_size=3, limits=replace(Limits(), export_ticks=2)
        )
        status = verifier.advance(self.db)
        exported = verifier.export(self.db, status)
        self.assertEqual([r["seq"] for r in exported["receipts"]], [2, 3])
        self.assertEqual({i["receipt"] for i in exported["inputs"]}, {2, 3})
        self.assertEqual(set(exported["spike_windows"]), {2, 3})
        self.assertEqual(exported["export"]["checked_end_seq"], 3)
        self.assertTrue(exported["export"]["truncated"])
        self.assertFalse(exported["integrity"]["valid"])

    def test_export_byte_bound_and_oversized_single_record_are_explicit(self):
        self.fill(5, body="data" * 200)
        limits = replace(Limits(), export_bytes=4096)
        verifier = ReceiptVerifier(limits=limits)
        status = verifier.advance(self.db)
        exported = verifier.export(self.db, status)
        self.assertLessEqual(
            len(json.dumps(exported, separators=(",", ":")).encode()), 4096
        )
        self.assertTrue(exported["export"]["truncated"])
        self.assertGreater(len(exported["receipts"]), 0)
        self.assertLess(len(exported["receipts"]), 5)
        smaller = ReceiptVerifier(limits=replace(Limits(), export_bytes=2048))
        self.fixture.add(body="large" * 1000)
        status = smaller.advance(self.db)
        exported = smaller.export(self.db, status)
        self.assertTrue(exported["export"]["truncated"])
        self.assertEqual(exported["receipts"], [])
        self.assertIn("reason", exported["export"])
        self.assertLessEqual(
            len(json.dumps(exported, separators=(",", ":")).encode()), 2048
        )

    def test_export_detects_evidence_change_after_scan(self):
        self.fill(2)
        verifier = ReceiptVerifier()
        status = verifier.advance(self.db)
        self.db.execute("UPDATE inputs SET payload='{}' WHERE receipt=1")
        with self.assertRaises(VerificationError) as caught:
            verifier.export(self.db, status)
        self.assertEqual(verifier.failure(caught.exception)["first_failure"], 1)

    def test_decompression_and_sql_payload_limits_fail_closed(self):
        self.fixture.add(spikes="a" * 5000)
        verifier = ReceiptVerifier(limits=replace(Limits(), spike_bytes=1024))
        with self.assertRaises(VerificationUnavailable) as caught:
            verifier.advance(self.db)
        self.assertEqual(verifier.failure(caught.exception)["phase"], "unavailable")
        self.assertEqual(verifier.seq, 0)
        limited = ReceiptVerifier(limits=replace(Limits(), receipt_bytes=64))
        with self.assertRaises(VerificationUnavailable):
            limited.advance(self.db)

    def test_unbounded_or_invalid_limits_cannot_enable_unbounded_query(self):
        for limit in (0, -1, True, 257):
            with self.subTest(batch=limit), self.assertRaises(ValueError):
                ReceiptVerifier(batch_size=limit)
        for field, value in (("export_ticks", -1), ("export_bytes", 2**40)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ReceiptVerifier(limits=replace(Limits(), **{field: value}))

    def test_canonical_bytes_preserve_original_contract(self):
        value = {"z": "é", "a": [1.0, False, None, -2]}
        self.assertEqual(canonical(value), b'{"a":[1.0,false,null,-2],"z":"\\u00e9"}')
        with self.assertRaises(ValueError):
            canonical(float("nan"))

    def test_worker_yields_briefly_while_catching_up(self):
        self.fill(5)

        @contextmanager
        def connect():
            yield self.db

        class Stop:
            def __init__(self):
                self.waits = []

            def is_set(self):
                return len(self.waits) == 3

            def wait(self, seconds):
                self.waits.append(seconds)

        stop = Stop()
        published = []
        with patch(
            "verify_worker.publish",
            side_effect=lambda p, n, v: published.append((n, v)),
        ):
            run(ROOT, connect, stop, batch_size=2)
        self.assertEqual(stop.waits, [0.05, 0.05, 10])
        statuses = [v for name, v in published if name == "verify.json"]
        self.assertEqual([v["valid"] for v in statuses], [False, False, True])

    def test_import_is_stdlib_only_and_does_not_start_service(self):
        program = (
            "import verify_worker,sys; "
            "assert not {'life','brian2','numpy','psycopg'} & set(sys.modules)"
        )
        subprocess.run([sys.executable, "-c", program], cwd=ROOT, check=True, timeout=5)

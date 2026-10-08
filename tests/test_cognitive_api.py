"""Cached public reads remain bounded, fresh and unable to mutate the model."""

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CognitiveApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.public = ROOT / ".test-state" / uuid.uuid4().hex
        for name in ("engine", "cognition", "treasury", "discovery", "commerce"):
            (cls.public / name).mkdir(parents=True, exist_ok=True)
        cls.state = {"phase": "running", "tick": 12, "updated_utc": "fixture"}
        for folder in ("engine", "cognition"):
            (cls.public / folder / "status.json").write_text(json.dumps(cls.state))
        (cls.public / "cognition" / "effects.json").write_text("[]")
        (cls.public / "discovery" / "status.json").write_text(json.dumps(cls.state))
        (cls.public / "commerce" / "status.json").write_text(
            json.dumps({"phase": "guarded", "financial_execution": False})
        )
        (cls.public / "commerce" / "index.json").write_text(
            json.dumps({"pages": 1, "events": 1})
        )
        (cls.public / "commerce" / "ledger-0.json").write_text(
            json.dumps({"page": 0, "records": [{"state": "fixture"}]})
        )
        with socket.socket() as channel:
            channel.bind(("127.0.0.1", 0))
            cls.port = channel.getsockname()[1]
        environment = {
            **os.environ,
            "NURIA_PUBLIC": str(cls.public),
            "NURIA_API_PORT": str(cls.port),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        cls.server = subprocess.Popen(
            [sys.executable, str(ROOT / "api.py")],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                cls.get("/healthz")
                break
            except Exception:
                time.sleep(0.02)
        else:
            cls.server.terminate()
            cls.server.wait(timeout=5)
            raise RuntimeError("Cached API did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)

    @classmethod
    def get(cls, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{cls.port}" + path, timeout=2
        ) as response:
            return json.load(response)

    def test_aggregate_health_includes_cognitive_worker(self):
        self.assertTrue(self.get("/healthz")["healthy"])
        self.assertEqual(self.get("/healthz")["cognitive_tick"], 12)
        self.assertIn("discovery_trial", self.get("/healthz"))

    def test_cognitive_evidence_is_read_only(self):
        self.assertEqual(self.get("/api/cognition/effects"), [])
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/cognition/effects",
            data=b"{}",
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(failure.exception.code, 405)

    def test_missing_evidence_is_unavailable(self):
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.get("/api/cognition/decisions")
        self.assertEqual(failure.exception.code, 503)

    def test_financial_pages_are_bounded_and_cannot_traverse_paths(self):
        self.assertEqual(self.get("/api/commerce/ledger?page=0")["page"], 0)
        self.assertEqual(self.get("/download/commerce?page=0")["page"], 0)
        for query, code in (
            ("page=../policy", 400),
            ("page=0&page=1", 400),
            ("page=99999999999999", 400),
            ("page=1", 404),
            ("page=-1", 400),
        ):
            with (
                self.subTest(query=query),
                self.assertRaises(urllib.error.HTTPError) as error,
            ):
                self.get("/api/commerce/ledger?" + query)
            self.assertEqual(error.exception.code, code)

    def test_discovery_is_cache_only_and_stale_is_unavailable(self):
        self.assertEqual(self.get("/api/discovery")["tick"], 12)
        path = self.public / "discovery" / "status.json"
        os.utime(path, (time.time() - 60, time.time() - 60))
        time.sleep(1.05)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.get("/api/discovery")
        self.assertEqual(failure.exception.code, 503)
        os.utime(path, None)

    def test_arbitrary_paths_are_not_filesystem_reads(self):
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.get("/api/cognition/../../README.md")
        self.assertEqual(failure.exception.code, 404)

    def test_commerce_only_publishes_cached_evidence(self):
        self.assertFalse(self.get("/api/commerce")["financial_execution"])
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/commerce", data=b"{}", method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(failure.exception.code, 405)

"""Qualification spending-boundary checks; no provider inference is performed."""

import time
import unittest
from types import SimpleNamespace

from scripts.evaluate_recovery_options import QualificationEnvelope


class RecoveryEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.initial = {
            "requests": 100,
            "accounted_cost_and_liability_micro_usd": 10_000_000,
            "blocked": False,
        }
        self.current = dict(self.initial)
        self.calls = 0
        self.provider = SimpleNamespace(
            budget=SimpleNamespace(snapshot=lambda: dict(self.current)),
            model="gpt-6-astra",
            effort="medium",
            input_rate=10_000_000,
            output_rate=50_000_000,
            configuration={"model": "gpt-6-astra"},
        )

        def complete(*args, **kwargs):
            self.calls += 1
            return {"content": "{}", "usage": {"total_tokens": 1}}

        self.delegate = SimpleNamespace(complete=complete)
        self.limits = {
            "calls": 12,
            "additional_micro_usd": 4_000_000,
            "wall_seconds": 1200,
        }

    def invoke(self, *, started=None):
        client = QualificationEnvelope(
            self.provider,
            self.delegate,
            self.initial,
            self.limits,
            time.time() if started is None else started,
        )
        return client.complete(
            [{"role": "user", "content": "Read enrolled data"}], max_tokens=2400
        )

    def test_full_next_request_ceiling_is_admitted_before_disclosure(self):
        self.current["accounted_cost_and_liability_micro_usd"] = 13_999_999
        with self.assertRaisesRegex(ValueError, "sublimit"):
            self.invoke()
        self.assertEqual(self.calls, 0)

    def test_request_count_pending_liability_and_old_wall_time_refuse(self):
        self.current["requests"] = 112
        with self.assertRaises(ValueError):
            self.invoke()
        self.current = {**self.initial, "blocked": True}
        with self.assertRaises(ValueError):
            self.invoke()
        self.current = dict(self.initial)
        with self.assertRaises(ValueError):
            self.invoke(started=time.time() - 1201)
        self.assertEqual(self.calls, 0)

    def test_valid_request_preserves_the_original_shared_baseline(self):
        before = dict(self.initial)
        self.assertEqual(self.invoke()["content"], "{}")
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.initial, before)


if __name__ == "__main__":
    unittest.main()

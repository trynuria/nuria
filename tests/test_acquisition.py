"""Delayed credit, refusal, retained evidence and acquisition source isolation."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cognition.acquisition import Acquisition
from scripts.evaluate_acquisition import PROTOCOL, trial


class AcquisitionTests(unittest.TestCase):
    def model(self):
        path = (
            Path(
                tempfile.mkdtemp(
                    prefix="acquisition-", dir=Path(__file__).parents[1] / ".test-state"
                )
            )
            / "research.sqlite3"
        )
        model = Acquisition(path, "synthetic:test")
        self.addCleanup(model.db.close)
        return path, model

    def test_delivery_precedes_delayed_feedback_and_credit_is_once_only(self):
        _, model = self.model()
        terms = model.choose("one", "uncertain", 0.5, {"A": 0.03}, 10)
        with self.assertRaises(ValueError):
            model.settle("one", 1, 30)
        prediction = 0.5 if terms["selection"] == "free" else 0.9
        model.deliver("one", prediction, 11)
        with self.assertRaises(ValueError):
            model.settle("one", 1, 11)
        result = model.settle("one", 1, 30)
        before = model.verify()
        self.assertEqual(model.settle("one", 1, 30), result)
        self.assertEqual(model.verify(), before)
        with self.assertRaises(ValueError):
            model.deliver("one", 0.1, 12)
        with self.assertRaises(ValueError):
            model.settle("one", 0, 31)
        self.assertEqual(model.verify(), before)

    def test_restart_preserves_pending_terms_estimates_and_next_choice(self):
        path, model = self.model()
        terms = model.choose("pending", "uncertain", 0.5, {"A": 0.03}, 10)
        model.deliver("pending", 0.5 if terms["selection"] == "free" else 0.9, 11)
        restored = Acquisition(path, "synthetic:test")
        self.addCleanup(restored.db.close)
        self.assertEqual(
            model.settle("pending", 1, 30), restored.settle("pending", 1, 30)
        )
        self.assertEqual(
            model.choose("next", "uncertain", 0.5, {"A": 0.03}, 40),
            restored.choose("next", "uncertain", 0.5, {"A": 0.03}, 40),
        )
        with self.assertRaises(ValueError):
            restored.choose("next", "uncertain", 0.5, {"A": 0.04}, 40)
        with self.assertRaises(ValueError):
            Acquisition(path, "synthetic:other")
        with self.assertRaises(ValueError):
            Acquisition(":memory:", "solana_finalized")

    def test_cap_free_refusal_and_failed_delivery_cost_are_separate(self):
        _, model = self.model()
        capped = model.choose("capped", "uncertain", 0.5, {"A": 0.11}, 0)
        self.assertEqual(capped["selection"], "free")
        self.assertEqual(capped["cost"], 0)
        selected = None
        for n in range(500):
            terms = model.choose(str(n), "uncertain", 0.5, {"A": 0.03}, 10 + n * 10)
            if terms["selection"] == "A":
                selected = str(n)
                break
        self.assertIsNotNone(selected)
        model.deliver(selected, None, terms["inputs"]["at"] + 1)
        result = model.settle(selected, 1, terms["inputs"]["at"] + 20)
        self.assertFalse(result["delivered"])
        self.assertEqual(result["gross_gain"], 0)
        self.assertEqual(result["net_gain"], -0.03)
        self.assertEqual(
            model.db.execute(
                "SELECT gain FROM estimates WHERE provider='A'"
            ).fetchone()[0],
            0,
        )
        model.verify()

    def test_invalid_data_and_failed_journal_write_cannot_mutate_choice(self):
        _, model = self.model()
        before = model.verify()
        for value in (True, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                model.choose("bad", "uncertain", value, {"A": 0.03}, 0)
        with patch.object(
            model, "event", side_effect=RuntimeError("fixture write failure")
        ):
            with self.assertRaises(RuntimeError):
                model.choose("rollback", "uncertain", 0.5, {"A": 0.03}, 0)
        self.assertEqual(model.verify(), before)
        self.assertEqual(
            model.db.execute("SELECT count(*) FROM trials").fetchone()[0], 0
        )

    def test_tampered_decisions_and_model_estimates_fail_restart_audit(self):
        path, model = self.model()
        model.choose("one", "uncertain", 0.5, {}, 0)
        model.db.execute("UPDATE trials SET terms=?", (json.dumps({"changed": True}),))
        with self.assertRaises(ValueError):
            model.verify()
        model.db.close()
        with self.assertRaises(ValueError):
            Acquisition(path, "synthetic:test")

    def test_paired_latent_world_and_useless_purchase_control(self):
        protocol = dict(PROTOCOL, opportunities=150, warmup=30)
        adaptive = trial(protocol, "useless", 301, "adaptive")
        free = trial(protocol, "useless", 301, "free")
        paid = trial(protocol, "useless", 301, "always_paid")
        self.assertEqual(adaptive["world_sha256"], free["world_sha256"])
        self.assertEqual(free["world_sha256"], paid["world_sha256"])
        self.assertEqual(free["net_gain"], 0)
        self.assertAlmostEqual(paid["net_gain"], -protocol["cost"])
        self.assertLessEqual(adaptive["net_gain"], 0)
        self.assertGreater(adaptive["net_gain"], paid["net_gain"])
        self.assertEqual(adaptive["journal"]["events"], 450)


if __name__ == "__main__":
    unittest.main()

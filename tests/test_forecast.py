"""Forecast timing, honest ablations, persistence and source isolation."""

import json
import sqlite3
import unittest

import numpy as np

from cognition.forecast import ForecastCouncil, diagnose
from cognition.memory import EpisodicMemory
from scripts.evaluate_forecasts import legacy_comparison, stream


class ForecastTests(unittest.TestCase):
    def test_scores_the_previous_forecast_before_training_on_its_outcome(self):
        rows = stream("amount_context", 31, 5, 3)
        model = ForecastCouncil()
        first = model.observe(rows[0])
        self.assertIsNone(first["feedback"])
        stored = model.pending["probability"]
        feedback = model.observe(rows[1])["feedback"]
        self.assertEqual(feedback["probability"], stored)
        self.assertEqual(
            feedback["loss"], (stored - int(rows[1]["side"] == "buy")) ** 2
        )
        self.assertEqual(model.metrics()["evaluated"], 1)

    def test_checkpoint_preserves_pending_prediction_attention_and_outcome_memory(self):
        rows = stream("context_switch", 31, 100, 50)
        model = ForecastCouncil()
        for row in rows[:90]:
            model.observe(row, 0.4)
        restored = ForecastCouncil(json.loads(json.dumps(model.state())))
        for row in rows[90:]:
            self.assertEqual(model.observe(row, 0.6), restored.observe(row, 0.6))
        self.assertEqual(model.state(), restored.state())

    def test_frozen_parameters_include_context_age_and_logistic_weights(self):
        rows = stream("context_switch", 31, 100, 50)
        model = ForecastCouncil()
        for row in rows[:50]:
            model.observe(row)
        before = json.loads(json.dumps(model.state()))
        for row in rows[50:]:
            model.observe(row, learn=False)
        after = model.state()
        for key in ("clock", "table", "logistic", "log_weights"):
            self.assertEqual(before[key], after[key])
        self.assertGreater(after["stats"]["n"], before["stats"]["n"])

    def test_source_switch_is_rejected_before_mutating_evaluation(self):
        rows = stream("amount_context", 31, 2, 1)
        model = ForecastCouncil()
        model.observe(rows[0])
        before = json.loads(json.dumps(model.state()))
        with self.assertRaises(ValueError):
            model.observe(dict(rows[1], source="live"))
        self.assertEqual(model.state(), before)

    def test_memory_and_attention_help_a_recurring_context_without_future_labels(self):
        rows = stream("amount_context", 31, 600, 300)
        full = ForecastCouncil()
        no_memory = ForecastCouncil(memory=False)
        uniform = ForecastCouncil(attention=False)
        scores = [[], [], []]
        for i, row in enumerate(rows):
            for model, values in zip((full, no_memory, uniform), scores):
                result = model.observe(row)
                if i > 200:
                    values.append(result["feedback"]["loss"])
        self.assertLess(np.mean(scores[0]), np.mean(scores[1]))
        self.assertLess(np.mean(scores[0]), np.mean(scores[2]))
        self.assertGreater(full.metrics()["attention"]["amount"], 0.5)

    def test_invalid_neural_probability_cannot_create_a_forecast(self):
        row = stream("amount_context", 31, 1, 1)[0]
        for value in (float("nan"), -1, 2):
            with self.assertRaises(ValueError):
                ForecastCouncil().observe(row, value)
        model = ForecastCouncil()
        model.observe(row)
        before = json.loads(json.dumps(model.state()))
        with self.assertRaises(ValueError):
            model.observe(dict(row, id="next"), float("nan"))
        self.assertEqual(model.state(), before)

    def test_warmup_is_the_same_number_of_outcomes_in_frozen_branch(self):
        model = ForecastCouncil()
        rows = stream("amount_context", 31, 80, 40)
        for i, row in enumerate(rows):
            model.observe(row, learn=i <= 20)
        self.assertEqual(model.updates, 20)
        self.assertEqual(model.metrics()["evaluated"], 80)

    def test_diagnostic_uses_recorded_source_and_reports_negative_results(self):
        rows = stream("unpredictable", 31, 99, 50)
        result = diagnose(rows)
        self.assertEqual(result["first_input"], rows[0]["id"])
        self.assertEqual(result["last_input"], rows[-1]["id"])
        self.assertEqual(result["scored"], len(rows) - len(rows) // 3 - 1)
        self.assertIn("context_logistic", result["brier"])
        with self.assertRaises(ValueError):
            diagnose(rows[:10])
        with self.assertRaises(ValueError):
            diagnose(rows[:-1] + [dict(rows[-1], source="live")])

    def test_legacy_correction_keeps_the_stronger_simple_predictor(self):
        for task in legacy_comparison():
            self.assertEqual(
                task["simple_predictor"]["accuracy"],
                task["published_neural"]["accuracy"],
            )
            self.assertLess(
                task["simple_predictor"]["brier"], task["published_neural"]["brier"]
            )

    def test_recall_can_exclude_other_sources(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        memory = EpisodicMemory(db)
        row = stream("amount_context", 31, 1, 1)[0]
        memory.add(1, dict(row, id="test", source="test"), [0.5] * 64, 1)
        memory.add(2, dict(row, id="live", source="pump"), [0.5] * 64, 1)
        found = memory.recall([0.5] * 64, memory.context(row), source="pump")
        self.assertEqual([item["id"] for item in found], ["live"])


if __name__ == "__main__":
    unittest.main()

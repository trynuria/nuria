"""Chronological learning, continuity, bounded memory and useful sequences."""

import copy
import json
import unittest

from cognition.adaptive_forecast import MAX_CELLS, MAX_LAG, VERSION, AdaptiveForecast
from cognition.forecast import ForecastCouncil
from scripts.evaluate_adaptive_memory import information_choice, stream


def rows(task="delayed_copy", seed=77, count=600):
    return stream(task, seed, count)[0]


class AdaptiveForecastTests(unittest.TestCase):
    def test_scores_stored_probability_before_learning_the_new_label(self):
        events = rows(count=3)
        model = AdaptiveForecast()
        self.assertIsNone(model.observe(events[0])["feedback"])
        prior = model.pending["probability"]
        observed = model.observe(events[1])
        self.assertEqual(observed["feedback"]["probability"], prior)
        self.assertEqual(
            observed["feedback"]["loss"], (prior - int(events[1]["side"] == "buy")) ** 2
        )
        self.assertEqual(model.metrics()["trained_transitions"], 1)

    def test_ordered_memory_learns_a_delayed_relation(self):
        models = [AdaptiveForecast(), AdaptiveForecast(sequence=False)]
        losses = [[], []]
        for index, event in enumerate(rows(count=900)):
            for model, scores in zip(models, losses):
                observed = model.observe(event)
                if index > 300:
                    scores.append(observed["feedback"]["loss"])
        self.assertLess(sum(losses[0]) / len(losses[0]), 0.16)
        self.assertLess(sum(losses[0]), sum(losses[1]) - 20)

    def test_legacy_migration_retains_parameters_pending_prediction_and_metrics(self):
        events = rows("stable_context", count=100)
        old = ForecastCouncil()
        for event in events[:80]:
            old.observe(event, 0.4)
        old_state = json.loads(json.dumps(old.state()))
        upgraded = AdaptiveForecast(old_state)
        self.assertEqual(upgraded.base.state(), old_state)
        self.assertEqual(upgraded.metrics()["evaluated"], 0)
        self.assertEqual(upgraded.metrics()["legacy_metrics"]["evaluated"], 79)
        self.assertIsNone(upgraded.observe(events[80], 0.6)["feedback"])
        old.observe(events[80], 0.6)
        self.assertEqual(upgraded.base.state(), old.state())
        upgraded.observe(events[81], 0.3)
        self.assertEqual(upgraded.metrics()["evaluated"], 1)
        self.assertEqual(upgraded.metrics()["legacy_metrics"]["evaluated"], 81)
        self.assertEqual(old_state["stats"]["n"], 79)

    def test_json_checkpoint_continues_exactly_with_pending_predictions(self):
        events = rows("lag_switch", count=500)
        model = AdaptiveForecast()
        for event in events[:380]:
            model.observe(event, 0.45)
        restored = AdaptiveForecast(json.loads(json.dumps(model.state())))
        for index, event in enumerate(events[380:]):
            options = {"learn": index % 3 != 1}
            self.assertEqual(
                model.observe(event, 0.6, **options),
                restored.observe(event, 0.6, **options),
            )
        self.assertEqual(model.state(), restored.state())

    def test_foreign_or_invalid_input_cannot_mutate_learning(self):
        events = rows(count=3)
        model = AdaptiveForecast()
        model.observe(events[0])
        before = model.state()
        for event, probability in [
            ({**events[1], "source": "foreign"}, 0.5),
            ({**events[1], "quote_amount": float("nan")}, 0.5),
            (events[1], float("nan")),
            (events[1], -0.1),
            (events[1], 1.1),
        ]:
            with self.assertRaises(ValueError):
                model.observe(event, probability)
            self.assertEqual(model.state(), before)

    def test_frozen_learning_retains_parameters_but_advances_context(self):
        events = rows("lag_switch", count=450)
        model = AdaptiveForecast()
        for event in events[:300]:
            model.observe(event)
        before = model.state()
        for event in events[300:]:
            model.observe(event, learn=False)
        after = model.state()
        for key in ("banks", "mixture", "clock", "updates"):
            self.assertEqual(before[key], after[key])
        for key in ("table", "logistic", "log_weights", "clock", "updates"):
            self.assertEqual(before["base"][key], after["base"][key])
        self.assertGreater(after["stats"]["n"], before["stats"]["n"])
        self.assertNotEqual(before["observed_sides"], after["observed_sides"])

    def test_memory_bound_survives_varied_long_streams(self):
        model = AdaptiveForecast()
        for event in rows("iid_fair", count=6000):
            model.observe(event)
        self.assertLessEqual(len(model.observed_sides), MAX_LAG)
        self.assertLessEqual(model.metrics()["memory_cells"], 2 * MAX_CELLS)
        self.assertLess(len(json.dumps(model.state())), 500000)

    def test_checkpoint_configuration_corruption_and_future_clocks_are_refused(self):
        model = AdaptiveForecast()
        for event in rows(count=20):
            model.observe(event)
        original = model.state()
        with self.assertRaises(ValueError):
            AdaptiveForecast(original, fast=False)
        alterations = [
            lambda s: s.update(version="unrecognized"),
            lambda s: s.update(mixture=[1.0]),
            lambda s: s.update(observed_sides=[0] * 17),
            lambda s: s["pending"].update(probability=float("nan")),
            lambda s: s["banks"]["slow"]["cells"].update(
                buy=[1.0, 2.0, s["clock"] + 1]
            ),
        ]
        for mutate in alterations:
            bad = copy.deepcopy(original)
            mutate(bad)
            with self.assertRaises(ValueError):
                AdaptiveForecast(bad)

    def test_task_names_event_ids_and_hidden_tags_do_not_drive_predictions(self):
        a, b = AdaptiveForecast(), AdaptiveForecast()
        for event in rows("parity", count=100):
            altered = {
                **event,
                "id": "other-" + event["id"],
                "task": "secret",
                "phase": 999,
                "oracle_probability": 1.0,
            }
            self.assertEqual(
                a.observe(event)["prediction_buy"], b.observe(altered)["prediction_buy"]
            )

    def test_state_exports_and_constructor_do_not_alias_the_callers_data(self):
        model = AdaptiveForecast()
        for event in rows(count=20):
            model.observe(event)
        exported = model.state()
        before = model.state()
        restored = AdaptiveForecast(exported)
        exported["base"]["stats"]["n"] = 0
        exported["pending"]["probability"] = 0
        self.assertEqual(restored.state(), before)
        self.assertEqual(model.state(), before)

    def test_information_choice_is_made_without_the_next_label_or_measurement(self):
        offer = {"quality": 0.9, "cost": 0.15}
        for probability in (0.5, 0.8, 0.99):
            decisions = {
                information_choice(probability, offer, measurement, outcome)[0]
                for measurement in (0, 1)
                for outcome in (0, 1)
            }
            self.assertEqual(len(decisions), 1)
        self.assertTrue(information_choice(0.5, offer, 0, 0)[0])
        self.assertFalse(information_choice(0.99, offer, 0, 0)[0])

    def test_new_version_has_distinct_metrics_and_real_update_count(self):
        events = rows(count=3)
        model = AdaptiveForecast()
        model.observe(events[0], learn=False)
        model.observe(events[1], learn=True)
        self.assertEqual(model.metrics()["trained_transitions"], 1)
        self.assertEqual(model.metrics()["version"], VERSION)
        self.assertEqual(model.metrics()["started_input"], events[0]["id"])


if __name__ == "__main__":
    unittest.main()

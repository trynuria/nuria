"""Partial-observation boundaries, outcome selection and independent replay."""

import importlib.util
import json
import os
import unittest
import uuid
from pathlib import Path

import numpy as np

from cognition.active_observation import (
    SENTINELS,
    RecordPicker,
    SentinelHistory,
    candidate_predictions,
    capture,
    choose_units,
    evaluate,
    feature_map,
    fit_ridge,
    history_features,
    predict_ridge,
    select,
    sentinel_values,
    verify,
)
from cognition.biology import write_new
from cognition.nwb_spikes import import_nwb

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "research/biology/acquisition-01"


class ObservationTests(unittest.TestCase):
    def test_policy_boundary_rejects_full_windows_and_invalid_sentinels(self):
        for bad in (
            np.zeros(49),
            [-1, 0, 0, 0],
            [0.1, 0, 0, 0],
            [float("nan"), 0, 0, 0],
        ):
            with self.assertRaises(ValueError):
                sentinel_values(bad)

    def test_features_are_causal_and_unobserved_units_do_not_enter(self):
        counts = np.random.default_rng(7).poisson(1, (20, 49))
        first = history_features(counts[:, SENTINELS])
        changed = counts.copy()
        changed[10:] += 7
        changed[:, [i for i in range(49) if i not in SENTINELS]] += 50
        actual = history_features(changed[:, SENTINELS])
        np.testing.assert_array_equal(first[:10], actual[:10])
        expected = np.r_[np.zeros(16), np.log1p(counts[0, SENTINELS])]
        np.testing.assert_array_equal(first[0], expected)

    def test_streamed_history_matches_batch_and_returns_owned_snapshot(self):
        counts = np.arange(40).reshape(10, 4)
        batch = history_features(counts)
        state = SentinelHistory()
        snapshots = [state.observe(row) for row in counts]
        np.testing.assert_array_equal(batch, snapshots)
        state.observe([0, 0, 0, 0])
        np.testing.assert_array_equal(batch[0], snapshots[0])

    def test_actions_have_fixed_budget_no_duplicate_reads_and_stable_ties(self):
        chosen = choose_units(np.zeros(49))
        self.assertEqual(len(chosen), 12)
        self.assertFalse(set(chosen) & set(SENTINELS))
        self.assertEqual(
            chosen.tolist(), [i for i in range(49) if i not in SENTINELS][:12]
        )
        with self.assertRaises(ValueError):
            capture(np.ones((2, 49), dtype=int), np.zeros((2, 12), dtype=int))
        with self.assertRaises(ValueError):
            choose_units(np.full(49, float("inf")))

    def test_captured_counts_come_only_from_actual_selected_records(self):
        counts = np.arange(98).reshape(2, 49)
        actions = np.array([choose_units(np.arange(49))] * 2)
        result = capture(counts, actions)
        expected = sum(
            int(counts[t, list(SENTINELS) + actions[t].tolist()].sum())
            for t in range(2)
        )
        self.assertEqual(result["acquired_spikes"], expected)
        self.assertEqual(result["unit_records_read"], 32)
        self.assertEqual(result["available_unit_records"], 98)
        np.testing.assert_array_equal(result["actions"], actions)

    def test_training_outcomes_actually_change_the_decoder(self):
        x = np.arange(60).reshape(20, 3) / 10
        y = np.tile(np.sin(x[:, :1]), (1, 49))
        fit = fit_ridge(x, y, 1)
        changed = fit_ridge(x, y + 2, 1)
        self.assertFalse(np.allclose(fit["weights"], changed["weights"]))
        np.testing.assert_allclose(predict_ridge(changed, x) - predict_ridge(fit, x), 2)

    def test_quadratic_and_neural_maps_add_no_new_observations(self):
        h = np.arange(80).reshape(4, 20).astype(float)
        q = feature_map(h, None, "quadratic_history")
        self.assertEqual(q.shape, (4, 230))
        np.testing.assert_array_equal(q[:, :20], h)
        n = np.ones((4, 128))
        np.testing.assert_array_equal(
            feature_map(h, n, "history_plus_spiking")[:, 20:], n
        )
        with self.assertRaises(ValueError):
            feature_map(h, np.ones((3, 128)), "history_plus_spiking")

    def test_nearest_neighbors_uses_only_training_outcomes(self):
        fit = {
            "reference": np.array([[0] * 20, [1] * 20, [2] * 20]).tolist(),
            "targets": np.array([[0] * 49, [3] * 49, [6] * 49]).tolist(),
            "scale": [1] * 20,
        }
        predictions = candidate_predictions(
            "sentinel_knn", np.zeros((2, 20)), None, None, fit, 3
        )
        np.testing.assert_array_equal(predictions, np.full((2, 49), 3))

    def test_selection_refuses_to_receive_the_test_interval(self):
        for windows in (320, 481, 720):
            with self.assertRaises(ValueError):
                select(np.zeros((windows, 49)), np.zeros((windows, 128)))


@unittest.skipUnless(PACKAGE.exists(), "Retained experiment package is not present")
class RetainedEvidenceTests(unittest.TestCase):
    def test_unobserved_test_records_cannot_change_any_policy_action(self):
        selection = json.loads((PACKAGE / "selection.json").read_bytes())
        binned = json.loads((PACKAGE / "binned.json").read_bytes())
        neural = json.loads((PACKAGE / "neural.json").read_bytes())["features"]
        original = evaluate(binned, neural, selection)
        counts = np.asarray(binned["counts"])
        hidden = [i for i in range(49) if i not in SENTINELS]
        counts[480:, hidden] = counts[480:, hidden[::-1]]
        binned["counts"] = counts.tolist()
        # An explicitly altered test fixture preserves every population total.
        changed = evaluate(binned, neural, selection)
        for method in original["methods"]:
            self.assertEqual(
                original["methods"][method]["actions"],
                changed["methods"][method]["actions"],
            )
        self.assertNotEqual(
            original["methods"]["sentinel_knn"]["acquired_spikes"],
            changed["methods"]["sentinel_knn"]["acquired_spikes"],
        )

    @unittest.skipUnless(
        importlib.util.find_spec("pynwb") is not None
        and os.environ.get("NURIA_DANDI_NWB"),
        "Set NURIA_DANDI_NWB with the optional PyNWB environment",
    )
    def test_all_180_seconds_reproduce_from_the_original_recording(self):
        folder = ROOT / ".test-state" / ("acquisition-nwb-" + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        path = folder / "binned.json"
        import_nwb(Path(os.environ["NURIA_DANDI_NWB"]), path, seconds=180)
        self.assertEqual(
            json.loads(path.read_bytes()),
            json.loads((PACKAGE / "binned.json").read_bytes()),
        )

    def test_streaming_picker_reproduces_every_committed_action_without_extra_counts(
        self,
    ):
        selection = json.loads((PACKAGE / "selection.json").read_bytes())
        binned = json.loads((PACKAGE / "binned.json").read_bytes())
        result = json.loads((PACKAGE / "result.json").read_bytes())
        counts = np.asarray(binned["counts"])
        picker = RecordPicker(selection, counts[:480])
        decisions = [picker.observe(row[list(SENTINELS)]) for row in counts]
        actual = [record["extra_unit_ids"] for record in decisions[480:]]
        expected = result["methods"][selection["selected_method"]]["actions"]
        self.assertEqual(actual, expected)
        self.assertEqual([d["window"] for d in decisions], list(range(720)))
        with self.assertRaises(ValueError):
            picker.observe([0, 0, 0, 0])
        modified = counts[:480].copy()
        modified[0, 1] += 1
        with self.assertRaises(ValueError):
            RecordPicker(selection, modified)

    def test_actual_acquisition_results_reconstruct(self):
        self.assertTrue(verify(PACKAGE)["verified"])

    def test_a_changed_acquisition_or_outcome_is_rejected(self):
        folder = ROOT / ".test-state" / ("acquisition-" + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        for name in ("protocol", "selection", "binned", "neural", "result"):
            value = json.loads((PACKAGE / (name + ".json")).read_bytes())
            if name == "result":
                value["methods"][value["selected_method"]]["acquired_spikes"] += 1
            write_new(folder / (name + ".json"), value)
        with self.assertRaises(ValueError):
            verify(folder)


if __name__ == "__main__":
    unittest.main()

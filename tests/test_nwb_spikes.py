"""Recorded-data identity, conserved import and causal held-out evaluation."""

import importlib.util
import json
import os
import unittest
import uuid
from pathlib import Path

import numpy as np

from cognition.biology import write_new
from cognition.nwb_spikes import import_nwb, validate_binned
from cognition.organoid_replay import design, drive_for_counts, score, verify

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "research/biology/dandi-01"


def fresh():
    path = ROOT / ".test-state" / ("nwb-" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    return path


def observed():
    return json.loads((PACKAGE / "binned.json").read_bytes())


class BinnedDataTests(unittest.TestCase):
    def test_recorded_human_subset_preserves_all_units_and_counts(self):
        data = observed()
        counts = validate_binned(data)
        self.assertEqual(counts.shape, (480, 49))
        self.assertEqual(int(counts.sum()), 3089)
        self.assertEqual(data["source_total_spikes"], 4721)
        self.assertEqual(data["excluded_outside_interval_spikes"], 1632)
        self.assertFalse(data["live"])
        self.assertEqual(data["source"]["species"], "Homo sapiens")

    def test_invalid_counts_source_and_missing_timing_are_refused(self):
        for change in (
            lambda d: d["counts"][0].__setitem__(0, -1),
            lambda d: d.__setitem__("included_spikes", 0),
            lambda d: d.__setitem__("live", True),
            lambda d: d["source"].__setitem__("species", "Mus musculus"),
            lambda d: d.__setitem__("stop_seconds", 121),
        ):
            data = observed()
            change(data)
            with self.assertRaises(ValueError):
                validate_binned(data)

    def test_drive_preserves_order_and_never_reaches_other_populations(self):
        drive = drive_for_counts([0, 1, 3, 9])
        values = [drive[i] for i in (0, 32, 64, 96)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(drive[0], 0)
        self.assertLessEqual(drive.max(), 0.18)
        self.assertFalse(np.any(drive[128:]))
        for bad in ([float("nan")], [-1], np.zeros(129)):
            with self.assertRaises(ValueError):
                drive_for_counts(bad)

    @unittest.skipUnless(
        importlib.util.find_spec("pynwb") is not None
        and os.environ.get("NURIA_DANDI_NWB"),
        "Set NURIA_DANDI_NWB with the optional PyNWB environment",
    )
    def test_actual_nwb_import_reproduces_published_counts(self):
        path = fresh() / "binned.json"
        import_nwb(Path(os.environ["NURIA_DANDI_NWB"]), path)
        self.assertEqual(json.loads(path.read_bytes()), observed())

    def test_wrong_raw_file_is_rejected_before_vendor_reader(self):
        path = fresh() / "wrong.nwb"
        path.write_bytes(b"not the pinned original")
        with self.assertRaises(ValueError):
            import_nwb(path, path.with_suffix(".json"))


class EvaluationTests(unittest.TestCase):
    def test_future_counts_cannot_change_past_features(self):
        counts = validate_binned(observed())
        original, targets, origins = design(counts)
        changed = counts.copy()
        changed[100:] += 7
        actual, _, _ = design(changed)
        np.testing.assert_array_equal(original[origins < 100], actual[origins < 100])
        self.assertEqual(targets[0], np.log1p(counts[4].sum()))
        self.assertEqual(origins[0], 3)

    def test_baseline_fitting_does_not_use_test_targets(self):
        counts = validate_binned(observed())
        neural = {
            name: np.zeros((len(counts), 64))
            for name in ("history_plus_recording_drive", "history_plus_zero_drive")
        }
        x, _, _ = design(counts)
        first = score(x, neural, counts)
        changed = counts.copy()
        changed[400:] += 2
        x2, _, _ = design(changed)
        second = score(x2, neural, changed)
        self.assertEqual(
            first["selected_simple_baseline"], second["selected_simple_baseline"]
        )
        for name in ("ridge_history", *neural):
            self.assertEqual(
                first["methods"][name]["fit"]["weights"],
                second["methods"][name]["fit"]["weights"],
            )
            self.assertEqual(
                first["methods"][name]["validation_mse"],
                second["methods"][name]["validation_mse"],
            )

    def test_public_readouts_reconstruct_and_score_tampering_fails(self):
        self.assertTrue(
            verify(PACKAGE / "binned.json", PACKAGE / "result.json")["verified"]
        )
        result = json.loads((PACKAGE / "result.json").read_bytes())
        result["methods"]["history_plus_recording_drive"]["test_mse"] += 0.01
        path = fresh() / "tampered.json"
        write_new(path, result)
        with self.assertRaises(ValueError):
            verify(PACKAGE / "binned.json", path)

    def test_source_digest_and_test_indices_are_checked(self):
        for key in ("binned_sha256", "target_bin_indices", "changed_spike_windows"):
            result = json.loads((PACKAGE / "result.json").read_bytes())
            result[key] = (
                "0" * 64
                if key == "binned_sha256"
                else ([0] if key == "target_bin_indices" else 0)
            )
            path = fresh() / "wrong-claim.json"
            write_new(path, result)
            with self.assertRaises(ValueError):
                verify(PACKAGE / "binned.json", path)


if __name__ == "__main__":
    unittest.main()

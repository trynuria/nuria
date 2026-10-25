"""Numeric witness integrity and unsafe-array refusal, without checkpoint loads."""

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "weight_delta_replay", HERE / "verify_weight_delta.py"
)
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)
WORK = Path(
    os.environ.get(
        "NURIA_WORK_EVIDENCE", str(Path.cwd() / "research/hybrid-agent/work-trials")
    )
)


class WeightDeltaTests(unittest.TestCase):
    def test_original_numeric_witness(self):
        before = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in HERE.iterdir()
            if p.is_file()
        }
        result = replay.verify(HERE, WORK)
        self.assertTrue(result["passed"])
        self.assertEqual(result["synapses"], 22920)
        self.assertEqual(result["changed_synapses"], 205)
        self.assertFalse(result["pickle_loaded_by_this_verifier"])
        self.assertFalse(result["dynamics_reexecuted"])
        self.assertEqual(
            before,
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in HERE.iterdir()
                if p.is_file()
            },
        )

    def test_changed_numeric_state_no_longer_matches_source(self):
        original = replay.read_arrays

        def changed(directory, record):
            arrays = original(directory, record)
            arrays["after_plastic_w"] = arrays["after_plastic_w"].copy()
            arrays["after_plastic_w"][0] += 0.00001
            return arrays

        with (
            patch.object(replay, "read_arrays", side_effect=changed),
            self.assertRaisesRegex(ValueError, "Numeric projected-state identity"),
        ):
            replay.verify(HERE, WORK)

    def test_credit_cannot_use_duplicate_or_other_indices(self):
        original = replay.read_arrays

        def changed(directory, record):
            arrays = original(directory, record)
            arrays["origin_indices"] = arrays["origin_indices"].copy()
            arrays["origin_indices"][0] = arrays["origin_indices"][1]
            return arrays

        with (
            patch.object(replay, "read_arrays", side_effect=changed),
            self.assertRaisesRegex(ValueError, "Numeric eligibility"),
        ):
            replay.verify(HERE, WORK)

    def test_wrong_original_archive_refused(self):
        derivation = json.loads((HERE / "derivation.json").read_bytes())
        derivation["source_archive_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Original work archive identity"):
            replay.source_records(WORK, derivation)

    def test_object_array_rejected_before_loading(self):
        derivation = json.loads((HERE / "derivation.json").read_bytes())
        arrays = replay.read_arrays(HERE, derivation["arrays"])
        arrays["origin_indices"] = np.asarray([0] * 205, dtype=object)
        # This controlled fixture is retained; the verifier must reject its header
        # before allowing NumPy to deserialize any object values.
        directory = Path(tempfile.mkdtemp(prefix="nuria-weight-object-fixture-"))
        np.savez_compressed(directory / "arrays.npz", **arrays)
        raw = (directory / "arrays.npz").read_bytes()
        record = dict(
            derivation["arrays"], bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()
        )
        with self.assertRaisesRegex(ValueError, "Unsafe numeric array header"):
            replay.read_arrays(directory, record)


if __name__ == "__main__":
    unittest.main()

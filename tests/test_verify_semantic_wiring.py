"""Retained real observations, without additional model or Brian2 execution."""

import gzip
import hashlib
import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cognition.semantic_encoder import semantic_projection
from scripts.verify_semantic_wiring import CURRENT_ATOL, compare_currents, verify


class VerifySemanticWiringTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.original = root / "research/semantic-input"
        self.copy = root / ".test-state" / ("semantic-proof-" + uuid.uuid4().hex)
        shutil.copytree(self.original, self.copy)

    def test_complete_twelve_encode_four_window_bundle_reproduces(self):
        result = verify(self.original)
        self.assertEqual(result["recorded_encodes"], 12)
        self.assertEqual(result["recorded_neural_windows"], 4)
        self.assertEqual(result["new_model_calls"], 0)

    def test_modified_compressed_observation_is_refused(self):
        path = self.copy / "evidence/semantic-output.json.gz"
        raw = bytearray(path.read_bytes())
        raw[-12] ^= 1
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify(self.copy)

    def test_missing_observation_refused_even_after_manifest_checksum_update(self):
        path = self.copy / "evidence/semantic-output.json.gz"
        output = json.loads(gzip.decompress(path.read_bytes()))
        output["vectors"].pop("invoice-a")
        raw = (json.dumps(output, indent=2) + "\n").encode()
        path.write_bytes(gzip.compress(raw, mtime=0))
        manifest_path = self.copy / "evidence-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        item = next(
            row
            for row in manifest["files"]
            if row["path"] == "evidence/semantic-output.json.gz"
        )
        item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        item["uncompressed_sha256"] = hashlib.sha256(raw).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            verify(self.copy)

    def test_only_bounded_float32_rounding_is_allowed(self):
        actual = {
            "regional_drive": [0.1] * 128 + [0.0] * 896,
            "context": {"memory_features": [0.5] * 64, "salience": 1, "energy": 1},
        }
        recorded = json.loads(json.dumps(actual))
        recorded["context"]["memory_features"][0] += CURRENT_ATOL / 2
        self.assertEqual(compare_currents(actual, recorded), CURRENT_ATOL / 2)
        recorded["context"]["memory_features"][0] += 0.0001
        with self.assertRaisesRegex(ValueError, "agreement bound"):
            compare_currents(actual, recorded)

    def test_zero_channels_and_scalar_controls_remain_exact(self):
        actual = {
            "regional_drive": [0.0] * 1024,
            "context": {"memory_features": [0.5] * 64, "salience": 1, "energy": 1},
        }
        recorded = json.loads(json.dumps(actual))
        recorded["regional_drive"][-1] = CURRENT_ATOL / 100
        with self.assertRaisesRegex(ValueError, "agreement bound"):
            compare_currents(actual, recorded)
        recorded["regional_drive"][-1] = 0
        recorded["context"]["salience"] += CURRENT_ATOL / 100
        with self.assertRaisesRegex(ValueError, "scalar controls"):
            compare_currents(actual, recorded)

    def test_different_projection_refused_before_numerical_tolerance(self):
        matrix = semantic_projection()
        matrix[0, 0] *= -1
        with patch(
            "scripts.verify_semantic_wiring.semantic_projection", return_value=matrix
        ):
            with self.assertRaisesRegex(ValueError, "exact matrix"):
                verify(self.original)


if __name__ == "__main__":
    unittest.main()

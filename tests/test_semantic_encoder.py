"""Scripted ONNX/tokenizer fixtures and numeric checks; no model is loaded."""

import copy
import hashlib
import json
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from cognition import semantic_encoder as semantic


class FixtureTokenizer:
    def __init__(self):
        self.questions = []

    def no_padding(self):
        pass

    def no_truncation(self):
        pass

    def encode(self, question, *, add_special_tokens):
        self.questions.append(question)
        count = len(question.split()) + 2
        return SimpleNamespace(
            ids=list(range(count)), attention_mask=[1] * count, type_ids=[0] * count
        )


class FixtureSession:
    def __init__(self, *, providers=None, error=False):
        self.providers = ["CPUExecutionProvider"] if providers is None else providers
        self.error = error
        self.calls = []

    def get_providers(self):
        return self.providers

    def get_inputs(self):
        return [
            SimpleNamespace(name=name, type="tensor(int64)")
            for name in ("input_ids", "attention_mask", "token_type_ids")
        ]

    def get_outputs(self):
        return [SimpleNamespace(name="last_hidden_state")]

    def run(self, outputs, inputs):
        self.calls.append(copy.deepcopy(inputs))
        if self.error:
            raise RuntimeError("Scripted runtime failure")
        count = inputs["input_ids"].shape[1]
        values = np.zeros((1, count, 384), dtype=np.float32)
        values[0, :, 0] = 1
        return [values]


class SemanticEncoderTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("semantic-encoder-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)
        weights, tokenizer = (
            b"Inert scripted fixture, not an ONNX model",
            b"Inert tokenizer fixture",
        )
        (self.root / "weights.onnx").write_bytes(weights)
        (self.root / "tokenizer.json").write_bytes(tokenizer)
        self.manifest = {
            "schema": semantic.VERSION,
            "model": "fixture/not-a-pretrained-model",
            "revision": "0" * 40,
            "model_file": {
                "local_path": "weights.onnx",
                "bytes": len(weights),
                "sha256": hashlib.sha256(weights).hexdigest(),
            },
            "tokenizer_file": {
                "local_path": "tokenizer.json",
                "bytes": len(tokenizer),
                "git_blob_sha1": hashlib.sha1(
                    b"blob " + str(len(tokenizer)).encode() + b"\0" + tokenizer
                ).hexdigest(),
            },
            "dimensions": 384,
            "maximum_input_bytes": 5000,
            "maximum_wordpieces": 256,
            "pooling": "attention-mask mean then L2",
            "runtime_versions": {
                "onnxruntime": "scripted-onnx",
                "tokenizers": "scripted-tokenizer",
                "numpy": np.__version__,
            },
        }
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.tokenizer = FixtureTokenizer()
        self.session = FixtureSession()
        self.options = None

    def runtime(self):
        def options():
            self.options = SimpleNamespace()
            return self.options

        def session(*args, **kwargs):
            self.assertEqual(kwargs["providers"], ["CPUExecutionProvider"])
            return self.session

        backend = SimpleNamespace(
            __version__="scripted-onnx",
            SessionOptions=options,
            ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"),
            InferenceSession=session,
        )
        tokenizer_class = SimpleNamespace(from_file=lambda path: self.tokenizer)
        return backend, tokenizer_class, "scripted-tokenizer"

    def encoder(self, **kwargs):
        with patch.object(semantic, "_load_runtime", side_effect=self.runtime):
            return semantic.FrozenSemanticEncoder(
                self.root, self.manifest_path, **kwargs
            )

    def test_question_only_packet_has_normalized_vector_and_bound_receipt(self):
        encoder = self.encoder()
        question = "Count unresolved deliveries."
        packet = encoder.observe(question)
        self.assertEqual(self.tokenizer.questions, [question])
        self.assertEqual(np.linalg.norm(packet["vector"]), 1)
        self.assertEqual(len(packet["vector"]), 384)
        self.assertEqual(packet["receipt"]["binding_sha256"], encoder.binding_sha256)
        self.assertEqual(
            packet["receipt"]["text_sha256"],
            hashlib.sha256(question.encode()).hexdigest(),
        )
        self.assertEqual(
            packet["receipt"]["vector_float32_sha256"],
            hashlib.sha256(
                np.asarray(packet["vector"], dtype="<f4").tobytes()
            ).hexdigest(),
        )
        receipt = packet["receipt"]
        self.assertEqual(
            receipt["receipt_sha256"],
            semantic.digest(
                {k: v for k, v in receipt.items() if k != "receipt_sha256"}
            ),
        )
        self.assertEqual(self.options.intra_op_num_threads, 2)
        self.assertEqual(self.options.inter_op_num_threads, 1)
        self.assertEqual(self.options.execution_mode, "sequential")
        self.assertFalse(self.options.enable_cpu_mem_arena)
        self.assertNotIn(str(self.root), json.dumps(encoder.binding))

    def test_binding_properties_are_detached_and_read_only(self):
        encoder = self.encoder()
        detached = encoder.binding
        detached["runtime"]["numpy"] = "changed"
        self.assertNotEqual(detached, encoder.binding)
        self.assertEqual(encoder.binding_sha256, semantic.digest(encoder.binding))
        with self.assertRaises(AttributeError):
            encoder.binding_sha256 = "0" * 64

    def test_expected_binding_is_lazy_and_matches_the_executing_runtime(self):
        with (
            patch.object(semantic, "_load_runtime") as runtime,
            patch.object(semantic, "_verify_files") as files,
        ):
            expected = semantic.binding_from_manifest(self.manifest_path, max_calls=7)
        runtime.assert_not_called()
        files.assert_not_called()
        encoder = self.encoder(max_calls=7)
        self.assertEqual(expected, encoder.binding)
        self.assertEqual(semantic.digest(expected), encoder.binding_sha256)

    def test_expected_binding_needs_no_weights_but_rejects_malformed_metadata(self):
        metadata_only = self.root / "metadata-only"
        metadata_only.mkdir()
        path = metadata_only / "manifest.json"
        path.write_text(json.dumps(self.manifest))
        with patch.object(semantic, "_load_runtime") as runtime:
            binding = semantic.binding_from_manifest(path)
            self.assertEqual(binding["max_calls"], 12)
            runtime.assert_not_called()
        changed = copy.deepcopy(self.manifest)
        changed["runtime_versions"]["numpy"] = 123
        path.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            semantic.binding_from_manifest(path)
        with self.assertRaises(ValueError):
            semantic.binding_from_manifest(self.manifest_path, max_calls=True)

    def test_model_identity_is_checked_before_optional_runtime_loading(self):
        (self.root / "weights.onnx").write_bytes(b"changed")
        with (
            patch.object(semantic, "_load_runtime") as runtime,
            self.assertRaises(ValueError),
        ):
            semantic.FrozenSemanticEncoder(self.root, self.manifest_path)
        runtime.assert_not_called()

    def test_tokenizer_git_blob_identity_is_checked(self):
        (self.root / "tokenizer.json").write_bytes(b"Other tokenizer content")
        with self.assertRaisesRegex(ValueError, "file identity|tokenizer checksum"):
            self.encoder()

    def test_changed_weight_file_refuses_an_existing_runtime_before_inference(self):
        encoder = self.encoder()
        (self.root / "weights.onnx").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            encoder.observe("Count unresolved deliveries.")
        self.assertEqual(encoder.calls, 0)
        self.assertEqual(self.session.calls, [])

    def test_runtime_versions_and_gpu_provider_are_refused(self):
        altered = copy.deepcopy(self.manifest)
        altered["runtime_versions"]["onnxruntime"] = "another-runtime"
        self.manifest_path.write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError, "runtime versions"):
            self.encoder()
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.session.providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        with self.assertRaisesRegex(ValueError, "CPU-only"):
            self.encoder()

    def test_byte_and_wordpiece_limits_refuse_without_hidden_truncation(self):
        encoder = self.encoder()
        for question in ("", "x" * 5001, "word " * 300):
            with self.subTest(length=len(question)), self.assertRaises(ValueError):
                encoder.observe(question)
        self.assertEqual(self.session.calls, [])
        self.assertEqual(encoder.calls, 0)

    def test_call_ceiling_is_checked_before_another_execution(self):
        encoder = self.encoder(max_calls=1)
        encoder.observe("Count unresolved deliveries.")
        with self.assertRaisesRegex(ValueError, "call ceiling"):
            encoder.observe("Count unresolved deliveries again.")
        self.assertEqual(encoder.calls, 1)
        self.assertEqual(len(self.session.calls), 1)

    def test_changed_call_allowance_is_refused_under_the_original_binding(self):
        encoder = self.encoder(max_calls=1)
        encoder.observe("Count records.")
        encoder.max_calls = 2
        with self.assertRaises(ValueError):
            encoder.observe("Count records again.")
        self.assertEqual(len(self.session.calls), 1)

    def test_ambiguous_runtime_failure_stops_without_a_free_retry(self):
        encoder = self.encoder()
        self.session.error = True
        with self.assertRaises(RuntimeError):
            encoder.observe("Count unresolved deliveries.")
        with self.assertRaisesRegex(ValueError, "stopped"):
            encoder.observe("Count unresolved deliveries again.")
        self.assertTrue(encoder.failed)
        self.assertEqual(encoder.calls, 1)
        self.assertEqual(len(self.session.calls), 1)

    def test_attention_mask_excludes_padding_before_normalization(self):
        values = np.zeros((1, 3, 384), dtype=np.float32)
        values[0, 0, 0], values[0, 1, 1], values[0, 2, 2] = 1, 1, 1000
        pooled = semantic.mean_pool(values, [[1, 1, 0]])
        self.assertAlmostEqual(float(pooled[0]), 1 / np.sqrt(2), places=6)
        self.assertAlmostEqual(float(pooled[1]), 1 / np.sqrt(2), places=6)
        self.assertEqual(float(pooled[2]), 0)
        with self.assertRaises(ValueError):
            semantic.mean_pool(values, [[0, 0, 0]])
        with self.assertRaises(ValueError):
            semantic.mean_pool(values, [[1, 2, 0]])

    def test_projection_is_independent_of_global_random_state(self):
        before = np.random.get_state()
        first, second = semantic.semantic_projection(), semantic.semantic_projection()
        after = np.random.get_state()
        np.testing.assert_array_equal(first, second)
        self.assertEqual(before[0], after[0])
        np.testing.assert_array_equal(before[1], after[1])
        self.assertEqual(before[2:], after[2:])

    def test_signed_currents_preserve_negation_and_the_existing_drive_bounds(self):
        matrix = semantic.semantic_projection()
        vector = np.zeros(384)
        vector[0] = 1
        positive, negative = (
            semantic.semantic_currents(vector, matrix),
            semantic.semantic_currents(-vector, matrix),
        )
        p, n = (
            np.asarray(positive["regional_drive"]),
            np.asarray(negative["regional_drive"]),
        )
        np.testing.assert_allclose(p[:64], n[64:128])
        np.testing.assert_allclose(p[64:128], n[:64])
        self.assertLessEqual(abs(p).max(), 0.2)
        self.assertEqual(p.shape, (1024,))
        self.assertFalse(p[128:].any())
        self.assertEqual(positive["context"]["salience"], 1)
        self.assertEqual(len(positive["context"]["memory_features"]), 64)
        np.testing.assert_allclose(
            np.asarray(positive["context"]["memory_features"])
            + negative["context"]["memory_features"],
            np.ones(64),
        )

    def test_nonfinite_degenerate_or_wrong_dimension_inputs_are_refused(self):
        matrix = semantic.semantic_projection()
        for vector in (
            [0] * 384,
            [1] * 383,
            [float("nan")] * 384,
            [float("inf")] * 384,
        ):
            with self.subTest(length=len(vector)), self.assertRaises(ValueError):
                semantic.semantic_currents(vector, matrix)

    def test_identity_metadata_has_no_current_mapping_argument(self):
        vector = np.ones(384)
        matrix = semantic.semantic_projection()
        first = semantic.semantic_currents(vector, matrix)
        second = semantic.semantic_currents(vector, matrix)
        self.assertEqual(first, second)
        with self.assertRaises(TypeError):
            semantic.semantic_currents(vector, matrix, job_id="different-provenance")


if __name__ == "__main__":
    unittest.main()

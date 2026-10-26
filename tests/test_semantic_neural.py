"""Actual Brian2 persistence and host enforcement with explicit encoder fixtures.

The scripted vectors below test software boundaries. They are not pretrained
model observations, useful-task performance or evidence of a neural advantage.
"""

import copy
import hashlib
import json
import shutil
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

import numpy as np

from cognition.analyst_workspace import AnalystWorkspace, grade_result
from cognition.grounded_agent import GroundedAnalyst
from cognition.procedural_memory import ProceduralMemory, digest
from cognition.semantic_neural import SemanticNeuralBridge


class FixtureEncoder:
    def __init__(self, *, fail=False, revision="fixture-v1"):
        self.fail, self.calls = fail, []
        self._binding = {
            "schema": "unit-fixture.semantic-encoder.v1",
            "model": "scripted-unit-fixture",
            "revision": revision,
            "dimensions": 384,
        }

    @property
    def binding(self):
        return copy.deepcopy(self._binding)

    @property
    def binding_sha256(self):
        return digest(self.binding)

    def observe(self, question):
        self.calls.append(question)
        if self.fail:
            raise RuntimeError("Explicit unresolved encoder fixture")
        vector = np.zeros(384, dtype="<f4")
        vector[0 if "sum" in question else 1] = 1
        receipt = {
            "schema": "nuria.semantic-observation.v1",
            "binding_sha256": self.binding_sha256,
            "text_sha256": hashlib.sha256(question.encode()).hexdigest(),
            "vector_float32_sha256": hashlib.sha256(vector.tobytes()).hexdigest(),
            "dimensions": 384,
            "fixture": True,
        }
        receipt["receipt_sha256"] = digest(receipt)
        return {"vector": vector.tolist(), "receipt": receipt}


class FixtureReasoner:
    enable_thinking = False

    def __init__(self):
        self.messages = []

    def complete(self, messages, *, max_tokens, seed):
        self.messages.append(copy.deepcopy(messages))
        sql = "SELECT sum(amount) FROM records"
        action = {"tool": "query", "arguments": {"sql": sql}}
        if len(self.messages) > 1:
            action = {"tool": "final", "arguments": {"sql": sql}}
        content = json.dumps(action)
        return {
            "content": content,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "request_sha256": digest(messages),
            "response_sha256": digest(content),
            "finish_reason": "stop",
            "wall_seconds": 0,
        }


class SemanticNeuralTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("semantic-neural-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)

    def bridge(self, name, encoder=None, **changes):
        arguments = {
            "source_id": "fixture-source",
            "model_id": "fixture-analyst",
            "tool_version": "nuria.analyst-workspace.v1",
            "encoder": FixtureEncoder() if encoder is None else encoder,
            "mode": "neural",
        }
        arguments.update(changes)
        return SemanticNeuralBridge(self.root / name, **arguments)

    def test_actual_spikes_receive_semantic_fixture_and_no_identity_texture(self):
        encoder = FixtureEncoder()
        bridge = self.bridge("lab", encoder)
        try:
            decision = bridge.choose("job-a", "1" * 64, "sum records", {})
            self.assertEqual(encoder.calls, ["sum records"])
            self.assertGreater(len(decision["spikes"]), 0)
            self.assertTrue(np.isfinite(decision["sensory_drive"]).all())
            self.assertLessEqual(max(abs(x) for x in decision["sensory_drive"]), 0.2)
            self.assertEqual(len(decision["memory_features"]), 64)
            self.assertEqual(bridge.state["jobs"]["job-a"]["status"], "chosen")
            self.assertEqual(bridge.head["sequence"], 2)
            same = bridge.choose("job-a", "1" * 64, "sum records", {})
            self.assertEqual(same, decision)
            self.assertEqual(len(encoder.calls), 1)
        finally:
            bridge.close()

    def test_identical_meaning_is_invariant_to_job_source_and_policy_hashes(self):
        first = self.bridge("first", source_id="source-one")
        try:
            left = first.choose("job-one", "1" * 64, "sum records", {})
        finally:
            first.close()
        second = self.bridge("second", source_id="source-two")
        try:
            right = second.choose("different-job", "9" * 64, "sum records", {})
        finally:
            second.close()
        for key in (
            "semantic_input_sha256",
            "sensory_drive",
            "memory_features",
            "spikes_sha256",
        ):
            self.assertEqual(left[key], right[key])
        self.assertNotEqual(left["origin_sha256"], right["origin_sha256"])

    def test_immutable_checkpoint_restores_next_window_and_random_state(self):
        original = self.bridge("original")
        try:
            original.choose("first", "1" * 64, "sum records", {})
            restored_hash = original.brain.state_hash()
        finally:
            original.close()
        shutil.copytree(self.root / "original", self.root / "branch")
        continued = self.bridge("original")
        try:
            self.assertEqual(continued.brain.state_hash(), restored_hash)
            left = continued.choose("next", "2" * 64, "latest record", {})
        finally:
            continued.close()
        restored = self.bridge("branch")
        try:
            self.assertEqual(restored.brain.state_hash(), restored_hash)
            right = restored.choose("next", "2" * 64, "latest record", {})
        finally:
            restored.close()
        self.assertEqual(left["spikes"], right["spikes"])
        self.assertEqual(left["after_state_sha256"], right["after_state_sha256"])

    def test_delayed_credit_updates_original_trace_once(self):
        bridge = self.bridge("credit")
        try:
            first = bridge.choose("first", "1" * 64, "sum records", {})
            trace = copy.deepcopy(bridge.state["jobs"]["first"]["eligibility"])
            bridge.choose("second", "2" * 64, "latest record", {})
            indices = trace["indices"]
            self.assertTrue(indices)
            before = np.asarray(bridge.brain.exc.w[:])[indices].copy()
            expected = np.clip(
                before + 0.002 * np.asarray(trace["eligibility"]), 0.002, 0.18
            )
            terminal = bridge.settle("first", True, "f" * 64)
            np.testing.assert_array_equal(
                np.asarray(bridge.brain.exc.w[:])[indices], expected
            )
            self.assertEqual(terminal["decision_sha256"], first["decision_sha256"])
            after = bridge.brain.state_hash()
            self.assertEqual(bridge.settle("first", True, "f" * 64), terminal)
            self.assertEqual(bridge.brain.state_hash(), after)
            with self.assertRaises(ValueError):
                bridge.settle("first", False, "f" * 64)
        finally:
            bridge.close()

    def test_interrupted_embedding_remains_reserved_after_recovery(self):
        encoder = FixtureEncoder(fail=True)
        bridge = self.bridge("interrupted", encoder)
        try:
            with self.assertRaises(RuntimeError):
                bridge.choose("pending", "1" * 64, "sum records", {})
            self.assertEqual(
                bridge.state["jobs"]["pending"]["status"], "embedding_reserved"
            )
        finally:
            bridge.close()
        replacement_encoder = FixtureEncoder()
        recovered = self.bridge("interrupted", replacement_encoder)
        try:
            for ident in ("pending", "another-job"):
                with self.assertRaisesRegex(RuntimeError, "reconciliation"):
                    recovered.choose(ident, "1" * 64, "sum records", {})
            self.assertEqual(replacement_encoder.calls, [])
            with self.assertRaises(ValueError):
                recovered.settle("pending", True, "f" * 64)
        finally:
            recovered.close()

    def test_recovery_refuses_changed_encoder_or_source(self):
        bridge = self.bridge("binding")
        bridge.close()
        with self.assertRaisesRegex(ValueError, "source, encoder"):
            self.bridge("binding", FixtureEncoder(revision="different-fixture"))
        with self.assertRaisesRegex(ValueError, "source, encoder"):
            self.bridge("binding", source_id="changed-source")

    def test_changed_projection_is_refused_before_an_encoding_disclosure(self):
        encoder = FixtureEncoder()
        bridge = self.bridge("projection", encoder)
        try:
            bridge.matrix *= -1
            with self.assertRaisesRegex(ValueError, "identity changed"):
                bridge.choose("job", "1" * 64, "sum records", {})
            self.assertEqual(encoder.calls, [])
            self.assertEqual(bridge.state["jobs"], {})
        finally:
            bridge.close()

    def test_host_enforces_first_tool_before_independently_checked_answer(self):
        database = self.root / "records.sqlite"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE records(amount INTEGER)")
            connection.executemany("INSERT INTO records VALUES(?)", [(1,), (2,)])
        documents = {}
        for section in ("business", "columns", "schema"):
            path = self.root / (section + ".txt")
            path.write_text("records has an additive integer amount field")
            documents[section] = {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        revision = "a" * 40
        registry = {
            "schema": "nuria.analyst-workspace.v1",
            "revision": revision,
            "databases": {
                "fixture": {
                    "database": {
                        "path": database.name,
                        "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                    },
                    "documents": documents,
                }
            },
        }
        manifest = self.root / "registry.json"
        manifest.write_text(json.dumps(registry))
        workspace = AnalystWorkspace(manifest)
        bridge = self.bridge("enforced", source_id=revision, mode="fixed")
        memory = ProceduralMemory(
            self.root / "memory.sqlite",
            source_id=revision,
            model_id="fixture-analyst",
            tool_version="nuria.analyst-workspace.v1",
        )
        try:
            decision = bridge.choose("analysis", "1" * 64, "sum records", {})
            context = {
                key: decision[key]
                for key in ("first_tool", "utilities", "action_spikes", "window_ms")
            }
            result = GroundedAnalyst(
                FixtureReasoner(),
                workspace,
                memory,
                memory_mode="procedure",
                initial_tool=decision["first_tool"],
                neural_context=context,
            ).run(job_id="analysis", question="sum records", db_id="fixture")
            self.assertEqual(result["trace"][0]["kind"], "host_initial_action")
            self.assertEqual(
                result["trace"][0]["action"]["tool"], decision["first_tool"]
            )
            accepted = grade_result(
                result["selected_receipt"], [[3]], expected_columns=1
            )
            self.assertTrue(accepted)
            terminal = memory.settle(
                result["episode_id"],
                verify_terminal=lambda request: {
                    "accepted": accepted,
                    "checker_id": "independent-offline-fixture-sum",
                    "evidence_sha256": digest(
                        {"expected": [[3]], "proposal": request["proposal_sha256"]}
                    ),
                },
            )
            credit = bridge.settle(
                "analysis", terminal["accepted"], terminal["receipt_sha256"]
            )
            self.assertEqual(credit["evidence_sha256"], terminal["receipt_sha256"])
        finally:
            memory.close()
            bridge.close()


if __name__ == "__main__":
    unittest.main()

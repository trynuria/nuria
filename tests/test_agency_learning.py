"""Real-task memory must retain failures without granting itself acceptance."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agency.learning import ExperienceMemory, task_frame
from agency.model import BridgeModel, Gateway, parse_action
from agency.runner import Mission
from studio.store import Store, atomic, digest
from studio.worker import BudgetExhausted


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = ExperienceMemory(self.root / "memory")
        self.addCleanup(self.memory.close)

    def recorded_mission(self, name, *, accepted=True, draft=None, transfers=None):
        root = self.root / name
        review = {
            "approved": accepted,
            "reason": "Actual checks passed"
            if accepted
            else "Missing evidence was accepted",
            "lesson": "Check evidence presence as well as content before publishing output.",
            "limitations": ["Finite checks do not cover all inputs"],
            "learning_approved": accepted,
            "transfer": transfers or [],
        }
        data = {
            "id": name,
            "status": "accepted" if accepted else "building",
            "goal": {
                "title": "Validate an archive before publication",
                "problem": "Missing evidence can bypass integrity validation",
            },
            "learning_draft": draft,
            "delivery": {
                "review": review,
                "after": {"exit_code": 0, "timed_out": False},
                "artifacts": {},
                "acceptance_sha256": "a" * 64,
            }
            if accepted
            else {},
        }
        atomic(root / "mission.json", data)
        atomic(root / "reviews" / "act-001-delivery.json", review)
        return root

    def test_failure_is_retrievable_but_cannot_become_an_accepted_skill(self):
        draft = {
            "title": "Bad rule",
            "when": "Any archive",
            "method": "Ignore missing fields",
            "pitfalls": "None",
        }
        root = self.recorded_mission("mission-0001", accepted=False, draft=draft)
        self.memory.ingest(root)
        found = self.memory.search("missing evidence integrity")
        self.assertEqual(found[0]["kind"], "review_observation")
        self.assertFalse(found[0]["evidence"]["accepted"])
        self.assertNotIn("procedure", self.memory.export()["records"])
        self.assertEqual(list((self.root / "memory" / "skills").glob("*/SKILL.md")), [])

    def test_accepted_procedure_survives_restart_and_is_available_to_other_tasks(self):
        draft = {
            "title": "Authenticate complete evidence",
            "when": "Validating authenticated archives",
            "method": "Check required evidence presence before verifying its digest",
            "pitfalls": "A matching digest alone cannot establish the original sender",
        }
        self.memory.ingest(self.recorded_mission("mission-0001", draft=draft))
        reopened = ExperienceMemory(self.root / "memory")
        self.addCleanup(reopened.close)
        found = reopened.search(
            "authenticate evidence archive", exclude_mission="mission-0002"
        )
        self.assertTrue(any(item["kind"] == "procedure" for item in found))
        self.assertTrue(list((self.root / "memory" / "skills").glob("*/SKILL.md")))
        self.assertEqual(
            reopened.search("evidence", exclude_mission="mission-0001"), []
        )

    def test_declared_use_is_not_transfer_and_rejection_cannot_support_it(self):
        self.memory.ingest(self.recorded_mission("mission-0001"))
        lesson = self.memory.search("evidence")[0]["id"]
        self.memory.declare_use(
            "mission-0002",
            "act-002-123456abcdef",
            [
                {
                    "id": lesson,
                    "application": "Check missing fields in a different parser",
                }
            ],
        )
        self.assertEqual(self.memory.export()["transfer_feedback"], [])
        root = self.recorded_mission(
            "mission-0002",
            accepted=False,
            transfers=[
                {
                    "id": lesson,
                    "verdict": "supported",
                    "evidence": "I claimed it worked",
                }
            ],
        )
        self.memory.ingest(root)
        self.assertEqual(self.memory.export()["transfer_feedback"], [])

    def test_cross_task_contradiction_and_later_success_both_survive(self):
        self.memory.ingest(self.recorded_mission("mission-0001"))
        lesson = self.memory.search("evidence")[0]["id"]
        self.memory.declare_use(
            "mission-0002",
            "act-002-123456abcdef",
            [
                {
                    "id": lesson,
                    "application": "Adapt the integrity method to another format",
                }
            ],
        )
        root = self.recorded_mission(
            "mission-0002",
            accepted=False,
            transfers=[
                {
                    "id": lesson,
                    "verdict": "contradicted",
                    "evidence": "The required missing-field case still failed",
                }
            ],
        )
        self.memory.ingest(root)
        old = json.loads((root / "reviews" / "act-001-delivery.json").read_text())
        self.recorded_mission(
            "mission-0002",
            accepted=True,
            transfers=[
                {
                    "id": lesson,
                    "verdict": "supported",
                    "evidence": "Different parser now rejects both omitted and corrupt evidence",
                }
            ],
        )
        atomic(root / "reviews" / "act-000-delivery.json", old)
        self.memory.ingest(root)
        self.memory.ingest(root)
        feedback = self.memory.export()["transfer_feedback"]
        self.assertEqual(
            {f["verdict"] for f in feedback}, {"supported", "contradicted"}
        )
        self.assertEqual(len(feedback), 2)

    def test_task_frame_keeps_goal_plan_and_prior_task_evidence_after_long_history(
        self,
    ):
        self.memory.ingest(self.recorded_mission("mission-0001"))
        root = self.recorded_mission("mission-0002", accepted=False)
        data = json.loads((root / "mission.json").read_text())
        data["working_state"] = {
            "steps": [{"task": "Check the missing evidence branch", "status": "todo"}]
        }
        frame = task_frame(
            SimpleNamespace(root=root, data=data),
            self.memory,
            [{"role": "user", "content": "x" * 100000}],
        )
        self.assertEqual(frame["goal"], data["goal"])
        self.assertEqual(frame["plan"], data["working_state"])
        self.assertIsNotNone(frame["last_delivery_review"])
        self.assertEqual(
            frame["relevant_experience"][0]["origin_mission"], "mission-0001"
        )
        self.assertEqual(frame["recent"][0]["content"], "x" * 100000)

    def test_action_serialization_recovery_does_not_choose_between_different_commands(
        self,
    ):
        first = {"kind": "exec", "command": "cat /work/actual.py"}
        second = {**first, "reason": "Inspect the actual implementation"}
        self.assertEqual(parse_action(json.dumps(first) + r"\n"), first)
        self.assertEqual(
            parse_action(json.dumps(first) + "\n" + json.dumps(second)), second
        )
        with self.assertRaises(ValueError):
            parse_action(
                json.dumps(first)
                + "\n"
                + json.dumps({"kind": "exec", "command": "different command"})
            )
        with self.assertRaises(ValueError):
            parse_action(json.dumps(first) + '\n{"kind":"exec","command":"unfinished')

    def test_task_frame_can_compare_two_files_read_in_separate_steps(self):
        root = self.recorded_mission("mission-0002", accepted=False)
        mission = SimpleNamespace(
            root=root, data=json.loads((root / "mission.json").read_text())
        )
        messages = [
            {
                "role": "assistant",
                "content": '{"kind":"exec","command":"cat producer.py"}',
            },
            {"role": "user", "content": "producer: required evidence has a digest"},
            {
                "role": "assistant",
                "content": '{"kind":"exec","command":"cat implementation.py"}',
            },
            {"role": "user", "content": "implementation: skips absent evidence"},
        ]
        frame = task_frame(mission, self.memory, messages)
        self.assertEqual(frame["recent"], messages)

    def test_new_mission_can_retrieve_before_a_goal_has_been_selected(self):
        mission = SimpleNamespace(
            root=self.root / "mission-0003",
            data={"id": "mission-0003", "status": "exploring"},
        )
        messages = [
            {"role": "user", "content": "Select useful work from real evidence"}
        ]
        frame = task_frame(mission, self.memory, messages)
        self.assertEqual(frame["goal"], {})
        self.assertEqual(frame["recent"], messages)
        self.assertEqual(frame["relevant_experience"], [])

    def test_goal_selection_uses_current_status_not_stale_resumption_reasons(self):
        library = self.root / "library"
        atomic(
            library / "memory.json",
            [
                {
                    "id": "mission-0005",
                    "status": "revisit",
                    "lesson": "mission-0004 is unresolved",
                },
                {
                    "id": "mission-0004",
                    "status": "accepted",
                    "goal": {"title": "Archive audit"},
                },
            ],
        )
        mission = SimpleNamespace(
            root=self.root / "mission-0006",
            library=library,
            data={"id": "mission-0006", "status": "exploring"},
        )
        frame = task_frame(mission, self.memory, [])
        index = frame["outcome_index"]
        self.assertTrue(index["available"])
        self.assertEqual(index["total"], 2)
        self.assertEqual(index["status_counts"], {"accepted": 1, "revisit": 1})
        self.assertEqual(index["items"][0]["id"], "mission-0004")
        self.assertEqual(index["items"][0]["status"], "accepted")
        self.assertFalse(any(item["can_reopen"] for item in index["items"]))
        self.assertNotIn("unresolved", json.dumps(index["items"]))

    def test_recent_context_is_bounded_without_cutting_latest_observation(self):
        root = self.recorded_mission("mission-0002", accepted=False)
        mission = SimpleNamespace(
            root=root, data=json.loads((root / "mission.json").read_text())
        )
        messages = []
        for index in range(10):
            messages.extend(
                [
                    {"role": "assistant", "content": f"read {index}"},
                    {"role": "user", "content": str(index) + "x" * 18000},
                ]
            )
        frame = task_frame(mission, self.memory, messages)
        self.assertEqual(frame["recent"], messages[-4:])
        self.assertIn("/observations/trajectory.json", frame["complete_records"])

    def test_unknown_settlement_cannot_be_bypassed_with_new_learning_context(self):
        root = self.recorded_mission("mission-0002", accepted=False)
        store = Store(self.root / "store")
        self.addCleanup(store.db.close)
        client = Mock()
        client.complete.side_effect = TimeoutError("Unknown settlement")
        gateway = Gateway(store, client, root)
        atomic(
            root / "requests" / "act-000.json",
            {"messages": [{"role": "user", "content": "old"}], "tokens": 1024},
        )
        mission = SimpleNamespace(
            root=root, data=json.loads((root / "mission.json").read_text())
        )
        with self.assertRaises(RuntimeError):
            BridgeModel(gateway, mission, self.memory).query([])
        self.assertEqual(client.complete.call_count, 1)

    def test_known_denial_can_use_new_frame_without_changing_old_request(self):
        root = self.recorded_mission("mission-0002", accepted=False)
        store = Store(self.root / "store")
        self.addCleanup(store.db.close)
        client = Mock()
        client.complete.side_effect = [
            BudgetExhausted(),
            {"content": '{"kind":"exec","command":"true"}', "usage": {}},
        ]
        gateway = Gateway(store, client, root)
        path = root / "requests" / "act-000.json"
        old = {"messages": [{"role": "user", "content": "old"}], "tokens": 1024}
        atomic(path, old)
        mission = SimpleNamespace(
            root=root, data=json.loads((root / "mission.json").read_text())
        )
        result = BridgeModel(gateway, mission, self.memory).query([])
        self.assertEqual(result["extra"]["actions"][0]["kind"], "exec")
        self.assertEqual(json.loads(path.read_text()), old)
        self.assertNotEqual(
            client.complete.call_args_list[0].args[0],
            client.complete.call_args_list[1].args[0],
        )
        self.assertEqual(
            json.loads(path.with_name("act-000-not-issued.json").read_text())[
                "original_request_sha256"
            ],
            digest(old),
        )

    def test_malformed_optional_assessment_does_not_invalidate_a_delivery(self):
        self.memory.ingest(self.recorded_mission("mission-0001"))
        ident = self.memory.search("evidence")[0]["id"]
        self.memory.declare_use(
            "mission-0002",
            "act-002-123456abcdef",
            [{"id": ident, "application": "Apply the method"}],
        )
        root = self.recorded_mission(
            "mission-0002",
            transfers=[{"id": ident, "verdict": "supported", "evidence": None}],
        )
        self.memory.ingest(root)
        self.assertEqual(self.memory.export()["records"]["accepted_method"], 2)
        self.assertEqual(self.memory.export()["transfer_feedback"], [])

    def test_unknown_revision_is_rejected_before_delivery(self):
        mission = Mission(
            self.root / "mission-0003",
            Mock(),
            Mock(),
            self.root / "public",
            self.root / "library",
            memory=self.memory,
        )
        result = mission.execute(
            {
                "kind": "submit",
                "action_id": "act-001-123456abcdef",
                "procedure": {
                    "title": "Revision",
                    "when": "A real task",
                    "method": "Adapt an earlier method",
                    "pitfalls": "Limited checks",
                    "revises": "missing",
                },
            }
        )
        self.assertIn("error", result)
        self.assertNotIn("delivery", mission.data)


if __name__ == "__main__":
    unittest.main()

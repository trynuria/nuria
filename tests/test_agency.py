"""Recovery, immutable acceptance, and the real offline execution boundary."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from agency.model import BridgeModel, Gateway, PersistentAgent
from agency.policy import SYSTEM
from agency.runner import Mission
from agency.sandbox import ShellWorkspace, files_under, public_url
from studio.store import Store, atomic


class AgencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "mission-0001"
        self.source = self.base / "source"
        self.source.mkdir()
        self.library = self.base / "library"
        self.shell = ShellWorkspace(
            self.root, self.source, self.library, bwrap="/usr/bin/bwrap"
        )
        self.client = Mock()
        self.client.complete.return_value = {
            "content": json.dumps(
                {
                    "kind": "exec",
                    "command": "echo inspected",
                    "reason": "inspect actual inputs",
                }
            ),
            "usage": {},
        }
        self.store = Store(self.base / "state")
        self.addCleanup(self.store.db.close)
        self.gateway = Gateway(self.store, self.client, self.root)
        self.mission = Mission(
            self.root, self.shell, self.gateway, self.base / "public", self.library
        )

    def agent(self):
        return PersistentAgent(
            BridgeModel(self.gateway),
            self.mission,
            system_template=SYSTEM,
            instance_template="{{ task }}",
            cost_limit=0,
            output_path=self.root / "agent.json",
        )

    def test_resume_saved_assistant_executes_once_without_a_second_paid_call(self):
        agent = self.agent()
        agent.add_messages(
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Inspect and choose work"},
        )
        agent.query()  # Crash after the durable assistant decision, before its tool.
        resumed = self.agent()
        self.assertTrue(resumed.restore())
        self.shell.execute = Mock(return_value={"exit_code": 0, "stdout": "inspected"})
        resumed.resume_step()
        self.assertEqual(self.client.complete.call_count, 1)
        self.assertEqual(self.shell.execute.call_count, 1)
        action = agent.messages[-1]["extra"]["actions"][0]
        self.mission.execute(action)
        self.assertEqual(self.shell.execute.call_count, 1)

    def test_unobserved_settled_response_replays_original_request(self):
        first = self.gateway.ask(
            "crash-before-checkpoint", [{"role": "user", "content": "original"}]
        )
        second = self.gateway.ask(
            "crash-before-checkpoint",
            [{"role": "user", "content": "later ambient state"}],
        )
        self.assertEqual(first, second)
        self.assertEqual(self.client.complete.call_count, 1)

    def test_uncertain_shell_action_is_not_repeated(self):
        action = {
            "kind": "exec",
            "action_id": "act-000-123456abcdef",
            "command": "increment-something",
        }
        atomic(
            self.root / "actions" / (action["action_id"] + ".json"),
            {"state": "running", "action": action},
        )
        self.shell.execute = Mock()
        result = self.mission.execute(action)
        self.assertTrue(result["interrupted"])
        self.shell.execute.assert_not_called()

    def test_uncertain_inference_stops_the_action_instead_of_buying_a_new_one(self):
        self.mission.data["events"].append({"kind": "exec"})
        self.client.complete.side_effect = TimeoutError("transport interrupted")
        action = {
            "kind": "goal",
            "action_id": "act-001-123456abcdef",
            "title": "Useful task",
        }
        with self.assertRaises(RuntimeError):
            self.mission.execute(action)
        journal = json.loads(
            (self.root / "actions" / (action["action_id"] + ".json")).read_text()
        )
        self.assertEqual(journal["state"], "running")
        self.assertEqual(self.client.complete.call_count, 1)

    def test_artifact_links_and_path_traversal_fail_closed(self):
        (self.shell.work / "outside").symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            files_under(self.shell.work)
        with self.assertRaises(ValueError):
            self.shell.execute("true", "../../escape")

    def test_public_fetch_cannot_become_a_private_or_authenticated_request(self):
        for url in (
            "http://api.github.com/",
            "https://127.0.0.1/",
            "https://api.github.com.evil.example/",
            "https://user:pass@github.com/",
            "https://github.com:444/",
        ):
            with self.assertRaises(ValueError):
                public_url(url)
        self.assertEqual(
            public_url("https://docs.python.org/3/#section"),
            "https://docs.python.org/3/",
        )

    def test_acceptance_is_frozen_before_code_and_requires_independent_failure(self):
        self.mission.data["events"].append({"kind": "exec"})
        self.gateway.decision = Mock(
            return_value={
                "approved": True,
                "checks": "assert False",
                "criteria": ["Three actual cases"],
            }
        )
        self.mission.checker = Mock(return_value={"exit_code": 1, "timed_out": False})
        action = {
            "kind": "goal",
            "action_id": "act-001-123456abcdef",
            "title": "Useful task",
        }
        self.assertTrue(self.mission.goal(action)["accepted"])
        frozen = (self.root / "acceptance.json").read_bytes()
        self.gateway.decision.return_value["checks"] = "print('weakened')"
        self.mission.goal({**action, "action_id": "act-002-123456abcdef"})
        self.assertEqual((self.root / "acceptance.json").read_bytes(), frozen)
        self.assertEqual(self.gateway.decision.call_count, 1)

    def test_passing_checker_without_implementation_is_rejected(self):
        self.mission.data["events"].append({"kind": "exec"})
        self.gateway.decision = Mock(
            return_value={"approved": True, "checks": "print('vacuous')"}
        )
        self.mission.checker = Mock(return_value={"exit_code": 0, "timed_out": False})
        self.assertFalse(
            self.mission.goal({"action_id": "act-001-123456abcdef"})["accepted"]
        )
        self.assertFalse((self.root / "acceptance.json").exists())

    def test_revisit_reuses_an_unfinished_mission_but_cannot_reopen_success(self):
        previous = self.root.parent / "mission-0009" / "mission.json"
        atomic(previous, {"status": "abandoned", "goal": {"title": "retained"}})
        result = self.mission.execute(
            {
                "kind": "resume",
                "mission": "mission-0009",
                "action_id": "act-001-123456abcdef",
                "reason": "Execution environment now supports its unchanged checks",
            }
        )
        self.assertEqual(result["resume_mission"], "mission-0009")
        atomic(previous, {"status": "accepted"})
        result = self.mission.execute(
            {
                "kind": "resume",
                "mission": "mission-0009",
                "action_id": "act-002-123456abcdef",
            }
        )
        self.assertIn("error", result)

    @unittest.skipUnless(
        os.environ.get("NURIA_TEST_LINUX_ISOLATION") == "1", "Requires Linux namespaces"
    )
    def test_real_namespace_readonly_inputs_no_secrets_network_and_persistence(self):
        self.shell.bwrap = os.environ.get("NURIA_TEST_BWRAP") or shutil.which("bwrap")
        (self.source / "input.txt").write_text("source")
        (self.library / "retained.txt").write_text("prior tool")
        script = """python3 - <<'PY'
import os, pathlib, socket
assert not pathlib.Path('/etc/nuria-studio/bridge.token').exists()
assert not os.environ.get('AGENCY_TEST_SECRET')
for name in ('/repo/input.txt', '/library/retained.txt', '/work/forbidden'):
    try: pathlib.Path(name).write_text('change')
    except OSError: pass
    else: raise AssertionError(name)
try: socket.create_connection(('1.1.1.1', 443), timeout=0.1)
except OSError: pass
else: raise AssertionError('network escaped')
print('boundary passed')
PY"""
        before = self.shell.execute(script, "boundary")
        self.assertEqual(before["exit_code"], 0, before)
        first = self.shell.execute(
            "printf persistent > /work/result.txt", "write", writable=True
        )
        self.assertEqual(first["exit_code"], 0, first)
        second = self.shell.execute("cat /work/result.txt", "read")
        self.assertEqual(second["stdout"], "persistent")


if __name__ == "__main__":
    unittest.main()

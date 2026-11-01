"""Outcome checks reject the exact kind of defect that self-tests missed."""

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.acceptance import finish, repair, snapshot
from studio.outcomes import validate_challenge
from studio.store import atomic
from studio.worker import Campaign
from tests.test_studio import Client


def story_challenge():
    return {
        "summary": "Both fresh and stale endings must be reachable with three nights.",
        "claims": [
            {
                "name": "Stale ending is reachable",
                "kind": "reachable",
                "max_depth": 6,
                "max_states": 128,
                "actions": [
                    {"type": "move", "from": a, "to": b}
                    for a in ("garden", "kitchen", "dock")
                    for b in ("garden", "kitchen", "dock")
                    if a != b
                ],
                "expect": [{"path": "state.bread", "op": "eq", "value": "stale"}],
            },
            {
                "name": "Fresh ending survives",
                "kind": "case",
                "actions": [{"type": "move", "from": "dock", "to": "garden"}] * 2,
                "expect": [
                    {"path": "state.bread", "op": "eq", "value": "fresh"},
                    {
                        "sum": ["state.n.garden", "state.n.kitchen", "state.n.dock"],
                        "op": "eq",
                        "value": 3,
                    },
                ],
            },
        ],
    }


class OutcomeTests(unittest.TestCase):
    def test_criteria_require_real_bounded_assertions(self):
        value = story_challenge()
        validate_challenge(value)
        for mutation in ("depth", "empty", "field", "operator"):
            bad = json.loads(json.dumps(value))
            if mutation == "depth":
                bad["claims"][0]["max_depth"] = 100
            elif mutation == "empty":
                bad["claims"][0]["expect"] = []
            elif mutation == "field":
                bad["claims"][0]["expect"][0]["path"] = "process.env"
            else:
                bad["claims"][0]["expect"][0]["op"] = "eval"
            with self.assertRaises(ValueError):
                validate_challenge(bad)

    @unittest.skipUnless(shutil.which("node"), "Node required")
    def test_unreachable_ending_fails_then_same_frozen_criteria_pass(self):
        engine = """globalThis.NuriaEngine={create:()=>({n:{garden:0,kitchen:0,dock:3},bread:'none'}),act(s,a){if(s.n[a.from]>0){s.n[a.from]--;s.n[a.to]++}s.bread=s.n.garden<2?'none':s.n.kitchen>=THRESHOLD?'stale':'fresh';return s},metrics:s=>({nights:Object.values(s.n).reduce((a,b)=>a+b,0)}),runTests(){throw Error('Builder tests must not run in independent acceptance')}};"""
        checker = Path(__file__).resolve().parents[1] / "studio/check_outcomes.cjs"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            atomic(root / "inputs.json", {})
            atomic(root / "challenge.json", story_challenge())
            frozen_hash = hashlib.sha256(
                (root / "challenge.json").read_bytes()
            ).hexdigest()
            for threshold, expected in ((2, False), (1, True)):
                (root / "engine.js").write_text(
                    engine.replace("THRESHOLD", str(threshold))
                )
                result = subprocess.run(
                    [shutil.which("node"), str(checker)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=8,
                )
                report = json.loads(result.stdout)
                self.assertEqual(report["passed"], expected)
                self.assertEqual(report["challenge_sha256"], frozen_hash)
                self.assertTrue(report["results"][1]["passed"])
                if not expected:
                    self.assertEqual(report["results"][0]["visited"], 10)
                    self.assertTrue(report["results"][0]["complete"])
                    self.assertEqual(
                        report["results"][0]["outcome"],
                        "unreachable under declared actions",
                    )
                else:
                    self.assertEqual(len(report["results"][0]["witness"]), 3)
            # Depth exhaustion must never masquerade as an exhaustive impossibility proof.
            limited = story_challenge()
            limited["claims"][0]["max_depth"] = 1
            atomic(root / "challenge.json", limited)
            report = json.loads(
                subprocess.check_output(
                    [shutil.which("node"), str(checker)], cwd=root, text=True
                )
            )
            self.assertFalse(report["passed"])
            self.assertFalse(report["results"][0]["complete"])
            self.assertEqual(
                report["results"][0]["outcome"], "no witness within search bounds"
            )

    def test_missing_asserted_field_cannot_pass_and_cases_remain_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            atomic(root / "inputs.json", {})
            atomic(root / "challenge.json", story_challenge())
            (root / "engine.js").write_text(
                "globalThis.NuriaEngine={create:()=>({}),act:s=>s,metrics:()=>({})};"
            )
            checker = Path(__file__).resolve().parents[1] / "studio/check_outcomes.cjs"
            if not shutil.which("node"):
                self.skipTest("Node required")
            report = json.loads(
                subprocess.check_output(
                    [shutil.which("node"), str(checker)], cwd=root, text=True
                )
            )
            self.assertFalse(report["passed"])
            self.assertEqual(
                report["results"][0]["outcome"], "unreachable under declared actions"
            )
            self.assertTrue(report["results"][0]["last_checks"][0]["missing"])
            self.assertIn("Missing asserted field", report["results"][1]["error"])

    @unittest.skipUnless(shutil.which("node"), "Node required")
    def test_reachable_goal_can_be_created_by_an_action(self):
        checker = Path(__file__).resolve().parents[1] / "studio/check_outcomes.cjs"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            atomic(root / "inputs.json", {})
            atomic(
                root / "challenge.json",
                {
                    "claims": [
                        {
                            "name": "Export is created after teaching",
                            "kind": "reachable",
                            "actions": [{"type": "teach"}, {"type": "export"}],
                            "expect": [
                                {"path": "state.export.text", "op": "ne", "value": ""},
                                {"sum": ["state.export.rows"], "op": "eq", "value": 2},
                            ],
                        },
                        {
                            "name": "Missing is not unequal",
                            "kind": "reachable",
                            "actions": [{"type": "noop"}],
                            "expect": [{"path": "state.never", "op": "ne", "value": 0}],
                        },
                    ]
                },
            )
            (root / "engine.js").write_text(
                "globalThis.NuriaEngine={create:()=>({}),metrics:()=>({}),"
                "act(s,a){if(a.type==='teach')s.ready=true;"
                "if(a.type==='export'&&s.ready)s.export={text:'learned',rows:2};return s}};"
            )
            report = json.loads(
                subprocess.check_output(
                    [shutil.which("node"), str(checker)], cwd=root, text=True
                )
            )
            reached, absent = report["results"]
            self.assertTrue(reached["passed"])
            self.assertEqual(
                reached["witness"], [{"type": "teach"}, {"type": "export"}]
            )
            self.assertFalse(absent["passed"])
            self.assertNotIn("error", absent)
            self.assertTrue(absent["last_checks"][0]["missing"])

    def test_repair_keeps_original_and_cannot_retain_a_failed_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "engine.js").write_text("original")
            snapshot(root)
            (root / "engine.js").write_text("repair")
            snapshot(root)
            self.assertEqual(
                (root / "revisions/before-acceptance/engine.js").read_text(), "original"
            )
            campaign = Campaign(root / "state", Client(), "/usr/bin/node")
            self.addCleanup(campaign.store.db.close)
            with self.assertRaises(ValueError):
                finish(
                    campaign,
                    {"id": "invention-001"},
                    {
                        "id": "a",
                        "checks": {"passed": True},
                        "outcomes": {"passed": False},
                    },
                )

    def test_restarted_retention_does_not_buy_another_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            client = Client()
            campaign = Campaign(temp, client, "/usr/bin/node")
            self.addCleanup(campaign.store.db.close)
            campaign.store.put(
                "projects",
                "invention-001",
                {"id": "invention-001", "status": "retaining"},
            )
            with patch.object(campaign, "retain") as retain:
                self.assertTrue(campaign.tick())
                retain.assert_called_once()
            self.assertEqual(client.calls, 0)

    def test_replay_after_source_write_verifies_without_buying_another_call(self):
        with tempfile.TemporaryDirectory() as temp:
            client = Client()

            def complete(call_id, **request):
                client.calls += 1
                return {
                    "content": json.dumps(
                        {"files": {"engine.js": "repaired"}, "note": "Fixed"}
                    ),
                    "usage": {},
                }

            client.complete = complete
            campaign = Campaign(
                temp, client, "/usr/bin/node", browser_runtime="/runtime"
            )
            self.addCleanup(campaign.store.db.close)
            candidate = {
                "id": "a",
                "revision": 1,
                "challenge": story_challenge(),
                "checks": {"passed": False},
                "outcomes": {"passed": False},
            }
            project = {
                "id": "invention-001",
                "selected": "a",
                "candidates": [candidate],
                "concepts": [{"id": "a"}],
            }
            directory = campaign.directory(project, "a")
            directory.mkdir(parents=True)
            before = {
                "engine.js": "original",
                "app.js": "app",
                "style.css": "style",
                "index.html": "html",
            }
            for name, content in before.items():
                (directory / name).write_text(content)
            atomic(directory / "challenge.json", candidate["challenge"])
            snapshot(directory)
            campaign.store.call(
                client,
                "invention-001-a-repair-0",
                [{"role": "user", "content": json.dumps({"files": before})}],
                10000,
            )
            (directory / "engine.js").write_text("repaired")
            with (
                patch("studio.acceptance.check", return_value={"passed": True}),
                patch("studio.acceptance.browser_check", return_value={"passed": True}),
                patch("studio.acceptance.finish") as finished,
            ):
                repair(campaign, project)
                finished.assert_called_once()
            self.assertEqual(client.calls, 1)
            self.assertEqual(
                (directory / "revisions/before-acceptance/engine.js").read_text(),
                "original",
            )


if __name__ == "__main__":
    unittest.main()

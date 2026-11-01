"""Portfolio decisions and executable acceptance of new kinds of inventions."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.executor import browser_check
from studio.frontiers import next_direction, portfolio, validate_concept


class FrontierTests(unittest.TestCase):
    def test_browser_crashes_retry_locally_but_application_failures_do_not(self):
        crash = {
            "passed": False,
            "errors": ["Page.goto: Target page, context or browser has been closed"],
            "views": [],
        }
        failure = {
            "passed": False,
            "errors": ["Action needs one visible control: #make"],
            "views": [],
        }
        for results, calls, unavailable in (
            ([crash, {"passed": True}], 2, False),
            ([crash, crash], 2, True),
            ([failure], 1, False),
        ):
            with (
                tempfile.TemporaryDirectory() as temp,
                patch(
                    "studio.executor._browser_check_once",
                    side_effect=[dict(result) for result in results],
                ) as run,
            ):
                (Path(temp) / "browser-proof").mkdir()
                report = browser_check(temp, "/unused")
                self.assertEqual(run.call_count, calls)
                self.assertEqual(report["unavailable"], unavailable)
                self.assertEqual(
                    json.loads((Path(temp) / "browser-proof/browser.json").read_text()),
                    report,
                )

    def test_directions_do_not_follow_the_last_critique_forever(self):
        projects = [
            {
                "id": "old",
                "title": "Echo",
                "status": "complete",
                "review": {"next_brief": "Audit the echo again"},
            }
        ]
        for i, expected in enumerate(("visual", "game", "useful")):
            frontier, mode, brief, parent = next_direction(projects)
            self.assertEqual(
                (frontier["id"], mode, parent), (expected, "explore", None)
            )
            self.assertNotIn("Audit the echo again", brief)
            projects.append(
                {
                    "id": str(i),
                    "title": expected,
                    "status": "complete",
                    "contract_version": 2,
                    "frontier": frontier,
                    "mode": mode,
                    "review": {
                        "usefulness": i,
                        "next_brief": "A concrete better outcome",
                    },
                }
            )
        frontier, mode, brief, parent = next_direction(projects)
        self.assertEqual((frontier["id"], mode, parent), ("useful", "improve", "2"))
        projects.append(
            {
                "id": "3",
                "status": "complete",
                "contract_version": 2,
                "frontier": frontier,
                "mode": mode,
                "review": {},
            }
        )
        self.assertEqual(next_direction(projects)[0]["id"], "world")
        self.assertEqual(sum(p["completed"] for p in portfolio(projects)), 4)

    def test_journey_is_concrete_and_bounded_before_building(self):
        concept = {
            "title": "Tool",
            "thesis": "Change a shape",
            "interaction": "Make",
            "mechanism": "A deterministic transform",
            "payoff": "Keep an SVG",
            "engine_actions": [{"type": "set", "value": 1}],
            "alternate_actions": [{"type": "set", "value": 2}],
            "journey": {
                "steps": [{"action": "click", "selector": "#make"}],
                "result": "#result",
                "download": "#save",
            },
        }
        validate_concept(concept)
        for change in (
            {"engine_actions": []},
            {"alternate_actions": concept["engine_actions"]},
            {
                "journey": {
                    "steps": [{"action": "evaluate", "selector": "#make"}],
                    "result": "#result",
                }
            },
        ):
            with self.assertRaises(ValueError):
                validate_concept({**concept, **change})

    @unittest.skipUnless(
        shutil.which("node"), "Node is needed for action contract checks"
    )
    def test_discrete_tools_pass_without_animation_and_real_failures_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "inputs.json").write_text("{}")
            (root / "contract.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "engine_actions": [{"type": "set", "value": 3}],
                        "alternate_actions": [{"type": "set", "value": 9}],
                    }
                )
            )
            engine = "globalThis.NuriaEngine={create:seed=>({seed,value:0}),act(s,a){s.value=a.value;return s},metrics:s=>({result:s.value}),runTests:()=>({passed:true,results:[{name:'edge',pass:true},{name:'roundtrip',pass:true},{name:'reset',pass:RESULT}]})};"
            checker = Path(__file__).resolve().parents[1] / "studio/check_invention.cjs"
            for valid in (True, False):
                (root / "engine.js").write_text(
                    engine.replace("RESULT", str(valid).lower())
                )
                result = subprocess.run(
                    [shutil.which("node"), str(checker)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )
                report = json.loads(result.stdout)
                self.assertEqual(report["passed"], valid)
                self.assertEqual(report["metrics"], {"result": 3})
                self.assertEqual(report["alternate_metrics"], {"result": 9})


if __name__ == "__main__":
    unittest.main()

"""Real persistence and public boundary checks for the invention runner."""

import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from studio.executor import retained_engine, write_files
from studio.gateway import create_server
from studio.provider import load_reasoner
from studio.server import handler
from studio.store import Store
from studio.worker import Campaign, parse


class Client:
    def __init__(self):
        self.calls = 0

    def complete(self, call_id, **request):
        self.calls += 1
        return {"content": '{"ok": true}', "usage": {}}

    def budget(self):
        return {"remaining_micro_usd": 1000000, "spent_micro_usd": 0, "blocked": False}


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_settled_call_replays_across_process_reopen(self):
        client = Client()
        store = Store(self.root)
        args = ("project-stage", [{"role": "user", "content": "same input"}])
        first = store.call(client, *args)
        store.db.close()
        resumed = Store(self.root)
        self.assertEqual(first, resumed.call(client, *args))
        self.assertEqual(client.calls, 1)
        with self.assertRaises(ValueError):
            resumed.call(client, args[0], [{"role": "user", "content": "changed"}])
        resumed.db.close()

    def test_partial_stage_uses_frozen_request(self):
        client = Client()
        campaign = Campaign(self.root, client, "/usr/bin/node")
        project = {"id": "invention-001"}
        first = campaign.ask(
            project, "build-0", [{"role": "user", "content": "old files"}]
        )
        again = campaign.ask(
            project,
            "build-0",
            [{"role": "user", "content": "files already written before crash"}],
        )
        self.assertEqual(first, again)
        self.assertEqual(client.calls, 1)
        campaign.store.db.close()

    def test_feedback_cannot_change_project_or_tool_order(self):
        store = Store(self.root)
        for index in (1, 2, 3):
            pid = f"invention-{index:03d}"
            store.put("projects", pid, {"id": pid})
            store.put(
                "skills", f"tool-{index}", {"id": f"tool-{index}", "project_id": pid}
            )
        for table, key in (("projects", "invention-001"), ("skills", "tool-1")):
            value = store.get(table, key)
            value["feedback"] = "A later observation on earlier work"
            store.put(table, key, value)
        self.assertEqual(
            [p["id"] for p in store.all("projects")],
            [f"invention-{i:03d}" for i in (1, 2, 3)],
        )
        self.assertEqual(
            [s["project_id"] for s in store.all("skills")],
            [f"invention-{i:03d}" for i in (1, 2, 3)],
        )
        store.db.close()

    def test_artifact_cannot_write_outside_workspace(self):
        for name in ("../outside", "/etc/anything", "toolkit.js", "inputs.json"):
            with self.assertRaises(ValueError):
                write_files(self.root / "work", {name: "bad"})
        self.assertFalse((self.root / "outside").exists())
        write_files(self.root / "work", {"engine.js": "const a=1;"})
        self.assertTrue((self.root / "work/engine.js").exists())

    def test_feedback_survives_a_worker_step_and_cannot_rewind_progress(self):
        worker, operator = Store(self.root), Store(self.root)
        self.addCleanup(worker.db.close)
        self.addCleanup(operator.db.close)
        project = {"id": "invention-001", "status": "building"}
        worker.save(project, "began", "Starting work")
        stale_operator = operator.get("projects", project["id"])
        project["status"] = "reviewing"
        worker.save(project, "prototype", "Built the tool")
        operator.save(stale_operator, "operator_feedback", "Test a new real input")
        self.assertEqual(stale_operator["status"], "reviewing")
        project["status"] = "challenging"
        worker.save(project, "selected", "Ready for acceptance")
        worker.save(project)
        final = operator.get("projects", project["id"])
        self.assertEqual(final["status"], "challenging")
        feedback = [e for e in final["events"] if e["kind"] == "operator_feedback"]
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0]["summary"], "Test a new real input")
        self.assertEqual(
            json.loads((self.root / "projects/invention-001/project.json").read_text()),
            final,
        )

    def test_retention_preserves_owner_lessons(self):
        campaign = Campaign(self.root, Client(), "/usr/bin/node")
        self.addCleanup(campaign.store.db.close)
        with patch("studio.worker.capture", return_value={}):
            project = campaign.project("Build a useful tool")
        directory = campaign.directory(project, "a")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "engine.js").write_text("retained tool")
        project.update(
            candidates=[{"id": "a", "status": "tested", "checks": {"passed": True}}],
            selected="a",
            review={"lesson": "Critic observation"},
        )
        owner_lesson = {
            "project": project["id"],
            "lesson": "Owner observation",
            "source": "operator",
        }
        campaign.store.put("meta", "taste", {"lessons": [owner_lesson]})
        campaign.retain(project)
        self.assertIn(owner_lesson, campaign.store.get("meta", "taste")["lessons"])

    def test_rejected_model_write_is_feedback_instead_of_restart_loop(self):
        campaign = Campaign(self.root, Client(), "/usr/bin/node")
        self.addCleanup(campaign.store.db.close)
        with patch("studio.worker.capture", return_value={}):
            project = campaign.project("Build a small instrument")
        candidate = {
            "id": "a",
            "title": "First idea",
            "status": "building",
            "attempt": 0,
            "revision": 0,
        }
        project.update(
            status="building", concepts=[{"id": "a"}], candidates=[candidate]
        )
        campaign.directory(project, "a").mkdir(parents=True)
        with patch.object(
            campaign,
            "ask",
            return_value={"files": {"toolkit.js": "forbidden replacement"}},
        ):
            campaign.build(project, candidate)
        saved = campaign.store.get("projects", project["id"])["candidates"][0]
        self.assertEqual(saved["attempt"], 1)
        self.assertFalse(saved["checks"]["passed"])
        self.assertIn("Write only", saved["note"])
        self.assertFalse((campaign.directory(project, "a") / "toolkit.js").exists())

    def test_redundant_helper_does_not_discard_allowed_source(self):
        campaign = Campaign(self.root, Client(), "/usr/bin/node")
        self.addCleanup(campaign.store.db.close)
        with patch("studio.worker.capture", return_value={}):
            project = campaign.project("Build a small instrument")
        candidate = {
            "id": "a",
            "title": "First idea",
            "status": "building",
            "attempt": 0,
            "revision": 0,
        }
        project.update(
            status="building", concepts=[{"id": "a"}], candidates=[candidate]
        )
        directory = campaign.directory(project, "a")
        directory.mkdir(parents=True)
        (directory / "toolkit.js").write_text("preserved tool")
        with (
            patch.object(
                campaign,
                "ask",
                return_value={
                    "files": {
                        "toolkit.js": "forbidden replacement",
                        "engine.js": "allowed source",
                    }
                },
            ),
            patch(
                "studio.worker.check",
                return_value={"passed": False, "details": ["More files needed"]},
            ),
        ):
            campaign.build(project, candidate)
        self.assertEqual((directory / "engine.js").read_text(), "allowed source")
        self.assertEqual((directory / "toolkit.js").read_text(), "preserved tool")
        saved = campaign.store.get("projects", project["id"])["candidates"][0]
        self.assertEqual(saved["checks"]["rejected_writes"], ["toolkit.js"])

    def test_public_server_exposes_only_public_projection(self):
        public, web = self.root / "public", self.root / "web"
        public.mkdir()
        web.mkdir()
        (public / "status.json").write_text('{"phase":"creating"}')
        artifact = public / "creations/invention-001/a"
        artifact.mkdir(parents=True)
        original = '<title>World</title><script src="./app.js"></script><link href="style.css" rel="stylesheet">'
        (artifact / "index.html").write_text(original)
        (artifact / "app.js").write_text("original")
        (artifact / "style.css").write_text("body{}")
        (self.root / "private.json").write_text("private")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(public, web))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(base + "/api/studio") as result:
                self.assertEqual(json.load(result)["phase"], "creating")
            with urllib.request.urlopen(
                base + "/creations/invention-001/a/index.html"
            ) as result:
                self.assertIn(
                    "sandbox allow-scripts", result.headers["Content-Security-Policy"]
                )
                self.assertEqual(result.headers["X-Frame-Options"], "SAMEORIGIN")
                self.assertEqual(result.headers["Cache-Control"], "no-store")
                body = result.read().decode()
                self.assertIn(
                    "./app.js?v=" + hashlib.sha256(b"original").hexdigest(), body
                )
            # A repair must change the dependency URL, even when a CDN retains
            # the old canonical app.js for hours. Source evidence stays intact.
            (artifact / "app.js").write_text("repaired")
            with urllib.request.urlopen(
                base + "/creations/invention-001/a/index.html"
            ) as result:
                body = result.read().decode()
                version = hashlib.sha256(b"repaired").hexdigest()
                self.assertIn("./app.js?v=" + version, body)
            with urllib.request.urlopen(
                base + "/creations/invention-001/a/app.js?v=" + version
            ) as result:
                self.assertEqual(result.read(), b"repaired")
            self.assertEqual((artifact / "index.html").read_text(), original)
            for route in (
                "/private.json",
                "/creations/../../private.json",
                "/api/complete",
            ):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base + route)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(
                    urllib.request.Request(base + "/api/studio", data=b"{}")
                )
            self.assertEqual(error.exception.code, 501)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_budget_pause_creates_no_project_or_paid_call(self):
        client = Client()
        client.budget = lambda: {
            "remaining_micro_usd": 0,
            "spent_micro_usd": 1000000,
            "blocked": False,
        }
        campaign = Campaign(self.root, client, "/usr/bin/node")
        self.assertFalse(campaign.tick())
        self.assertEqual(campaign.status, "budget_paused")
        self.assertEqual(campaign.store.all("projects"), [])
        self.assertEqual(client.calls, 0)
        campaign.store.db.close()

    def test_autonomous_campaign_continues_after_three_and_after_failure(self):
        campaign = Campaign(self.root, Client(), "/usr/bin/node")
        self.addCleanup(campaign.store.db.close)
        with (
            patch("studio.worker.capture", return_value={}),
            patch.object(campaign, "plan"),
        ):
            for _ in range(3):
                p = campaign.project("original brief")
                p.update(
                    status="complete",
                    review={"next_brief": "Make a playable instrument"},
                )
                campaign.store.save(p)
            self.assertTrue(campaign.tick())
            fourth = campaign.store.all("projects")[-1]
            self.assertEqual(fourth["id"], "invention-004")
            self.assertEqual(fourth["parent"], "invention-003")
            self.assertIn("Make a playable instrument", fourth["brief"])
            fourth.update(
                status="stopped",
                title="A failed mechanism",
                candidates=[
                    {"checks": {"passed": False, "details": ["State is unbounded"]}}
                ],
            )
            campaign.store.save(fourth)
            self.assertTrue(campaign.tick())
            fifth = campaign.store.all("projects")[-1]
            self.assertEqual(fifth["id"], "invention-005")
            self.assertIn("State is unbounded", fifth["brief"])
            self.assertEqual(fifth["parent"], fourth["id"])

    def test_explicit_project_and_original_request_caps_still_stop(self):
        client = Client()
        campaign = Campaign(self.root, client, "/usr/bin/node", max_projects=1)
        self.addCleanup(campaign.store.db.close)
        with patch("studio.worker.capture", return_value={}):
            p = campaign.project("finite trial")
        p["status"] = "complete"
        campaign.store.save(p)
        self.assertFalse(campaign.tick())
        self.assertEqual(campaign.status, "campaign_complete")
        campaign.max_projects = 0
        client.budget = lambda: {
            "remaining_micro_usd": 1000000,
            "blocked": False,
            "remaining_requests": 0,
        }
        self.assertFalse(campaign.tick())
        self.assertEqual(campaign.status, "budget_paused")
        self.assertEqual(len(campaign.store.all("projects")), 1)
        self.assertEqual(client.calls, 0)

    def test_missing_authoritative_ledger_cannot_be_recreated(self):
        config = {
            "key_file": str(self.root / "provider.key"),
            "budget_file": str(self.root / "missing.sqlite"),
            "max_cost_micro_usd": 20000000,
            "max_requests": 800,
            "model": "test-model",
            "reasoning_effort": "low",
            "input_micro_usd_per_million_tokens": 2000000,
            "output_micro_usd_per_million_tokens": 10000000,
        }
        path = self.root / "config.json"
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        with self.assertRaises(FileNotFoundError):
            load_reasoner(path, require_existing_budget=True)
        self.assertFalse(Path(config["budget_file"]).exists())

    def test_gateway_replay_preflight_and_original_caps(self):
        class Budget:
            value = {
                "accounted_cost_and_liability_micro_usd": 5000000,
                "remaining_headroom_micro_usd": 15000000,
                "maximum_requests": 800,
                "requests": 386,
                "blocked": False,
            }

            def snapshot(self):
                return dict(self.value)

        class Reasoner:
            budget = Budget()
            model = "test-model"
            max_input_bytes = 120000
            input_rate, output_rate = 2000000, 10000000
            calls = 0

            def complete(self, messages, *, max_tokens):
                self.calls += 1
                self.budget.value["requests"] += 1
                return {"content": "{}", "usage": {"output_tokens": 1}}

        token, journal = self.root / "token", self.root / "gateway.sqlite"
        token.write_text("fixture-token")
        reasoner = Reasoner()
        with patch("studio.gateway.load_reasoner", return_value=reasoner):
            with self.assertRaises(sqlite3.OperationalError):
                create_server(
                    "unused", token, journal, 0, 14000000, require_existing_state=True
                )
            self.assertFalse(journal.exists())
            server = create_server("unused", token, journal, 0, 14000000)

            def request(payload=None):
                url = f"http://127.0.0.1:{server.server_port}"
                req = urllib.request.Request(
                    url + ("/complete" if payload else "/budget"),
                    data=json.dumps(payload).encode() if payload else None,
                    headers={"Authorization": "Bearer fixture-token"},
                )

                def send():
                    try:
                        with urllib.request.urlopen(req, timeout=3) as response:
                            return response.status, json.load(response)
                    except urllib.error.HTTPError as error:
                        code = error.code
                        error.close()
                        return code, None

                with ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(send)
                    server.handle_request()
                    return result.result()

            payload = {
                "id": "first",
                "messages": [{"role": "user", "content": "Make something"}],
                "max_tokens": 1000,
            }
            try:
                self.assertEqual(request()[1]["remaining_requests"], 414)
                self.assertEqual(
                    request(
                        {**payload, "messages": [{"role": "tool", "content": "bad"}]}
                    )[0],
                    400,
                )
                large = {
                    **payload,
                    "messages": [{"role": "user", "content": "x" * 118000}],
                }
                self.assertEqual(request(large)[0], 413)
                self.assertEqual(
                    server.journal.execute("SELECT COUNT(*) FROM calls").fetchone()[0],
                    0,
                )
                first = request(payload)
                self.assertEqual(first[0], 200)
            finally:
                server.server_close()
                server.journal.close()
            server = create_server(
                "unused", token, journal, 0, 14000000, require_existing_state=True
            )
            try:
                self.assertEqual(request(payload), first)
                self.assertEqual(reasoner.calls, 1)
                reasoner.budget.value["requests"] = 800
                self.assertEqual(request({**payload, "id": "second"})[0], 402)
                reasoner.budget.value.update(
                    requests=387, remaining_headroom_micro_usd=1
                )
                self.assertEqual(request({**payload, "id": "third"})[0], 402)
                reasoner.budget.value.update(
                    remaining_headroom_micro_usd=15000000, blocked=True
                )
                self.assertEqual(request({**payload, "id": "fourth"})[0], 409)
                self.assertEqual(reasoner.calls, 1)
            finally:
                server.server_close()
                server.journal.close()

    def test_json_protocol(self):
        self.assertEqual(parse('```json\n{"a":1}\n```'), {"a": 1})
        self.assertEqual(parse('I am building the instrument.\n\n{"a":1}'), {"a": 1})
        self.assertEqual(parse('I am building the instrument.{"a":1}'), {"a": 1})
        self.assertEqual(parse('I will test this.\n```json\n{"a":1}\n```'), {"a": 1})
        with self.assertRaises(ValueError):
            parse("[]")
        for text in (
            '{"a":1}\n{"b":2}',
            'I am building.\n{"files":',
            'I am building.\n{"a":1}\nUnparsed tail',
        ):
            with self.assertRaises(ValueError):
                parse(text)

    @unittest.skipUnless(
        shutil.which("node"), "Node is required for executable tool composition"
    )
    def test_engine_checker_executes_project_tests_and_preserves_tool_api(self):
        directory = self.root / "candidate"
        directory.mkdir()
        (directory / "inputs.json").write_text("{}")
        (directory / "toolkit.js").write_text(
            "globalThis.NuriaEngine={create(seed){return {t:0,v:seed}},step(s,dt,p){s.t+=dt;s.v+=p.energy+p.curiosity+1;return s},metrics(s){return {time:s.t,value:s.v}},answer(){return 42}};"
        )
        checker = Path(__file__).resolve().parents[1] / "studio/check_engine.cjs"
        for expected in (True, False):
            body = "NuriaToolkit.answer()===42" if expected else "false"
            (directory / "engine.js").write_text(
                "globalThis.NuriaEngine={create:NuriaToolkit.create,step:NuriaToolkit.step,metrics:NuriaToolkit.metrics,runTests(){return {passed:true,results:[{name:'extended tool API',pass:"
                + body
                + "}]}}};"
            )
            result = subprocess.run(
                [shutil.which("node"), str(checker)],
                cwd=directory,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            report = json.loads(result.stdout)
            self.assertEqual(report["passed"], expected)
            self.assertTrue(report["project_tests"]["executed"])
            self.assertEqual(
                report["project_tests"]["report"]["results"][0]["pass"], expected
            )

    @unittest.skipUnless(
        shutil.which("node"), "Node is required for executable tool composition"
    )
    def test_retained_engine_keeps_transitive_dependencies(self):
        directory = self.root / "candidate"
        directory.mkdir()
        (directory / "engine.js").write_text(
            "globalThis.NuriaEngine = {value: () => 1};"
        )
        code = retained_engine(directory)
        for _ in range(2):
            (directory / "toolkit.js").write_text(code)
            (directory / "engine.js").write_text(
                "globalThis.NuriaEngine = {value: () => globalThis.NuriaToolkit.value() + 1};"
            )
            code = retained_engine(directory)
        result = subprocess.run(
            [
                shutil.which("node"),
                "-e",
                code
                + "\nglobalThis.NuriaToolkit = NuriaEngine; console.log(NuriaEngine.value());",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "3")


if __name__ == "__main__":
    unittest.main()

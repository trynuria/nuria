"""Real persistence and public boundary checks for the invention runner."""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from studio.executor import retained_engine, write_files
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

    def test_artifact_cannot_write_outside_workspace(self):
        for name in ("../outside", "/etc/anything", "toolkit.js", "inputs.json"):
            with self.assertRaises(ValueError):
                write_files(self.root / "work", {name: "bad"})
        self.assertFalse((self.root / "outside").exists())
        write_files(self.root / "work", {"engine.js": "const a=1;"})
        self.assertTrue((self.root / "work/engine.js").exists())

    def test_public_server_exposes_only_public_projection(self):
        public, web = self.root / "public", self.root / "web"
        public.mkdir()
        web.mkdir()
        (public / "status.json").write_text('{"phase":"creating"}')
        artifact = public / "creations/invention-001/a"
        artifact.mkdir(parents=True)
        (artifact / "index.html").write_text("<title>World</title>")
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

    def test_json_protocol(self):
        self.assertEqual(parse('```json\n{"a":1}\n```'), {"a": 1})
        with self.assertRaises(ValueError):
            parse("[]")

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

"""Actual HTTP updates, full recovery, conditional caching and browser decoding."""

import gzip
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
from unittest.mock import patch

from studio.live_state import SnapshotCache
from studio.server import handler
from studio.sparse_patch import make_patch
from studio.store import atomic


class LiveStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "status.json"
        self.document = {
            "projects": [{"id": "invention-001", "payload": list(range(4000))}],
            "a/~": {"": 1},
            "heartbeat": 1,
        }
        atomic(self.path, self.document)

    def start(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.root, self.root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(cleanup)
        return f"http://127.0.0.1:{server.server_port}/api/studio"

    def test_http_full_delta_unchanged_and_stale_recovery(self):
        url = self.start()
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        ) as response:
            self.assertEqual(
                json.loads(gzip.decompress(response.read())), self.document
            )
            version = response.headers["X-Studio-Version"]
            etag = response.headers["ETag"]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                urllib.request.Request(url, headers={"If-None-Match": etag})
            )
        self.assertEqual(caught.exception.code, 304)
        caught.exception.close()
        with urllib.request.urlopen(url + "?since=" + version) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.read(), b"")
        self.document["heartbeat"] = 2
        self.document["a/~"][""] = True
        atomic(self.path, self.document)
        with urllib.request.urlopen(url + "?since=" + version) as response:
            self.assertEqual(response.headers["X-Studio-Update"], "patch-v1")
            self.assertEqual(response.headers["X-Studio-Base"], version)
            operations = json.load(response)
            self.assertIn([2, "/a~1~0/", True], operations)
            self.assertIn([2, "/heartbeat", 2], operations)
        for _ in range(3):
            self.document["heartbeat"] += 1
            atomic(self.path, self.document)
            with urllib.request.urlopen(url) as response:
                response.read()
        with urllib.request.urlopen(url + "?since=" + version) as response:
            self.assertEqual(response.headers["X-Studio-Update"], "full")
            self.assertEqual(json.load(response), self.document)
        with urllib.request.urlopen(
            urllib.request.Request(
                url, headers={"Accept-Encoding": "gzip;q=0"}, method="HEAD"
            )
        ) as response:
            self.assertIsNone(response.headers.get("Content-Encoding"))
            self.assertEqual(
                int(response.headers["Content-Length"]), self.path.stat().st_size
            )
            self.assertEqual(response.read(), b"")

    def test_cached_requests_do_not_repeat_compression_or_diff(self):
        cache = SnapshotCache(self.path, make_patch)
        _, _, headers = cache.response(compressed=True)
        base = headers["X-Studio-Version"]
        self.document["heartbeat"] += 1
        atomic(self.path, self.document)
        expected = cache.response(base=base, compressed=True)
        with patch(
            "studio.live_state.gzip.compress",
            side_effect=AssertionError("recompressed"),
        ):
            with patch.object(
                cache, "make_patch", side_effect=AssertionError("recomputed")
            ):
                self.assertEqual(cache.response(base=base, compressed=True), expected)
                self.assertEqual(cache.response(compressed=True)[0], 200)

    @unittest.skipUnless(
        shutil.which("node"), "Node required for actual browser decoder"
    )
    def test_browser_reconstruction_and_one_read_recovery(self):
        web = Path(__file__).resolve().parents[1] / "studio/web"
        cases = [
            (
                {"a/b": {"~": [1, 2, 3]}, "same": "retained"},
                {"a/b": {"~": [0, 1, 3, 4]}, "same": "retained"},
            ),
            ({"x": 1}, {"x": True, "y": None}),
            ([1, 2], {"x": [3]}),
        ]
        from studio.live_state import compact_patch

        payload = [
            {"old": a, "new": b, "patch": compact_patch(make_patch(a, b))}
            for a, b in cases
        ]
        script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
vm.runInThisContext(fs.readFileSync(process.argv[1]+'/json-patch.js','utf8'));
vm.runInThisContext(fs.readFileSync(process.argv[1]+'/live-state.js','utf8'));
const cases=JSON.parse(fs.readFileSync(0,'utf8')),base='a'.repeat(64),next='b'.repeat(64);
const response=(data,kind='patch-v1',extra={})=>new Response(JSON.stringify(data),{headers:{'X-Studio-Update':kind,'X-Studio-Base':base,'X-Studio-Version':next,...extra}});
(async()=>{
 for(const c of cases){
  const before=JSON.stringify(c.old);let calls=0;
  const r=await NuriaLiveState.read(c.old,base,undefined,async()=>{calls++;return response(c.patch)});
  assert.deepEqual(r.data,c.new);assert.equal(JSON.stringify(c.old),before);assert.equal(calls,1);
 }
 for(const bad of [response([[2,'/missing/child',1]]),response([],'patch-v1',{'X-Studio-Base':next}),response([[9,'/x',1]]),response([[0,'/__proto__/polluted',1]])]){
  let calls=0;const old={x:1};
  const r=await NuriaLiveState.read(old,base,undefined,async()=>++calls===1?bad:response({x:2,all:'retained'},'full'));
  assert.deepEqual(r.data,{x:2,all:'retained'});assert.equal(calls,2);assert.deepEqual(old,{x:1});assert.equal({}.polluted,undefined);
 }
 let r=await NuriaLiveState.read({x:1},base,undefined,async()=>new Response(null,{status:204,headers:{'X-Studio-Version':base}}));assert.deepEqual(r.data,{x:1});
 r=await NuriaLiveState.read(null,null,undefined,async()=>new Response('{"x":1}'));assert.equal(r.version,null);
 console.log('Browser reconstruction, immutability, rollback, prototype and recovery checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script, str(web)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

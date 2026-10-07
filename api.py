"""Read-only cached evidence API. No database, RPC or signing credentials."""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PUBLIC = Path(os.environ["NURIA_PUBLIC"])
ROUTES = {
    "/api/status": "status.json",
    "/api/topology": "topology.json",
    "/api/events": "events.json",
    "/api/receipts": "receipts.json",
    "/api/verify": "verify.json",
    "/download/ledger": "ledger.json",
    "/download/state": "network-state.json",
}
CACHE = {}


def load(name):
    now = time.monotonic()
    if name not in CACHE or now - CACHE[name][0] > 1:
        path = (
            PUBLIC
            / (
                "engine"
                if name
                in ("status.json", "topology.json", "events.json", "receipts.json")
                else "verify"
            )
            / name
        )
        CACHE[name] = (now, path.read_bytes(), path.stat().st_mtime)
    return CACHE[name][1:]


class Handler(BaseHTTPRequestHandler):
    def send(self, raw, status=200, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "public,max-age=2")
        if filename:
            self.send_header(
                "Content-Disposition", 'attachment; filename="' + filename + '"'
            )
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/healthz":
                raw, stamp = load("status.json")
                state = json.loads(raw)
                ok = state.get("phase") == "running" and time.time() - stamp < 15
                return self.send(
                    json.dumps(
                        {
                            "healthy": ok,
                            "updated_utc": state.get("updated_utc"),
                            "tick": state.get("tick"),
                        }
                    ).encode(),
                    200 if ok else 503,
                )
            if path not in ROUTES:
                return self.send(b'{"error":"Not found"}', 404)
            raw, stamp = load(ROUTES[path])
            if path == "/api/status":
                state = json.loads(raw)
                try:
                    state["deployment"] = json.loads(
                        Path("/opt/nuria/release.json").read_text()
                    )
                except (OSError, ValueError):
                    pass
                raw = json.dumps(state, separators=(",", ":")).encode()
            if path.startswith("/api/") and time.time() - stamp > (
                900 if path == "/api/verify" else 15
            ):
                return self.send(b'{"error":"Evidence unavailable or stale"}', 503)
            self.send(
                raw,
                filename={
                    "/download/ledger": "nuria-evidence.json",
                    "/download/state": "nuria-network-state.json",
                }.get(path),
            )
        except (OSError, ValueError):
            self.send(b'{"error":"Evidence unavailable"}', 503)

    def do_POST(self):
        self.send(b'{"error":"Read-only API"}', 405)

    def log_message(self, *_):
        pass


ThreadingHTTPServer(("127.0.0.1", 3040), Handler).serve_forever()

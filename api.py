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
    "/api/cognition/status": "cognition/status.json",
    "/api/cognition/topology": "cognition/topology.json",
    "/api/cognition/decisions": "cognition/decisions.json",
    "/api/cognition/effects": "cognition/effects.json",
    "/api/cognition/benchmark": "cognition/benchmark.json",
    "/api/discovery": "discovery/status.json",
    "/api/treasury": "treasury/treasury.json",
    "/api/commerce": "commerce/status.json",
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
            PUBLIC / name
            if "/" in name
            else PUBLIC
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
                cognitive_raw, cognitive_stamp = load("cognition/status.json")
                cognitive = json.loads(cognitive_raw)
                discovery_raw, discovery_stamp = load("discovery/status.json")
                discovery = json.loads(discovery_raw)
                ok = (
                    state.get("phase") == "running"
                    and time.time() - stamp < 15
                    and cognitive.get("phase") == "running"
                    and time.time() - cognitive_stamp < 15
                    and discovery.get("phase") == "running"
                    and time.time() - discovery_stamp < 15
                )
                return self.send(
                    json.dumps(
                        {
                            "healthy": ok,
                            "updated_utc": state.get("updated_utc"),
                            "tick": state.get("tick"),
                            "cognitive_tick": cognitive.get("tick"),
                            "discovery_trial": discovery.get("trials"),
                        }
                    ).encode(),
                    200 if ok else 503,
                )
            if path not in ROUTES:
                return self.send(b'{"error":"Not found"}', 404)
            raw, stamp = load(ROUTES[path])
            if path in ("/api/status", "/api/cognition/status"):
                state = json.loads(raw)
                try:
                    state["deployment"] = json.loads(
                        Path("/opt/nuria/release.json").read_text()
                    )
                except (OSError, ValueError):
                    pass
                raw = json.dumps(state, separators=(",", ":")).encode()
            if (
                path.startswith("/api/")
                and path not in ("/api/cognition/topology", "/api/cognition/benchmark")
                and time.time() - stamp
                > (
                    900
                    if path == "/api/verify"
                    else 60
                    if path in ("/api/treasury", "/api/commerce")
                    else 15
                )
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


ThreadingHTTPServer(
    ("127.0.0.1", int(os.environ.get("NURIA_API_PORT", "3040"))), Handler
).serve_forever()

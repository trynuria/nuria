"""Loopback-only inference bridge sharing the existing durable allowance.

The operator's provider key and allowance database stay in their original
location. SSH forwards this private interface to the isolated project worker.
Request IDs survive lost responses; ambiguous provider calls are never retried.
"""

import argparse
import hmac
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from studio.provider import load_reasoner
from studio.store import digest


def serve(config, token_file, journal, port, ceiling):
    reasoner = load_reasoner(config)
    token = Path(token_file).read_text().strip()
    db = sqlite3.connect(journal)
    db.execute(
        "CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, hash TEXT, state TEXT, response TEXT)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY, origin INTEGER, ceiling INTEGER)"
    )
    origin = reasoner.budget.snapshot()["accounted_cost_and_liability_micro_usd"]
    db.execute("INSERT OR IGNORE INTO policy VALUES (1,?,?)", (origin, ceiling))
    db.commit()
    saved_origin, saved_ceiling = db.execute(
        "SELECT origin,ceiling FROM policy WHERE id=1"
    ).fetchone()
    if saved_ceiling != ceiling:
        raise ValueError("The campaign allowance cannot change on restart")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authenticated(self):
            return hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token
            )

        def do_GET(self):
            if not self.authenticated():
                return self.reply(403, {"error": "forbidden"})
            budget = reasoner.budget.snapshot()
            used = budget["accounted_cost_and_liability_micro_usd"] - saved_origin
            self.reply(
                200,
                {
                    "remaining_micro_usd": max(
                        0,
                        min(
                            budget["remaining_headroom_micro_usd"], saved_ceiling - used
                        ),
                    ),
                    "spent_micro_usd": used,
                    "blocked": budget["blocked"],
                    "model": reasoner.model,
                },
            )

        def do_POST(self):
            if self.path != "/complete" or not self.authenticated():
                return self.reply(403, {"error": "forbidden"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 120000:
                    raise ValueError("request_limit")
                req = json.loads(self.rfile.read(size))
                if (
                    set(req) != {"id", "messages", "max_tokens"}
                    or not isinstance(req["id"], str)
                    or len(req["id"]) > 128
                ):
                    raise ValueError("invalid_request")
                identity = digest(req)
                row = db.execute(
                    "SELECT hash,state,response FROM calls WHERE id=?", (req["id"],)
                ).fetchone()
                if row:
                    if row[0] != identity:
                        return self.reply(409, {"error": "request_identity_changed"})
                    if row[1] == "settled":
                        return self.reply(200, json.loads(row[2]))
                    return self.reply(409, {"error": "prior_request_unresolved"})
                limit = req["max_tokens"]
                if type(limit) is not int or not 256 <= limit <= 12000:
                    raise ValueError("output_limit")
                budget = reasoner.budget.snapshot()
                # Match the existing client's conservative UTF-8 input envelope.
                bound = (
                    (size + 2048 + len(req["messages"]) * 128)
                    * reasoner.input_rate
                    // 1000000
                    + limit * reasoner.output_rate // 1000000
                    + 1000
                )
                if (
                    budget["accounted_cost_and_liability_micro_usd"]
                    - saved_origin
                    + bound
                    > saved_ceiling
                ):
                    return self.reply(402, {"error": "campaign_budget_exhausted"})
                db.execute(
                    "INSERT INTO calls VALUES (?,?,?,NULL)",
                    (req["id"], identity, "pending"),
                )
                db.commit()
                try:
                    result = reasoner.complete(req["messages"], max_tokens=limit)
                except Exception as error:
                    # A known-settled incomplete response can be repaired under
                    # a different ID. Unknown settlement remains a hard stop.
                    result = {
                        "error": getattr(error, "code", type(error).__name__),
                        "usage": getattr(error, "usage", None),
                    }
                db.execute(
                    "UPDATE calls SET state=?,response=? WHERE id=?",
                    ("settled", json.dumps(result), req["id"]),
                )
                db.commit()
                return self.reply(200, result)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                return self.reply(400, {"error": "invalid_request"})

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--port", type=int, default=19441)
    parser.add_argument("--ceiling", type=int, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    serve(args.config, args.token_file, args.journal, args.port, args.ceiling)

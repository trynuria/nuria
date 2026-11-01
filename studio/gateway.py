"""Loopback-only inference service sharing the existing durable allowance.

The provider key and authoritative ledger belong to a separate service user.
The isolated project worker receives only a private loopback capability.
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


def create_server(
    config, token_file, journal, port, ceiling, *, require_existing_state=False
):
    reasoner = load_reasoner(config, require_existing_budget=require_existing_state)
    token = Path(token_file).read_text().strip()
    if not token:
        raise ValueError("A private inference token is required")
    if require_existing_state:
        db = sqlite3.connect(Path(journal).absolute().as_uri() + "?mode=rw", uri=True)
        if (
            db.execute("SELECT origin,ceiling FROM policy WHERE id=1").fetchone()
            is None
        ):
            raise ValueError("The existing inference journal must be preserved")
        db.execute("SELECT id,hash,state,response FROM calls").fetchall()
    else:
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
    if origin < saved_origin:
        raise ValueError("The shared ledger predates this inference journal")

    def blocked(budget):
        return (
            budget["blocked"]
            or db.execute(
                "SELECT 1 FROM calls WHERE state != 'settled' LIMIT 1"
            ).fetchone()
            is not None
        )

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
                    "blocked": blocked(budget),
                    "remaining_requests": max(
                        0, budget["maximum_requests"] - budget["requests"]
                    ),
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
                    type(req) is not dict
                    or set(req) != {"id", "messages", "max_tokens"}
                    or not isinstance(req["id"], str)
                    or not 1 <= len(req["id"]) <= 128
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
                messages = req["messages"]
                if type(messages) is not list or not 1 <= len(messages) <= 64:
                    raise ValueError("message_limit")
                for message in messages:
                    if (
                        type(message) is not dict
                        or set(message) != {"role", "content"}
                        or message["role"] not in ("system", "user", "assistant")
                        or type(message["content"]) is not str
                    ):
                        raise ValueError("invalid_message")
                if size + 2048 > reasoner.max_input_bytes:
                    return self.reply(413, {"error": "input_limit"})
                budget = reasoner.budget.snapshot()
                if blocked(budget):
                    return self.reply(409, {"error": "prior_request_unresolved"})
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
                    or bound > budget["remaining_headroom_micro_usd"]
                    or budget["requests"] >= budget["maximum_requests"]
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

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.journal = db
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--port", type=int, default=19441)
    parser.add_argument("--ceiling", type=int, required=True)
    parser.add_argument("--require-existing-state", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    server = create_server(
        args.config,
        args.token_file,
        args.journal,
        args.port,
        args.ceiling,
        require_existing_state=args.require_existing_state,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        server.journal.close()

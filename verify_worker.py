"""Incremental receipt checks and bounded evidence exports, outside web requests."""

import json
import os
import signal
import threading
import time
import zlib
from pathlib import Path

from life import canonical, digest, utc
from publish import publish
from store import connect

PUBLIC = Path(os.environ["NURIA_PUBLIC"])
stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop.set())
signal.signal(signal.SIGINT, lambda *_: stop.set())
seq = 0
previous = "0" * 64
last_full = time.monotonic()
last_export = 0
while not stop.is_set():
    try:
        if time.monotonic() - last_full > 86400:
            seq = 0
            previous = "0" * 64
            last_full = time.monotonic()
        with connect(None) as db:
            head = db.execute(
                "SELECT seq,hash FROM receipts ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            end = head[0] if head else 0
            rows = db.execute(
                "SELECT seq,previous_hash,hash,payload FROM receipts WHERE seq>? AND seq<=? ORDER BY seq",
                (seq, end),
            ).fetchall()
            for number, parent, current, payload in rows:
                if (
                    number != seq + 1
                    or parent != previous
                    or digest(parent.encode() + payload.encode()) != current
                ):
                    raise RuntimeError("Receipt chain mismatch")
                value = json.loads(payload)
                raw = db.execute(
                    "SELECT data FROM spike_windows WHERE seq=?", (number,)
                ).fetchone()
                if not raw or digest(zlib.decompress(raw[0])) != value["spikes_sha256"]:
                    raise RuntimeError("Spike evidence mismatch")
                if len(value["input_ids"]) != len(value["input_evidence_sha256"]):
                    raise RuntimeError("Input evidence length mismatch")
                for ident, expected in zip(
                    value["input_ids"], value["input_evidence_sha256"]
                ):
                    event = db.execute(
                        "SELECT payload,receipt FROM inputs WHERE id=?", (ident,)
                    ).fetchone()
                    if (
                        not event
                        or event[1] != number
                        or digest(canonical(json.loads(event[0]))) != expected
                    ):
                        raise RuntimeError("Input evidence mismatch")
                seq = number
                previous = current
            result = {
                "valid": True,
                "checked": seq,
                "head": previous,
                "verified_utc": utc(),
                "scope": "Local input, spike and receipt hash integrity. No independent witness or Solana anchoring.",
            }
            publish(PUBLIC, "verify.json", result)
            if time.monotonic() - last_export > 30:
                receipts = list(
                    reversed(
                        db.execute(
                            "SELECT seq,previous_hash,hash,payload FROM receipts WHERE seq<=? ORDER BY seq DESC LIMIT 300",
                            (end,),
                        ).fetchall()
                    )
                )
                start = receipts[0][0] if receipts else 0
                inputs = db.execute(
                    "SELECT payload,receipt FROM inputs WHERE receipt>=? AND receipt<=? ORDER BY rowid",
                    (start, end),
                ).fetchall()
                spikes = {
                    r[0]: json.loads(zlib.decompress(r[1]))
                    for r in db.execute(
                        "SELECT seq,data FROM spike_windows WHERE seq>=? AND seq<=?",
                        (start, end),
                    )
                }
                publish(
                    PUBLIC,
                    "ledger.json",
                    {
                        "receipts": [
                            dict(
                                json.loads(r[3]),
                                seq=r[0],
                                previous_hash=r[1],
                                hash=r[2],
                            )
                            for r in receipts
                        ],
                        "inputs": [
                            dict(json.loads(r[0]), receipt=r[1]) for r in inputs
                        ],
                        "spike_windows": spikes,
                        "scope": "Latest 300 durable ticks; complete history retained privately",
                        "integrity": result,
                    },
                )
                try:
                    publish(
                        PUBLIC,
                        "network-state.json",
                        {
                            "state": json.loads(
                                (PUBLIC.parent / "engine/status.json").read_text()
                            ),
                            "topology": json.loads(
                                (PUBLIC.parent / "engine/topology.json").read_text()
                            ),
                        },
                    )
                except (OSError, ValueError):
                    pass
                last_export = time.monotonic()
    except Exception as exc:
        publish(
            PUBLIC,
            "verify.json",
            {
                "valid": False,
                "checked": seq,
                "first_failure": seq + 1,
                "reason": str(exc)
                if isinstance(exc, RuntimeError)
                else "Verification unavailable",
                "verified_utc": utc(),
            },
        )
    stop.wait(10)

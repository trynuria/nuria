"""Bounded receipt checks and evidence exports, outside neural/web workers."""

import json
import os
import signal
import threading
import time
from pathlib import Path

from publish import publish
from receipt_verifier import ChainChanged, ReceiptVerifier


def read_bounded_json(path, maximum=4 * 1024 * 1024):
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Network-state export exceeds size limit")
    return json.loads(raw)


def run(public, connect, stop, *, batch_size=256):
    """Drain a fixed scan target in bounded batches; never load Brian2."""
    verifier = ReceiptVerifier(batch_size=batch_size)
    last_full = time.monotonic()
    last_export = 0.0
    while not stop.is_set():
        result = None
        try:
            if time.monotonic() - last_full >= 86400:
                verifier.reset()
                last_full = time.monotonic()
            with connect() as db:
                result = verifier.advance(db)
                publish(public, "verify.json", result)
                if verifier.seq and time.monotonic() - last_export >= 30:
                    publish(public, "ledger.json", verifier.export(db, result))
                    last_export = time.monotonic()
                    try:
                        network = {
                            "state": read_bounded_json(
                                public.parent / "engine/status.json"
                            ),
                            "topology": read_bounded_json(
                                public.parent / "engine/topology.json"
                            ),
                        }
                        if len(json.dumps(network).encode()) > 8 * 1024 * 1024:
                            raise ValueError("Network-state export exceeds size limit")
                        publish(
                            public,
                            "network-state.json",
                            network,
                        )
                    except (OSError, ValueError):
                        pass
        except Exception as exc:
            result = verifier.failure(exc)
            publish(public, "verify.json", result)
            if isinstance(exc, ChainChanged):
                # A restored/replaced database must earn a fresh full scan. No
                # public status file or unchecked persisted cursor is trusted.
                verifier.reset()
                last_full = time.monotonic()
        # Backlog work yields cooperatively instead of sleeping ten seconds per
        # batch. Errors back off; a current, checked head uses the ordinary poll.
        delay = 0.05 if result and result["phase"] == "catching_up" else 10
        stop.wait(delay)


def main():
    # The PostgreSQL adapter is imported only by the service entry point.
    from store import connect

    public = Path(os.environ["NURIA_PUBLIC"])
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run(public, lambda: connect(None), stop)


if __name__ == "__main__":
    main()

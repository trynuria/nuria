"""Single authoritative neural writer. Public readers consume cached files."""

import hashlib
import os
import signal
from pathlib import Path

from life import Life
from publish import publish

ROOT = Path(__file__).parent
DATA = Path(os.environ["NURIA_DATA"])
PUBLIC = Path(os.environ["NURIA_PUBLIC"])
SOURCE = hashlib.sha256(
    b"".join(
        (ROOT / f).read_bytes()
        for f in (
            "engine.py",
            "life.py",
            "store.py",
            "pump_feed.py",
            "ingest.py",
            "api.py",
            "index.html",
            "pump-idl.json",
            "pump_amm-idl.json",
        )
    )
).hexdigest()
life = Life(DATA / "continuous", SOURCE)


def stop(*_):
    life.stop.set()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
life.start()
while life.thread.is_alive():
    try:
        publish(PUBLIC, "status.json", life.snapshot())
        if life.topology:
            publish(PUBLIC, "topology.json", life.topology)
        publish(PUBLIC, "events.json", life.events())
        publish(PUBLIC, "receipts.json", life.receipts())
    except Exception:
        pass
    life.stop.wait(1)
life.thread.join()
publish(PUBLIC, "status.json", life.snapshot())
if life.state.get("phase") == "error":
    raise SystemExit(1)

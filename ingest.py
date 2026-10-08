"""Isolated, bounded Helius reader; no public API process receives a key."""

import os
import signal
import threading
from pathlib import Path

from life import utc
from publish import publish
from pump_feed import PumpFeed
from store import connect


class Queue:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.feed_state = {}

    def db(self):
        return connect(None)


queue = Queue()
feed = PumpFeed(queue, Path(__file__).parent)


def stop(*_):
    queue.stop.set()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
feed.thread.start()
while feed.thread.is_alive():
    with queue.lock:
        publish(os.environ["NURIA_FEED_DIR"], "status.json", queue.feed_state)
        if os.environ.get("NURIA_TOKEN_PUBLIC"):
            token = queue.feed_state.get("token", {})
            publish(
                os.environ["NURIA_TOKEN_PUBLIC"],
                "status.json",
                {
                    **token,
                    "phase": queue.feed_state.get("phase", "unknown"),
                    "updated_utc": utc(),
                    "quote_mint": queue.feed_state.get("quote_mint"),
                    "quote_unit": queue.feed_state.get("quote_unit"),
                    "quote_decimals": queue.feed_state.get("quote_decimals"),
                    "verified_creator": queue.feed_state.get("creator_wallet"),
                    "identity_verified_slot": queue.feed_state.get(
                        "identity_verified_slot"
                    ),
                    "addresses": queue.feed_state.get("addresses", []),
                    "error": queue.feed_state.get("error"),
                },
            )
    queue.stop.wait(1)
feed.thread.join(20)

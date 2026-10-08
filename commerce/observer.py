"""Financial-wallet reconciliation outside the purchase and neural paths."""

import fcntl
import json
import os
import signal
import threading
import time
from pathlib import Path

from cognition.fee_observer import rpc
from commerce.config import Policy
from commerce.fees import WSOL
from commerce.ledger import Ledger
from commerce.solana import associated
from commerce.wallets import WalletObserver
from publish import publish


def main():
    private, public = (
        Path(os.environ["NURIA_COMMERCE_DATA"]),
        Path(os.environ["NURIA_COMMERCE_PUBLIC"]),
    )
    ledger = Ledger(private / "commerce.sqlite3")
    lock = (private / "observer.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    last_request = 0

    def paced(method, params):
        nonlocal last_request
        time.sleep(max(0, 0.5 - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        return rpc(method, params)

    observer = WalletObserver(ledger, paced)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        status = {
            "checked_at": time.time(),
            "phase": "unconfigured",
            "scope": "Finalized financial-wallet and USDC-account history, within RPC retention. Gaps remain explicit.",
        }
        try:
            policy = Policy.load(
                json.loads(Path(os.environ["NURIA_COMMERCE_CONFIG"]).read_text())
            )
            wallets = [
                v
                for v in (
                    policy.creator_wallet,
                    policy.spending_wallet,
                    policy.reserve_wallet,
                )
                if v
            ]
            if wallets:
                observer.scan(
                    [
                        *wallets,
                        *(associated(w) for w in wallets),
                        *(associated(w, WSOL) for w in wallets),
                    ],
                    time.time(),
                    transaction_budget=100,
                )
                status["phase"] = "observing"
        except Exception:
            status["phase"] = "unknown"
            status["error"] = (
                "Financial history check failed; no balance or coverage inferred."
            )
        publish(public, "observer.json", status)
        stop.wait(30)


if __name__ == "__main__":
    main()

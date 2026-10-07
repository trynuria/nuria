"""Read-only finalized treasury observations using server-held Helius access."""

from __future__ import annotations

import json
import os
import signal
import threading
import time
import urllib.request
from pathlib import Path

from solders.pubkey import Pubkey

from cognition.treasury import public_treasury
from publish import publish


def rpc(method: str, params: list):
    key = os.environ.get("HELIUS_API_KEY")
    if not key:
        raise RuntimeError("Treasury RPC access is not configured")
    try:
        request = urllib.request.Request(
            os.environ.get("HELIUS_RPC_URL")
            or "https://mainnet.helius-rpc.com/?api-key=" + key,
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
        if result.get("error"):
            raise ValueError("RPC rejected request")
        return result["result"]
    except Exception:
        raise RuntimeError("Treasury RPC failed; balance evidence is unknown") from None


def observe(configuration: dict, fetch=rpc) -> dict:
    mint, wallet = configuration.get("mint"), configuration.get("creator_wallet")
    if not mint or not wallet:
        return public_treasury()
    Pubkey.from_string(mint)
    Pubkey.from_string(wallet)
    result = fetch("getBalance", [wallet, {"commitment": "finalized"}])
    balance = result.get("value")
    slot = result.get("context", {}).get("slot")
    if type(balance) is not int or balance < 0 or type(slot) is not int or slot < 0:
        raise RuntimeError("Treasury RPC returned invalid evidence")
    return public_treasury(
        {
            "mint": mint,
            "wallet": wallet,
            "balance_lamports": balance,
            "checked_at": time.time(),
            "phase": "observed",
            "slot": slot,
            "verified_fee_receipts": None,
            "creator_fee_accrual": None,
            "scope": "Finalized wallet balance; fee provenance remains unverified",
        }
    )


def main() -> None:
    public = Path(os.environ["NURIA_TREASURY_PUBLIC"])
    configuration = Path(os.environ["NURIA_TREASURY_CONFIG"])
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        try:
            config = (
                json.loads(configuration.read_text()) if configuration.exists() else {}
            )
            result = observe(config)
        except Exception:
            result = public_treasury()
            result["phase"] = "unknown"
            result["error"] = (
                "Treasury evidence unavailable; no balance or fee amount inferred"
            )
        publish(public, "treasury.json", result)
        stop.wait(20)


if __name__ == "__main__":
    main()

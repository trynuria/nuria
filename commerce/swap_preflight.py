"""Current Jupiter route, fee and economic checks without any signing authority."""

import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlencode

from commerce.config import USDC
from commerce.fees import WSOL, program_identity
from commerce.solana import associated
from commerce.swaps import (
    JUPITER,
    conversion_economics,
    prepare,
    resolved_accounts,
    temporary_native_cleanup,
)
from commerce.transport import request


def inspect(wallet, amount, floor, reserve, fetch, http=request):
    if any(type(n) is not int or n <= 0 for n in (amount, floor, reserve)):
        raise ValueError(
            "Explicit positive native input, USDC floor and reserve required"
        )
    key = os.environ.get("JUPITER_API_KEY")
    if not key:
        raise ValueError("Project Jupiter API access is missing")
    endpoint = "https://api.jup.ag/swap/v2/build?" + urlencode(
        {
            "inputMint": WSOL,
            "outputMint": USDC,
            "amount": str(amount),
            "taker": wallet,
            "slippageBps": "50",
        }
    )
    code, _, raw = http(endpoint, {"x-api-key": key})
    if code != 200:
        raise ValueError("Current Jupiter route is unavailable")
    build, added = temporary_native_cleanup(json.loads(raw), wallet, fetch)
    tx, _ = prepare(build, wallet, amount, floor, 50, fetch)
    keys, addresses = resolved_accounts(tx.message, build)
    before = fetch(
        "getMultipleAccounts",
        [addresses, {"encoding": "base64", "commitment": "finalized"}],
    )
    if added and before["value"][addresses.index(associated(wallet, WSOL))] is not None:
        raise ValueError("Temporary native account assumption changed")
    message = b"\x80" + bytes(tx.message)
    fee = fetch(
        "getFeeForMessage",
        [base64.b64encode(message).decode(), {"commitment": "finalized"}],
    )["value"]
    if type(fee) is not int or not 0 < fee <= 100_000:
        raise ValueError("Conversion fee quote is unknown or excessive")
    simulation = fetch(
        "simulateTransaction",
        [
            base64.b64encode(bytes(tx)).decode(),
            {
                "encoding": "base64",
                "sigVerify": False,
                "commitment": "finalized",
                "minContextSlot": before["context"]["slot"],
                "accounts": {"encoding": "base64", "addresses": addresses},
            },
        ],
    )
    if (
        type(simulation.get("context", {}).get("slot")) is not int
        or simulation["context"]["slot"] < before["context"]["slot"]
    ):
        raise ValueError("Conversion simulation predates its account snapshot")
    economics = conversion_economics(
        keys,
        addresses,
        before["value"],
        simulation["value"],
        wallet,
        amount,
        floor,
        fee,
        reserve,
    )
    return {
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "wallet": wallet,
        "simulation_only": True,
        "transaction_signed": False,
        "broadcast": False,
        "scope": "Current unsigned simulation, not custody approval, a finalized conversion or a renewed spending allowance.",
        "snapshot_slot": before["context"]["slot"],
        "simulation_slot": simulation["context"]["slot"],
        "minimum_micro_usdc": floor,
        "quoted_micro_usdc": int(build["outAmount"]),
        "slippage_bps": 50,
        "quote_sha256": hashlib.sha256(raw).hexdigest(),
        "message_sha256": hashlib.sha256(message).hexdigest(),
        "temporary_native_cleanup_added": added,
        "simulated_economics": economics,
        "deployment": program_identity(fetch, JUPITER),
    }


def main():
    from cognition.fee_observer import rpc

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--input-lamports", type=int, required=True)
    parser.add_argument("--minimum-micro-usdc", type=int, required=True)
    parser.add_argument("--reserve-lamports", type=int, required=True)
    args = parser.parse_args()
    try:
        result = inspect(
            args.wallet,
            args.input_lamports,
            args.minimum_micro_usdc,
            args.reserve_lamports,
            rpc,
        )
    except Exception:
        parser.exit(
            1,
            "Unsigned conversion preflight failed; no signature or broadcast attempted. Provider details redacted.\n",
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

"""Exact SPL evidence checks using a privately supplied RPC callable."""

import base64
import time

from solders.pubkey import Pubkey

from commerce.config import ATA, TOKEN, USDC


def associated(owner, mint=USDC):
    return str(
        Pubkey.find_program_address(
            [
                bytes(Pubkey.from_string(owner)),
                bytes(Pubkey.from_string(TOKEN)),
                bytes(Pubkey.from_string(mint)),
            ],
            Pubkey.from_string(ATA),
        )[0]
    )


def usdc_balance(wallet, fetch, minimum_slot=0):
    result = fetch(
        "getAccountInfo",
        [
            associated(wallet),
            {
                "encoding": "jsonParsed",
                "commitment": "finalized",
                "minContextSlot": minimum_slot,
            },
        ],
    )
    if (
        type(result.get("context", {}).get("slot")) is not int
        or result["context"]["slot"] < minimum_slot
    ):
        raise ValueError("USDC balance predates a verified payment")
    account = result.get("value")
    if not account:
        return {
            "micro_usdc": 0,
            "checked_at": time.time(),
            "slot": result["context"]["slot"],
            "account_exists": False,
        }
    if account.get("owner") != TOKEN:
        raise ValueError("Spending token account has an unexpected program")
    info = account["data"]["parsed"]["info"]
    amount = info["tokenAmount"]
    if (
        info.get("mint") != USDC
        or info.get("owner") != wallet
        or amount.get("decimals") != 6
        or info.get("state") != "initialized"
        or info.get("delegate")
    ):
        raise ValueError("Spending USDC account does not match policy")
    units = amount["amount"]
    if not isinstance(units, str) or not units.isdigit():
        raise ValueError("Invalid USDC balance evidence")
    return {
        "micro_usdc": int(units),
        "checked_at": time.time(),
        "slot": result["context"]["slot"],
        "account_exists": True,
    }


def settlement(signature, client_signature, wallet, recipient, units, fetch):
    """Verify the authorized message's client signature and exact token deltas."""
    from solders.signature import Signature

    Signature.from_string(signature)
    tx = fetch(
        "getTransaction",
        [
            signature,
            {
                "encoding": "json",
                "commitment": "finalized",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )
    if not tx:
        return None
    if (
        tx["meta"].get("err") is not None
        or client_signature not in tx["transaction"]["signatures"]
        or tx["transaction"]["signatures"][0] != signature
    ):
        raise ValueError(
            "Settlement does not contain the authorized successful transaction"
        )
    keys = tx["transaction"]["message"]["accountKeys"]
    if any(not isinstance(key, str) for key in keys):
        raise ValueError("Unsupported transaction evidence encoding")
    before, after = (
        tx["meta"].get("preTokenBalances", []),
        tx["meta"].get("postTokenBalances", []),
    )

    def amount(rows, owner):
        matching = [
            row
            for row in rows
            if row.get("mint") == USDC
            and row.get("owner") == owner
            and keys[row["accountIndex"]] == associated(owner)
        ]
        if len(matching) != 1 or matching[0]["uiTokenAmount"].get("decimals") != 6:
            raise ValueError("Settlement lacks exact associated-token evidence")
        return int(matching[0]["uiTokenAmount"]["amount"])

    if (
        amount(before, wallet) - amount(after, wallet) != units
        or amount(after, recipient) - amount(before, recipient) != units
    ):
        raise ValueError("Settlement token deltas differ from the authorized invoice")
    return {
        "transaction": signature,
        "slot": tx["slot"],
        "asset": USDC,
        "micro_usdc": units,
        "recipient": recipient,
        "finalized": True,
    }


def account_bytes(account):
    return base64.b64decode(account["data"][0], validate=True)

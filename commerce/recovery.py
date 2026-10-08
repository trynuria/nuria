"""Bounded finalized-history reconciliation for disclosed exact payments.

An expired blockhash alone does not establish that a payment was never included.
The scan must reach a recorded pre-signing wallet-history anchor. This is evidence
from the configured RPC provider, not a proof of universal history completeness.
"""

import base64
import hashlib
import time

from solders.signature import Signature
from solders.transaction import VersionedTransaction

from commerce.x402 import decode_header, inspect_message


def history_anchor(wallet, fetch, minimum_slot):
    rows = fetch(
        "getSignaturesForAddress",
        [
            wallet,
            {"commitment": "finalized", "limit": 1, "minContextSlot": minimum_slot},
        ],
    )
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or rows[0].get("confirmationStatus") != "finalized"
    ):
        raise ValueError("A finalized pre-signing wallet history anchor is required")
    row = rows[0]
    if type(row.get("slot")) is not int or row["slot"] < 0:
        raise ValueError("Invalid wallet history anchor")
    Signature.from_string(row["signature"])
    return {"signature": row["signature"], "slot": row["slot"]}


def inspect_history(authorization, accepted, wallet, anchor, fetch, max_pages=10):
    """Return a matching inclusion or an anchored expiration; never authorize."""
    if (
        not isinstance(anchor, dict)
        or type(anchor.get("slot")) is not int
        or anchor["slot"] < 0
        or not isinstance(anchor.get("signature"), str)
        or type(max_pages) is not int
        or not 0 < max_pages <= 10
    ):
        raise ValueError("Missing pre-signing history anchor; keep payment unresolved")
    payload = decode_header(authorization["header"])
    wire = base64.b64decode(payload["payload"]["transaction"], validate=True)
    tx = VersionedTransaction.from_bytes(wire)
    raw_message = b"\x80" + bytes(tx.message)
    inspect_message(raw_message, wallet, accepted)
    if (
        payload.get("accepted") != accepted
        or hashlib.sha256(wire).hexdigest() != authorization["transaction_sha256"]
        or str(tx.signatures[1]) != authorization["client_signature"]
        or not tx.signatures[1].verify(tx.message.account_keys[1], raw_message)
    ):
        raise ValueError("Stored payment authorization identity is inconsistent")
    validity = fetch(
        "isBlockhashValid",
        [
            str(tx.message.recent_blockhash),
            {"commitment": "finalized", "minContextSlot": anchor["slot"]},
        ],
    )
    context_slot = validity.get("context", {}).get("slot")
    if type(context_slot) is not int or context_slot < anchor["slot"]:
        raise ValueError("Blockhash check predates the history anchor")
    if validity.get("value") is not False:
        return None  # A live authorization can still settle; do not release it.
    before, seen, last_slot, scanned = None, set(), None, []
    for _ in range(max_pages):
        options = {
            "commitment": "finalized",
            "minContextSlot": context_slot,
            "limit": 100,
        }
        if before:
            options["before"] = before
        rows = fetch("getSignaturesForAddress", [wallet, options])
        if not isinstance(rows, list) or not 0 < len(rows) <= 100:
            raise ValueError("History ended before the anchor; coverage is unknown")
        for row in rows:
            signature, slot = row.get("signature"), row.get("slot")
            if (
                not isinstance(signature, str)
                or signature in seen
                or type(slot) is not int
                or slot < anchor["slot"]
                or (last_slot is not None and slot > last_slot)
                or row.get("confirmationStatus") != "finalized"
            ):
                raise ValueError("History pagination is incomplete or inconsistent")
            seen.add(signature)
            last_slot = slot
            result = fetch(
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "base64",
                        "commitment": "finalized",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            )
            if not result or result.get("slot") != slot or not result.get("meta"):
                raise ValueError("Finalized transaction evidence is missing")
            actual = VersionedTransaction.from_bytes(
                base64.b64decode(result["transaction"][0], validate=True)
            )
            if (
                str(actual.signatures[0]) != signature
                or wallet not in [str(k) for k in actual.message.account_keys]
                or not all(actual.verify_with_results())
            ):
                raise ValueError(
                    "History transaction identity differs from the wallet scan"
                )
            scanned.append({"signature": signature, "slot": slot})
            if bytes(actual.message) == bytes(tx.message) and authorization[
                "client_signature"
            ] in [str(s) for s in actual.signatures]:
                return {
                    "state": "included",
                    "transaction": signature,
                    "slot": slot,
                    "failed": result["meta"].get("err") is not None,
                }
            if signature == anchor["signature"]:
                if slot != anchor["slot"]:
                    raise ValueError("History anchor slot changed")
                return {
                    "state": "expired_unsettled",
                    "wallet": wallet,
                    "client_signature": authorization["client_signature"],
                    "transaction_sha256": authorization["transaction_sha256"],
                    "blockhash": str(tx.message.recent_blockhash),
                    "finalized_expiry_slot": context_slot,
                    "anchor": anchor,
                    "history": scanned,
                    "checked_at": time.time(),
                    "scope": "Configured finalized RPC history reached the pre-signing anchor; no matching inclusion. Not an independent archive attestation.",
                }
        before = rows[-1]["signature"]
    raise ValueError("Bounded scan did not reach the anchor; retain reservation")

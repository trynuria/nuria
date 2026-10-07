"""Exact Solana x402 with invoice and pre-sign message inspection."""

import base64
import hashlib
import json
import math

from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from commerce.config import COMPUTE, MEMO, NETWORK, SOURCE, TOKEN, USDC
from commerce.solana import associated


def decode_header(value):
    if not isinstance(value, str) or len(value) > 16_384:
        raise ValueError("Missing or oversized x402 header")
    result = json.loads(base64.b64decode(value, validate=True))
    if not isinstance(result, dict):
        raise ValueError("x402 header must contain an object")
    return result


def quote(header, provider):
    required = decode_header(header)
    if required.get("x402Version") != 2 or len(required.get("accepts", [])) > 16:
        raise ValueError("Only bounded x402 v2 invoices are supported")
    matches = []
    for option in required.get("accepts", []):
        if (
            option.get("scheme") != "exact"
            or option.get("network") != NETWORK
            or option.get("asset") != USDC
        ):
            continue
        amount = option.get("amount")
        timeout = option.get("maxTimeoutSeconds")
        extra = option.get("extra", {})
        if (
            option.get("payTo") != provider.recipient
            or not isinstance(amount, str)
            or not amount.isdigit()
            or not 0 < int(amount) <= provider.maximum_micro_usdc
            or type(timeout) is not int
            or not 0 < timeout <= 120
            or extra.get("feePayer") != provider.fee_payer
            or set(extra) - {"feePayer", "memo"}
        ):
            raise ValueError(
                "Invoice differs from approved price, recipient or fee payer"
            )
        memo = extra.get("memo")
        if memo is not None and (
            not isinstance(memo, str) or not 0 < len(memo.encode()) <= 256
        ):
            raise ValueError("Invoice memo is invalid")
        matches.append(option)
    if len(matches) != 1:
        raise ValueError("Invoice requires exactly one approved Solana USDC option")
    resource = required.get("resource", {})
    if resource and resource.get("url") != provider.endpoint:
        raise ValueError("Invoice resource differs from approved endpoint")
    return matches[0]


def inspect_message(raw, wallet, accepted):
    """Reject anything beyond the SDK's four-instruction exact payment."""
    if not raw or raw[0] != 0x80:
        raise ValueError("Only v0 payment messages are allowed")
    message = MessageV0.from_bytes(raw[1:])
    keys = [str(k) for k in message.account_keys]
    fee_payer = accepted["extra"]["feePayer"]
    if (
        message.address_table_lookups
        or message.header.num_required_signatures != 2
        or keys[:2] != [fee_payer, wallet]
    ):
        raise ValueError("Payment signer or lookup table differs from policy")
    source, destination = associated(wallet), associated(accepted["payTo"])
    if set(keys) != {
        fee_payer,
        wallet,
        source,
        destination,
        USDC,
        TOKEN,
        COMPUTE,
        MEMO,
    }:
        raise ValueError("Payment contains unapproved accounts")
    instructions = message.instructions
    if len(instructions) != 4:
        raise ValueError("Payment contains unexpected instructions")
    limit, price, transfer, memo = instructions
    if (
        keys[limit.program_id_index] != COMPUTE
        or limit.accounts
        or bytes(limit.data) != b"\x02" + (20_000).to_bytes(4, "little")
    ):
        raise ValueError("Payment compute limit differs from pinned SDK")
    if (
        keys[price.program_id_index] != COMPUTE
        or price.accounts
        or bytes(price.data) != b"\x03" + (1).to_bytes(8, "little")
    ):
        raise ValueError("Payment compute price differs from pinned SDK")
    accounts = [keys[i] for i in transfer.accounts]
    expected = b"\x0c" + int(accepted["amount"]).to_bytes(8, "little") + b"\x06"
    if (
        keys[transfer.program_id_index] != TOKEN
        or accounts != [source, USDC, destination, wallet]
        or bytes(transfer.data) != expected
    ):
        raise ValueError("Payment transfer differs from exact invoice")
    if (
        keys[memo.program_id_index] != MEMO
        or memo.accounts
        or not 0 < len(memo.data) <= 256
    ):
        raise ValueError("Unexpected payment memo")
    if (
        accepted["extra"].get("memo")
        and bytes(memo.data) != accepted["extra"]["memo"].encode()
    ):
        raise ValueError("Payment memo differs from invoice")
    return message


class GuardedSigner:
    """The SDK receives an inspector, not unrestricted keypair access."""

    def __init__(self, keypair, accepted):
        self._keypair = keypair
        self.accepted = accepted

    @property
    def address(self):
        return str(self._keypair.pubkey())

    @property
    def keypair(self):
        return self

    def sign_message(self, raw):
        inspect_message(raw, self.address, self.accepted)
        return self._keypair.sign_message(raw)


def authorize(keypair, accepted, rpc_url, scheme_factory=None):
    from x402.mechanisms.svm.exact.client import ExactSvmScheme
    from x402.schemas import PaymentRequirements

    signer = GuardedSigner(keypair, accepted)
    scheme = (scheme_factory or ExactSvmScheme)(signer, rpc_url=rpc_url)
    inner = scheme.create_payment_payload(PaymentRequirements.model_validate(accepted))
    tx = VersionedTransaction.from_bytes(
        base64.b64decode(inner["transaction"], validate=True)
    )
    inspect_message(b"\x80" + bytes(tx.message), signer.address, accepted)
    if not tx.signatures[1].verify(keypair.pubkey(), b"\x80" + bytes(tx.message)):
        raise ValueError("Client payment signature failed verification")
    payload = {"x402Version": 2, "accepted": accepted, "payload": inner}
    return {
        "header": base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode(),
        "client_signature": str(tx.signatures[1]),
        "transaction_sha256": hashlib.sha256(bytes(tx)).hexdigest(),
    }


def delivery(raw, mint, now):
    body = json.loads(raw)
    probability, expiry = body.get("p_buy"), body.get("expires_at")
    if (
        body.get("schema") != "nuria.forecast.v1"
        or body.get("mint") != mint
        or body.get("source") != SOURCE
        or type(probability) not in (int, float)
        or not math.isfinite(probability)
        or not 0 <= probability <= 1
        or type(expiry) not in (int, float)
        or not math.isfinite(expiry)
        or not now < expiry <= now + 600
    ):
        raise ValueError("Paid delivery does not match forecast contract")
    return {
        "schema": body["schema"],
        "mint": mint,
        "source": SOURCE,
        "p_buy": probability,
        "expires_at": expiry,
        "delivered_at": now,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }

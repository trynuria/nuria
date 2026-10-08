"""Parsed-transaction custody adapter. No wallet secret is held by the worker."""

import base64
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction


def invoke(value, credentials_file=None):
    completed = subprocess.run(
        [
            os.environ.get("NURIA_NODE", "/opt/nuria/node/bin/node"),
            str(Path(__file__).with_name("signer") / "privy.mjs"),
        ],
        input=json.dumps(value),
        text=True,
        capture_output=True,
        timeout=45,
        env={
            **os.environ,
            **(
                {"NURIA_PRIVY_CREDENTIALS": str(credentials_file)}
                if credentials_file
                else {}
            ),
        },
    )
    if completed.returncode or len(completed.stdout) > 8192:
        raise RuntimeError("Managed custody unavailable; no signature inferred")
    return json.loads(completed.stdout)


class ManagedSigner:
    def __init__(self, wallet, ledger, policy, call=invoke):
        self.wallet, self.ledger, self.policy, self.call = wallet, ledger, policy, call

    def pubkey(self):
        return Pubkey.from_string(self.wallet)

    def check(self):
        return self.call({"operation": "check", "wallet": self.wallet})

    def sign_message(self, raw):
        # x402's guarded adapter has inspected this message before reaching custody.
        if not raw or raw[0] != 0x80:
            raise ValueError("Managed payment requires a parsed v0 message")
        message = MessageV0.from_bytes(raw[1:])
        transaction = VersionedTransaction.populate(
            message, [Signature.default()] * message.header.num_required_signatures
        )
        return self.sign_transaction(transaction).signatures[
            list(message.account_keys).index(self.pubkey())
        ]

    def sign_transaction(self, transaction):
        raw = to_bytes_versioned(transaction.message)
        ident = hashlib.sha256(raw).hexdigest()
        self.ledger.claim_signature(
            ident, self.policy.monthly_signature_limit, time.time()
        )
        result = self.call(
            {
                "operation": "sign",
                "wallet": self.wallet,
                "request_id": ident,
                "transaction": base64.b64encode(bytes(transaction)).decode(),
            }
        )
        if result.get("encoding") != "base64":
            raise ValueError("Custody encoding differs from request")
        signed = VersionedTransaction.from_bytes(
            base64.b64decode(result["signed_transaction"], validate=True)
        )
        if to_bytes_versioned(signed.message) != raw:
            raise ValueError("Custody changed the authorized message")
        keys = list(signed.message.account_keys)
        index = keys.index(self.pubkey())
        if (
            index >= signed.message.header.num_required_signatures
            or not signed.signatures[index].verify(self.pubkey(), raw)
        ):
            raise ValueError("Custody signature does not verify")
        for n, signature in enumerate(transaction.signatures):
            if n != index and signed.signatures[n] != signature:
                raise ValueError("Custody changed another signer's signature")
        return signed

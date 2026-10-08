"""Exact native-SOL forwarding from a project creator wallet to its reserve.

A separate managed delegate must control the creator wallet. This adapter cannot
forward another person's fees or change the Pump fee beneficiary.
"""

import base64
import hashlib
import json
import time

from solders.hash import Hash
from solders.message import Message, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction, VersionedTransaction

from commerce.ledger import canonical


class Sweeper:
    def __init__(self, ledger, fetch):
        self.ledger, self.fetch = ledger, fetch
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS sweeps(
          id TEXT PRIMARY KEY,status TEXT NOT NULL,signature TEXT,wire TEXT,
          message_sha256 TEXT,terms TEXT NOT NULL,day TEXT NOT NULL,amount INTEGER NOT NULL,fee INTEGER NOT NULL)""")

    def sweep(self, policy, signer, controls, now):
        if controls.get("enabled") is not True:
            return
        if (
            policy.signer != "privy"
            or not policy.reserve_wallet
            or policy.creator_wallet in (policy.reserve_wallet, policy.spending_wallet)
            or str(signer.pubkey()) != policy.creator_wallet
        ):
            raise ValueError(
                "Forwarding requires a distinct managed creator and reserve"
            )
        daily, floor, minimum, gas = [
            controls.get(k)
            for k in (
                "daily_lamports",
                "retain_lamports",
                "minimum_lamports",
                "daily_gas_lamports",
            )
        ]
        if (
            any(type(v) is not int or v <= 0 for v in (daily, floor, minimum, gas))
            or minimum > daily
            or gas > 1_000_000
        ):
            raise ValueError(
                "Creator forwarding requires explicit native and gas ceilings"
            )
        db = self.ledger.db
        if db.execute(
            "SELECT 1 FROM sweeps WHERE status IN ('reserved','signed','submitted','uncertain')"
        ).fetchone():
            return
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        spent = db.execute(
            "SELECT coalesce(sum(amount),0),coalesce(sum(fee),0) FROM sweeps WHERE day=?",
            (day,),
        ).fetchone()
        balance = self.fetch(
            "getBalance", [policy.creator_wallet, {"commitment": "finalized"}]
        )["value"]
        amount = min(daily - spent[0], balance - floor - 100_000)
        if amount < minimum:
            return
        recent = self.fetch("getLatestBlockhash", [{"commitment": "finalized"}])[
            "value"
        ]
        message = Message.new_with_blockhash(
            [
                transfer(
                    TransferParams(
                        from_pubkey=Pubkey.from_string(policy.creator_wallet),
                        to_pubkey=Pubkey.from_string(policy.reserve_wallet),
                        lamports=amount,
                    )
                )
            ],
            Pubkey.from_string(policy.creator_wallet),
            Hash.from_string(recent["blockhash"]),
        )
        tx = VersionedTransaction.from_legacy(Transaction.new_unsigned(message))
        fee = self.fetch(
            "getFeeForMessage",
            [
                base64.b64encode(to_bytes_versioned(message)).decode(),
                {"commitment": "finalized"},
            ],
        )["value"]
        if type(fee) is not int or not 0 < fee <= 100_000:
            raise ValueError("Creator forwarding fee is unavailable or excessive")
        ident = hashlib.sha256(to_bytes_versioned(message)).hexdigest()
        terms = {
            "from": policy.creator_wallet,
            "to": policy.reserve_wallet,
            "lamports": amount,
            "last_valid_block_height": recent["lastValidBlockHeight"],
            "scope": "Creator wallet forwarding. External funding and other mint income are not attributed to Nuria.",
        }
        db.execute("BEGIN IMMEDIATE")
        try:
            current = db.execute(
                "SELECT coalesce(sum(amount),0),coalesce(sum(fee),0) FROM sweeps WHERE day=?",
                (day,),
            ).fetchone()
            if (
                db.execute(
                    "SELECT 1 FROM sweeps WHERE status IN ('reserved','signed','submitted','uncertain')"
                ).fetchone()
                or current[0] + amount > daily
                or current[1] + fee > gas
            ):
                raise ValueError(
                    "Creator forwarding is unresolved or reached its ceiling"
                )
            db.execute(
                "INSERT INTO sweeps(id,status,terms,day,amount,fee) VALUES(?,'reserved',?,?,?,?)",
                (ident, canonical(terms), day, amount, fee),
            )
            self.ledger.event(
                {"state": "sweep_reserved", "id": ident, "at": now, **terms}
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        simulation = self.fetch(
            "simulateTransaction",
            [
                base64.b64encode(bytes(tx)).decode(),
                {"encoding": "base64", "sigVerify": False, "commitment": "finalized"},
            ],
        )
        if simulation["value"].get("err") is not None:
            db.execute("UPDATE sweeps SET status='failed' WHERE id=?", (ident,))
            self.ledger.record(
                {
                    "state": "sweep_failed_before_disclosure",
                    "id": ident,
                    "at": time.time(),
                }
            )
            return
        signed = signer.sign_transaction(tx)
        wire, signature = (
            base64.b64encode(bytes(signed)).decode(),
            str(signed.signatures[0]),
        )
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "UPDATE sweeps SET status='signed',wire=?,signature=?,message_sha256=? WHERE id=?",
                (
                    wire,
                    signature,
                    hashlib.sha256(to_bytes_versioned(signed.message)).hexdigest(),
                    ident,
                ),
            )
            self.ledger.event(
                {
                    "state": "sweep_authorized",
                    "id": ident,
                    "transaction": signature,
                    "at": time.time(),
                    **terms,
                }
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        try:
            returned = self.fetch(
                "sendTransaction",
                [
                    wire,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "preflightCommitment": "finalized",
                        "maxRetries": 0,
                    },
                ],
            )
            if returned != signature:
                raise ValueError("Creator forwarding signature mismatch")
            db.execute("UPDATE sweeps SET status='submitted' WHERE id=?", (ident,))
        except Exception:
            db.execute("UPDATE sweeps SET status='uncertain' WHERE id=?", (ident,))
            self.ledger.record(
                {
                    "state": "sweep_uncertain",
                    "id": ident,
                    "transaction": signature,
                    "at": time.time(),
                }
            )

    def reconcile(self):
        db = self.ledger.db
        for ident, signature, raw, expected, reserved_fee in db.execute(
            "SELECT id,signature,terms,message_sha256,fee FROM sweeps WHERE status IN ('signed','submitted','uncertain') AND signature IS NOT NULL"
        ).fetchall():
            tx = self.fetch(
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
            if not tx:
                continue
            wire = VersionedTransaction.from_bytes(
                base64.b64decode(tx["transaction"][0], validate=True)
            )
            if (
                str(wire.signatures[0]) != signature
                or hashlib.sha256(to_bytes_versioned(wire.message)).hexdigest()
                != expected
                or tx["meta"]["fee"] > reserved_fee
            ):
                raise ValueError(
                    "Creator forwarding settlement differs from authorization"
                )
            terms, meta = json.loads(raw), tx["meta"]
            keys = [str(k) for k in wire.message.account_keys]
            source, target = keys.index(terms["from"]), keys.index(terms["to"])
            debit = (
                meta["preBalances"][source] - meta["postBalances"][source] - meta["fee"]
            )
            credit = meta["postBalances"][target] - meta["preBalances"][target]
            success = meta.get("err") is None
            if success and (debit != terms["lamports"] or credit != terms["lamports"]):
                raise ValueError(
                    "Creator forwarding deltas differ from the reserved amount"
                )
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "UPDATE sweeps SET status=? WHERE id=?",
                    ("finalized" if success else "failed", ident),
                )
                self.ledger.event(
                    {
                        "state": "sweep_finalized"
                        if success
                        else "sweep_failed_onchain",
                        "id": ident,
                        "transaction": signature,
                        "slot": tx["slot"],
                        "finalized": True,
                        "network_fee_lamports": meta["fee"],
                        "at": time.time(),
                        **terms,
                    }
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

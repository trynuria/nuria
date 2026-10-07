"""Isolated payment preparation with durable caps and replay protection.

This module never signs or broadcasts. A funded signing service must independently
recheck the published policy before it signs the single-transfer message.
"""

from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from solders.hash import Hash
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from cognition.treasury import SpendingPolicy, intent_hash


class PaymentGate:
    def __init__(self, database: Path, policy: SpendingPolicy):
        self.policy = policy
        self.db = sqlite3.connect(database, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS reservations(
            id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL, intent_hash TEXT NOT NULL,
            day TEXT NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT '')""")

    def reserve(self, intent: dict, observation: dict, now: float) -> dict:
        ident = intent.get("id")
        if not isinstance(ident, str) or not ident or len(ident) > 128:
            raise ValueError("Payment requires a bounded unique intent ID")
        digest = intent_hash(intent)
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT intent_hash,status FROM reservations WHERE id=?", (ident,)
            ).fetchone()
            if previous:
                if previous[0] != digest:
                    raise ValueError("Intent ID was reused with different terms")
                raise ValueError(
                    "Intent already reserved; execution must reconcile its existing record"
                )
            daily = self.db.execute(
                "SELECT coalesce(sum(amount),0) FROM reservations WHERE day=?", (day,)
            ).fetchone()[0]
            pending = self.db.execute(
                "SELECT coalesce(sum(amount),0) FROM reservations WHERE status IN ('reserved','submitted','uncertain')"
            ).fetchone()[0]
            effective = dict(observation)
            if type(effective.get("balance_lamports")) is int:
                effective["balance_lamports"] -= pending
            self.policy.validate(intent, effective, daily, now)
            total = intent["lamports"] + intent.get("maximum_fee_lamports", 0)
            self.db.execute(
                "INSERT INTO reservations(id,job_id,intent_hash,day,amount,status) VALUES(?,?,?,?,?,'reserved')",
                (ident, intent["job_id"], digest, day, total),
            )
            self.db.execute("COMMIT")
            return {
                "id": ident,
                "intent_sha256": digest,
                "reserved_lamports": total,
                "status": "reserved",
            }
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    @staticmethod
    def unsigned_transfer(intent: dict, payer: str, blockhash: str) -> dict:
        if (
            intent.get("asset") != "SOL"
            or type(intent.get("lamports")) is not int
            or intent["lamports"] <= 0
        ):
            raise ValueError("Only positive integer SOL transfers are supported")
        sender = Pubkey.from_string(payer)
        recipient = Pubkey.from_string(intent["recipient"])
        if sender == recipient:
            raise ValueError("Self-payment is not a funded job")
        instruction = transfer(
            TransferParams(
                from_pubkey=sender, to_pubkey=recipient, lamports=intent["lamports"]
            )
        )
        message = Message.new_with_blockhash(
            [instruction], sender, Hash.from_string(blockhash)
        )
        transaction = Transaction.new_unsigned(message)
        return {
            "message_base64": base64.b64encode(bytes(message)).decode(),
            "transaction_base64": base64.b64encode(bytes(transaction)).decode(),
            "instructions": 1,
            "program": "11111111111111111111111111111111",
            "signed": False,
            "broadcast": False,
        }

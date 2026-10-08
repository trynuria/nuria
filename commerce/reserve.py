"""Destination-bound daily USDC replenishment from a Squads V4 reserve."""

import base64
import hashlib
import json
import time

from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from commerce.config import USDC
from commerce.fees import SYSTEM, program_identity
from commerce.ledger import canonical
from commerce.solana import associated
from commerce.squads import build, check_multisig

SQUADS = "SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf"


class Replenisher:
    def __init__(self, ledger, fetch, planner=build):
        self.ledger, self.fetch, self.planner = ledger, fetch, planner
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS replenishments(
          id TEXT PRIMARY KEY, status TEXT NOT NULL, signature TEXT, wire TEXT,
          message_sha256 TEXT, terms TEXT NOT NULL, day TEXT NOT NULL, amount INTEGER NOT NULL, fee INTEGER NOT NULL)""")

    def replenish(self, policy, signer, controls, balance, now):
        if controls.get("enabled") is not True:
            return
        if (
            policy.signer != "privy"
            or not policy.reserve_wallet
            or policy.reserve_wallet == policy.spending_wallet
        ):
            raise ValueError(
                "A separately owned reserve and managed operating signer are required"
            )
        asset = controls.get("asset", "USDC")
        if asset not in ("SOL", "USDC"):
            raise ValueError("Unsupported reserve asset")
        native = asset == "SOL"
        maximum = controls.get("daily_lamports" if native else "daily_micro_usdc")
        target = controls.get("target_lamports" if native else "target_micro_usdc")
        gas = controls.get("daily_gas_lamports")
        if (
            type(maximum) is not int
            or maximum <= 0
            or (not native and maximum > policy.per_day_micro_usdc)
            or type(target) is not int
            or target <= 0
            or (
                not native
                and target > policy.per_day_micro_usdc + policy.reserve_micro_usdc
            )
            or (native and target > maximum)
            or type(gas) is not int
            or not 0 < gas <= 1_000_000
        ):
            raise ValueError("Reserve funding and gas ceilings are unreviewed")
        if not -2 <= now - balance["checked_at"] <= 30:
            raise ValueError("Operating inventory is stale")
        db = self.ledger.db
        if db.execute(
            "SELECT 1 FROM replenishments WHERE status IN ('reserved','signed','submitted','uncertain')"
        ).fetchone():
            return
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        committed = db.execute(
            "SELECT coalesce(sum(amount),0),coalesce(sum(fee),0) FROM replenishments WHERE day=?",
            (day,),
        ).fetchone()
        inventory = (
            self.fetch(
                "getBalance", [policy.spending_wallet, {"commitment": "finalized"}]
            )["value"]
            if native
            else balance["micro_usdc"]
        )
        amount = min(maximum - committed[0], max(0, target - inventory))
        if amount <= 0:
            return
        deployment = program_identity(self.fetch, SQUADS)
        if deployment["program_data_sha256"] != controls.get(
            "squads_program_data_sha256"
        ):
            raise ValueError("Squads deployed program differs from reviewed pin")
        accounts = self.fetch(
            "getMultipleAccounts",
            [
                [controls["spending_limit"], controls["multisig"]],
                {"encoding": "base64", "commitment": "finalized"},
            ],
        )
        limit, multisig = accounts["value"]
        if (
            not limit
            or limit["owner"] != SQUADS
            or not multisig
            or multisig["owner"] != SQUADS
        ):
            raise ValueError("Reserve accounts are missing or unverified")
        check_multisig(
            base64.b64decode(multisig["data"][0], validate=True),
            controls["multisig"],
            policy.spending_wallet,
        )
        recent = self.fetch("getLatestBlockhash", [{"commitment": "finalized"}])[
            "value"
        ]
        prepared = self.planner(
            {
                "account": limit["data"][0],
                "owner": limit["owner"],
                "multisig": controls["multisig"],
                "vault": policy.reserve_wallet,
                "vault_index": controls["vault_index"],
                "mint": SYSTEM if native else USDC,
                "maximum": maximum,
                "amount": amount,
                "wallet": policy.spending_wallet,
                "spending_limit": controls["spending_limit"],
                "blockhash": recent["blockhash"],
                "now": now,
            }
        )
        tx = VersionedTransaction.from_bytes(
            base64.b64decode(prepared["transaction"], validate=True)
        )
        keys = [str(k) for k in tx.message.account_keys]
        if (
            prepared["program"] != SQUADS
            or tx.message.address_table_lookups
            or tx.message.header.num_required_signatures != 1
            or keys[0] != policy.spending_wallet
            or len(tx.message.instructions) != 1
            or keys[tx.message.instructions[0].program_id_index] != SQUADS
        ):
            raise ValueError(
                "Reserve preparation contains unexpected signing authority"
            )
        fee = self.fetch(
            "getFeeForMessage",
            [
                base64.b64encode(to_bytes_versioned(tx.message)).decode(),
                {"commitment": "finalized"},
            ],
        )["value"]
        if type(fee) is not int or not 0 < fee <= 100_000 or committed[1] + fee > gas:
            raise ValueError("Reserve network fee exceeds its separate gas budget")
        terms = {
            "from": policy.reserve_wallet,
            "to": policy.spending_wallet,
            "asset": SYSTEM if native else USDC,
            "units": amount,
            "unit": "lamports" if native else "micro-USDC",
            "spending_limit": controls["spending_limit"],
            "allowance_units": prepared["allowance"],
            "deployment": deployment,
        }
        ident = hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest()
        db.execute("BEGIN IMMEDIATE")
        try:
            current = db.execute(
                "SELECT coalesce(sum(amount),0),coalesce(sum(fee),0) FROM replenishments WHERE day=?",
                (day,),
            ).fetchone()
            if (
                db.execute(
                    "SELECT 1 FROM replenishments WHERE status IN ('reserved','signed','submitted','uncertain')"
                ).fetchone()
                or current[0] + amount > maximum
                or current[1] + fee > gas
            ):
                raise ValueError(
                    "Reserve funding raced another reservation or reached its ceiling"
                )
            db.execute(
                "INSERT INTO replenishments(id,status,terms,day,amount,fee) VALUES(?,'reserved',?,?,?,?)",
                (ident, canonical(terms), day, amount, fee),
            )
            self.ledger.event(
                {"state": "funding_reserved", "id": ident, "at": now, **terms}
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        simulated = self.fetch(
            "simulateTransaction",
            [
                base64.b64encode(bytes(tx)).decode(),
                {"encoding": "base64", "commitment": "finalized", "sigVerify": False},
            ],
        )
        if simulated["value"].get("err") is not None:
            db.execute("UPDATE replenishments SET status='failed' WHERE id=?", (ident,))
            self.ledger.record(
                {
                    "state": "funding_failed_before_disclosure",
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
                "UPDATE replenishments SET status='signed',wire=?,signature=?,message_sha256=? WHERE id=?",
                (
                    wire,
                    signature,
                    hashlib.sha256(to_bytes_versioned(signed.message)).hexdigest(),
                    ident,
                ),
            )
            self.ledger.event(
                {
                    "state": "funding_authorized",
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
                        "preflightCommitment": "finalized",
                        "skipPreflight": False,
                        "maxRetries": 0,
                    },
                ],
            )
            if returned != signature:
                raise ValueError("Funding RPC signature mismatch")
            db.execute(
                "UPDATE replenishments SET status='submitted' WHERE id=?", (ident,)
            )
        except Exception:
            db.execute(
                "UPDATE replenishments SET status='uncertain' WHERE id=?", (ident,)
            )
            self.ledger.record(
                {
                    "state": "funding_uncertain",
                    "id": ident,
                    "transaction": signature,
                    "at": time.time(),
                }
            )

    def reconcile(self):
        db = self.ledger.db
        for ident, signature, raw, expected, reserved_fee in db.execute(
            "SELECT id,signature,terms,message_sha256,fee FROM replenishments WHERE status IN ('signed','submitted','uncertain') AND signature IS NOT NULL"
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
                raise ValueError("Funding settlement differs from authorization")
            terms, meta = json.loads(raw), tx["meta"]
            keys = [str(k) for k in wire.message.account_keys]

            def units(rows, wallet):
                found = [
                    r
                    for r in rows
                    if r.get("owner") == wallet
                    and r.get("mint") == USDC
                    and keys[r["accountIndex"]] == associated(wallet)
                ]
                if len(found) != 1 or found[0]["uiTokenAmount"]["decimals"] != 6:
                    raise ValueError("Funding lacks exact token balance evidence")
                return int(found[0]["uiTokenAmount"]["amount"])

            success = meta.get("err") is None
            if success:
                if terms["asset"] == SYSTEM:
                    source, target = keys.index(terms["from"]), keys.index(terms["to"])
                    debit = meta["preBalances"][source] - meta["postBalances"][source]
                    credit = (
                        meta["postBalances"][target]
                        - meta["preBalances"][target]
                        + meta["fee"]
                    )
                else:
                    debit = units(meta["preTokenBalances"], terms["from"]) - units(
                        meta["postTokenBalances"], terms["from"]
                    )
                    credit = units(meta["postTokenBalances"], terms["to"]) - units(
                        meta["preTokenBalances"], terms["to"]
                    )
                if debit != terms["units"] or credit != terms["units"]:
                    raise ValueError(
                        "Reserve funding deltas differ from the intended amount"
                    )
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "UPDATE replenishments SET status=? WHERE id=?",
                    ("finalized" if success else "failed", ident),
                )
                self.ledger.event(
                    {
                        "state": "funding_finalized"
                        if success
                        else "funding_failed_onchain",
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

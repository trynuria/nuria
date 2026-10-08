"""Pinned standard-Pump collection with durable, once-only submission.

Collection only moves accrued protocol funds to the existing beneficiary. This
does not give the operating signer control of the beneficiary or reserve vault.
"""

import base64
import hashlib
import json
import time

from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from commerce.fees import AMM, claim_plan, program_identity
from commerce.ledger import canonical


class Collector:
    def __init__(self, ledger, fetch):
        self.ledger, self.fetch = ledger, fetch
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS collections(
          id TEXT PRIMARY KEY, status TEXT NOT NULL, signature TEXT, wire TEXT,
          message_sha256 TEXT, terms TEXT NOT NULL, day TEXT NOT NULL, fee INTEGER NOT NULL)""")

    def collect(self, observation, policy, signer, controls, now):
        if controls.get("enabled") is not True:
            return
        if (
            policy.signer != "privy"
            or not policy.creator_wallet
            or not policy.spending_wallet
        ):
            raise ValueError(
                "Collection requires managed signing and a supported live curve"
            )
        minimum = controls.get("minimum_claim_lamports")
        daily = controls.get("daily_gas_lamports")
        interval = controls.get("interval_seconds", 60)
        if (
            type(minimum) is not int
            or minimum < 1_000_000
            or type(daily) is not int
            or not 0 < daily <= 1_000_000
            or type(interval) is not int
            or interval < 60
        ):
            raise ValueError(
                "Collection thresholds and gas ceiling require reviewed configuration"
            )
        previous = self.ledger.db.execute(
            "SELECT terms FROM collections ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if previous and now - json.loads(previous[0]).get("prepared_at", 0) < interval:
            return
        if (
            observation["vault_balance_lamports"]
            + observation.get("amm_vault_wsol_lamports", 0)
            < minimum
        ):
            return
        if self.ledger.db.execute(
            "SELECT 1 FROM collections WHERE status IN ('reserved','signed','submitted','uncertain')"
        ).fetchone():
            return  # Resolve the old signature before preparing another message.
        deployment = program_identity(self.fetch)
        amm_deployment = (
            program_identity(self.fetch, AMM) if observation.get("graduated") else None
        )
        recent = self.fetch("getLatestBlockhash", [{"commitment": "finalized"}])[
            "value"
        ]
        legacy = claim_plan(
            observation,
            policy.spending_wallet,
            recent["blockhash"],
            deployment,
            controls.get("pump_program_data_sha256"),
            amm_deployment,
            controls.get("amm_program_data_sha256"),
        )
        tx = VersionedTransaction.from_legacy(legacy)
        fee = self.fetch(
            "getFeeForMessage",
            [
                base64.b64encode(bytes(legacy.message)).decode(),
                {"commitment": "finalized"},
            ],
        )["value"]
        if type(fee) is not int or not 0 < fee <= 100_000:
            raise ValueError("Collection gas quote is unknown or excessive")
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        ident = hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest()
        terms = {
            "prepared_at": now,
            "creator_wallet": policy.creator_wallet,
            "vault": observation["vault"],
            "mint": policy.mint,
            "observed_slot": observation["slot"],
            "deployment": deployment,
            "amm_deployment": amm_deployment,
            "amm_token_vault": observation.get("amm_token_vault"),
            "last_valid_block_height": recent["lastValidBlockHeight"],
        }
        db = self.ledger.db
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute(
                "SELECT 1 FROM collections WHERE status IN ('reserved','signed','submitted','uncertain')"
            ).fetchone():
                raise ValueError("An earlier collection requires reconciliation")
            committed = db.execute(
                "SELECT coalesce(sum(fee),0) FROM collections WHERE day=?", (day,)
            ).fetchone()[0]
            if committed + fee > daily:
                raise ValueError("Collection gas budget reached")
            db.execute(
                "INSERT INTO collections(id,status,terms,day,fee) VALUES(?,'reserved',?,?,?)",
                (ident, canonical(terms), day, fee),
            )
            self.ledger.event(
                {
                    "state": "collection_reserved",
                    "id": ident,
                    "at": now,
                    "network_fee_ceiling_lamports": fee,
                    **terms,
                }
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
            db.execute("UPDATE collections SET status='failed' WHERE id=?", (ident,))
            self.ledger.record(
                {
                    "state": "collection_failed_before_disclosure",
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
                "UPDATE collections SET status='signed',wire=?,signature=?,message_sha256=? WHERE id=?",
                (
                    wire,
                    signature,
                    hashlib.sha256(to_bytes_versioned(signed.message)).hexdigest(),
                    ident,
                ),
            )
            self.ledger.event(
                {
                    "state": "collection_authorized",
                    "id": ident,
                    "transaction": signature,
                    "at": time.time(),
                    "recipient": policy.creator_wallet,
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
                raise ValueError("Collection RPC signature mismatch")
            db.execute("UPDATE collections SET status='submitted' WHERE id=?", (ident,))
        except Exception:
            db.execute("UPDATE collections SET status='uncertain' WHERE id=?", (ident,))
            self.ledger.record(
                {
                    "state": "collection_uncertain",
                    "id": ident,
                    "transaction": signature,
                    "at": time.time(),
                }
            )
        return ident

    def reconcile(self):
        db = self.ledger.db
        for ident, signature, raw, expected, reserved_fee in db.execute(
            "SELECT id,signature,terms,message_sha256,fee FROM collections WHERE status IN ('signed','submitted','uncertain') AND signature IS NOT NULL"
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
            ):
                raise ValueError("Finalized collection differs from authorized message")
            terms, meta = json.loads(raw), tx["meta"]
            if meta["fee"] > reserved_fee:
                raise ValueError("Collection fee exceeds reserved ceiling")
            keys = [str(k) for k in wire.message.account_keys]
            creator, vault = (
                keys.index(terms["creator_wallet"]),
                keys.index(terms["vault"]),
            )
            credit = meta["postBalances"][creator] - meta["preBalances"][creator]
            net_credit = credit
            if str(wire.message.account_keys[0]) == terms["creator_wallet"]:
                credit += meta["fee"]
            debit = meta["preBalances"][vault] - meta["postBalances"][vault]
            if terms.get("amm_token_vault"):
                token_index = keys.index(terms["amm_token_vault"])
                before = sum(
                    int(r["uiTokenAmount"]["amount"])
                    for r in meta.get("preTokenBalances", [])
                    if r["accountIndex"] == token_index
                )
                after = sum(
                    int(r["uiTokenAmount"]["amount"])
                    for r in meta.get("postTokenBalances", [])
                    if r["accountIndex"] == token_index
                )
                debit += before - after
            success = meta.get("err") is None
            if success and (credit <= 0 or credit != debit):
                raise ValueError(
                    "Collection beneficiary credit does not match vault debit"
                )
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "UPDATE collections SET status=? WHERE id=?",
                    ("finalized" if success else "failed", ident),
                )
                self.ledger.event(
                    {
                        "state": "collection_finalized"
                        if success
                        else "collection_failed_onchain",
                        "id": ident,
                        "transaction": signature,
                        "slot": tx["slot"],
                        "at": time.time(),
                        "recipient": terms["creator_wallet"],
                        "collected_lamports": credit if success else 0,
                        "net_creator_delta_lamports": net_credit,
                        "network_fee_lamports": meta["fee"],
                        "finalized": True,
                        "scope": "Creator-vault collection; accumulated funds may cover several mints.",
                    }
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

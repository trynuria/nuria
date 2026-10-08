"""Pinned standard-Pump collection with durable, once-only submission.

Collection only moves accrued protocol funds to the existing beneficiary. This
does not give the operating signer authority over a different beneficiary.
"""

import base64
import hashlib
import json
import time

from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
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
            + observation.get("curve_creator_fee_lamports", 0)
            + observation.get("pool_creator_fee_lamports", 0)
            < minimum
        ):
            return
        if self.ledger.db.execute(
            "SELECT 1 FROM collections WHERE status IN ('reserved','signed','submitted','uncertain')"
        ).fetchone():
            return  # Resolve the old signature before preparing another message.
        if (
            observation.get("creator_wallet") != policy.creator_wallet
            or observation.get("mint") != policy.mint
        ):
            raise ValueError(
                "Collection observation differs from configured beneficiary or mint"
            )
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
            "curve": observation.get("curve")
            if observation.get("curve_creator_fee_lamports", 0)
            else None,
            "pool_quote_token_account": observation.get("pool_quote_token_account")
            if observation.get("pool_creator_fee_lamports", 0)
            else None,
            "claim_interface": observation.get("claim_interface"),
            "rent_ceiling_lamports": 0,
            "last_valid_block_height": recent["lastValidBlockHeight"],
        }
        db = self.ledger.db
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute(
                "SELECT 1 FROM collections WHERE status IN ('reserved','signed','submitted','uncertain')"
            ).fetchone():
                raise ValueError("An earlier collection requires reconciliation")
            latest = db.execute(
                "SELECT terms FROM collections ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if latest and now - json.loads(latest[0]).get("prepared_at", 0) < interval:
                raise ValueError("Collection cadence changed during preparation")
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
        # This adapter has no rent authority. Verify simulated account layouts
        # before signing, including unused native-quote ATA placeholders.
        addresses = [
            str(key)
            for n, key in enumerate(tx.message.account_keys)
            if writable(tx.message, n)
        ]
        try:
            before = self.fetch(
                "getMultipleAccounts",
                [
                    addresses,
                    {
                        "encoding": "base64",
                        "commitment": "finalized",
                        "minContextSlot": observation["slot"],
                    },
                ],
            )
            simulation = self.fetch(
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
            if simulation["value"].get("err") is not None:
                raise ValueError("Claim simulation failed")
            reject_rent_changes(before["value"], simulation["value"].get("accounts"))
            economics = claim_economics(
                tx.message,
                addresses,
                before["value"],
                simulation["value"],
                terms,
                fee,
                minimum,
            )
            db.execute(
                "UPDATE collections SET terms=? WHERE id=?",
                (canonical({**terms, "simulated_economics": economics}), ident),
            )
            self.ledger.record(
                {
                    "state": "collection_simulation_verified",
                    "id": ident,
                    "at": time.time(),
                    **economics,
                }
            )
        except Exception:
            db.execute("UPDATE collections SET status='failed' WHERE id=?", (ident,))
            self.ledger.record(
                {
                    "state": "collection_failed_before_disclosure",
                    "id": ident,
                    "at": time.time(),
                    "reason": "Simulation lacks a useful conserved payout or requires unapproved account changes",
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
                not all(wire.verify_with_results())
                or str(wire.signatures[0]) != signature
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
            curve_debit = 0
            if terms.get("curve"):
                curve_index = keys.index(terms["curve"])
                curve_debit = (
                    meta["preBalances"][curve_index] - meta["postBalances"][curve_index]
                )
                debit += curve_debit
            pool_debit = 0
            if terms.get("pool_quote_token_account"):
                pool_debit = token_debit(
                    meta, keys.index(terms["pool_quote_token_account"])
                )
                debit += pool_debit
            if terms.get("amm_token_vault"):
                debit += token_debit(meta, keys.index(terms["amm_token_vault"]))
            success = meta.get("err") is None
            if success and (
                credit < 0 or curve_debit < 0 or pool_debit < 0 or credit != debit
            ):
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
                        "rent_lamports": 0 if terms.get("claim_interface") else None,
                        "curve_swept_lamports": curve_debit if success else 0,
                        "pool_swept_lamports": pool_debit if success else 0,
                        "finalized": True,
                        "scope": "Creator-vault collection; accumulated funds may cover several mints.",
                    }
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise


def reject_rent_changes(before, after):
    """A native collector without a rent budget must not allocate accounts."""
    from commerce.solana import account_bytes

    if (
        not isinstance(before, list)
        or not isinstance(after, list)
        or len(before) != len(after)
    ):
        raise ValueError("Claim account simulation is incomplete")
    for pre, post in zip(before, after, strict=True):
        if not pre and post:
            raise ValueError("Claim would create an account with unapproved rent")
        if pre and not post:
            raise ValueError("Claim would unexpectedly close an account")
        if (
            pre
            and post
            and (
                pre.get("owner") != post.get("owner")
                or len(account_bytes(pre)) != len(account_bytes(post))
            )
        ):
            raise ValueError("Claim would change ownership or account allocation")


def token_debit(meta, index):
    def units(rows):
        matching = [r for r in rows if r["accountIndex"] == index]
        if len(matching) > 1:
            raise ValueError("Duplicate claim token balance")
        if not matching:
            return 0
        value = matching[0]
        from commerce.fees import WSOL

        if value.get("mint") != WSOL or value["uiTokenAmount"].get("decimals") != 9:
            raise ValueError("Claim token balance is not native WSOL")
        return int(value["uiTokenAmount"]["amount"])

    return units(meta.get("preTokenBalances", [])) - units(
        meta.get("postTokenBalances", [])
    )


def claim_economics(message, addresses, before, simulation, terms, fee, minimum):
    """Require a positive conserved native payout without hidden rent or transfers.

    Full pre/post balance arrays come from one simulation bank. Earlier account
    snapshots must match those pre-balances; races are refused, not inferred away.
    WSOL source amounts must move with lamports and retain the same rent reserve.
    """
    from commerce.config import TOKEN
    from commerce.fees import SYSTEM, WSOL
    from commerce.solana import account_bytes

    keys = [str(k) for k in message.account_keys]
    pre, post = simulation.get("preBalances"), simulation.get("postBalances")
    after = simulation.get("accounts")
    reject_rent_changes(before, after)
    if (
        simulation.get("fee") != fee
        or not isinstance(pre, list)
        or not isinstance(post, list)
        or len(pre) != len(keys)
        or len(post) != len(keys)
        or any(type(n) is not int or n < 0 for n in [*pre, *post])
        or not isinstance(after, list)
        or len(after) != len(addresses)
        or len(before) != len(addresses)
        or addresses != [key for n, key in enumerate(keys) if writable(message, n)]
    ):
        raise ValueError("Complete same-bank simulated balances and fee are required")
    states = dict(zip(addresses, zip(before, after, strict=True), strict=True))
    for address, (initial, final) in states.items():
        index = keys.index(address)
        if (initial.get("lamports") if initial else 0) != pre[index] or (
            final.get("lamports") if final else 0
        ) != post[index]:
            raise ValueError("Simulation and account snapshots differ")
    creator, payer = terms["creator_wallet"], keys[0]
    sources = {
        terms["vault"],
        *(
            terms.get(k)
            for k in ("curve", "pool_quote_token_account", "amm_token_vault")
            if terms.get(k)
        ),
    }
    if (
        creator in sources
        or payer in sources
        or not {creator, payer, *sources}.issubset(states)
    ):
        raise ValueError("Claim source aliases a wallet")
    deltas = {key: post[n] - pre[n] for n, key in enumerate(keys)}
    for address in (creator, payer):
        initial, final = states[address]
        if (
            not initial
            or not final
            or initial["owner"] != SYSTEM
            or account_bytes(initial)
            or account_bytes(final)
        ):
            raise ValueError("Claim wallet is not an existing native system account")
    gross = deltas[creator] + (fee if payer == creator else 0)
    debit = -sum(deltas[address] for address in sources)
    if gross < minimum or gross <= fee or gross != debit:
        raise ValueError("Claim does not deliver the required conserved useful payout")
    for address, delta in deltas.items():
        if address in sources:
            if delta > 0:
                raise ValueError(
                    "A claim source retains new funds; bridge may have skipped"
                )
        elif address == creator:
            continue
        elif address == payer:
            if delta != -fee:
                raise ValueError("Payer movement exceeds the network fee")
        elif delta:
            raise ValueError("Unexpected lamport movement or unapproved rent top-up")
    for field in ("pool_quote_token_account", "amm_token_vault"):
        address = terms.get(field)
        if not address:
            continue
        initial, final = states[address]
        if initial is None and final is None:
            continue
        raw, changed = account_bytes(initial), account_bytes(final)
        if (
            initial["owner"] != TOKEN
            or len(raw) != 165
            or len(changed) != 165
            or str(Pubkey.from_bytes(raw[:32])) != WSOL
            or raw[108] != 1
            or int.from_bytes(raw[72:76], "little") != 0
            or int.from_bytes(raw[109:113], "little") != 1
            or raw[:64] != changed[:64]
            or raw[72:] != changed[72:]
        ):
            raise ValueError("Claim WSOL source changes identity, delegate or reserve")
        reserve = int.from_bytes(raw[113:121], "little")
        old_amount, new_amount = (
            int.from_bytes(raw[64:72], "little"),
            int.from_bytes(changed[64:72], "little"),
        )
        if (
            initial["lamports"] - reserve != old_amount
            or final["lamports"] - reserve != new_amount
        ):
            raise ValueError("WSOL source lamports do not match native token units")
    if sum(deltas.values()) != -fee:
        raise ValueError("Simulated native balance conservation failed")
    return {
        "gross_beneficiary_lamports": gross,
        "net_beneficiary_lamports": deltas[creator],
        "source_debit_lamports": debit,
        "network_fee_lamports": fee,
        "unapproved_rent_lamports": 0,
        "scope": "Pre-sign simulation, not finalized collection or mint-specific income",
    }


def writable(message, index):
    header = message.header
    signed = header.num_required_signatures
    return (
        index < signed - header.num_readonly_signed_accounts
        or signed
        <= index
        < len(message.account_keys) - header.num_readonly_unsigned_accounts
    )

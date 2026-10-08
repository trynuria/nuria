"""Finalized financial-wallet history with resumable scans and visible gaps."""

import hashlib
import time

from commerce.config import USDC
from commerce.ledger import canonical


def movement(tx, signature, wallets):
    meta, transaction = tx["meta"], tx["transaction"]
    if transaction["signatures"][0] != signature:
        raise ValueError("Financial transaction identifier mismatch")
    keys = transaction["message"]["accountKeys"]
    if any(not isinstance(key, str) for key in keys):
        raise ValueError("Financial transaction requires JSON account keys")
    loaded = meta.get("loadedAddresses", {})
    keys = [*keys, *loaded.get("writable", []), *loaded.get("readonly", [])]
    changes = []
    for wallet in wallets:
        if wallet in keys:
            index = keys.index(wallet)
            before, after = meta["preBalances"][index], meta["postBalances"][index]
            if before != after:
                changes.append(
                    {
                        "wallet": wallet,
                        "asset": "SOL",
                        "before": before,
                        "after": after,
                        "delta": after - before,
                        "unit": "lamports",
                    }
                )
        for mint in {
            b["mint"]
            for b in [
                *meta.get("preTokenBalances", []),
                *meta.get("postTokenBalances", []),
            ]
            if b.get("owner") == wallet
        }:

            def units(rows):
                return sum(
                    int(b["uiTokenAmount"]["amount"])
                    for b in rows
                    if b.get("owner") == wallet and b["mint"] == mint
                )

            before, after = (
                units(meta.get("preTokenBalances", [])),
                units(meta.get("postTokenBalances", [])),
            )
            if before != after:
                changes.append(
                    {
                        "wallet": wallet,
                        "asset": mint,
                        "before": before,
                        "after": after,
                        "delta": after - before,
                        "unit": "micro-USDC" if mint == USDC else "raw token units",
                    }
                )
    programs = sorted(
        {keys[ix["programIdIndex"]] for ix in transaction["message"]["instructions"]}
    )
    return {
        "state": "wallet_movement",
        "transaction": signature,
        "slot": tx["slot"],
        "block_time": tx.get("blockTime"),
        "finalized": True,
        "success": meta.get("err") is None,
        "network_fee_lamports": meta["fee"],
        "fee_payer": keys[0],
        "changes": changes,
        "programs": programs,
        "rpc_evidence_sha256": hashlib.sha256(canonical(tx).encode()).hexdigest(),
        "scope": "Observed balance changes and fee. Program presence alone does not establish fee provenance or endorsement.",
    }


class WalletObserver:
    def __init__(self, ledger, fetch):
        self.ledger, self.fetch = ledger, fetch

    def scan(self, wallets, now, page_budget=2, transaction_budget=20):
        db = self.ledger.db
        wallets = sorted(set(wallets))
        for wallet in wallets:
            db.execute(
                "INSERT OR IGNORE INTO wallet_cursors(wallet) VALUES(?)", (wallet,)
            )
            newest, before, candidate, phase = db.execute(
                "SELECT newest,scan_before,candidate,phase FROM wallet_cursors WHERE wallet=?",
                (wallet,),
            ).fetchone()
            for _ in range(page_budget):
                options = {"commitment": "finalized", "limit": 1000}
                if before:
                    options["before"] = before
                rows = self.fetch("getSignaturesForAddress", [wallet, options])
                if not isinstance(rows, list):
                    raise ValueError("Financial history is unknown")
                if rows and candidate is None:
                    candidate = rows[0]["signature"]
                found = (
                    any(row["signature"] == newest for row in rows) if newest else False
                )
                unseen = []
                for row in rows:
                    if row["signature"] == newest:
                        break
                    unseen.append(row["signature"])
                complete = found or len(rows) < 1000
                # First scan starts from available history; RPC retention is unknown.
                previous_gap = phase == "history_gap"
                phase = "caught_up_available_history" if complete else "backfilling"
                if complete and newest and not found:
                    phase = "history_gap"
                if previous_gap:
                    phase = "history_gap"
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.executemany(
                        "INSERT OR IGNORE INTO chain_transactions(signature) VALUES(?)",
                        [(s,) for s in unseen],
                    )
                    db.execute(
                        "UPDATE wallet_cursors SET newest=?,scan_before=?,candidate=?,checked=?,phase=? WHERE wallet=?",
                        (
                            candidate if complete else newest,
                            None if complete else rows[-1]["signature"],
                            None if complete else candidate,
                            now,
                            phase,
                            wallet,
                        ),
                    )
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise
                if complete:
                    break
                before = rows[-1]["signature"]
        for (signature,) in db.execute(
            "SELECT signature FROM chain_transactions WHERE state='pending' ORDER BY rowid LIMIT ?",
            (transaction_budget,),
        ).fetchall():
            tx = self.fetch(
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
            if tx is None:
                continue
            evidence = movement(tx, signature, wallets)
            db.execute("BEGIN IMMEDIATE")
            try:
                self.ledger.event({**evidence, "at": time.time()})
                db.execute(
                    "UPDATE chain_transactions SET state='verified',evidence=? WHERE signature=?",
                    (canonical(evidence), signature),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

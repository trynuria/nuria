"""Profile changes bind all readers while preserving financial authority."""

import sqlite3
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pump_feed import PUMP, WBTC, PumpFeed, persist_event
from tests.test_decoder import CREATOR, MINT, ROOT, trade_bytes
from token_profile import apply, public, validate


class TokenProfileTests(unittest.TestCase):
    def profile(self):
        return {
            "schema": "nuria.token-config.v1",
            "mode": "test",
            "mint": MINT,
            "creator_wallet": CREATOR,
            "pool": None,
        }

    def test_shared_identity_never_enables_spending_or_replaces_its_wallet(self):
        result = apply(
            {"enabled": False, "spending_wallet": None, "providers": []}, self.profile()
        )
        self.assertFalse(result["enabled"])
        self.assertIsNone(result["spending_wallet"])
        self.assertEqual(result["creator_wallet"], CREATOR)
        self.assertNotEqual(public(self.profile())["source"], "solana_finalized")
        self.assertEqual(
            public({**self.profile(), "mode": "production"})["source"],
            "solana_finalized",
        )

    def test_unsupported_fields_bad_addresses_and_changed_modes_fail(self):
        for changes in (
            {"secret": "not-allowed"},
            {"mint": "invalid"},
            {"mode": "live-ish"},
        ):
            with self.assertRaises((ValueError, TypeError)):
                validate({**self.profile(), **changes})

    def test_each_configuration_has_a_distinct_public_commitment(self):
        original = public(self.profile())["configuration_sha256"]
        self.assertNotEqual(
            original,
            public({**self.profile(), "mode": "production"})["configuration_sha256"],
        )

    def test_verified_non_sol_quote_uses_exact_units_and_amount_field(self):
        feed = PumpFeed(SimpleNamespace(lock=threading.RLock(), feed_state={}), ROOT)
        feed.pool_quotes = {}
        tx = {
            "version": 1,
            "slot": 42,
            "meta": {"err": None},
            "transaction": {"message": {"accountKeys": [], "instructions": []}},
        }

        def parse(decoded):
            with patch.object(feed.decoder, "events", return_value=[(PUMP, decoded)]):
                return feed.parse("fixture-signature", tx, MINT)

        try:
            name, values = feed.decoder.decode(PUMP, trade_bytes())
            values.update(
                quote_mint=WBTC, quote_amount=64682, creator_fee=647, sol_amount=0
            )
            feed.quote_units[WBTC] = {"symbol": "WBTC", "decimals": 8}
            feed.profile = self.profile()
            feed.curve_quote = WBTC
            event = parse((name, values))[0]
            self.assertEqual(event["quote_amount"], 0.00064682)
            self.assertEqual(event["creator_fee"], 0.00000647)
            self.assertEqual(event["quote_unit"], "WBTC")
            self.assertEqual(event["token_mode"], "test")
            self.assertNotEqual(event["source"], "solana_finalized")
            del values["quote_amount"]
            with self.assertRaises(RuntimeError):
                parse((name, values))
        finally:
            feed.executor.shutdown(wait=True)

    def test_unique_trade_accrual_is_transactional_and_mint_specific(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute(
            "CREATE TABLE inputs(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,created TEXT,receipt TEXT)"
        )
        db.executescript((ROOT / "deploy/token-transactions.sql").read_text())
        event = {
            "id": "fixture:0",
            "source": "fixture",
            "mint": MINT,
            "quote_mint": WBTC,
            "quote_unit": "WBTC",
            "quote_decimals": 8,
            "creator_fee_raw": "647",
        }
        with db:
            self.assertTrue(persist_event(db, MINT, event))
            self.assertFalse(persist_event(db, MINT, event))
        self.assertEqual(
            db.execute("SELECT creator_fee_raw,trades FROM token_totals").fetchone(),
            ("647", 1),
        )
        with self.assertRaises(ValueError), db:
            persist_event(db, CREATOR, event)
        self.assertEqual(db.execute("SELECT count(*) FROM inputs").fetchone()[0], 1)

    def test_cursor_namespaces_keep_previous_tokens_and_pending_failures(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript((ROOT / "deploy/token-transactions.sql").read_text())
        for mint, status in ((MINT, "pending"), (CREATOR, "decoded")):
            db.execute(
                "INSERT INTO token_transactions(mint,signature,slot,sequence,status) VALUES(?, 'same-signature', 1, 1, ?)",
                (mint, status),
            )
        self.assertEqual(
            db.execute(
                "SELECT status FROM token_transactions WHERE mint=?", (MINT,)
            ).fetchone()[0],
            "pending",
        )
        self.assertEqual(
            db.execute("SELECT count(*) FROM token_transactions").fetchone()[0], 2
        )


if __name__ == "__main__":
    unittest.main()

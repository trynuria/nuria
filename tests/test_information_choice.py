"""Persistent resource decisions preserve zero-cost and unavailable evidence."""

import json
import tempfile
import unittest
from pathlib import Path

from commerce.config import Provider
from commerce.information import ENDPOINT, InformationChoice, context
from commerce.ledger import Ledger


class InformationTests(unittest.TestCase):
    def setUp(self):
        self.path = (
            Path(
                tempfile.mkdtemp(
                    prefix="information-", dir=Path(__file__).parents[1] / ".test-state"
                )
            )
            / "ledger.sqlite3"
        )
        self.ledger = Ledger(self.path)
        self.addCleanup(self.ledger.db.close)
        self.provider = Provider(
            **json.loads(
                (
                    Path(__file__).parents[1] / "commerce/provider.example.json"
                ).read_text()
            )
        )

    def selected(self, ident="one"):
        return {"provider": self.provider, "decision_hash": ident}

    def test_free_resource_refuses_redundant_payment_and_survives_restart(self):
        calls = []
        raw = b'{"solana":{"usd":115.84,"last_updated_at":1000}}'

        def http(url):
            calls.append(url)
            return 200, {}, raw

        chooser = InformationChoice(self.ledger, http)
        first = chooser.consider(self.selected(), 1010)
        self.assertEqual(first["selection"], "free")
        self.assertEqual(first["expense_micro_usdc"], 0)
        self.assertEqual(first["context"]["received_at"], 1010)
        self.assertEqual(calls, [ENDPOINT])
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0
        )
        restored = Ledger(self.path)
        self.addCleanup(restored.db.close)

        def unused(_):
            self.fail("Fresh cached context must not repeat a public API request")

        second = InformationChoice(restored, unused)
        self.assertEqual(second.consider(self.selected(), 1015), first)
        reuse = second.consider(self.selected("two"), 1020)
        self.assertEqual(
            reuse["context"]["response_sha256"], first["context"]["response_sha256"]
        )
        self.assertEqual(reuse["context"]["received_at"], 1010)
        self.assertEqual(len(restored.recent()), 2)
        restored.verify()

    def test_future_stale_invalid_and_unavailable_data_defer_instead_of_spend(self):
        for stamp in (879, 1001):
            with self.assertRaises(ValueError):
                context(
                    json.dumps(
                        {"solana": {"usd": 115, "last_updated_at": stamp}}
                    ).encode(),
                    1000,
                )
        for usd in (True, 0, -1, "115", float("inf")):
            with self.assertRaises(ValueError):
                context(
                    json.dumps(
                        {"solana": {"usd": usd, "last_updated_at": 1000}}
                    ).encode(),
                    1000,
                )
        calls = []
        chooser = InformationChoice(
            self.ledger, lambda u: calls.append(u) or (429, {}, b"")
        )
        first = chooser.consider(self.selected(), 1000)
        self.assertEqual(first["selection"], "defer")
        self.assertIsNone(first["context"])
        self.assertEqual(
            chooser.consider(self.selected("next"), 1059)["selection"], "defer"
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0
        )

    def test_forecast_information_is_not_called_equivalent_to_a_free_price(self):
        def forbidden(_):
            self.fail("No unrelated data request")

        value = self.selected()
        value["provider"] = Provider(
            "forecast",
            "https://fixture.example/forecast",
            self.provider.recipient,
            self.provider.fee_payer,
            10000,
        )
        self.assertIsNone(
            InformationChoice(self.ledger, forbidden).consider(value, 1000)
        )
        self.assertEqual(len(self.ledger.recent()), 0)

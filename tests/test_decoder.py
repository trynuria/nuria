"""Protocol decoding and failure semantics without network requests."""

import json
import struct
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pump_feed import PUMP, Decoder, PumpFeed, b58decode, b58encode

ROOT = Path(__file__).resolve().parents[1]
MINT = b58encode(bytes([1]) * 32)
USER = b58encode(bytes([2]) * 32)
CREATOR = b58encode(bytes([3]) * 32)


def trade_bytes(is_buy=1):
    # Fixed Borsh prefix through creator_fee. Later fields are optional for
    # historical events; this layout is independent of Decoder.read().
    tag = next(
        e["discriminator"]
        for e in json.loads((ROOT / "pump-idl.json").read_text())["events"]
        if e["name"] == "TradeEvent"
    )
    return b"".join(
        [
            bytes(tag),
            bytes([1]) * 32,
            struct.pack("<QQB", 2_000_000_000, 123_456, is_buy),
            bytes([2]) * 32,
            struct.pack("<qQQQQ", 1_700_000_000, 100, 200, 300, 400),
            bytes([4]) * 32,
            struct.pack("<QQ", 100, 20_000_000),
            bytes([3]) * 32,
            struct.pack("<QQ", 30, 6_000_000),
        ]
    )


class DecoderTests(unittest.TestCase):
    def setUp(self):
        queue = SimpleNamespace(lock=threading.RLock(), feed_state={})
        self.feed = PumpFeed(queue, ROOT)
        self.feed.pool_quotes = {}
        self.addCleanup(self.feed.executor.shutdown, wait=True)
        self.tx = {
            "slot": 42,
            "meta": {"err": None},
            "transaction": {"message": {"accountKeys": [], "instructions": []}},
        }

    def parse(self, decoded, mint=MINT):
        with patch.object(self.feed.decoder, "events", return_value=[(PUMP, decoded)]):
            return self.feed.parse("fixture-signature", self.tx, mint)

    def test_borsh_trade_prefix_decodes(self):
        name, event = self.feed.decoder.decode(PUMP, trade_bytes())
        self.assertEqual(name, "TradeEvent")
        self.assertEqual(event["mint"], MINT)
        self.assertEqual(event["creator"], CREATOR)
        self.assertEqual(event["creator_fee"], 6_000_000)

    def test_fee_beneficiary_is_distinct_from_trader(self):
        event = self.parse(self.feed.decoder.decode(PUMP, trade_bytes()))[0]
        self.assertEqual(event["user"], USER)
        self.assertEqual(event["creator_fee_wallet"], CREATOR)
        self.assertEqual(event["quote_amount"], 2)
        self.assertEqual(event["creator_fee"], 0.006)
        self.assertEqual(event["side"], "buy")
        self.assertEqual(event["finality"], "finalized")

    def test_sell_direction(self):
        self.assertEqual(
            self.parse(self.feed.decoder.decode(PUMP, trade_bytes(0)))[0]["side"],
            "sell",
        )

    def test_modern_sol_quote_amount_is_used_when_legacy_field_is_zero(self):
        name, event = self.feed.decoder.decode(PUMP, trade_bytes())
        event.update(
            sol_amount=0,
            quote_amount=1_500_000_000,
            quote_mint="So11111111111111111111111111111111111111112",
        )
        parsed = self.parse((name, event))[0]
        self.assertEqual(parsed["quote_amount"], 1.5)
        self.assertEqual(parsed["quote_unit"], "SOL")

    def test_wrong_mint_is_ignored(self):
        self.assertEqual(
            self.parse(self.feed.decoder.decode(PUMP, trade_bytes()), USER), []
        )

    def test_truncated_creator_fee_remains_unresolved(self):
        decoded = self.feed.decoder.decode(PUMP, trade_bytes()[:-8])
        with self.assertRaisesRegex(RuntimeError, "schema incomplete"):
            self.parse(decoded)

    def test_invalid_boolean_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid bool"):
            self.feed.decoder.decode(PUMP, trade_bytes(2))

    def test_unknown_discriminator_is_ignored(self):
        self.assertIsNone(Decoder(ROOT).decode(PUMP, b"\x00" * 24))

    def test_missing_transaction_remains_unresolved(self):
        with self.assertRaisesRegex(RuntimeError, "retry pending"):
            self.feed.parse("fixture-signature", None, MINT)

    def test_non_sol_quote_is_not_mislabelled(self):
        name, event = self.feed.decoder.decode(PUMP, trade_bytes())
        event["quote_mint"] = USER
        with self.assertRaisesRegex(RuntimeError, "verified unit decoder"):
            self.parse((name, event))

    def test_rpc_errors_do_not_disclose_credentials(self):
        with (
            patch.dict("os.environ", {"HELIUS_API_KEY": "unit-test"}),
            patch(
                "urllib.request.urlopen",
                side_effect=OSError("credential-bearing request failed"),
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                self.feed.rpc("getSlot", [])
        self.assertEqual(
            str(raised.exception), "Helius RPC request failed; coverage is unknown"
        )
        self.assertTrue(raised.exception.__suppress_context__)

    def test_base58_preserves_leading_zeros(self):
        for raw in [bytes(32), b"\x00\x00\xff", bytes(range(32))]:
            with self.subTest(raw=raw):
                self.assertEqual(b58decode(b58encode(raw)), raw)

    def test_base58_rejects_invalid_alphabet(self):
        with self.assertRaises(ValueError):
            b58decode("0OIl")

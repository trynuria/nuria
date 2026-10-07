"""Financial boundaries, ambiguous settlement and zero-cost job lifecycle tests."""

import base64
import dataclasses
import hashlib
import json
import sqlite3
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from commerce.backup import snapshot
from commerce.config import NETWORK, SOURCE, USDC, Policy, Provider
from commerce.fees import CURVE_DISC, PUMP, SYSTEM, claim_plan, observe, pda
from commerce.ledger import Ledger, canonical
from commerce.solana import associated, settlement, usdc_balance
from commerce.transport import public_addresses
from commerce.worker import Executor, select_job
from commerce.x402 import delivery, quote

ROOT = Path(__file__).resolve().parents[1]


def fresh():
    directory = ROOT / ".test-state" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    return directory


def public_key():
    return str(Keypair().pubkey())


def fixture():
    provider = Provider(
        "forecast",
        "https://provider.example/forecast",
        public_key(),
        public_key(),
        100_000,
    )
    policy = Policy(
        True,
        public_key(),
        public_key(),
        public_key(),
        200_000,
        500_000,
        100_000,
        60,
        (provider,),
    )
    accepted = {
        "scheme": "exact",
        "network": NETWORK,
        "asset": USDC,
        "amount": "100000",
        "payTo": provider.recipient,
        "maxTimeoutSeconds": 60,
        "extra": {"feePayer": provider.fee_payer},
    }
    return policy, provider, accepted


def encoded(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def job(ident="one", decision="decision", now=1_780_000_000):
    return {
        "id": ident,
        "decision_hash": decision,
        "provider": "forecast",
        "action": "predict",
        "amount": 100_000,
        "created": now,
    }


class CommerceTests(unittest.TestCase):
    def test_payment_backup_retains_uncertain_reservation(self):
        policy, _, _ = fixture()
        directory = fresh()
        ledger = Ledger(directory / "live.sqlite3")
        now = 1_780_000_000
        ledger.reserve(job(), policy, {"micro_usdc": 1_000_000, "checked_at": now}, now)
        ledger.transition("one", "authorized", {}, now)
        ledger.transition("one", "uncertain", {}, now)
        evidence = snapshot(directory / "live.sqlite3", directory / "saved.sqlite3")
        restored = Ledger(directory / "saved.sqlite3")
        self.addCleanup(ledger.db.close)
        self.addCleanup(restored.db.close)
        self.assertTrue(evidence["valid"])
        self.assertEqual(restored.summary()["reserved_micro_usdc"], 100_000)
        self.assertEqual(restored.summary()["counts"], {"uncertain": 1})

    def test_balance_cannot_predate_verified_settlement(self):
        with self.assertRaises(ValueError):
            usdc_balance(
                public_key(),
                lambda *_: {"context": {"slot": 1}, "value": None},
                minimum_slot=2,
            )

    def test_configuration_is_disabled_and_explicit(self):
        policy = Policy.load({})
        self.assertFalse(policy.enabled)
        self.assertIn("spending_wallet", policy.missing())
        with self.assertRaises(ValueError):
            Policy.load({"enabled": True})
        with self.assertRaises(ValueError):
            Policy.load({"enabled": "false"})
        with self.assertRaises(ValueError):
            Policy.load({"per_job_micro_usdc": True})

    def test_provider_does_not_accept_embedded_credentials_or_private_dns(self):
        _, provider, _ = fixture()
        for url in (
            "http://api.example/a",
            "https://secret@api.example/a",
            "https://api.example/a?key=secret",
            "https://api.example:8443/a",
        ):
            with self.assertRaises(ValueError):
                dataclasses.replace(provider, endpoint=url).validate()
        for address in ("127.0.0.1", "::1", "169.254.169.254", "10.0.0.1"):
            with patch(
                "socket.getaddrinfo", return_value=[(0, 0, 0, "", (address, 443))]
            ):
                with self.assertRaises(ValueError):
                    public_addresses("provider.example")

    def test_invoice_exact_asset_network_recipient_and_cap(self):
        _, provider, accepted = fixture()
        self.assertEqual(
            quote(encoded({"x402Version": 2, "accepts": [accepted]}), provider),
            accepted,
        )
        changes = [
            {"amount": "100001"},
            {"amount": "0"},
            {"amount": "-1"},
            {"asset": public_key()},
            {"network": "eip155:8453"},
            {"payTo": public_key()},
            {"extra": {"feePayer": public_key()}},
            {
                "extra": {
                    "feePayer": provider.fee_payer,
                    "recentBlockhash": public_key(),
                }
            },
            {"maxTimeoutSeconds": 121},
        ]
        for change in changes:
            with self.assertRaises(ValueError):
                quote(
                    encoded({"x402Version": 2, "accepts": [{**accepted, **change}]}),
                    provider,
                )
        with self.assertRaises(ValueError):
            quote(
                encoded({"x402Version": 2, "accepts": [accepted, accepted]}), provider
            )

    def test_reservations_survive_restart_and_day_rollover(self):
        policy, _, _ = fixture()
        path = fresh() / "payments.sqlite3"
        ledger = Ledger(path)
        now = 1_780_000_000
        ledger.reserve(job(), policy, {"micro_usdc": 300_000, "checked_at": now}, now)
        ledger.db.close()
        restored = Ledger(path)
        with self.assertRaises(ValueError):
            restored.reserve(
                job("two", "other"),
                policy,
                {"micro_usdc": 250_000, "checked_at": now + 86400},
                now + 86400,
            )
        self.assertEqual(restored.summary()["reserved_micro_usdc"], 100_000)

    def test_concurrent_gate_and_duplicate_decision(self):
        policy, _, _ = fixture()
        path = fresh() / "payments.sqlite3"
        first, second = Ledger(path), Ledger(path)
        now = 1_780_000_000
        first.reserve(job(), policy, {"micro_usdc": 1_000_000, "checked_at": now}, now)
        with self.assertRaises(sqlite3.IntegrityError):
            second.reserve(
                job("different-id"),
                policy,
                {"micro_usdc": 1_000_000, "checked_at": now + 60},
                now + 60,
            )
        self.assertEqual(second.summary()["integrity"]["events"], 1)
        with self.assertRaises(ValueError):
            second.reserve(
                job("another", "another"),
                policy,
                {"micro_usdc": 1_000_000, "checked_at": now - 31},
                now,
            )

    def test_tampering_and_lifecycle_fail_closed(self):
        policy, _, _ = fixture()
        ledger = Ledger(fresh() / "payments.sqlite3")
        now = 1_780_000_000
        ledger.reserve(job(), policy, {"micro_usdc": 1_000_000, "checked_at": now}, now)
        with self.assertRaises(ValueError):
            ledger.transition("one", "delivered", {}, now)
        ledger.transition("one", "authorized", {}, now)
        ledger.transition("one", "uncertain", {}, now)
        with self.assertRaises(ValueError):
            ledger.transition("one", "failed", {}, now)
        ledger.db.execute("UPDATE events SET payload='{}' WHERE seq=1")
        with self.assertRaises(RuntimeError):
            ledger.verify()

    def test_bad_delivery_and_wrong_settlement_are_rejected(self):
        policy, provider, _ = fixture()
        now = time.time()
        body = {
            "schema": "nuria.forecast.v1",
            "source": SOURCE,
            "mint": policy.mint,
            "p_buy": 0.75,
            "expires_at": now + 60,
        }
        self.assertEqual(
            delivery(json.dumps(body).encode(), policy.mint, now)["p_buy"], 0.75
        )
        for change in (
            {"p_buy": float("nan")},
            {"p_buy": True},
            {"mint": public_key()},
            {"source": "test"},
            {"expires_at": now - 1},
        ):
            with self.assertRaises(ValueError):
                delivery(json.dumps({**body, **change}).encode(), policy.mint, now)
        sig = str(Keypair().sign_message(b"fixture"))
        with self.assertRaises(ValueError):
            settlement(
                sig,
                "different",
                policy.spending_wallet,
                provider.recipient,
                1,
                lambda *_: {
                    "meta": {"err": None},
                    "transaction": {"signatures": [sig]},
                },
            )

    def test_payment_timeout_remains_reserved_and_never_repaid(self):
        policy, provider, accepted = fixture()
        ledger = Ledger(fresh() / "payments.sqlite3")
        selected = {
            "provider": provider,
            "decision_hash": "a" * 64,
            "action": "predict",
            "cursor": 0,
            "baseline_p_buy": 0.5,
            "expected_value": 0.5,
        }
        calls = []

        def http(url, headers=None):
            calls.append(headers)
            if headers:
                raise TimeoutError("fixture only")
            return (
                402,
                {
                    "payment-required": encoded(
                        {"x402Version": 2, "accepts": [accepted]}
                    )
                },
                b"",
            )

        def fetch(method, params):
            if method == "simulateTransaction":
                return {"value": {"err": None}}
            return {
                "context": {"slot": 1},
                "value": {
                    "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "data": {
                        "parsed": {
                            "info": {
                                "owner": policy.spending_wallet,
                                "mint": USDC,
                                "state": "initialized",
                                "tokenAmount": {"decimals": 6, "amount": "1000000"},
                            }
                        }
                    },
                },
            }

        executor = Executor(
            ledger,
            fetch,
            http,
            lambda *_: {
                "header": encoded({"payload": {"transaction": "fixture"}}),
                "client_signature": "fixture",
                "transaction_sha256": "a" * 64,
            },
        )
        with self.assertRaises(TimeoutError):
            executor.purchase(policy, selected, None, "fixture", time.time())
        self.assertEqual(ledger.summary()["counts"], {"uncertain": 1})
        with self.assertRaises(ValueError):
            executor.purchase(policy, selected, None, "fixture", time.time() + 120)
        self.assertEqual(sum(bool(headers) for headers in calls), 1)

    def test_cognitive_decisions_require_live_source_and_valid_hash(self):
        policy, _, _ = fixture()
        ledger = Ledger(fresh() / "payments.sqlite3")
        now = time.time()
        payload = {
            "utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "decision": {"action": "predict"},
            "kind": "decision",
        }
        parent = "0" * 64
        decision = {
            **payload,
            "hash": hashlib.sha256((parent + canonical(payload)).encode()).hexdigest(),
            "previous_hash": parent,
        }
        cognition = {
            "phase": "running",
            "source_cursor": 1,
            "workspace": {"uncertainty": 0.8},
            "learning": {"sources": {"test": {"prediction": {"probability": 0.5}}}},
        }
        self.assertIsNone(select_job(policy, cognition, decision, now, ledger))
        cognition["learning"]["sources"][SOURCE] = {"prediction": {"probability": 0.5}}
        self.assertIsNotNone(select_job(policy, cognition, decision, now, ledger))
        with self.assertRaises(ValueError):
            select_job(policy, cognition, {**decision, "hash": "a" * 64}, now, ledger)

    def test_pump_mode_identity_and_unsigned_claim(self):
        mint, creator, payer = public_key(), public_key(), public_key()
        raw = bytearray(160)
        raw[:8], raw[49:81] = (
            CURVE_DISC,
            bytes(Pubkey.from_string(creator)),
        )
        raw[83:115] = bytes(32)
        curve = {"owner": PUMP, "data": [base64.b64encode(raw).decode(), "base64"]}
        vault = {"owner": SYSTEM, "lamports": 500_000, "data": ["", "base64"]}
        observation = observe(
            mint,
            creator,
            lambda *_: {"context": {"slot": 1}, "value": [curve, vault, None]},
        )
        self.assertEqual(observation["vault"], pda(b"creator-vault", creator))
        tx = claim_plan(
            observation,
            payer,
            str(Hash.default()),
            {"program_data_sha256": "a" * 64},
            "a" * 64,
        )
        self.assertEqual(len(tx.message.instructions), 1)
        self.assertFalse(tx.is_signed())
        with self.assertRaises(ValueError):
            claim_plan(
                observation,
                payer,
                str(Hash.default()),
                {"program_data_sha256": "b" * 64},
                "a" * 64,
            )
        raw[124] = 1
        curve["data"][0] = base64.b64encode(raw).decode()
        self.assertFalse(
            observe(
                mint,
                creator,
                lambda *_: {"context": {"slot": 1}, "value": [curve, vault, None]},
            )["standard_claim_supported"]
        )

    def test_settlement_requires_exact_incoming_and_outgoing_usdc(self):
        policy, provider, _ = fixture()
        signature, client = (
            str(Keypair().sign_message(b"fee")),
            str(Keypair().sign_message(b"client")),
        )
        keys = [associated(policy.spending_wallet), associated(provider.recipient)]

        def balance(index, owner, amount):
            return {
                "accountIndex": index,
                "owner": owner,
                "mint": USDC,
                "uiTokenAmount": {"amount": str(amount), "decimals": 6},
            }

        tx = {
            "slot": 1,
            "transaction": {
                "signatures": [signature, client],
                "message": {"accountKeys": keys},
            },
            "meta": {
                "err": None,
                "preTokenBalances": [
                    balance(0, policy.spending_wallet, 200_000),
                    balance(1, provider.recipient, 0),
                ],
                "postTokenBalances": [
                    balance(0, policy.spending_wallet, 100_000),
                    balance(1, provider.recipient, 100_000),
                ],
            },
        }
        self.assertTrue(
            settlement(
                signature,
                client,
                policy.spending_wallet,
                provider.recipient,
                100_000,
                lambda *_: tx,
            )["finalized"]
        )
        with self.assertRaises(ValueError):
            settlement(
                signature,
                client,
                policy.spending_wallet,
                provider.recipient,
                99_999,
                lambda *_: tx,
            )


if __name__ == "__main__":
    unittest.main()

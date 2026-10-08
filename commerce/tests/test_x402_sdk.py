"""Real SDK signatures with synthetic RPC evidence; no network or funded key."""

import base64
import json
import math
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from x402.mechanisms.svm.exact.client import ExactSvmScheme

from commerce.config import NETWORK, SOURCE, TOKEN, USDC, Policy, Provider
from commerce.ledger import Ledger
from commerce.solana import associated
from commerce.worker import Executor
from commerce.x402 import authorize, inspect_message


class SDKTests(unittest.TestCase):
    def test_real_sdk_partial_signature_and_guard(self):
        key, facilitator, merchant = Keypair(), Keypair(), Keypair()
        accepted = {
            "scheme": "exact",
            "network": NETWORK,
            "asset": USDC,
            "amount": "1000",
            "payTo": str(merchant.pubkey()),
            "maxTimeoutSeconds": 60,
            "extra": {"feePayer": str(facilitator.pubkey())},
        }

        class FixtureScheme(ExactSvmScheme):
            def _get_client(self, network):
                data = bytearray(82)
                data[44] = 6
                return SimpleNamespace(
                    get_account_info=lambda _: SimpleNamespace(
                        value=SimpleNamespace(
                            owner=Pubkey.from_string(TOKEN), data=bytes(data)
                        )
                    ),
                    get_latest_blockhash=lambda: SimpleNamespace(
                        value=SimpleNamespace(blockhash=Hash.default())
                    ),
                )

        result = authorize(key, accepted, "unused-offline-fixture", FixtureScheme)
        self.assertEqual(len(result["transaction_sha256"]), 64)
        payload = json.loads(base64.b64decode(result["header"]))
        tx = VersionedTransaction.from_bytes(
            base64.b64decode(payload["payload"]["transaction"])
        )
        raw = b"\x80" + bytes(tx.message)
        self.assertTrue(tx.signatures[1].verify(key.pubkey(), raw))
        with self.assertRaises(ValueError):
            inspect_message(raw, str(key.pubkey()), {**accepted, "amount": "1001"})
        with self.assertRaises(ValueError):
            inspect_message(raw, str(merchant.pubkey()), accepted)

    def test_invoice_to_settlement_delivery_and_measured_outcome(self):
        key, facilitator, merchant = Keypair(), Keypair(), Keypair()
        mint = str(Keypair().pubkey())
        provider = Provider(
            "fixture",
            "https://fixture.example/forecast",
            str(merchant.pubkey()),
            str(facilitator.pubkey()),
            1000,
        )
        policy = Policy(
            True,
            mint,
            str(Keypair().pubkey()),
            str(key.pubkey()),
            1000,
            10_000,
            1000,
            60,
            (provider,),
        )
        accepted = {
            "scheme": "exact",
            "network": NETWORK,
            "asset": USDC,
            "amount": "1000",
            "payTo": provider.recipient,
            "maxTimeoutSeconds": 60,
            "extra": {"feePayer": provider.fee_payer},
        }
        directory = (
            Path(__file__).resolve().parents[2] / ".test-state" / uuid.uuid4().hex
        )
        directory.mkdir(parents=True)
        ledger = Ledger(directory / "commerce.sqlite3")
        self.addCleanup(ledger.db.close)
        client_signature = None
        signature = str(facilitator.sign_message(b"synthetic-settlement"))

        def encoded(body):
            return base64.b64encode(json.dumps(body).encode()).decode()

        def http(endpoint, headers=None):
            nonlocal client_signature
            if not headers:
                return (
                    402,
                    {
                        "payment-required": encoded(
                            {"x402Version": 2, "accepts": [accepted]}
                        )
                    },
                    b"",
                )
            payload = json.loads(base64.b64decode(headers["PAYMENT-SIGNATURE"]))
            transaction = VersionedTransaction.from_bytes(
                base64.b64decode(payload["payload"]["transaction"])
            )
            client_signature = str(transaction.signatures[1])
            body = {
                "schema": "nuria.forecast.v1",
                "mint": mint,
                "source": SOURCE,
                "p_buy": 0.9,
                "expires_at": time.time() + 60,
            }
            return (
                200,
                {
                    "payment-response": encoded(
                        {"success": True, "network": NETWORK, "transaction": signature}
                    )
                },
                json.dumps(body).encode(),
            )

        def fetch(method, params):
            if method == "simulateTransaction":
                simulation = VersionedTransaction.from_bytes(
                    base64.b64decode(params[0])
                )
                self.assertTrue(
                    all(bytes(s) == bytes(64) for s in simulation.signatures)
                )
                return {"value": {"err": None}}
            if method == "getAccountInfo":
                return {
                    "context": {"slot": 1},
                    "value": {
                        "owner": TOKEN,
                        "data": {
                            "parsed": {
                                "info": {
                                    "owner": policy.spending_wallet,
                                    "mint": USDC,
                                    "state": "initialized",
                                    "tokenAmount": {"decimals": 6, "amount": "100000"},
                                }
                            }
                        },
                    },
                }
            if params[0] == "synthetic-outcome":
                return {"blockTime": math.ceil(time.time()) + 1, "meta": {"err": None}}

            def balance(index, owner, amount):
                return {
                    "accountIndex": index,
                    "owner": owner,
                    "mint": USDC,
                    "uiTokenAmount": {"amount": str(amount), "decimals": 6},
                }

            return {
                "slot": 1,
                "transaction": {
                    "signatures": [signature, client_signature],
                    "message": {
                        "accountKeys": [
                            associated(policy.spending_wallet),
                            associated(provider.recipient),
                        ]
                    },
                },
                "meta": {
                    "err": None,
                    "preTokenBalances": [
                        balance(0, policy.spending_wallet, 100_000),
                        balance(1, provider.recipient, 1000),
                    ],
                    "postTokenBalances": [
                        balance(0, policy.spending_wallet, 99_000),
                        balance(1, provider.recipient, 2000),
                    ],
                },
            }

        class FixtureScheme(ExactSvmScheme):
            def _get_client(self, network):
                data = bytearray(82)
                data[44] = 6
                return SimpleNamespace(
                    get_account_info=lambda _: SimpleNamespace(
                        value=SimpleNamespace(
                            owner=Pubkey.from_string(TOKEN), data=bytes(data)
                        )
                    ),
                    get_latest_blockhash=lambda: SimpleNamespace(
                        value=SimpleNamespace(blockhash=Hash.default())
                    ),
                )

        executor = Executor(
            ledger,
            fetch,
            http,
            lambda key, accepted, url: authorize(key, accepted, url, FixtureScheme),
        )
        selected = {
            "provider": provider,
            "decision_hash": "a" * 64,
            "action": "predict",
            "cursor": 0,
            "baseline_p_buy": 0.5,
            "expected_value": 0.5,
        }
        executor.purchase(policy, selected, key, "unused-offline-fixture", time.time())
        self.assertEqual(ledger.summary()["counts"], {"delivered": 1})
        executor.evaluate(
            [
                {
                    "source": SOURCE,
                    "event_order": 1,
                    "signature": "synthetic-outcome",
                    "source_payload": {"mint": mint},
                    "side": "buy",
                    "input_id": "fixture-outcome",
                }
            ],
            time.time(),
        )
        self.assertEqual(ledger.summary()["counts"], {"evaluated": 1})
        self.assertGreater(executor.feedback()[0]["reward"], 0)
        executor.evaluate([], time.time())
        self.assertEqual(len(executor.feedback()), 1)


if __name__ == "__main__":
    unittest.main()

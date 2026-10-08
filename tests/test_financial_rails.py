"""Financial invariants across custody, funding, history and public proofs."""

import base64
import copy
import dataclasses
import hashlib
import json
import unittest
import uuid
from pathlib import Path

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from commerce.config import NETWORK, TOKEN, USDC, Policy, Provider
from commerce.evidence import export_pages, verify_pages
from commerce.fees import AMM, CURVE_DISC, PUMP, SYSTEM, WSOL, claim_plan, observe
from commerce.funding import Collector
from commerce.ledger import Ledger
from commerce.managed import ManagedSigner
from commerce.reserve import Replenisher
from commerce.solana import associated
from commerce.squads import build, check_multisig
from commerce.swaps import JUPITER, Converter, inspect, prepare
from commerce.sweep import Sweeper
from commerce.wallets import WalletObserver
from commerce.x402 import delivery, quote

ROOT = Path(__file__).resolve().parents[1]


def directory():
    path = ROOT / ".test-state" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def swap_fixture():
    wallet = str(Keypair().pubkey())
    source, target = associated(wallet, WSOL), associated(wallet)
    amount, quoted, slip = 1_000_000, 200_000, 50
    tail = (
        amount.to_bytes(8, "little")
        + quoted.to_bytes(8, "little")
        + slip.to_bytes(2, "little")
        + b"\x00"
    )
    data = (
        hashlib.sha256(b"global:route").digest()[:8]
        + b"\x01\x00\x00\x00\x00\x64\x00\x01"
        + tail
    )

    def instruction(program, keys, raw):
        return {
            "programId": program,
            "accounts": [
                {
                    "pubkey": k,
                    "isSigner": k == wallet,
                    "isWritable": k in (wallet, source, target),
                }
                for k in keys
            ],
            "data": base64.b64encode(raw).decode(),
        }

    value = {
        "inputMint": WSOL,
        "outputMint": USDC,
        "inAmount": str(amount),
        "outAmount": str(quoted),
        "otherAmountThreshold": str(quoted * (10000 - slip) // 10000),
        "swapMode": "ExactIn",
        "slippageBps": slip,
        "setupInstructions": [
            instruction(
                SYSTEM,
                [wallet, source],
                (2).to_bytes(4, "little") + amount.to_bytes(8, "little"),
            )
        ],
        "swapInstruction": instruction(
            JUPITER, [TOKEN, wallet, source, target, JUPITER, USDC], data
        ),
        "computeBudgetInstructions": [],
        "otherInstructions": [],
        "tipInstruction": None,
        "cleanupInstruction": None,
    }
    return value, wallet, amount


class FinancialRailTests(unittest.TestCase):
    def ledger(self):
        path = directory()
        result = Ledger(path / "ledger.sqlite3")
        self.addCleanup(result.db.close)
        return result, path

    def test_public_export_contains_all_events_and_detects_corruption(self):
        ledger, path = self.ledger()
        for n in range(501):
            ledger.record({"state": "fixture", "at": n})
        index = export_pages(ledger, path)
        self.assertEqual(index["pages"], 3)
        pages = [json.loads((path / f"ledger-{n}.json").read_text()) for n in range(3)]
        self.assertEqual(verify_pages(index, pages)["events"], 501)
        pages[1]["records"][0]["payload"]["at"] = 99
        with self.assertRaises(ValueError):
            verify_pages(index, pages)

    def test_old_snapshot_verifies_during_tail_growth(self):
        ledger, path = self.ledger()
        ledger.record({"state": "first"})
        first = export_pages(ledger, path)
        ledger.record({"state": "later"})
        export_pages(ledger, path)
        pages = [json.loads((path / "ledger-0.json").read_text())]
        self.assertEqual(verify_pages(first, pages)["events"], 1)
        self.assertEqual(len(pages[0]["records"]), 2)

    def test_signature_ceiling_is_durable_across_handles(self):
        ledger, path = self.ledger()
        other = Ledger(path / "ledger.sqlite3")
        self.addCleanup(other.db.close)
        ledger.claim_signature("same", 1, 1780000000)
        other.claim_signature("same", 1, 1780000000)
        with self.assertRaises(ValueError):
            other.claim_signature("new", 1, 1780000000)
        self.assertEqual(
            ledger.db.execute("SELECT count(*) FROM signing_requests").fetchone()[0], 1
        )

    def test_managed_signature_keeps_the_exact_transaction(self):
        ledger, _ = self.ledger()
        key = Keypair()
        ix = Instruction(
            key.pubkey(), b"fixture", [AccountMeta(key.pubkey(), True, False)]
        )
        msg = MessageV0.try_compile(key.pubkey(), [ix], [], Hash.default())
        tx = VersionedTransaction(msg, [key])

        def signer(value):
            return {
                "encoding": "base64",
                "signed_transaction": base64.b64encode(bytes(tx)).decode(),
            }

        remote = ManagedSigner(str(key.pubkey()), ledger, Policy(), signer)
        self.assertEqual(remote.sign_transaction(tx).message, tx.message)
        other = MessageV0.try_compile(
            key.pubkey(),
            [
                Instruction(
                    key.pubkey(), b"changed", [AccountMeta(key.pubkey(), True, False)]
                )
            ],
            [],
            Hash.default(),
        )
        with self.assertRaises(ValueError):
            remote.sign_transaction(VersionedTransaction(other, [key]))

    def test_squads_codec_matches_official_sdk_for_both_assets(self):
        vectors = json.loads((ROOT / "tests/fixtures-squads-v4.json").read_text())[
            "vectors"
        ]
        for vector in vectors:
            with self.subTest(mint=vector["input"]["mint"]):
                result = build(vector["input"])
                actual = VersionedTransaction.from_bytes(
                    base64.b64decode(result["transaction"])
                ).message
                expected = VersionedTransaction.from_bytes(
                    base64.b64decode(vector["expected_transaction"])
                ).message

                def intent(message):
                    return [
                        {
                            "program": str(message.account_keys[ix.program_id_index]),
                            "data": bytes(ix.data),
                            "accounts": [
                                (
                                    str(message.account_keys[n]),
                                    message.is_signer(n),
                                    message.is_maybe_writable(n),
                                )
                                for n in ix.accounts
                            ],
                        }
                        for ix in message.instructions
                    ]

                self.assertEqual(intent(actual), intent(expected))
                self.assertEqual(actual.recent_blockhash, expected.recent_blockhash)
                self.assertEqual(
                    actual.header.num_required_signatures,
                    expected.header.num_required_signatures,
                )
                owner = check_multisig(
                    base64.b64decode(vector["multisig_account"]),
                    vector["input"]["multisig"],
                    vector["input"]["wallet"],
                )
                self.assertEqual(owner["threshold"], 2)

    def test_reserve_rejects_wrong_destination_cap_asset_and_member(self):
        vector = json.loads((ROOT / "tests/fixtures-squads-v4.json").read_text())[
            "vectors"
        ][0]
        for field, value in (
            ("wallet", str(Keypair().pubkey())),
            ("maximum", 1),
            ("mint", SYSTEM),
            ("vault", str(Keypair().pubkey())),
            ("spending_limit", str(Keypair().pubkey())),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                build({**vector["input"], field: value})

    def test_reserve_cannot_grant_the_worker_admin_or_vote_authority(self):
        vector = json.loads((ROOT / "tests/fixtures-squads-v4.json").read_text())[
            "vectors"
        ][0]
        raw = bytearray(base64.b64decode(vector["multisig_account"]))
        # First member follows fixed header, None rent collector, bump and vec length.
        raw[8 + 32 + 32 + 2 + 4 + 8 + 8 + 1 + 1 + 4 + 32] = 7
        with self.assertRaises(ValueError):
            check_multisig(
                bytes(raw), vector["input"]["multisig"], vector["input"]["wallet"]
            )

    def test_swap_checks_onchain_tail_not_just_http_quote(self):
        value, wallet, amount = swap_fixture()
        self.assertEqual(len(inspect(value, wallet, amount, 190_000, 50)), 2)
        raw = bytearray(base64.b64decode(value["swapInstruction"]["data"]))
        raw[-3:-1] = (9000).to_bytes(2, "little")
        value["swapInstruction"]["data"] = base64.b64encode(raw).decode()
        with self.assertRaises(ValueError):
            inspect(value, wallet, amount, 190_000, 50)

    def test_swap_rejects_arbitrary_instruction_or_destination(self):
        value, wallet, amount = swap_fixture()
        corrupted = copy.deepcopy(value)
        corrupted["setupInstructions"][0]["accounts"][1]["pubkey"] = str(
            Keypair().pubkey()
        )
        with self.assertRaises(ValueError):
            inspect(corrupted, wallet, amount, 190_000, 50)
        corrupted = copy.deepcopy(value)
        corrupted["swapInstruction"]["programId"] = str(Keypair().pubkey())
        with self.assertRaises(ValueError):
            inspect(corrupted, wallet, amount, 190_000, 50)

    def test_swap_lookup_contents_must_match_finalized_rpc(self):
        value, wallet, amount = swap_fixture()
        value["addressesByLookupTableAddress"] = {str(Keypair().pubkey()): [wallet]}
        with self.assertRaises(ValueError):
            prepare(
                value,
                wallet,
                amount,
                190_000,
                50,
                lambda *_: {"context": {"slot": 1}, "value": None},
            )

    def test_wallet_backfill_resumes_without_advancing_past_the_gap(self):
        ledger, _ = self.ledger()
        wallet = str(Keypair().pubkey())
        rows = [{"signature": str(n)} for n in range(1000)]

        def first(method, params):
            return rows if method == "getSignaturesForAddress" else None

        WalletObserver(ledger, first).scan(
            [wallet], 1780000000, page_budget=1, transaction_budget=0
        )
        cursor = ledger.db.execute(
            "SELECT newest,scan_before,phase FROM wallet_cursors"
        ).fetchone()
        self.assertEqual(cursor, (None, "999", "backfilling"))

        def next_page(method, params):
            self.assertEqual(params[1]["before"], "999")
            return []

        WalletObserver(ledger, next_page).scan(
            [wallet], 1780000001, page_budget=1, transaction_budget=0
        )
        self.assertEqual(
            ledger.db.execute(
                "SELECT newest,scan_before,phase FROM wallet_cursors"
            ).fetchone(),
            ("0", None, "caught_up_available_history"),
        )
        self.assertEqual(
            ledger.db.execute("SELECT count(*) FROM chain_transactions").fetchone()[0],
            1000,
        )

    def test_financial_history_gap_is_not_reset_by_a_later_good_check(self):
        ledger, _ = self.ledger()
        wallet = str(Keypair().pubkey())
        ledger.db.execute(
            "INSERT INTO wallet_cursors(wallet,newest,phase) VALUES(?,'old','caught_up_available_history')",
            (wallet,),
        )
        WalletObserver(ledger, lambda *_: [{"signature": "new"}]).scan(
            [wallet], 1780000000, transaction_budget=0
        )
        self.assertEqual(
            ledger.db.execute("SELECT phase FROM wallet_cursors").fetchone()[0],
            "history_gap",
        )
        WalletObserver(ledger, lambda *_: [{"signature": "new"}]).scan(
            [wallet], 1780000001, transaction_budget=0
        )
        self.assertEqual(
            ledger.db.execute("SELECT phase FROM wallet_cursors").fetchone()[0],
            "history_gap",
        )

    def test_market_delivery_is_separate_from_a_learning_result(self):
        result = delivery(
            b'{"solana":{"usd":150}}',
            str(Keypair().pubkey()),
            1780000000,
            "coingecko.sol-price.v1",
        )
        self.assertEqual(result["usd"], 150)
        self.assertNotIn("p_buy", result)
        with self.assertRaises(ValueError):
            delivery(
                b'{"solana":{"usd":0}}', None, 1780000000, "coingecko.sol-price.v1"
            )

    def test_unresolved_payment_breaker_is_independent_of_day_rollover(self):
        ledger, _ = self.ledger()
        policy = dataclasses.replace(
            Policy(),
            enabled=True,
            per_job_micro_usdc=1000,
            per_day_micro_usdc=10000,
            reserve_micro_usdc=0,
            cooldown_seconds=60,
            maximum_unresolved_jobs=1,
        )
        ledger.reserve(
            {
                "id": "old",
                "decision_hash": "old",
                "provider": "fixture",
                "action": "compare",
                "amount": 1000,
            },
            policy,
            {"micro_usdc": 10000, "checked_at": 1780000000},
            1780000000,
        )
        ledger.transition("old", "authorized", {}, 1780000000)
        ledger.transition("old", "uncertain", {}, 1780000000)
        now = 1780000000 + 86400
        with self.assertRaises(ValueError):
            ledger.reserve(
                {
                    "id": "new",
                    "decision_hash": "new",
                    "provider": "fixture",
                    "action": "compare",
                    "amount": 1000,
                },
                policy,
                {"micro_usdc": 10000, "checked_at": now},
                now,
            )

    def test_live_merchant_resource_omits_query_only_for_the_fixed_adapter(self):
        raw = json.loads((ROOT / "commerce/provider.example.json").read_text())
        provider = Provider(**raw)
        accepted = {
            "scheme": "exact",
            "network": NETWORK,
            "asset": USDC,
            "amount": "10000",
            "payTo": provider.recipient,
            "maxTimeoutSeconds": 60,
            "extra": {"feePayer": provider.fee_payer},
        }
        invoice = {
            "x402Version": 2,
            "accepts": [accepted],
            "resource": {"url": provider.endpoint.split("?")[0]},
        }

        def header():
            return base64.b64encode(json.dumps(invoice).encode()).decode()

        self.assertEqual(quote(header(), provider), accepted)
        invoice["resource"]["url"] += "?ids=bitcoin&vs_currencies=usd"
        with self.assertRaises(ValueError):
            quote(header(), provider)
        with self.assertRaises(ValueError):
            quote(
                header(),
                dataclasses.replace(
                    provider, endpoint=provider.endpoint.replace("solana", "bitcoin")
                ),
            )

    def test_creator_forwarding_persists_before_send_and_blocks_duplicate_after_restart(
        self,
    ):
        ledger, path = self.ledger()
        key, target, operating = (
            Keypair(),
            str(Keypair().pubkey()),
            str(Keypair().pubkey()),
        )
        policy = dataclasses.replace(
            Policy(),
            creator_wallet=str(key.pubkey()),
            reserve_wallet=target,
            spending_wallet=operating,
        )
        controls = {
            "enabled": True,
            "daily_lamports": 1000000,
            "retain_lamports": 1000000,
            "minimum_lamports": 1000000,
            "daily_gas_lamports": 10000,
        }
        events, captured = [], {}

        class Signer:
            def pubkey(self):
                return key.pubkey()

            def sign_transaction(self, tx):
                events.append("sign")
                return VersionedTransaction(tx.message, [key])

        def fetch(method, params):
            if method == "getBalance":
                return {"value": 3000000}
            if method == "getLatestBlockhash":
                return {
                    "value": {
                        "blockhash": str(Hash.default()),
                        "lastValidBlockHeight": 100,
                    }
                }
            if method == "getFeeForMessage":
                return {"value": 5000}
            if method == "simulateTransaction":
                tx = VersionedTransaction.from_bytes(base64.b64decode(params[0]))
                self.assertFalse(params[1]["sigVerify"])
                self.assertTrue(all(bytes(s) == bytes(64) for s in tx.signatures))
                self.assertNotIn("sign", events)
                events.append("simulate")
                return {"value": {"err": None}}
            if method == "sendTransaction":
                events.append("send")
                captured["wire"] = params[0]
                row = ledger.db.execute(
                    "SELECT status,signature,wire FROM sweeps"
                ).fetchone()
                self.assertEqual(row[0], "signed")
                self.assertEqual(row[2], params[0])
                raise TimeoutError("Ambiguous submission")
            if method == "getTransaction":
                return None
            raise AssertionError(method)

        Sweeper(ledger, fetch).sweep(policy, Signer(), controls, 1780000000)
        self.assertEqual(events, ["simulate", "sign", "send"])
        self.assertEqual(
            ledger.db.execute("SELECT status FROM sweeps").fetchone()[0], "uncertain"
        )
        restored = Ledger(path / "ledger.sqlite3")
        self.addCleanup(restored.db.close)
        worker = Sweeper(restored, fetch)
        worker.reconcile()
        worker.sweep(policy, Signer(), controls, 1780000000 + 86400)
        self.assertEqual(events.count("send"), 1)
        self.assertEqual(
            restored.db.execute("SELECT count(*) FROM sweeps").fetchone()[0], 1
        )

    def test_all_money_rails_reconcile_once_and_reject_wrong_finalized_deltas(self):
        for table, factory in (
            ("collections", Collector),
            ("replenishments", Replenisher),
            ("conversions", Converter),
            ("sweeps", Sweeper),
        ):
            with self.subTest(rail=table):
                ledger, path = self.ledger()
                key = Keypair()
                wallet, reserve = str(key.pubkey()), str(Keypair().pubkey())
                creator, vault = str(Keypair().pubkey()), str(Keypair().pubkey())
                source, target = associated(wallet, WSOL), associated(wallet)
                accounts = [
                    wallet,
                    reserve,
                    creator,
                    vault,
                    source,
                    target,
                    associated(reserve),
                ]
                ix = Instruction(
                    key.pubkey(),
                    b"fixture",
                    [
                        AccountMeta(Pubkey.from_string(a), a == wallet, True)
                        for a in accounts
                    ],
                )
                tx = VersionedTransaction(
                    MessageV0.try_compile(key.pubkey(), [ix], [], Hash.default()), [key]
                )
                keys = [str(k) for k in tx.message.account_keys]
                positions = {k: keys.index(k) for k in accounts}
                pre, post = [2000000] * len(keys), [2000000] * len(keys)
                post[positions[wallet]] -= 5000
                if table == "collections":
                    post[positions[creator]] += 1000000
                    post[positions[vault]] -= 1000000
                    terms = {"creator_wallet": creator, "vault": vault}
                elif table == "replenishments":
                    post[positions[reserve]] -= 1000000
                    post[positions[wallet]] += 1000000
                    terms = {
                        "from": reserve,
                        "to": wallet,
                        "asset": SYSTEM,
                        "units": 1000000,
                    }
                elif table == "sweeps":
                    post[positions[wallet]] -= 1000000
                    post[positions[reserve]] += 1000000
                    terms = {"from": wallet, "to": reserve, "lamports": 1000000}
                else:
                    post[positions[wallet]] -= 1000000
                    terms = {
                        "wallet": wallet,
                        "input_lamports": 1000000,
                        "minimum_micro_usdc": 100,
                    }

                def token(amount):
                    return [
                        {
                            "owner": wallet,
                            "mint": USDC,
                            "accountIndex": positions[target],
                            "uiTokenAmount": {"amount": str(amount), "decimals": 6},
                        }
                    ]

                evidence = {
                    "slot": 100,
                    "transaction": [base64.b64encode(bytes(tx)).decode(), "base64"],
                    "meta": {
                        "err": None,
                        "fee": 5000,
                        "preBalances": pre,
                        "postBalances": post,
                        "preTokenBalances": token(0),
                        "postTokenBalances": token(100),
                    },
                }
                worker = factory(ledger, lambda *_: evidence)
                common = {
                    "id": "fixture",
                    "status": "uncertain",
                    "signature": str(tx.signatures[0]),
                    "wire": evidence["transaction"][0],
                    "message_sha256": hashlib.sha256(
                        bytes([0x80]) + bytes(tx.message)
                    ).hexdigest(),
                    "terms": json.dumps(terms),
                    "day": "2026-10-08",
                    "fee": 5000,
                }
                if table == "replenishments" or table == "sweeps":
                    common["amount"] = 1000000
                if table == "conversions":
                    common["input"] = 1000000
                ledger.db.execute(
                    f"INSERT INTO {table}({','.join(common)}) VALUES({','.join('?' for _ in common)})",
                    tuple(common.values()),
                )
                post[positions[wallet]] -= 1 if table != "collections" else 0
                if table == "collections":
                    post[positions[creator]] += 1
                with self.assertRaises(ValueError):
                    worker.reconcile()
                self.assertEqual(
                    ledger.db.execute(f"SELECT status FROM {table}").fetchone()[0],
                    "uncertain",
                )
                post[positions[wallet]] += 1 if table != "collections" else 0
                if table == "collections":
                    post[positions[creator]] -= 1
                worker.reconcile()
                self.assertEqual(
                    ledger.db.execute(f"SELECT status FROM {table}").fetchone()[0],
                    "finalized",
                )
                event_count = ledger.db.execute(
                    "SELECT count(*) FROM events"
                ).fetchone()[0]
                worker.reconcile()
                self.assertEqual(
                    ledger.db.execute("SELECT count(*) FROM events").fetchone()[0],
                    event_count,
                )

    def test_graduated_pump_claim_preserves_curve_gates_and_pins_both_programs(self):
        mint, creator, pool, payer = [str(Keypair().pubkey()) for _ in range(4)]
        curve = bytearray(160)
        curve[:8] = CURVE_DISC
        curve[48] = 1
        curve[49:81] = bytes(Pubkey.from_string(creator))
        amm = bytearray(287)
        amm[:8] = hashlib.sha256(b"account:Pool").digest()[:8]
        amm[43:75] = bytes(Pubkey.from_string(mint))
        amm[75:107] = bytes(Pubkey.from_string(WSOL))
        amm[211:243] = bytes(Pubkey.from_string(creator))

        def account(owner, raw):
            return {"owner": owner, "data": [base64.b64encode(raw).decode(), "base64"]}

        def fetch(method, params):
            if method == "getMultipleAccounts":
                return {
                    "context": {"slot": 100},
                    "value": [account(PUMP, curve), None, None],
                }
            return {
                "context": {"slot": 100},
                "value": account(AMM, amm) if params[0] == pool else None,
            }

        result = observe(mint, creator, fetch, pool)
        self.assertTrue(result["standard_claim_supported"])
        planned = claim_plan(
            result,
            payer,
            str(Hash.default()),
            {"program_data_sha256": "a" * 64},
            "a" * 64,
            {"program_data_sha256": "b" * 64},
            "b" * 64,
        )
        self.assertEqual(len(planned.message.instructions), 2)
        with self.assertRaises(ValueError):
            claim_plan(
                result,
                payer,
                str(Hash.default()),
                {"program_data_sha256": "a" * 64},
                "a" * 64,
                {"program_data_sha256": "b" * 64},
                "c" * 64,
            )
        for storage, index in (
            (curve, 81),
            (curve, 82),
            (curve, 124),
            (amm, 243),
            (amm, 244),
            (amm, 245),
            (amm, 270),
        ):
            with self.subTest(flag=index):
                storage[index] = 1
                self.assertFalse(
                    observe(mint, creator, fetch, pool)["standard_claim_supported"]
                )
                storage[index] = 0
        self.assertFalse(observe(mint, creator, fetch)["standard_claim_supported"])

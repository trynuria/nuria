"""Current Pump fee interfaces, unsigned official vectors and payout accounting."""

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from commerce.config import Policy
from commerce.fees import AMM, CURVE_DISC, PUMP, SYSTEM, WSOL, claim_plan, observe
from commerce.funding import Collector, claim_economics, reject_rent_changes, writable
from commerce.ledger import Ledger
from commerce.solana import associated

FIXTURE = json.loads((Path(__file__).parent / "fixtures-pump-sweep.json").read_text())


def account(owner, raw=b"", **extra):
    return {"owner": owner, "data": [base64.b64encode(raw).decode(), "base64"], **extra}


class PumpSweepTests(unittest.TestCase):
    def state(self):
        args = FIXTURE["input"]
        mint, creator, pool = args["mint"], args["creator"], args["pool"]
        curve = bytearray(166)
        curve[:8] = CURVE_DISC
        curve[48] = 1
        curve[49:81] = bytes(Pubkey.from_string(creator))
        curve[125:133] = (9000).to_bytes(8, "little")
        amm = bytearray(287)
        amm[:8] = hashlib.sha256(b"account:Pool").digest()[:8]
        amm[43:75] = bytes(Pubkey.from_string(mint))
        amm[75:107] = bytes(Pubkey.from_string(WSOL))
        amm[171:203] = bytes(Pubkey.from_string(associated(pool, WSOL)))
        amm[211:243] = bytes(Pubkey.from_string(creator))
        amm[245:261] = (-11000).to_bytes(16, "little", signed=True)
        amm[279:287] = (11000).to_bytes(8, "little")

        def fetch(method, params):
            if method == "getMultipleAccounts":
                return {
                    "context": {"slot": 1},
                    "value": [
                        account(PUMP, curve),
                        account(SYSTEM, lamports=7000),
                        None,
                    ],
                }
            return {
                "context": {"slot": 1},
                "value": account(AMM, amm) if params[0] == pool else None,
            }

        return curve, amm, fetch

    def test_swept_native_plan_matches_current_official_sdk(self):
        _, _, fetch = self.state()
        args = FIXTURE["input"]
        result = observe(args["mint"], args["creator"], fetch, args["pool"])
        planned = claim_plan(
            result,
            args["payer"],
            str(Hash.default()),
            {"program_data_sha256": "a" * 64},
            "a" * 64,
            {"program_data_sha256": "b" * 64},
            "b" * 64,
        )
        self.assertEqual(len(planned.message.instructions), 4)
        self.assertFalse(planned.is_signed())
        keys = [str(k) for k in planned.message.account_keys]
        for actual, expected in zip(
            planned.message.instructions, FIXTURE["instructions"], strict=True
        ):
            self.assertEqual(keys[actual.program_id_index], expected["program"])
            self.assertEqual(bytes(actual.data).hex(), expected["data"])
            self.assertEqual(
                [keys[n] for n in actual.accounts],
                [a["address"] for a in expected["accounts"]],
            )
        # Compilation combines privileges across all instructions.
        for index, key in enumerate(keys):
            roles = [
                a
                for ix in FIXTURE["instructions"]
                for a in ix["accounts"]
                if a["address"] == key
            ]
            self.assertEqual(
                planned.message.is_signer(index),
                key == args["payer"] or any(a["signer"] for a in roles),
            )
            self.assertEqual(
                writable(planned.message, index),
                key == args["payer"] or any(a["writable"] for a in roles),
            )
        self.assertLessEqual(len(bytes(planned)), 1232)
        for field in ("curve", "vault", "pool", "pool_quote_token_account"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                claim_plan(
                    {**result, field: str(Keypair().pubkey())},
                    args["payer"],
                    str(Hash.default()),
                    {"program_data_sha256": "a" * 64},
                    "a" * 64,
                    {"program_data_sha256": "b" * 64},
                    "b" * 64,
                )

    def test_appended_fee_fields_and_standard_negative_virtual_reserves(self):
        curve, pool, fetch = self.state()
        args = FIXTURE["input"]
        value = observe(args["mint"], args["creator"], fetch, args["pool"])
        self.assertTrue(value["standard_claim_supported"])
        self.assertEqual(value["curve_creator_fee_lamports"], 9000)
        self.assertEqual(value["pool_creator_fee_lamports"], 11000)
        del curve[125:]
        del pool[271:]
        value = observe(args["mint"], args["creator"], fetch, args["pool"])
        self.assertEqual(value["curve_creator_fee_lamports"], 0)
        self.assertEqual(value["pool_creator_fee_lamports"], 0)
        pool[245:261] = (1).to_bytes(16, "little", signed=True)
        self.assertFalse(
            observe(args["mint"], args["creator"], fetch, args["pool"])[
                "standard_claim_supported"
            ]
        )
        curve.append(1)
        with self.assertRaises(ValueError):
            observe(args["mint"], args["creator"], fetch, args["pool"])

    def test_simulation_rejects_creation_growth_closure_and_owner_change(self):
        initial = account(PUMP, b"abc", lamports=1000)
        reject_rent_changes([None, initial], [None, {**initial, "lamports": 500}])
        for before, after in (
            ([None], [initial]),
            ([initial], [None]),
            ([initial], [account(PUMP, b"abcd")]),
            ([initial], [account(AMM, b"abc")]),
            ([initial], None),
        ):
            with self.subTest(after=after), self.assertRaises(ValueError):
                reject_rent_changes(before, after)

    def test_collector_rejects_rent_before_signing_and_journals_before_send(self):
        for creates_account in (True, False):
            with self.subTest(creates_account=creates_account):
                path = Path(
                    tempfile.mkdtemp(
                        prefix="claim-simulation-",
                        dir=Path(__file__).parents[1] / ".test-state",
                    )
                )
                ledger = Ledger(path / "ledger.sqlite3")
                self.addCleanup(ledger.db.close)
                key = Keypair()
                args = FIXTURE["input"]
                curve_data, _, observe_rpc = self.state()
                observation = observe(
                    args["mint"], args["creator"], observe_rpc, args["pool"]
                )
                observation["vault_balance_lamports"] = 2_000_000
                observation["graduated"] = False
                observation["pool_creator_fee_lamports"] = 0
                observation.pop("amm_token_vault")
                policy = Policy(
                    mint=args["mint"],
                    creator_wallet=args["creator"],
                    spending_wallet=str(key.pubkey()),
                )
                events = []

                class Signer:
                    def sign_transaction(self, wire):
                        events.append("sign")
                        return VersionedTransaction(wire.message, [key])

                before = []

                def fetch(method, params):
                    if method == "getLatestBlockhash":
                        return {
                            "value": {
                                "blockhash": str(Hash.default()),
                                "lastValidBlockHeight": 100,
                            }
                        }
                    if method == "getFeeForMessage":
                        return {"value": 5000}
                    if method == "getMultipleAccounts":
                        for address in params[0]:
                            if address in (str(key.pubkey()), args["creator"]):
                                before.append(account(SYSTEM, lamports=10_000_000))
                            elif address == observation["vault"]:
                                before.append(account(SYSTEM, lamports=2_000_000))
                            elif address == observation["curve"]:
                                before.append(
                                    account(PUMP, curve_data, lamports=100_000_000)
                                )
                            else:
                                before.append(None)
                        self.addresses = params[0]
                        return {"context": {"slot": 1}, "value": before}
                    if method == "simulateTransaction":
                        self.assertEqual(events, [])
                        self.assertIn("accounts", params[1])
                        self.assertFalse(params[1]["sigVerify"])
                        after = [dict(a) if a else None for a in before]
                        deltas = {
                            observation["vault"]: -2_000_000,
                            observation["curve"]: -9000,
                            args["creator"]: 2_009_000,
                            str(key.pubkey()): -5000,
                        }
                        for address, delta in deltas.items():
                            after[self.addresses.index(address)]["lamports"] += delta
                        if creates_account:
                            after[after.index(None)] = account(SYSTEM, lamports=1000)
                        events.append("simulate")
                        wire = VersionedTransaction.from_bytes(
                            base64.b64decode(params[0])
                        )
                        keys = [str(k) for k in wire.message.account_keys]
                        pre = [
                            (
                                before[self.addresses.index(a)]["lamports"]
                                if before[self.addresses.index(a)]
                                else 0
                            )
                            if a in self.addresses
                            else 0
                            for a in keys
                        ]
                        post = [
                            p + deltas.get(a, 0) for a, p in zip(keys, pre, strict=True)
                        ]
                        return {
                            "value": {
                                "err": None,
                                "accounts": after,
                                "preBalances": pre,
                                "postBalances": post,
                                "fee": 5000,
                            }
                        }
                    if method == "sendTransaction":
                        row = ledger.db.execute(
                            "SELECT status,wire FROM collections"
                        ).fetchone()
                        self.assertEqual(row, ("signed", params[0]))
                        events.append("send")
                        return str(
                            VersionedTransaction.from_bytes(
                                base64.b64decode(params[0])
                            ).signatures[0]
                        )
                    raise AssertionError(method)

                controls = {
                    "enabled": True,
                    "minimum_claim_lamports": 1_000_000,
                    "daily_gas_lamports": 10000,
                    "pump_program_data_sha256": "a" * 64,
                    "amm_program_data_sha256": "b" * 64,
                }

                def identity(_, program=PUMP):
                    return {
                        "program": program,
                        "program_data_sha256": ("a" if program == PUMP else "b") * 64,
                    }

                worker = Collector(ledger, fetch)
                with patch("commerce.funding.program_identity", identity):
                    worker.collect(observation, policy, Signer(), controls, 1000)
                self.assertEqual(
                    events,
                    ["simulate"] if creates_account else ["simulate", "sign", "send"],
                )
                self.assertEqual(
                    ledger.db.execute("SELECT status FROM collections").fetchone()[0],
                    "failed" if creates_account else "submitted",
                )
                with patch("commerce.funding.program_identity", identity):
                    self.assertIsNone(
                        worker.collect(observation, policy, Signer(), controls, 1059)
                    )

    def test_sweep_credit_conserves_curve_pool_and_aggregate_vault_funds(self):
        directory = Path(
            tempfile.mkdtemp(
                prefix="pump-sweep-", dir=Path(__file__).parents[1] / ".test-state"
            )
        )
        ledger = Ledger(directory / "ledger.sqlite3")
        self.addCleanup(ledger.db.close)
        signer = Keypair()
        creator, curve, vault, pool_quote, amm_vault = [
            str(k) for k in [signer.pubkey(), *(Keypair().pubkey() for _ in range(4))]
        ]
        addresses = [creator, curve, vault, pool_quote, amm_vault]
        ix = Instruction(
            Pubkey.from_string(PUMP),
            b"payout-fixture",
            [AccountMeta(Pubkey.from_string(a), a == creator, True) for a in addresses],
        )
        wire = VersionedTransaction(
            MessageV0.try_compile(signer.pubkey(), [ix], [], Hash.default()), [signer]
        )
        keys = [str(k) for k in wire.message.account_keys]
        pre = [2000000] * len(keys)
        post = pre.copy()
        post[keys.index(creator)] += 42000 - 5000
        post[keys.index(curve)] -= 9000
        post[keys.index(vault)] -= 7000

        def token(address, amount):
            return {
                "accountIndex": keys.index(address),
                "mint": WSOL,
                "uiTokenAmount": {"amount": str(amount), "decimals": 9},
            }

        evidence = {
            "slot": 2,
            "transaction": [base64.b64encode(bytes(wire)).decode(), "base64"],
            "meta": {
                "err": None,
                "fee": 5000,
                "preBalances": pre,
                "postBalances": post,
                "preTokenBalances": [token(pool_quote, 11000), token(amm_vault, 15000)],
                "postTokenBalances": [token(pool_quote, 0), token(amm_vault, 0)],
            },
        }
        terms = {
            "creator_wallet": creator,
            "vault": vault,
            "curve": curve,
            "pool_quote_token_account": pool_quote,
            "amm_token_vault": amm_vault,
            "claim_interface": "native_sweep_v2",
        }
        collector = Collector(ledger, lambda *_: evidence)
        ledger.db.execute(
            "INSERT INTO collections(id,status,signature,message_sha256,terms,day,fee) VALUES('sweep','uncertain',?,?,?,'2026-10-08',5000)",
            (
                str(wire.signatures[0]),
                hashlib.sha256(to_bytes_versioned(wire.message)).hexdigest(),
                json.dumps(terms),
            ),
        )
        post[keys.index(creator)] += 1
        with self.assertRaises(ValueError):
            collector.reconcile()
        post[keys.index(creator)] -= 1
        collector.reconcile()
        self.assertEqual(ledger.recent()[0]["collected_lamports"], 42000)
        self.assertEqual(ledger.recent()[0]["curve_swept_lamports"], 9000)
        self.assertEqual(ledger.recent()[0]["pool_swept_lamports"], 11000)
        collector.reconcile()
        self.assertEqual(len(ledger.recent()), 1)

    def test_simulated_claim_rejects_skipped_bridge_hidden_topups_and_missing_balances(
        self,
    ):
        signer = Keypair()
        creator, vault, token_source, trap = [
            str(k) for k in (signer.pubkey(), *(Keypair().pubkey() for _ in range(3)))
        ]
        raw = bytearray(165)
        raw[:32] = bytes(Pubkey.from_string(WSOL))
        raw[32:64] = bytes(Keypair().pubkey())
        raw[64:72] = (2_000_000).to_bytes(8, "little")
        raw[108] = 1
        raw[109:113] = (1).to_bytes(4, "little")
        raw[113:121] = (2_039_280).to_bytes(8, "little")
        from commerce.config import TOKEN

        snapshots = {
            creator: account(SYSTEM, lamports=10_000_000),
            vault: account(SYSTEM, lamports=3_000_000),
            token_source: account(TOKEN, raw, lamports=4_039_280),
            trap: account(SYSTEM, lamports=100_000),
        }
        ix = Instruction(
            Pubkey.from_string(PUMP),
            b"simulation-fixture",
            [AccountMeta(Pubkey.from_string(a), a == creator, True) for a in snapshots],
        )
        message = MessageV0.try_compile(signer.pubkey(), [ix], [], Hash.default())
        keys = [str(k) for k in message.account_keys]
        addresses = [a for n, a in enumerate(keys) if writable(message, n)]
        before = [snapshots[a] for a in addresses]
        after = [dict(a) for a in before]
        after[addresses.index(creator)]["lamports"] += 5_000_000 - 5000
        after[addresses.index(vault)]["lamports"] -= 3_000_000
        changed = raw.copy()
        changed[64:72] = bytes(8)
        after[addresses.index(token_source)] = account(
            TOKEN, changed, lamports=2_039_280
        )
        pre = [snapshots[a]["lamports"] if a in snapshots else 1 for a in keys]
        post = [
            after[addresses.index(a)]["lamports"] if a in addresses else p
            for a, p in zip(keys, pre, strict=True)
        ]
        simulation = {
            "fee": 5000,
            "accounts": after,
            "preBalances": pre,
            "postBalances": post,
        }
        terms = {
            "creator_wallet": creator,
            "vault": vault,
            "amm_token_vault": token_source,
        }
        result = claim_economics(
            message, addresses, before, simulation, terms, 5000, 1_000_000
        )
        self.assertEqual(result["gross_beneficiary_lamports"], 5_000_000)
        self.assertEqual(result["net_beneficiary_lamports"], 4_995_000)
        for malformed in (
            {**simulation, "preBalances": None},
            {**simulation, "fee": 6000},
        ):
            with self.assertRaises(ValueError):
                claim_economics(
                    message, addresses, before, malformed, terms, 5000, 1_000_000
                )
        # An unchanged account allocation can still consume rent. Move ten
        # lamports from the beneficiary into an existing unrelated account.
        after[addresses.index(creator)]["lamports"] -= 10
        after[addresses.index(trap)]["lamports"] += 10
        post[keys.index(creator)] -= 10
        post[keys.index(trap)] += 10
        with self.assertRaises(ValueError):
            claim_economics(
                message, addresses, before, simulation, terms, 5000, 1_000_000
            )
        # A bridge that leaves funds in its source is not a useful collection.
        unchanged = [dict(a) for a in before]
        unchanged[addresses.index(creator)]["lamports"] -= 5000
        zero_post = pre.copy()
        zero_post[keys.index(creator)] -= 5000
        with self.assertRaises(ValueError):
            claim_economics(
                message,
                addresses,
                before,
                {
                    "fee": 5000,
                    "accounts": unchanged,
                    "preBalances": pre,
                    "postBalances": zero_post,
                },
                terms,
                5000,
                1_000_000,
            )

"""Unsigned conversion rejects hidden native costs and damaged token authority."""

import base64
import copy
import hashlib
import json
import os
import unittest
from unittest.mock import patch

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from commerce.config import TOKEN, USDC, Policy
from commerce.fees import SYSTEM, WSOL
from commerce.ledger import Ledger
from commerce.solana import associated
from commerce.swaps import (
    EVENT_AUTHORITY,
    JUPITER,
    TOKEN_2022,
    Converter,
    conversion_economics,
    inspect,
    temporary_native_cleanup,
)
from tests.test_financial_rails import directory, swap_fixture


def account(owner, lamports, raw=b""):
    return {
        "owner": owner,
        "lamports": lamports,
        "data": [base64.b64encode(raw).decode(), "base64"],
        "executable": False,
    }


def token(wallet, mint, units):
    raw = bytearray(165)
    raw[:32] = bytes(Pubkey.from_string(mint))
    raw[32:64] = bytes(Pubkey.from_string(wallet))
    raw[64:72] = units.to_bytes(8, "little")
    raw[108] = 1
    return account(TOKEN, 2_039_280, raw)


class ConversionTests(unittest.TestCase):
    def fixture(self):
        wallet = str(Keypair().pubkey())
        keys = [wallet, associated(wallet, WSOL), associated(wallet)]
        before = [account(SYSTEM, 50_000_000), None, token(wallet, USDC, 20_000_000)]
        after = copy.deepcopy(before)
        after[0]["lamports"] -= 1_005_000
        after[2] = token(wallet, USDC, 20_110_000)
        simulation = {
            "err": None,
            "fee": 5000,
            "preBalances": [a["lamports"] if a else 0 for a in before],
            "postBalances": [a["lamports"] if a else 0 for a in after],
            "accounts": after,
        }
        return keys, before, simulation, wallet

    def check(self, keys, before, simulation, wallet):
        return conversion_economics(
            keys, keys, before, simulation, wallet, 1_000_000, 100_000, 5000, 10_000_000
        )

    def test_exact_native_input_and_useful_usdc_without_retained_rent(self):
        keys, before, sim, wallet = self.fixture()
        result = self.check(keys, before, sim, wallet)
        self.assertEqual(result["input_lamports"], 1_000_000)
        self.assertEqual(result["output_micro_usdc"], 110_000)
        self.assertEqual(result["retained_rent_lamports"], 0)
        sim["accounts"][1] = account(SYSTEM, 0)
        self.assertEqual(self.check(keys, before, sim, wallet), result)

    def test_unknown_fee_or_partial_same_bank_evidence_refuses_signing(self):
        for field in ("fee", "preBalances", "postBalances", "accounts"):
            with self.subTest(field=field):
                keys, before, sim, wallet = self.fixture()
                del sim[field]
                with self.assertRaises(ValueError):
                    self.check(keys, before, sim, wallet)
        keys, before, sim, wallet = self.fixture()
        before[0]["lamports"] -= 1
        with self.assertRaises(ValueError):
            self.check(keys, before, sim, wallet)

    def test_extra_native_cost_low_output_or_reserve_consumption_is_refused(self):
        for variant in ("native", "output", "reserve"):
            with self.subTest(variant=variant):
                keys, before, sim, wallet = self.fixture()
                if variant == "output":
                    sim["accounts"][2] = token(wallet, USDC, 20_099_999)
                else:
                    debit = 1 if variant == "native" else 40_000_000
                    sim["accounts"][0]["lamports"] -= debit
                    sim["postBalances"][0] -= debit
                with self.assertRaises(ValueError):
                    self.check(keys, before, sim, wallet)

    def test_destination_creation_delegate_and_authority_changes_are_refused(self):
        for variant in ("creation", "delegate", "owner", "rent"):
            with self.subTest(variant=variant):
                keys, before, sim, wallet = self.fixture()
                if variant == "creation":
                    before[2] = None
                    sim["preBalances"][2] = 0
                elif variant == "rent":
                    sim["accounts"][2]["lamports"] += 1
                    sim["postBalances"][2] += 1
                else:
                    raw = bytearray(base64.b64decode(sim["accounts"][2]["data"][0]))
                    raw[72 if variant == "delegate" else 32] ^= 1
                    sim["accounts"][2]["data"][0] = base64.b64encode(raw).decode()
                with self.assertRaises(ValueError):
                    self.check(keys, before, sim, wallet)

    def test_new_retained_wsol_rent_and_other_wallet_asset_changes_are_refused(self):
        keys, before, sim, wallet = self.fixture()
        wsol = token(wallet, WSOL, 0)
        raw = bytearray(base64.b64decode(wsol["data"][0]))
        raw[109:113] = (1).to_bytes(4, "little")
        raw[113:121] = wsol["lamports"].to_bytes(8, "little")
        wsol["data"][0] = base64.b64encode(raw).decode()
        sim["accounts"][1] = wsol
        sim["postBalances"][1] = wsol["lamports"]
        sim["accounts"][0]["lamports"] -= wsol["lamports"]
        sim["postBalances"][0] -= wsol["lamports"]
        with self.assertRaises(ValueError):
            self.check(keys, before, sim, wallet)
        keys, before, sim, wallet = self.fixture()
        keys.append(str(Keypair().pubkey()))
        other = token(wallet, str(Keypair().pubkey()), 50)
        before.append(other)
        changed = copy.deepcopy(other)
        raw = bytearray(base64.b64decode(changed["data"][0]))
        raw[64] = 49
        changed["data"][0] = base64.b64encode(raw).decode()
        sim["accounts"].append(changed)
        sim["preBalances"].append(other["lamports"])
        sim["postBalances"].append(other["lamports"])
        with self.assertRaises(ValueError):
            self.check(keys, before, sim, wallet)
        other["owner"] = TOKEN_2022
        changed["owner"] = TOKEN_2022
        with self.assertRaises(ValueError):
            self.check(keys, before, sim, wallet)

    def v2(self, shared=False):
        build, wallet, amount = swap_fixture()
        ix = build["swapInstruction"]
        source, target = associated(wallet, WSOL), associated(wallet)
        prefix = [
            wallet,
            source,
            target,
            WSOL,
            USDC,
            TOKEN,
            TOKEN,
            JUPITER,
            EVENT_AUTHORITY,
            JUPITER,
        ]
        raw = hashlib.sha256(b"global:route_v2").digest()[:8]
        if shared:
            authority = str(
                Pubkey.find_program_address(
                    [b"authority", b"\x01"], Pubkey.from_string(JUPITER)
                )[0]
            )
            prefix = [
                authority,
                wallet,
                source,
                str(Keypair().pubkey()),
                str(Keypair().pubkey()),
                target,
                WSOL,
                USDC,
                TOKEN,
                TOKEN,
                EVENT_AUTHORITY,
                JUPITER,
            ]
            raw = (
                hashlib.sha256(b"global:shared_accounts_route_v2").digest()[:8]
                + b"\x01"
            )
        raw += (
            amount.to_bytes(8, "little")
            + (200_000).to_bytes(8, "little")
            + (50).to_bytes(2, "little")
            + bytes(4)
            + (1).to_bytes(4, "little")
            + b"\x00\x10\x27\x00\x01"
        )
        ix["accounts"] = [
            {"pubkey": k, "isSigner": k == wallet, "isWritable": k in (source, target)}
            for k in prefix
        ]
        ix["data"] = base64.b64encode(raw).decode()
        return build, wallet, amount

    def test_current_v2_routes_preserve_exact_amount_and_destination(self):
        for shared in (False, True):
            build, wallet, amount = self.v2(shared)
            self.assertEqual(len(inspect(build, wallet, amount, 199_000, 50)), 2)
            for offset in (
                (17 if shared else 16),
                (27 if shared else 26),
                (29 if shared else 28),
            ):
                bad = copy.deepcopy(build)
                raw = bytearray(base64.b64decode(bad["swapInstruction"]["data"]))
                raw[offset] ^= 1
                bad["swapInstruction"]["data"] = base64.b64encode(raw).decode()
                with self.assertRaises(ValueError):
                    inspect(bad, wallet, amount, 199_000, 50)
            bad = copy.deepcopy(build)
            bad["swapInstruction"]["accounts"][5 if shared else 2]["pubkey"] = str(
                Keypair().pubkey()
            )
            with self.assertRaises(ValueError):
                inspect(bad, wallet, amount, 199_000, 50)

    def test_temporary_cleanup_refunds_only_new_wsol_and_preserves_original_build(self):
        build, wallet, amount = self.v2()
        fixed, added = temporary_native_cleanup(
            build, wallet, lambda *_: {"value": None}
        )
        self.assertTrue(added)
        self.assertIsNone(build["cleanupInstruction"])
        self.assertEqual(len(inspect(fixed, wallet, amount, 199_000, 50)), 3)
        existing, added = temporary_native_cleanup(
            build, wallet, lambda *_: {"value": {"exists": True}}
        )
        self.assertFalse(added)
        self.assertIs(existing, build)

    def test_bad_simulated_credit_never_signs_and_ambiguous_send_blocks_replacement(
        self,
    ):
        build, _, amount = self.v2()
        key = Keypair()
        wallet = str(key.pubkey())
        old_wallet = build["swapInstruction"]["accounts"][0]["pubkey"]
        replacements = {
            old_wallet: wallet,
            associated(old_wallet, WSOL): associated(wallet, WSOL),
            associated(old_wallet): associated(wallet),
        }
        for ix in [*build["setupInstructions"], build["swapInstruction"]]:
            for meta in ix["accounts"]:
                meta["pubkey"] = replacements.get(meta["pubkey"], meta["pubkey"])
        ledger = Ledger(directory() / "conversion.sqlite3")
        self.addCleanup(ledger.db.close)
        captured, calls = {}, []
        credit = 99_999
        simulation_slot = 10

        def initial(address):
            if address == wallet:
                return account(SYSTEM, 50_000_000)
            if address == associated(wallet):
                return token(wallet, USDC, 20_000_000)
            return None

        def fetch(method, params):
            if method == "getBalance":
                return {"value": 50_000_000}
            if method == "getAccountInfo":
                return {"value": None}
            if method == "getLatestBlockhash":
                return {
                    "value": {
                        "blockhash": str(Hash.new_unique()),
                        "lastValidBlockHeight": 100,
                    }
                }
            if method == "getFeeForMessage":
                return {"value": 5000}
            if method == "getMultipleAccounts":
                return {
                    "context": {"slot": 10},
                    "value": [initial(a) for a in params[0]],
                }
            if method == "simulateTransaction":
                tx = VersionedTransaction.from_bytes(base64.b64decode(params[0]))
                keys = [str(k) for k in tx.message.account_keys]
                pre = [initial(a)["lamports"] if initial(a) else 0 for a in keys]
                post = pre.copy()
                post[keys.index(wallet)] -= amount + 5000
                after = [initial(a) for a in params[1]["accounts"]["addresses"]]
                for n, address in enumerate(params[1]["accounts"]["addresses"]):
                    if address == wallet:
                        after[n]["lamports"] -= amount + 5000
                    if address == associated(wallet):
                        after[n] = token(wallet, USDC, 20_000_000 + credit)
                self.assertTrue(all(bytes(s) == bytes(64) for s in tx.signatures))
                return {
                    "context": {"slot": simulation_slot},
                    "value": {
                        "err": None,
                        "fee": 5000,
                        "preBalances": pre,
                        "postBalances": post,
                        "accounts": after,
                    },
                }
            if method == "sendTransaction":
                calls.append("send")
                row = ledger.db.execute(
                    "SELECT status,wire FROM conversions WHERE status='signed'"
                ).fetchone()
                self.assertEqual(row, ("signed", params[0]))
                raise TimeoutError("Fixture ambiguous submission")
            raise AssertionError(method)

        class Signer:
            def sign_transaction(inner, tx):
                calls.append("sign")
                self.assertIn(
                    "simulated_economics",
                    json.loads(
                        ledger.db.execute(
                            "SELECT terms FROM conversions WHERE status='reserved'"
                        ).fetchone()[0]
                    ),
                )
                captured["tx"] = tx
                return VersionedTransaction(tx.message, [key])

        controls = {
            "enabled": True,
            "input_lamports": amount,
            "daily_input_lamports": amount * 2,
            "minimum_micro_usdc": 100_000,
            "daily_gas_lamports": 10_000,
            "sol_reserve_lamports": 10_000_000,
            "slippage_bps": 50,
            "jupiter_program_data_sha256": "fixture-pin",
        }
        worker = Converter(
            ledger, fetch, lambda *_: (200, {}, json.dumps(build).encode())
        )
        policy = Policy(
            signer="privy",
            spending_wallet=wallet,
            per_day_micro_usdc=25_000_000,
            reserve_micro_usdc=5_000_000,
        )
        with (
            patch.dict(os.environ, {"JUPITER_API_KEY": "unfunded-fixture"}),
            patch(
                "commerce.swaps.program_identity",
                return_value={"program_data_sha256": "fixture-pin"},
            ),
        ):
            worker.convert(
                policy, Signer(), controls, {"micro_usdc": 20_000_000}, 1780000000
            )
            self.assertEqual(calls, [])
            self.assertEqual(
                ledger.db.execute("SELECT status FROM conversions").fetchone()[0],
                "failed",
            )
            credit = 200_000
            simulation_slot = 9
            worker.convert(
                policy,
                Signer(),
                controls,
                {"micro_usdc": 20_000_000},
                1780000000 + 86400,
            )
            self.assertEqual(calls, [])
            simulation_slot = 10
            worker.convert(
                policy,
                Signer(),
                controls,
                {"micro_usdc": 20_000_000},
                1780000000 + 2 * 86400,
            )
            self.assertEqual(calls, ["sign", "send"])
            worker.convert(
                policy,
                Signer(),
                controls,
                {"micro_usdc": 20_000_000},
                1780000000 + 3 * 86400,
            )
            self.assertEqual(calls, ["sign", "send"])


if __name__ == "__main__":
    unittest.main()

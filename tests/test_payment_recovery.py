"""Expiry cannot hide a settled payment or release incomplete evidence."""

import base64
import hashlib
import time
import unittest

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from commerce.config import COMPUTE, MEMO, NETWORK, TOKEN, USDC, Policy
from commerce.ledger import Ledger, canonical
from commerce.recovery import inspect_history
from commerce.session_budget import SessionBudget
from commerce.solana import associated


def fixture():
    wallet, payer, recipient = Keypair(), Keypair(), Keypair()
    address = str(wallet.pubkey())
    accepted = {
        "scheme": "exact",
        "network": NETWORK,
        "asset": USDC,
        "payTo": str(recipient.pubkey()),
        "amount": "10000",
        "maxTimeoutSeconds": 60,
        "extra": {"feePayer": str(payer.pubkey())},
    }
    source, target = associated(address), associated(accepted["payTo"])
    instructions = [
        Instruction(
            Pubkey.from_string(COMPUTE), b"\x02" + (20_000).to_bytes(4, "little"), []
        ),
        Instruction(
            Pubkey.from_string(COMPUTE), b"\x03" + (1).to_bytes(8, "little"), []
        ),
        Instruction(
            Pubkey.from_string(TOKEN),
            b"\x0c" + (10000).to_bytes(8, "little") + b"\x06",
            [
                AccountMeta(Pubkey.from_string(source), False, True),
                AccountMeta(Pubkey.from_string(USDC), False, False),
                AccountMeta(Pubkey.from_string(target), False, True),
                AccountMeta(wallet.pubkey(), True, False),
            ],
        ),
        Instruction(Pubkey.from_string(MEMO), b"recovery-test", []),
    ]
    message = MessageV0.try_compile(payer.pubkey(), instructions, [], Hash.new_unique())
    client = wallet.sign_message(b"\x80" + bytes(message))
    unsigned = VersionedTransaction.populate(message, [Signature.default(), client])
    included = VersionedTransaction(message, [payer, wallet])
    checkpoint = VersionedTransaction(
        MessageV0.try_compile(wallet.pubkey(), [], [], Hash.new_unique()), [wallet]
    )
    anchor = {"signature": str(checkpoint.signatures[0]), "slot": 100}
    authorization = {
        "header": base64.b64encode(
            canonical(
                {
                    "x402Version": 2,
                    "accepted": accepted,
                    "payload": {
                        "transaction": base64.b64encode(bytes(unsigned)).decode()
                    },
                }
            ).encode()
        ).decode(),
        "client_signature": str(client),
        "transaction_sha256": hashlib.sha256(bytes(unsigned)).hexdigest(),
        "history_anchor": anchor,
    }

    def row(tx, slot):
        return {
            "signature": str(tx.signatures[0]),
            "slot": slot,
            "confirmationStatus": "finalized",
        }

    def evidence(tx, slot):
        return {
            "slot": slot,
            "transaction": [base64.b64encode(bytes(tx)).decode(), "base64"],
            "meta": {"err": None},
        }

    rows = [row(checkpoint, 100)]
    transactions = {
        anchor["signature"]: evidence(checkpoint, 100),
        str(included.signatures[0]): evidence(included, 110),
    }

    def fetch(method, params):
        if method == "isBlockhashValid":
            return {"value": False, "context": {"slot": 200}}
        if method == "getSignaturesForAddress":
            return rows
        if method == "getTransaction":
            return transactions.get(params[0])
        raise AssertionError(method)

    return (
        address,
        accepted,
        authorization,
        anchor,
        fetch,
        rows,
        transactions,
        included,
        row,
    )


class PaymentRecoveryTests(unittest.TestCase):
    def test_expiration_reaches_pre_signing_anchor(self):
        wallet, accepted, auth, anchor, fetch, *_ = fixture()
        result = inspect_history(auth, accepted, wallet, anchor, fetch)
        self.assertEqual(result["state"], "expired_unsettled")
        self.assertEqual(result["history"][-1], anchor)
        self.assertEqual(result["finalized_expiry_slot"], 200)

    def test_expired_but_included_is_not_unsettled(self):
        wallet, accepted, auth, anchor, fetch, rows, transactions, included, row = (
            fixture()
        )
        rows.insert(0, row(included, 110))
        result = inspect_history(auth, accepted, wallet, anchor, fetch)
        self.assertEqual(result["state"], "included")
        self.assertFalse(result["failed"])
        self.assertEqual(result["transaction"], str(included.signatures[0]))

    def test_missing_anchor_or_transaction_keeps_uncertainty(self):
        for missing in ("anchor", "transaction", "page"):
            with self.subTest(missing=missing):
                wallet, accepted, auth, anchor, fetch, rows, transactions, *_ = (
                    fixture()
                )
                if missing == "anchor":
                    anchor = None
                elif missing == "transaction":
                    transactions.clear()
                else:
                    rows.clear()
                with self.assertRaises(ValueError):
                    inspect_history(auth, accepted, wallet, anchor, fetch)

    def test_live_blockhash_does_not_scan_or_release(self):
        wallet, accepted, auth, anchor, _, *_ = fixture()

        def fetch(method, params):
            self.assertEqual(method, "isBlockhashValid")
            return {"value": True, "context": {"slot": 200}}

        self.assertIsNone(inspect_history(auth, accepted, wallet, anchor, fetch))

    def test_authorization_tampering_and_history_gaps_fail_closed(self):
        for changed in ("hash", "recipient", "slot", "commitment"):
            with self.subTest(changed=changed):
                wallet, accepted, auth, anchor, fetch, rows, *_ = fixture()
                if changed == "hash":
                    auth["transaction_sha256"] = "a" * 64
                elif changed == "recipient":
                    accepted = {**accepted, "payTo": str(Keypair().pubkey())}
                elif changed == "slot":
                    rows[0]["slot"] = 99
                else:
                    rows[0]["confirmationStatus"] = "confirmed"
                with self.assertRaises(ValueError):
                    inspect_history(auth, accepted, wallet, anchor, fetch)

    def test_repeated_page_and_bounded_scan_cannot_prove_absence(self):
        wallet, accepted, auth, anchor, fetch, rows, transactions, included, row = (
            fixture()
        )
        # The matching transaction is removed; an unrelated signature page repeats.
        other = Keypair()
        unrelated = VersionedTransaction(
            MessageV0.try_compile(
                other.pubkey(),
                [
                    Instruction(
                        Pubkey.from_string(MEMO),
                        b"unrelated",
                        [AccountMeta(Pubkey.from_string(wallet), False, False)],
                    )
                ],
                [],
                Hash.new_unique(),
            ),
            [other],
        )
        transactions[str(unrelated.signatures[0])] = {
            "slot": 110,
            "transaction": [base64.b64encode(bytes(unrelated)).decode(), "base64"],
            "meta": {"err": None},
        }
        rows[:] = [row(unrelated, 110)]
        for limit in (1, 2):
            with self.assertRaises(ValueError):
                inspect_history(auth, accepted, wallet, anchor, fetch, max_pages=limit)

    def test_ledger_and_total_budget_close_once_without_repaying(self):
        wallet, accepted, auth, anchor, fetch, *_ = fixture()
        now = time.time()
        policy = Policy(
            enabled=True,
            per_job_micro_usdc=2_000_000,
            per_day_micro_usdc=25_000_000,
            reserve_micro_usdc=0,
        )
        ledger = Ledger(":memory:")
        budget = SessionBudget(":memory:", 25_000_000, 2_000_000, now + 100)
        self.addCleanup(ledger.db.close)
        self.addCleanup(budget.db.close)
        job = {
            "id": "one",
            "decision_hash": "a" * 64,
            "provider": "fixture",
            "action": "compare",
            "amount": 10000,
            "accepted": accepted,
        }
        ledger.reserve(job, policy, {"micro_usdc": 20_000_000, "checked_at": now}, now)
        ledger.db.execute(
            "INSERT INTO authorizations VALUES(?,?,NULL)", ("one", canonical(auth))
        )
        ledger.transition("one", "authorized", {}, now)
        ledger.transition("one", "uncertain", {}, now)
        budget.reserve("one", 2_000_000, now)
        budget.disclose("one")
        with self.assertRaises(ValueError):
            budget.release_expired("one", ledger, now)
        proof = inspect_history(auth, accepted, wallet, anchor, fetch)
        with self.assertRaises(ValueError):
            ledger.close_expired("one", {**proof, "checked_at": now - 31}, now)
        with self.assertRaises(ValueError):
            ledger.close_expired(
                "one", {**proof, "client_signature": "wrong"}, time.time()
            )
        self.assertTrue(ledger.close_expired("one", proof, time.time()))
        self.assertFalse(ledger.close_expired("one", proof, time.time()))
        # Crash recovery may release later, after the evidence leaves the public tail.
        for n in range(50):
            ledger.record({"state": "other-history", "at": n})
        self.assertTrue(budget.release_expired("one", ledger, time.time() + 100))
        self.assertFalse(budget.release_expired("one", ledger, time.time()))
        self.assertEqual(
            budget.summary(),
            {"limit": 25_000_000, "spent": 0, "pending": 0, "remaining": 25_000_000},
        )
        with self.assertRaises(ValueError):
            budget.reserve("one", 2_000_000, time.time())
        with self.assertRaises(ValueError):
            budget.disclose("one")
        # Verified expiry frees capacity for new jobs, never for replaying this one.
        for n in range(12):
            budget.reserve("new-" + str(n), 2_000_000, time.time())
        budget.reserve("last-million", 1_000_000, time.time())
        self.assertEqual(budget.summary()["remaining"], 0)
        with self.assertRaises(ValueError):
            budget.reserve("over-total", 1, time.time())
        self.assertEqual(ledger.summary()["reserved_micro_usdc"], 0)
        self.assertTrue(ledger.verify()["valid"])

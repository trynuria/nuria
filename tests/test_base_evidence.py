"""A marketplace receipt must resolve to an exact finalized chain payment."""

import copy
import unittest

from commerce.base_evidence import TRANSFER, verify
from commerce.commissioning import BASE_USDC


class BaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tx = "0x" + "a" * 64
        self.wallet, self.recipient = "0x" + "1" * 40, "0x" + "2" * 40
        self.block_hash = "0x" + "b" * 64
        self.receipt = {
            "blockNumber": "0x64",
            "blockHash": self.block_hash,
            "transactionHash": self.tx,
            "status": "0x1",
            "from": self.wallet,
            "logs": [
                {
                    "address": BASE_USDC,
                    "topics": [
                        TRANSFER,
                        "0x" + "0" * 24 + self.wallet[2:],
                        "0x" + "0" * 24 + self.recipient[2:],
                    ],
                    "data": "0x" + format(1000000, "064x"),
                    "logIndex": "0x3",
                }
            ],
        }
        self.chain, self.finalized, self.code = "0x2105", 100, "0x"

    def rpc(self, method, args):
        if method == "eth_chainId":
            return self.chain
        if method == "eth_getTransactionReceipt":
            return self.receipt
        if method == "eth_getCode":
            return self.code
        if method == "eth_getBlockByNumber":
            return {"number": hex(self.finalized), "hash": self.block_hash}
        self.fail("Unexpected RPC method: " + method)

    def check(self):
        return verify(self.tx, self.wallet, self.recipient, 1000000, "c" * 64, self.rpc)

    def test_exact_finalized_payment_retains_recipient_and_block(self):
        receipt = self.check()
        self.assertEqual(receipt["recipient"], self.recipient)
        self.assertEqual(receipt["block"], 100)
        self.assertEqual(receipt["log_index"], 3)
        self.assertNotIn("delivered", receipt)

    def test_unfinalized_or_missing_receipt_is_unknown(self):
        self.finalized = 99
        self.assertIsNone(self.check())
        self.receipt = None
        self.assertIsNone(self.check())

    def test_wrong_chain_failed_receipt_or_reorg_is_refused(self):
        original = copy.deepcopy(self.receipt)
        self.chain = "0x1"
        with self.assertRaises(ValueError):
            self.check()
        self.chain = "0x2105"
        for changes in (
            {"status": "0x0"},
            {"blockHash": "0x" + "d" * 64},
            {"transactionHash": "0x" + "e" * 64},
        ):
            self.receipt = {**original, **changes}
            with self.assertRaises(ValueError):
                self.check()

    def test_smart_wallet_or_another_outer_sender_requires_separate_adapter(self):
        self.code = "0xef0100" + "a" * 40
        with self.assertRaises(ValueError):
            self.check()
        self.code = "0x"
        self.receipt["from"] = self.recipient
        with self.assertRaises(ValueError):
            self.check()

    def test_wrong_recipient_amount_or_multiple_outgoing_transfers_is_refused(self):
        original = copy.deepcopy(self.receipt["logs"])
        for change in ("recipient", "amount", "duplicate", "padding", "removed"):
            self.receipt["logs"] = copy.deepcopy(original)
            log = self.receipt["logs"][0]
            if change == "recipient":
                log["topics"][2] = "0x" + "0" * 24 + "3" * 40
            elif change == "amount":
                log["data"] = "0x" + format(2, "064x")
            elif change == "duplicate":
                self.receipt["logs"].append(copy.deepcopy(log))
            elif change == "padding":
                log["topics"][1] = "0x" + "a" * 24 + self.wallet[2:]
            else:
                log["removed"] = True
            with self.assertRaises(ValueError):
                self.check()


if __name__ == "__main__":
    unittest.main()

"""Read-only finalized Base USDC payment verification; no wallet signing."""

import re

from commerce.commissioning import BASE_USDC, HASH

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ADDRESS = re.compile(r"0x[a-fA-F0-9]{40}\Z")
TX = re.compile(r"0x[a-fA-F0-9]{64}\Z")


def verify(transaction, wallet, recipient, amount, offer_sha256, fetch):
    if (
        not TX.fullmatch(transaction)
        or not ADDRESS.fullmatch(wallet)
        or not ADDRESS.fullmatch(recipient)
        or type(amount) is not int
        or amount <= 0
        or not HASH.fullmatch(offer_sha256)
    ):
        raise ValueError("Expected payment identity is invalid")
    if int(fetch("eth_chainId", []), 16) != 8453:
        raise ValueError("Payment RPC is on another chain")
    receipt = fetch("eth_getTransactionReceipt", [transaction])
    finalized = fetch("eth_getBlockByNumber", ["finalized", False])
    if not receipt or not finalized:
        return None
    number = int(receipt["blockNumber"], 16)
    if number > int(finalized["number"], 16):
        return None
    block = fetch("eth_getBlockByNumber", [receipt["blockNumber"], False])
    if (
        not block
        or receipt["blockHash"] != block["hash"]
        or receipt["transactionHash"].lower() != transaction.lower()
        or receipt.get("status") != "0x1"
    ):
        raise ValueError("Payment receipt is inconsistent or failed")
    if (
        receipt["from"].lower() != wallet.lower()
        or fetch("eth_getCode", [wallet, receipt["blockNumber"]]) != "0x"
    ):
        raise ValueError("Expected plain-wallet sender is not established")
    transfers = []
    for item in receipt["logs"]:
        topics = item.get("topics", [])
        if (
            item["address"].lower() != BASE_USDC
            or not topics
            or topics[0].lower() != TRANSFER
        ):
            continue
        if (
            len(topics) != 3
            or item.get("removed", False)
            or not TX.fullmatch(item["data"])
            or any(not TX.fullmatch(t) for t in topics[1:])
            or any(t[2:26] != "0" * 24 for t in topics[1:])
        ):
            raise ValueError("USDC transfer log is malformed")
        sender, receiver = "0x" + topics[1][-40:], "0x" + topics[2][-40:]
        if sender.lower() == wallet.lower():
            transfers.append(
                (receiver.lower(), int(item["data"], 16), int(item["logIndex"], 16))
            )
    if len(transfers) != 1 or transfers[0][:2] != (recipient.lower(), amount):
        raise ValueError("Payment is not one exact authorized USDC transfer")
    return {
        "transaction": transaction,
        "chain_id": 8453,
        "token": BASE_USDC,
        "sender": wallet,
        "recipient": recipient,
        "amount_micro_usdc": amount,
        "block": number,
        "block_hash": block["hash"],
        "log_index": transfers[0][2],
        "finalized": True,
        "offer_sha256": offer_sha256,
        "scope": "Exact finalized transfer; delivery acceptance is separate",
    }

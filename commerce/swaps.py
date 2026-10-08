"""SOL-to-USDC inventory conversion through a constrained Jupiter v2 build.

Only the published exact-input v6 route layouts are accepted. New layouts,
unexpected setup instructions and unverified tables fail closed.
"""

import base64
import hashlib
import json
import os
import time
from urllib.parse import urlencode

from solders.address_lookup_table_account import (
    AddressLookupTable,
    AddressLookupTableAccount,
)
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from commerce.config import ATA, COMPUTE, TOKEN, USDC
from commerce.fees import SYSTEM, WSOL, program_identity
from commerce.ledger import canonical
from commerce.solana import account_bytes, associated
from commerce.transport import request

JUPITER = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
LOOKUP = "AddressLookupTab1e1111111111111111111111111"


def inspect(build, wallet, amount, floor, slip):
    if (
        build.get("inputMint") != WSOL
        or build.get("outputMint") != USDC
        or build.get("inAmount") != str(amount)
        or build.get("swapMode") != "ExactIn"
        or build.get("slippageBps") != slip
        or int(build.get("otherAmountThreshold", "0")) < floor
        or build.get("otherInstructions")
        or build.get("tipInstruction")
    ):
        raise ValueError("Swap quote differs from the bounded SOL-to-USDC intent")
    source, target = associated(wallet, WSOL), associated(wallet)
    raw_instructions = [
        *build.get("computeBudgetInstructions", []),
        *build.get("setupInstructions", []),
        build["swapInstruction"],
        *([build["cleanupInstruction"]] if build.get("cleanupInstruction") else []),
    ]
    if len(raw_instructions) > 12:
        raise ValueError("Swap contains excessive instructions")
    swaps, transfers, instructions = 0, 0, []
    for raw in raw_instructions:
        data = base64.b64decode(raw["data"], validate=True)
        program = raw["programId"]
        accounts = [a["pubkey"] for a in raw["accounts"]]
        if any(a["isSigner"] and a["pubkey"] != wallet for a in raw["accounts"]):
            raise ValueError("Swap contains another required signer")
        if program == COMPUTE:
            if accounts or not (
                (
                    len(data) == 5
                    and data[0] == 2
                    and int.from_bytes(data[1:], "little") <= 1_400_000
                )
                or (
                    len(data) == 9
                    and data[0] == 3
                    and int.from_bytes(data[1:], "little") <= 50_000
                )
            ):
                raise ValueError("Swap compute fee instruction exceeds its ceiling")
        elif program == ATA:
            if (
                data != b"\x01"
                or len(accounts) != 6
                or accounts[0] != wallet
                or accounts[2] != wallet
                or accounts[3] not in (WSOL, USDC)
                or accounts[1] != associated(wallet, accounts[3])
                or accounts[4:] != [SYSTEM, TOKEN]
            ):
                raise ValueError("Swap token-account setup differs from policy")
        elif program == SYSTEM:
            if accounts != [wallet, source] or data != (2).to_bytes(
                4, "little"
            ) + amount.to_bytes(8, "little"):
                raise ValueError("Swap SOL transfer differs from the exact input")
            transfers += 1
        elif program == TOKEN:
            if not (
                (data == b"\x11" and accounts == [source])
                or (data == b"\x09" and accounts == [source, wallet, wallet])
            ):
                raise ValueError("Swap requests unapproved token authority or transfer")
        elif program == JUPITER:
            swaps += 1
            if len(data) < 31:
                raise ValueError("Swap route is truncated")
            route = hashlib.sha256(b"global:route").digest()[:8]
            shared = hashlib.sha256(b"global:shared_accounts_route").digest()[:8]
            if data[:8] == route:
                if (
                    len(accounts) < 6
                    or accounts[0:4] != [TOKEN, wallet, source, target]
                    or accounts[5] != USDC
                    or accounts[4] not in (JUPITER, target)
                ):
                    raise ValueError("Swap route token destination differs")
            elif data[:8] == shared:
                if (
                    len(accounts) < 9
                    or accounts[0] != TOKEN
                    or accounts[2:4] != [wallet, source]
                    or accounts[6:9] != [target, WSOL, USDC]
                ):
                    raise ValueError("Shared swap route token destination differs")
            else:
                raise ValueError("Swap route layout is unsupported")
            incoming, quoted, actual_slip, fee_bps = (
                int.from_bytes(data[-19:-11], "little"),
                int.from_bytes(data[-11:-3], "little"),
                int.from_bytes(data[-3:-1], "little"),
                data[-1],
            )
            if (
                incoming != amount
                or actual_slip != slip
                or fee_bps != 0
                or quoted * (10000 - slip) // 10000 < floor
            ):
                raise ValueError(
                    "Onchain swap amount or minimum output differs from intent"
                )
        else:
            raise ValueError("Swap contains an unapproved top-level program")
        instructions.append(
            Instruction(
                Pubkey.from_string(program),
                data,
                [
                    AccountMeta(
                        Pubkey.from_string(a["pubkey"]), a["isSigner"], a["isWritable"]
                    )
                    for a in raw["accounts"]
                ],
            )
        )
    if swaps != 1 or transfers != 1:
        raise ValueError("Swap requires one exact native input and one route")
    return instructions


def prepare(build, wallet, amount, floor, slip, fetch):
    instructions = inspect(build, wallet, amount, floor, slip)
    tables = []
    declared = build.get("addressesByLookupTableAddress") or {}
    if len(declared) > 8:
        raise ValueError("Swap lookup table count exceeds limit")
    for address, advertised in declared.items():
        result = fetch(
            "getAccountInfo",
            [address, {"encoding": "base64", "commitment": "finalized"}],
        )
        account = result["value"]
        if not account or account["owner"] != LOOKUP:
            raise ValueError("Swap lookup table is unavailable")
        table = AddressLookupTable.deserialize(account_bytes(account))
        if (
            table.meta.deactivation_slot != 2**64 - 1
            or table.meta.last_extended_slot >= result["context"]["slot"]
            or [str(a) for a in table.addresses] != advertised
        ):
            raise ValueError("Swap lookup table differs from finalized evidence")
        tables.append(
            AddressLookupTableAccount(Pubkey.from_string(address), table.addresses)
        )
    recent = fetch("getLatestBlockhash", [{"commitment": "finalized"}])["value"]
    message = MessageV0.try_compile(
        Pubkey.from_string(wallet),
        instructions,
        tables,
        Hash.from_string(recent["blockhash"]),
    )
    if message.header.num_required_signatures != 1:
        raise ValueError("Unexpected swap signer count")
    tx = VersionedTransaction.populate(message, [Signature.default()])
    if len(bytes(tx)) > 1232:
        raise ValueError("Swap exceeds Solana transaction wire limit")
    return tx, recent


class Converter:
    def __init__(self, ledger, fetch, http=request):
        self.ledger, self.fetch, self.http = ledger, fetch, http
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS conversions(
          id TEXT PRIMARY KEY,status TEXT NOT NULL,signature TEXT,wire TEXT,
          message_sha256 TEXT,terms TEXT NOT NULL,day TEXT NOT NULL,input INTEGER NOT NULL,fee INTEGER NOT NULL)""")

    def convert(self, policy, signer, controls, inventory, now):
        if controls.get("enabled") is not True:
            return
        amount, daily, floor = (
            controls.get("input_lamports"),
            controls.get("daily_input_lamports"),
            controls.get("minimum_micro_usdc"),
        )
        gas, sol_floor = (
            controls.get("daily_gas_lamports"),
            controls.get("sol_reserve_lamports"),
        )
        if (
            policy.signer != "privy"
            or any(
                type(v) is not int or v <= 0
                for v in (amount, daily, floor, gas, sol_floor)
            )
            or amount > daily
            or floor > policy.per_day_micro_usdc
            or gas > 1_000_000
            or controls.get("slippage_bps") != 50
        ):
            raise ValueError(
                "Conversion input, output floor and gas budgets require review"
            )
        if inventory["micro_usdc"] >= controls.get(
            "target_micro_usdc", policy.per_day_micro_usdc + policy.reserve_micro_usdc
        ):
            return
        db = self.ledger.db
        if db.execute(
            "SELECT 1 FROM conversions WHERE status IN ('reserved','signed','submitted','uncertain')"
        ).fetchone():
            return
        balance = self.fetch(
            "getBalance", [policy.spending_wallet, {"commitment": "finalized"}]
        )["value"]
        if balance < amount + sol_floor + 100_000:
            return
        deployment = program_identity(self.fetch, JUPITER)
        if deployment["program_data_sha256"] != controls.get(
            "jupiter_program_data_sha256"
        ):
            raise ValueError("Jupiter deployed program differs from reviewed pin")
        key = os.environ.get("JUPITER_API_KEY")
        if not key:
            raise ValueError("Project Jupiter API access is missing")
        endpoint = "https://api.jup.ag/swap/v2/build?" + urlencode(
            {
                "inputMint": WSOL,
                "outputMint": USDC,
                "amount": str(amount),
                "taker": policy.spending_wallet,
                "slippageBps": "50",
            }
        )
        status, _, raw = self.http(endpoint, {"x-api-key": key})
        if status != 200:
            raise ValueError("Jupiter did not return a route")
        response = json.loads(raw)
        tx, recent = prepare(
            response, policy.spending_wallet, amount, floor, 50, self.fetch
        )
        quoted_fee = self.fetch(
            "getFeeForMessage",
            [
                base64.b64encode(to_bytes_versioned(tx.message)).decode(),
                {"commitment": "finalized"},
            ],
        )["value"]
        if type(quoted_fee) is not int or not 0 < quoted_fee <= 100_000:
            raise ValueError("Conversion fee quote is unknown or excessive")
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        terms = {
            "wallet": policy.spending_wallet,
            "input_asset": "SOL",
            "output_asset": USDC,
            "input_lamports": amount,
            "minimum_micro_usdc": floor,
            "slippage_bps": 50,
            "deployment": deployment,
            "quote_sha256": hashlib.sha256(raw).hexdigest(),
            "last_valid_block_height": recent["lastValidBlockHeight"],
        }
        ident = hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest()
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute(
                "SELECT 1 FROM conversions WHERE status IN ('reserved','signed','submitted','uncertain')"
            ).fetchone():
                raise ValueError("An earlier conversion requires reconciliation")
            spent = db.execute(
                "SELECT coalesce(sum(input),0),coalesce(sum(fee),0) FROM conversions WHERE day=?",
                (day,),
            ).fetchone()
            if spent[0] + amount > daily or spent[1] + quoted_fee > gas:
                raise ValueError("Conversion input or gas daily ceiling reached")
            db.execute(
                "INSERT INTO conversions(id,status,terms,day,input,fee) VALUES(?,'reserved',?,?,?,?)",
                (ident, canonical(terms), day, amount, quoted_fee),
            )
            self.ledger.event(
                {"state": "conversion_reserved", "id": ident, "at": now, **terms}
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        simulation = self.fetch(
            "simulateTransaction",
            [
                base64.b64encode(bytes(tx)).decode(),
                {"encoding": "base64", "sigVerify": False, "commitment": "finalized"},
            ],
        )
        if simulation["value"].get("err") is not None:
            db.execute("UPDATE conversions SET status='failed' WHERE id=?", (ident,))
            self.ledger.record(
                {
                    "state": "conversion_failed_before_disclosure",
                    "id": ident,
                    "at": time.time(),
                }
            )
            return
        signed = signer.sign_transaction(tx)
        wire, signature = (
            base64.b64encode(bytes(signed)).decode(),
            str(signed.signatures[0]),
        )
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "UPDATE conversions SET status='signed',wire=?,signature=?,message_sha256=? WHERE id=?",
                (
                    wire,
                    signature,
                    hashlib.sha256(to_bytes_versioned(signed.message)).hexdigest(),
                    ident,
                ),
            )
            self.ledger.event(
                {
                    "state": "conversion_authorized",
                    "id": ident,
                    "transaction": signature,
                    "at": time.time(),
                    **terms,
                }
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        try:
            returned = self.fetch(
                "sendTransaction",
                [
                    wire,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "preflightCommitment": "finalized",
                        "maxRetries": 0,
                    },
                ],
            )
            if returned != signature:
                raise ValueError("Conversion RPC signature mismatch")
            db.execute("UPDATE conversions SET status='submitted' WHERE id=?", (ident,))
        except Exception:
            db.execute("UPDATE conversions SET status='uncertain' WHERE id=?", (ident,))
            self.ledger.record(
                {
                    "state": "conversion_uncertain",
                    "id": ident,
                    "transaction": signature,
                    "at": time.time(),
                }
            )

    def reconcile(self):
        db = self.ledger.db
        for ident, signature, raw, expected, reserved_fee in db.execute(
            "SELECT id,signature,terms,message_sha256,fee FROM conversions WHERE status IN ('signed','submitted','uncertain') AND signature IS NOT NULL"
        ).fetchall():
            tx = self.fetch(
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "base64",
                        "commitment": "finalized",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            )
            if not tx:
                continue
            wire = VersionedTransaction.from_bytes(
                base64.b64decode(tx["transaction"][0], validate=True)
            )
            if (
                str(wire.signatures[0]) != signature
                or hashlib.sha256(to_bytes_versioned(wire.message)).hexdigest()
                != expected
                or tx["meta"]["fee"] > reserved_fee
            ):
                raise ValueError("Conversion settlement differs from signed intent")
            terms, meta = json.loads(raw), tx["meta"]
            keys = [str(k) for k in wire.message.account_keys]
            loaded = meta.get("loadedAddresses", {})
            keys.extend([*loaded.get("writable", []), *loaded.get("readonly", [])])

            def units(rows):
                matches = [
                    r
                    for r in rows
                    if r.get("owner") == terms["wallet"]
                    and r.get("mint") == USDC
                    and keys[r["accountIndex"]] == associated(terms["wallet"])
                ]
                if not matches:
                    return 0
                if len(matches) != 1 or matches[0]["uiTokenAmount"]["decimals"] != 6:
                    raise ValueError("Conversion USDC evidence differs")
                return int(matches[0]["uiTokenAmount"]["amount"])

            output = units(meta.get("postTokenBalances", [])) - units(
                meta.get("preTokenBalances", [])
            )
            success = meta.get("err") is None
            native_indices = [
                keys.index(k)
                for k in (
                    terms["wallet"],
                    associated(terms["wallet"], WSOL),
                    associated(terms["wallet"]),
                )
            ]
            native_input = (
                sum(
                    meta["preBalances"][n] - meta["postBalances"][n]
                    for n in native_indices
                )
                - meta["fee"]
            )
            if success and native_input != terms["input_lamports"]:
                raise ValueError("Conversion native input differs from signed intent")
            if success and output < terms["minimum_micro_usdc"]:
                raise ValueError("Conversion output does not meet the signed floor")
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "UPDATE conversions SET status=? WHERE id=?",
                    ("finalized" if success else "failed", ident),
                )
                self.ledger.event(
                    {
                        "state": "conversion_finalized"
                        if success
                        else "conversion_failed_onchain",
                        "id": ident,
                        "transaction": signature,
                        "slot": tx["slot"],
                        "at": time.time(),
                        "finalized": True,
                        "actual_micro_usdc": output,
                        "actual_input_lamports": native_input if success else 0,
                        "network_fee_lamports": meta["fee"],
                        **terms,
                    }
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

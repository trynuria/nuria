"""Read-only, finalized Pump/PumpSwap ingestion through the server's Helius key.

Idle until pump-source.json exists. No explorer, signing or external messages.
"""

import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from life import utc

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
SOL = "So11111111111111111111111111111111111111112"


def b58encode(raw):
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = ALPHABET[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def b58decode(value):
    n = 0
    for c in value:
        n = n * 58 + ALPHABET.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(value) - len(value.lstrip("1"))) + raw


class Decoder:
    def __init__(self, root):
        self.schemas = {}
        self.swap_tags = {}
        self.hashes = {}
        for file, program, names in [
            ("pump-idl.json", PUMP, ["TradeEvent"]),
            ("pump_amm-idl.json", AMM, ["BuyEvent", "SellEvent"]),
        ]:
            raw = (Path(root) / file).read_bytes()
            self.hashes[program] = hashlib.sha256(raw).hexdigest()
            idl = json.loads(raw)
            self.swap_tags[program] = {
                bytes(i["discriminator"])
                for i in idl["instructions"]
                if i["name"].startswith(("buy", "sell"))
            }
            types = {
                t["name"]: t["type"]["fields"]
                for t in idl["types"]
                if t["type"]["kind"] == "struct"
            }
            for event in idl["events"]:
                if event["name"] in names:
                    self.schemas[(program, bytes(event["discriminator"]))] = (
                        event["name"],
                        types[event["name"]],
                        types,
                    )

    def decode(self, program, raw):
        schema = self.schemas.get((program, raw[:8]))
        if not schema:
            return None
        name, fields, types = schema
        offset = 8

        def read(kind):
            nonlocal offset
            if isinstance(kind, dict):
                if "vec" in kind:
                    length = read("u32")
                    if length > 1000:
                        raise ValueError("Oversized event vector")
                    return [read(kind["vec"]) for _ in range(length)]
                if "defined" in kind:
                    return {
                        f["name"]: read(f["type"])
                        for f in types[kind["defined"]["name"]]
                    }
                raise ValueError("Unsupported event type")
            if kind == "string":
                length = read("u32")
                if length > 4096 or offset + length > len(raw):
                    raise ValueError("Invalid event string")
                value = raw[offset : offset + length].decode()
                offset += length
                return value
            length = {
                "u8": 1,
                "u16": 2,
                "u32": 4,
                "u64": 8,
                "i64": 8,
                "i128": 16,
                "u128": 16,
                "bool": 1,
                "pubkey": 32,
            }[kind]
            if offset + length > len(raw):
                raise EOFError()
            chunk = raw[offset : offset + length]
            offset += length
            if kind == "pubkey":
                return b58encode(chunk)
            if kind == "bool":
                if chunk[0] not in (0, 1):
                    raise ValueError("Invalid bool")
                return bool(chunk[0])
            return int.from_bytes(chunk, "little", signed=kind.startswith("i"))

        values = {}
        for field in fields:
            try:
                values[field["name"]] = read(field["type"])
            except EOFError:
                # Historical programs lack appended fields. Required core fields
                # are separately checked below; no absent amount is assumed zero.
                break
        return name, values

    def events(self, tx):
        keys = tx["transaction"]["message"]["accountKeys"]
        keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
        loaded = tx.get("meta", {}).get("loadedAddresses", {})
        keys += loaded.get("writable", []) + loaded.get("readonly", [])
        cpi = []
        for group in tx.get("meta", {}).get("innerInstructions", []) or []:
            for instruction in group["instructions"]:
                program = (
                    instruction.get("programId") or keys[instruction["programIdIndex"]]
                )
                if program not in (PUMP, AMM) or "data" not in instruction:
                    continue
                raw = b58decode(instruction["data"])
                # Anchor emit_cpi: instruction tag followed by event discriminator.
                if len(raw) > 16 and (program, raw[8:16]) in self.schemas:
                    result = self.decode(program, raw[8:])
                    if result:
                        cpi.append((program, result))
        if cpi:
            return cpi
        found, stack = [], []
        for line in tx.get("meta", {}).get("logMessages", []) or []:
            invoke = re.match(r"Program (\w+) invoke \[\d+\]", line)
            if invoke:
                stack.append(invoke[1])
            elif re.match(r"Program \w+ (success|failed)", line):
                if stack:
                    stack.pop()
            elif (
                line.startswith("Program data: ") and stack and stack[-1] in (PUMP, AMM)
            ):
                result = self.decode(
                    stack[-1], base64.b64decode(line[14:], validate=True)
                )
                if result:
                    found.append((stack[-1], result))
        return found


class PumpFeed:
    def __init__(self, life, root):
        self.life, self.root = life, Path(root)
        self.decoder = Decoder(root)
        self.thread = threading.Thread(target=self.run, daemon=True, name="pump-reader")
        self.rpc_lock = threading.Lock()
        self.next_rpc = 0.0
        self.executor = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="helius-fetch"
        )

    def status(self, **values):
        with self.life.lock:
            self.life.feed_state.update(values)

    def rpc(self, method, params):
        key = os.environ.get("HELIUS_API_KEY")
        if not key:
            raise RuntimeError("Server Helius credential not available to this service")
        with self.rpc_lock:
            delay = self.next_rpc - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_rpc = time.monotonic() + 1 / 15
        # Never propagate urllib exceptions: their messages may include this URL.
        try:
            request = urllib.request.Request(
                os.environ.get("HELIUS_RPC_URL")
                or "https://mainnet.helius-rpc.com/?api-key=" + key,
                data=json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            data = json.loads(urllib.request.urlopen(request, timeout=15).read())
            if data.get("error"):
                raise ValueError()
            return data["result"]
        except Exception:
            raise RuntimeError(
                "Helius RPC request failed; coverage is unknown"
            ) from None

    def discover(self, mint):
        from solders.pubkey import Pubkey

        curve = str(
            Pubkey.find_program_address(
                [b"bonding-curve", bytes(Pubkey.from_string(mint))],
                Pubkey.from_string(PUMP),
            )[0]
        )
        pools = self.rpc(
            "getProgramAccounts",
            [
                AMM,
                {
                    "commitment": "finalized",
                    "encoding": "base64",
                    "filters": [{"memcmp": {"offset": 43, "bytes": mint}}],
                },
            ],
        )
        addresses = [curve]
        quotes = {}
        for pool in pools:
            raw = base64.b64decode(pool["account"]["data"][0])
            if pool["account"]["owner"] != AMM or raw[43:75] != bytes(
                Pubkey.from_string(mint)
            ):
                raise RuntimeError("Pool identity verification failed")
            quotes[pool["pubkey"]] = b58encode(raw[75:107])
            addresses.append(pool["pubkey"])
        self.pool_quotes = quotes
        self.status(
            addresses=addresses,
            pools=len(pools),
            coverage="Finalized Pump curve and discovered PumpSwap pools. Other venues are not covered.",
            decoder_idl_sha256=self.decoder.hashes,
        )
        return addresses

    def scan(self, address):
        with self.life.db() as db:
            row = db.execute(
                "SELECT signature FROM source_cursors WHERE address=?", (address,)
            ).fetchone()
            progress = db.execute(
                "SELECT newest,before_signature FROM scan_progress WHERE address=?",
                (address,),
            ).fetchone()
        old = row[0] if row else None
        newest, before = progress if progress and progress[1] else (None, None)
        collected, reached = [], False
        for page in range(10):
            params = {"commitment": "finalized", "limit": 100}
            if before:
                params["before"] = before
            rows = self.rpc("getSignaturesForAddress", [address, params])
            if not rows:
                reached = True
                break
            if newest is None:
                newest = rows[0]["signature"]
            for item in rows:
                if item["signature"] == old:
                    reached = True
                    break
                collected.append(item)
            if reached or len(rows) < 100:
                reached = True
                break
            before = rows[-1]["signature"]
        with self.life.db() as db:
            for row in reversed(collected):
                db.execute(
                    "INSERT OR IGNORE INTO transactions(signature,slot,status) VALUES(?,?,?)",
                    (
                        row["signature"],
                        row["slot"],
                        "failed" if row["err"] else "pending",
                    ),
                )
            if reached and newest:
                db.execute(
                    "INSERT OR REPLACE INTO source_cursors VALUES(?,?)",
                    (address, newest),
                )
                # Progress is replaced with a completed marker, not deleted.
                db.execute(
                    "INSERT OR REPLACE INTO scan_progress VALUES(?,?,?)",
                    (address, newest, ""),
                )
            elif newest and before:
                db.execute(
                    "INSERT OR REPLACE INTO scan_progress VALUES(?,?,?)",
                    (address, newest, before),
                )
        return reached

    def parse(self, signature, tx, mint):
        if tx is None:
            raise RuntimeError(
                "Finalized transaction temporarily unavailable; retry pending"
            )
        if tx["meta"]["err"]:
            return []
        events = []
        for index, (program, (name, values)) in enumerate(self.decoder.events(tx)):
            if program == PUMP:
                if values.get("mint") != mint:
                    continue
                required = (
                    "sol_amount",
                    "token_amount",
                    "is_buy",
                    "user",
                    "creator_fee",
                )
                if any(k not in values for k in required):
                    raise RuntimeError(
                        "Trade event schema incomplete; transaction remains pending"
                    )
                quote_mint = values.get("quote_mint", SOL)
                if quote_mint not in (SOL, "11111111111111111111111111111111"):
                    raise RuntimeError("Non-SOL quote requires a verified unit decoder")
                side = "buy" if values["is_buy"] else "sell"
                quote_raw, amount_raw = values["sol_amount"], values["token_amount"]
                fee_raw = values["creator_fee"]
            else:
                pool = values.get("pool")
                if pool not in self.pool_quotes:
                    continue
                if self.pool_quotes[pool] != SOL:
                    raise RuntimeError(
                        "Non-SOL PumpSwap quote requires a verified unit decoder"
                    )
                side = "buy" if name == "BuyEvent" else "sell"
                quote_key = "quote_amount_in" if side == "buy" else "quote_amount_out"
                amount_key = "base_amount_out" if side == "buy" else "base_amount_in"
                if any(
                    k not in values
                    for k in (quote_key, amount_key, "coin_creator_fee", "user")
                ):
                    raise RuntimeError(
                        "AMM event schema incomplete; transaction remains pending"
                    )
                quote_raw, amount_raw, fee_raw = (
                    values[quote_key],
                    values[amount_key],
                    values["coin_creator_fee"],
                )
            events.append(
                {
                    "id": signature + ":" + str(index),
                    "source": "solana_finalized",
                    "side": side,
                    "quote_amount": quote_raw / 1e9,
                    "quote_unit": "SOL",
                    "token_amount_raw": str(amount_raw),
                    "creator_fee": fee_raw / 1e9,
                    "signature": signature,
                    "slot": tx["slot"],
                    "mint": mint,
                    "program": program,
                    "user": values["user"],
                    "created_utc": utc(),
                    "finality": "finalized",
                    "creator_fee_wallet": values.get(
                        "creator", values.get("coin_creator")
                    ),
                    "evidence_sha256": hashlib.sha256(
                        json.dumps(tx, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    "fee_basis": "Program event accrual; not evidence of treasury receipt",
                }
            )
        if not events:
            keys = tx["transaction"]["message"]["accountKeys"]
            keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
            loaded = tx["meta"].get("loadedAddresses", {})
            keys += loaded.get("writable", []) + loaded.get("readonly", [])
            instructions = list(tx["transaction"]["message"]["instructions"])
            for group in tx["meta"].get("innerInstructions", []) or []:
                instructions += group["instructions"]
            relevant = mint in keys or any(p in keys for p in self.pool_quotes)
            for instruction in instructions:
                program = (
                    instruction.get("programId") or keys[instruction["programIdIndex"]]
                )
                if (
                    relevant
                    and program in self.decoder.swap_tags
                    and "data" in instruction
                    and b58decode(instruction["data"])[:8]
                    in self.decoder.swap_tags[program]
                ):
                    raise RuntimeError(
                        "Relevant swap has no decodable event; transaction remains pending"
                    )
        return events

    def run(self):
        addresses, discovered = [], 0
        self.pool_quotes = {}
        while not self.life.stop.is_set():
            config = self.root / "pump-source.json"
            if not config.exists():
                self.status(
                    phase="not_launched",
                    mint=None,
                    coverage="No token connected. Inputs are simulated.",
                )
                self.life.stop.wait(5)
                continue
            try:
                mint = json.loads(config.read_text())["mint"]
                if len(b58decode(mint)) != 32:
                    raise RuntimeError("Invalid Solana mint configuration")
                self.status(mint=mint, phase="catching_up", error=None)
                if time.monotonic() - discovered > 60:
                    addresses = self.discover(mint)
                    discovered = time.monotonic()
                complete = True
                for address in addresses:
                    complete = self.scan(address) and complete
                with self.life.db() as db:
                    pending = db.execute(
                        "SELECT signature FROM transactions WHERE status='pending' AND retry_after<=? ORDER BY slot,rowid LIMIT 60",
                        (time.time(),),
                    ).fetchall()

                def fetch(row):
                    try:
                        tx = self.rpc(
                            "getTransaction",
                            [
                                row[0],
                                {
                                    "commitment": "finalized",
                                    "encoding": "json",
                                    "maxSupportedTransactionVersion": 0,
                                },
                            ],
                        )
                        return row[0], self.parse(row[0], tx, mint), None
                    except Exception:
                        return row[0], None, "Transaction unresolved; retry pending"

                failures = 0
                for signature, events, error in self.executor.map(fetch, pending):
                    with self.life.db() as db:
                        if error:
                            failures += 1
                            db.execute(
                                "UPDATE transactions SET attempts=attempts+1,retry_after=?,detail=? WHERE signature=?",
                                (time.time() + 30, error, signature),
                            )
                            continue
                        for event in events:
                            db.execute(
                                "INSERT OR IGNORE INTO inputs VALUES(?,?,?,?,NULL)",
                                (
                                    event["id"],
                                    event["source"],
                                    json.dumps(
                                        event, sort_keys=True, separators=(",", ":")
                                    ),
                                    utc(),
                                ),
                            )
                        db.execute(
                            "UPDATE transactions SET status=?,detail=? WHERE signature=?",
                            (
                                "decoded" if events else "no_matching_trade",
                                str(len(events)),
                                signature,
                            ),
                        )
                with self.life.db() as db:
                    remaining = db.execute(
                        "SELECT count(*) FROM transactions WHERE status='pending'"
                    ).fetchone()[0]
                self.status(
                    phase="catching_up" if remaining or not complete else "connected",
                    last_checked_utc=utc(),
                    error="Some finalized transactions remain unresolved"
                    if failures
                    else ("Historical scan is continuing" if not complete else None),
                    unresolved_transactions=remaining,
                    rpc_max_per_second=15,
                    poll_seconds=1,
                    finality="finalized",
                )
            except Exception as exc:
                message = (
                    str(exc)
                    if isinstance(exc, RuntimeError)
                    else "Trade reader failed; coverage unknown"
                )
                self.status(phase="gap", error=message)
            self.life.stop.wait(1)

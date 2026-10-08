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
from token_profile import load as load_profile
from token_profile import public as public_profile

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
SOL = "So11111111111111111111111111111111111111112"
WBTC = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
QUOTE_SYMBOLS = {SOL: "SOL", WBTC: "WBTC", USDC: "USDC"}
TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}


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


def persist_event(db, mint, event):
    """Commit a unique input and its raw fee accrual in the caller's transaction."""
    if event["mint"] != mint:
        raise ValueError("Input mint differs from its scan scope")
    inserted = db.execute(
        "INSERT OR IGNORE INTO inputs VALUES(?,?,?,?,NULL)",
        (
            event["id"],
            event["source"],
            json.dumps(event, sort_keys=True, separators=(",", ":")),
            utc(),
        ),
    ).rowcount
    if not inserted:
        return False
    old = db.execute(
        "SELECT creator_fee_raw,trades FROM token_totals WHERE mint=? AND quote_mint=?",
        (mint, event["quote_mint"]),
    ).fetchone()
    if old:
        db.execute(
            "UPDATE token_totals SET creator_fee_raw=?,trades=? WHERE mint=? AND quote_mint=?",
            (
                str(int(old[0]) + int(event["creator_fee_raw"])),
                old[1] + 1,
                mint,
                event["quote_mint"],
            ),
        )
    else:
        db.execute(
            "INSERT INTO token_totals VALUES(?,?,?,?,?,?)",
            (
                mint,
                event["quote_mint"],
                event["quote_unit"],
                event["quote_decimals"],
                event["creator_fee_raw"],
                1,
            ),
        )
    return True


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
        self.quote_units = {
            SOL: {"decimals": 9, "symbol": "SOL"},
            "11111111111111111111111111111111": {"decimals": 9, "symbol": "SOL"},
        }
        self.profile = None
        self.current_mint = None
        self.curve_quote = None

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
        info = self.rpc(
            "getAccountInfo", [curve, {"commitment": "finalized", "encoding": "base64"}]
        )
        account = info.get("value")
        raw = base64.b64decode(account["data"][0]) if account else b""
        if (
            not account
            or account["owner"] != PUMP
            or len(raw) < 115
            or raw[:8] != hashlib.sha256(b"account:BondingCurve").digest()[:8]
        ):
            raise RuntimeError("Configured token has no verified current Pump curve")
        actual_creator = b58encode(raw[49:81])
        if self.profile and actual_creator != self.profile["creator_wallet"]:
            raise RuntimeError("Configured creator differs from the current curve")
        quote = b58encode(raw[83:115])
        quote = SOL if quote == "11111111111111111111111111111111" else quote
        self.curve_quote = quote
        self.verify_quote(quote)
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
            if (
                len(raw) < 107
                or raw[:8] != hashlib.sha256(b"account:Pool").digest()[:8]
                or pool["account"]["owner"] != AMM
                or raw[43:75] != bytes(Pubkey.from_string(mint))
            ):
                raise RuntimeError("Pool identity verification failed")
            quotes[pool["pubkey"]] = b58encode(raw[75:107])
            self.verify_quote(quotes[pool["pubkey"]])
            addresses.append(pool["pubkey"])
        self.pool_quotes = quotes
        self.status(
            addresses=addresses,
            pools=len(pools),
            coverage="Finalized Pump curve and discovered PumpSwap pools. Other venues are not covered.",
            decoder_idl_sha256=self.decoder.hashes,
            creator_wallet=actual_creator,
            quote_mint=quote,
            quote_unit=self.quote_units[quote]["symbol"],
            quote_decimals=self.quote_units[quote]["decimals"],
            identity_verified_slot=info["context"]["slot"],
        )
        return addresses

    def verify_quote(self, mint):
        if mint in self.quote_units:
            return
        result = self.rpc(
            "getAccountInfo",
            [mint, {"encoding": "jsonParsed", "commitment": "finalized"}],
        )
        account = result.get("value") or {}
        info = account.get("data", {}).get("parsed", {}).get("info", {})
        decimals = info.get("decimals")
        if (
            account.get("owner") not in TOKEN_PROGRAMS
            or info.get("isInitialized") is not True
            or type(decimals) is not int
            or not 0 <= decimals <= 18
        ):
            raise RuntimeError("Quote mint units could not be verified")
        self.quote_units[mint] = {
            "decimals": decimals,
            "symbol": QUOTE_SYMBOLS.get(mint, mint[:8] + "…"),
        }

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
                    "INSERT OR IGNORE INTO token_transactions(mint,signature,slot,sequence,status) VALUES(?,?,?,?,?)",
                    (
                        self.current_mint,
                        row["signature"],
                        row["slot"],
                        db.execute(
                            "SELECT coalesce(max(sequence),0)+1 FROM token_transactions"
                        ).fetchone()[0],
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
                if (
                    self.curve_quote
                    and self.curve_quote != SOL
                    and quote_mint != self.curve_quote
                ):
                    raise RuntimeError("Trade quote differs from its verified curve")
                if quote_mint not in self.quote_units:
                    raise RuntimeError("Non-SOL quote requires a verified unit decoder")
                side = "buy" if values["is_buy"] else "sell"
                quote_raw, amount_raw = values["sol_amount"], values["token_amount"]
                if quote_mint not in (SOL, "11111111111111111111111111111111"):
                    if "quote_amount" not in values:
                        raise RuntimeError("Non-SOL quote amount is missing")
                    quote_raw = values["quote_amount"]
                fee_raw = values["creator_fee"]
            else:
                pool = values.get("pool")
                if pool not in self.pool_quotes:
                    continue
                quote_mint = self.pool_quotes[pool]
                if quote_mint not in self.quote_units:
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
                    "source": public_profile(self.profile)["source"]
                    if self.profile
                    else "solana_finalized",
                    "side": side,
                    "quote_amount": quote_raw
                    / 10 ** self.quote_units[quote_mint]["decimals"],
                    "quote_amount_raw": str(quote_raw),
                    "quote_mint": quote_mint,
                    "quote_decimals": self.quote_units[quote_mint]["decimals"],
                    "quote_unit": self.quote_units[quote_mint]["symbol"],
                    "token_amount_raw": str(amount_raw),
                    "creator_fee": fee_raw
                    / 10 ** self.quote_units[quote_mint]["decimals"],
                    "creator_fee_raw": str(fee_raw),
                    "configuration_sha256": public_profile(self.profile)[
                        "configuration_sha256"
                    ]
                    if self.profile
                    else None,
                    "token_mode": self.profile["mode"]
                    if self.profile
                    else "production",
                    "signature": signature,
                    "slot": tx["slot"],
                    "block_time": tx.get("blockTime"),
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
            config = Path(
                os.environ.get("NURIA_TOKEN_CONFIG", self.root / "pump-source.json")
            )
            if not config.exists():
                self.status(
                    phase="not_launched",
                    mint=None,
                    coverage="No token connected. Inputs are simulated.",
                )
                self.life.stop.wait(5)
                continue
            try:
                self.profile = (
                    load_profile(config)
                    if os.environ.get("NURIA_TOKEN_CONFIG")
                    else None
                )
                mint = (
                    self.profile["mint"]
                    if self.profile
                    else json.loads(config.read_text())["mint"]
                )
                if len(b58decode(mint)) != 32:
                    raise RuntimeError("Invalid Solana mint configuration")
                self.status(mint=mint, phase="catching_up", error=None)
                if self.profile:
                    self.status(
                        token=public_profile(self.profile), mode=self.profile["mode"]
                    )
                if mint != self.current_mint:
                    self.current_mint, addresses, discovered = mint, [], 0
                    self.pool_quotes = {}
                    self.status(
                        addresses=[],
                        pools=0,
                        creator_wallet=None,
                        quote_mint=None,
                        quote_unit=None,
                        quote_decimals=None,
                        identity_verified_slot=None,
                        unresolved_transactions=None,
                    )
                if time.monotonic() - discovered > 60:
                    addresses = self.discover(mint)
                    discovered = time.monotonic()
                complete = True
                for address in addresses:
                    complete = self.scan(address) and complete
                with self.life.db() as db:
                    pending = db.execute(
                        "SELECT signature FROM token_transactions WHERE mint=? AND status='pending' AND retry_after<=? ORDER BY slot,sequence LIMIT 60",
                        (mint, time.time()),
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
                                    "maxSupportedTransactionVersion": 1,
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
                                "UPDATE token_transactions SET attempts=attempts+1,retry_after=?,detail=? WHERE mint=? AND signature=?",
                                (time.time() + 30, error, mint, signature),
                            )
                            continue
                        for event in events:
                            persist_event(db, mint, event)
                        db.execute(
                            "UPDATE token_transactions SET status=?,detail=? WHERE mint=? AND signature=?",
                            (
                                "decoded" if events else "no_matching_trade",
                                str(len(events)),
                                mint,
                                signature,
                            ),
                        )
                with self.life.db() as db:
                    remaining = db.execute(
                        "SELECT count(*) FROM token_transactions WHERE mint=? AND status='pending'",
                        (mint,),
                    ).fetchone()[0]
                    totals = db.execute(
                        "SELECT quote_mint,quote_unit,decimals,creator_fee_raw,trades FROM token_totals WHERE mint=?",
                        (mint,),
                    ).fetchall()
                self.status(
                    phase="catching_up" if remaining or not complete else "connected",
                    last_checked_utc=utc(),
                    error="Some finalized transactions remain unresolved"
                    if failures
                    else ("Historical scan is continuing" if not complete else None),
                    unresolved_transactions=remaining,
                    recorded_accrual=[
                        {
                            "mint": quote,
                            "unit": unit,
                            "decimals": decimals,
                            "amount_raw": amount,
                            "amount": int(amount) / 10**decimals,
                            "trades": trades,
                        }
                        for quote, unit, decimals, amount, trades in totals
                    ],
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

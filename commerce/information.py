"""A fixed free alternative to buying basic SOL/USD market context.

This is resource selection, not a neural improvement result. No free response is
credited as a paid forecast, and unavailable free data never authorizes spending.
"""

import hashlib
import json
import math

from commerce.ledger import canonical
from commerce.transport import request

ENDPOINT = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd&include_last_updated_at=true"
MAX_AGE = 120
MIN_INTERVAL = 60


def context(raw, now):
    body = json.loads(raw)
    value = body.get("solana", {})
    price, observed = value.get("usd"), value.get("last_updated_at")
    if (
        type(price) not in (float, int)
        or not math.isfinite(price)
        or price <= 0
        or type(observed) is not int
        or not 0 <= now - observed <= MAX_AGE
    ):
        raise ValueError("Free context is invalid, future-dated or stale")
    return {
        "schema": "coingecko.sol-price.v1",
        "asset": "SOL",
        "usd": price,
        "observed_at": observed,
        "received_at": now,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "source": ENDPOINT,
    }


class InformationChoice:
    def __init__(self, ledger, http=request):
        self.ledger, self.http = ledger, http
        ledger.db.execute(
            "CREATE TABLE IF NOT EXISTS free_context(id TEXT PRIMARY KEY, checked REAL NOT NULL, payload TEXT)"
        )
        ledger.db.execute(
            "CREATE TABLE IF NOT EXISTS information_choices(id TEXT PRIMARY KEY, terms TEXT NOT NULL)"
        )

    def consider(self, selected, now):
        provider = selected["provider"]
        if provider.schema != "coingecko.sol-price.v1":
            return None  # No free equivalent is asserted for the forecast contract.
        ident = hashlib.sha256(
            (selected["decision_hash"] + ":SOL/USD").encode()
        ).hexdigest()
        previous = self.ledger.db.execute(
            "SELECT terms FROM information_choices WHERE id=?", (ident,)
        ).fetchone()
        if previous:
            return json.loads(previous[0])
        row = self.ledger.db.execute(
            "SELECT checked,payload FROM free_context WHERE id='SOL/USD'"
        ).fetchone()
        data = json.loads(row[1]) if row and row[1] else None
        if not row or now - row[0] >= MIN_INTERVAL:
            data = None
            try:
                status, _, raw = self.http(ENDPOINT)
                if status == 200:
                    data = context(raw, now)
            except Exception:
                pass  # Public failure record, without transport secrets or invented data.
            self.ledger.db.execute(
                "INSERT INTO free_context VALUES('SOL/USD',?,?) ON CONFLICT(id) DO UPDATE SET checked=excluded.checked,payload=excluded.payload",
                (now, canonical(data) if data else None),
            )
        if data and not (
            0 <= now - data["observed_at"] <= MAX_AGE
            and 0 <= now - data["received_at"] <= MAX_AGE
        ):
            data = None
        choice = {
            "id": ident,
            "state": "information_free_selected"
            if data
            else "information_purchase_deferred",
            "decision_hash": selected["decision_hash"],
            "provider": provider.id,
            "resource": "SOL/USD",
            "at": now,
            "selection": "free" if data else "defer",
            "declined_price_ceiling_micro_usdc": provider.maximum_micro_usdc,
            "expense_micro_usdc": 0,
            "context": data,
            "reason": "Fresh basic price context is available without payment"
            if data
            else "No fresh free context; incremental benefit of a paid basic price is unestablished",
            "scope": "Public API context, not a chain observation, signed payment, paid delivery or measured neural-learning benefit. No forecast-provider equivalence is inferred.",
        }
        db = self.ledger.db
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "INSERT INTO information_choices VALUES(?,?)",
                (ident, canonical(choice)),
            )
            self.ledger.event(choice)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        return choice

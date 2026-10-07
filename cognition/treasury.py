"""Fee observations and fail-closed boundaries for future delegated payments."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SpendingPolicy:
    per_action_lamports: int = 0
    per_day_lamports: int = 0
    reserve_lamports: int = 0
    recipients: tuple[str, ...] = ()
    enabled: bool = False

    def validate(
        self,
        intent: dict,
        treasury: dict,
        daily_reserved: int = 0,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        if any(
            type(value) is not int or value < 0
            for value in (
                self.per_action_lamports,
                self.per_day_lamports,
                self.reserve_lamports,
                daily_reserved,
            )
        ):
            raise ValueError("Spending policy limits are invalid")
        if not self.enabled:
            raise ValueError("Delegated spending is not connected")
        amount = intent.get("lamports")
        if type(amount) is not int or amount <= 0:
            raise ValueError("Spend amount must be positive integer lamports")
        if intent.get("asset") != "SOL":
            raise ValueError("Unsupported payment asset")
        if intent.get("recipient") not in self.recipients:
            raise ValueError("Payment recipient is not allowed")
        expires = intent.get("expires_at")
        if (
            type(expires) not in (int, float)
            or not math.isfinite(expires)
            or expires <= now
            or expires > now + 120
        ):
            raise ValueError("Payment intent expiry is invalid")
        balance = treasury.get("balance_lamports")
        checked = treasury.get("checked_at")
        if (
            type(balance) is not int
            or type(checked) not in (int, float)
            or not math.isfinite(checked)
            or now - checked > 60
            or checked > now + 5
        ):
            raise ValueError("Treasury balance evidence is missing or stale")
        fee = intent.get("maximum_fee_lamports", 0)
        if type(fee) is not int or fee < 0 or fee > 100_000:
            raise ValueError("Transaction fee cap is invalid")
        total = amount + fee
        if (
            total > self.per_action_lamports
            or daily_reserved + total > self.per_day_lamports
        ):
            raise ValueError("Payment exceeds published limits")
        if balance - total < self.reserve_lamports:
            raise ValueError("Payment would cross the reserve floor")
        if not isinstance(intent.get("job_id"), str) or not intent["job_id"]:
            raise ValueError("Payment requires a recorded job")

    def public(self) -> dict:
        return {
            "enabled": self.enabled,
            "per_action_lamports": self.per_action_lamports,
            "per_day_lamports": self.per_day_lamports,
            "reserve_lamports": self.reserve_lamports,
            "recipients": list(self.recipients),
            "execution": "No per-action human approval inside a configured policy; an isolated signer must enforce the policy independently",
        }


def intent_hash(intent: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            intent, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def public_treasury(
    observation: dict | None = None, policy: SpendingPolicy | None = None
) -> dict:
    observation = observation or {}
    balance = observation.get("balance_lamports")
    checked = observation.get("checked_at")
    fresh = (
        type(checked) in (int, float)
        and math.isfinite(checked)
        and 0 <= time.time() - checked < 60
    )
    return {
        "wallet": observation.get("wallet"),
        "mint": observation.get("mint"),
        "balance_lamports": balance if fresh and type(balance) is int else None,
        "balance_sol": balance / 1e9 if fresh and type(balance) is int else None,
        "checked_at": checked,
        "slot": observation.get("slot"),
        "phase": (
            "awaiting_configuration"
            if not observation.get("wallet")
            else observation.get("phase", "unknown")
            if fresh
            else "unknown"
        ),
        "creator_fee_accrual": observation.get("creator_fee_accrual"),
        "verified_fee_receipts": observation.get("verified_fee_receipts"),
        "spent_lamports": observation.get("spent_lamports", 0),
        "policy": (policy or SpendingPolicy()).public(),
        "rails": [
            {
                "id": "included_compute",
                "phase": "available",
                "description": "Existing server capacity for local experiments; incremental SOL spending is zero",
            },
            {
                "id": "solana_payment",
                "phase": "prepared",
                "description": "Requires funded dedicated wallet, published limits, allowed recipients and isolated signing",
            },
        ],
        "scope": "Trade accrual, wallet balance, verified creator-fee receipts and executed payments are separate evidence",
    }

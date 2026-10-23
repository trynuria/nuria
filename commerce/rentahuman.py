"""RentAHuman REST adapter with explicitly reviewed account response bindings.

No account creation, card checkout, wallet deposit or automatic top-up is exposed.
Private enrollment maps authenticated response fields after a no-charge rehearsal.
"""

import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote as escape

from commerce.tasks import contractor_brief, digest
from commerce.transport import request

ORIGIN = "https://rentahuman.ai"
SAFE_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")


def field(value, path):
    if not isinstance(path, list) or not 1 <= len(path) <= 8:
        raise ValueError("Reviewed response field mapping is missing")
    for part in path:
        if (
            not isinstance(value, dict)
            or not isinstance(part, str)
            or part not in value
        ):
            raise ValueError("Provider response differs from reviewed account contract")
        value = value[part]
    return value


def cents(value):
    if type(value) is not int or value < 0:
        raise ValueError("Provider amount must be integer USD cents")
    return value * 10000


class RentAHuman:
    def __init__(self, enrollment, api_key, http=request):
        self.profile, self.api_key, self.http = enrollment, api_key, http
        if (
            enrollment.get("reviewed") is not True
            or enrollment.get("gross_debit_cap_verified") is not True
            or not enrollment.get("account")
            or digest(enrollment.get("fields", {})) != enrollment.get("fields_sha256")
        ):
            raise ValueError(
                "Authenticated RentAHuman response contract is not reviewed"
            )
        if not api_key or any(c in api_key for c in "\r\n"):
            raise ValueError("Project marketplace authentication is missing")

    def call(self, method, path, body=None, operation_id=None):
        if not path.startswith("/api/") or any(x in path for x in ("..", "@", "#")):
            raise ValueError("Unsupported marketplace path")
        headers = {"X-API-Key": self.api_key}
        if operation_id:
            headers["Idempotency-Key"] = operation_id
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, _, raw = self.http(
            ORIGIN + path,
            headers,
            method=method,
            body=None
            if body is None
            else json.dumps(body, allow_nan=False, separators=(",", ":")).encode(),
        )
        if status != 200 and status != 201:
            raise ValueError("Marketplace request did not establish success")
        value = json.loads(raw)
        if value.get("success") is not True:
            raise ValueError("Marketplace response did not establish success")
        return value

    def get(self, response, key):
        return field(response, self.profile["fields"].get(key))

    def balance(self, now):
        report = self.call("GET", "/api/wallet/report")
        if self.get(report, "account") != self.profile["account"]:
            raise ValueError("Marketplace account identity differs")
        if self.get(report, "currency") != "usd":
            raise ValueError("Marketplace funding currency is unsupported")
        controls = self.call("GET", "/api/wallet/controls")
        if self.get(controls, "auto_topup") is not False:
            raise ValueError("Marketplace automatic top-up must be disabled")
        balance = self.call("GET", "/api/wallet/balance")
        return {
            "account": "rentahuman:" + self.profile["account"],
            "available_micro_usd": cents(self.get(balance, "balance_cents")),
            "checked_at": now,
            "verified": True,
        }

    def body(self, row, now):
        task = row["contract"]
        price = self.profile.get("worker_price_cents")
        if type(price) is not int or price <= 0:
            raise ValueError("An exact worker price is required")
        return {
            "title": task["title"],
            "description": json.dumps(
                contractor_brief(task, row["id"]), ensure_ascii=False
            ),
            "completionCriteria": "Return the committed nuria.task-result.v1 JSON with the task identity and result.",
            "evidenceTypes": ["text"],
            "price": float(Decimal(price) / 100),
            "priceType": "fixed",
            "estimatedHours": self.profile.get("estimated_hours", 1),
            "location": {"isRemoteAllowed": True},
            "deadline": datetime.fromtimestamp(
                now + task["delivery_seconds"], timezone.utc
            ).isoformat(),
            "spotsAvailable": 1,
            "autoAccept": False,
            "dryRun": True,
        }

    def quote(self, row, now):
        body = self.body(row, row["created"])
        response = self.call("POST", "/api/bounties", body)
        total = self.get(response, "preview_total_cents")
        limits = self.call("GET", "/api/wallet/controls")
        cap = cents(self.get(limits, "per_bounty_cap_cents"))
        if (
            self.get(limits, "auto_topup") is not False
            or not cents(total) <= cap <= row["contract"]["maximum_micro_usd"]
        ):
            raise ValueError(
                "Marketplace gross-debit cap is missing or exceeds the task ceiling"
            )
        return {
            "provider": row["provider"],
            "account": "rentahuman:" + self.profile["account"],
            "offer_id": digest(body),
            "total_micro_usd": cap,
            "expires_at": now + 60,
            "checked_at": now,
            "destination": {
                "basis": "platform_escrow",
                "platform": "rentahuman",
                "worker": None,
                "wallet": None,
            },
            "evidence_sha256": digest(response),
            "payment_basis": "platform_wallet",
        }

    @staticmethod
    def ident(value):
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise ValueError("Marketplace resource identity is invalid")
        return escape(value, safe="")

    def dispatch(self, row, now, operation_id):
        self.balance(now)
        body = self.body(row, row["created"])
        # The date and all other public terms must match the original preview.
        if digest(body) != row["quote"]["offer_id"]:
            raise ValueError("Bounty differs from the previewed contract")
        body.update(dryRun=False, idempotencyKey=operation_id)
        response = self.call("POST", "/api/bounties", body, operation_id)
        bounty = self.ident(self.get(response, "bounty_id"))
        return {"job_id": row["id"], "external_id": bounty, "bounty_id": bounty}

    def fund(self, row, operation_id):
        self.balance(time.time())
        bounty = self.ident(row["remote"]["bounty_id"])
        limits = self.call("GET", "/api/wallet/controls")
        if (
            self.get(limits, "auto_topup") is not False
            or cents(self.get(limits, "per_bounty_cap_cents")) != row["amount"]
        ):
            raise ValueError("Marketplace gross-debit controls changed")
        preview = self.call("GET", "/api/bounties/" + bounty + "/funding-quote")
        if (
            cents(self.get(preview, "funding_total_cents")) > row["amount"]
            or cents(self.get(preview, "funding_wallet_cents")) < row["amount"]
        ):
            raise ValueError("Funding total changed or requires a checkout")
        response = self.call(
            "POST", "/api/bounties/deposit", {"bountyId": bounty}, operation_id
        )
        if self.get(response, "funding_source") != "wallet":
            raise ValueError("Wallet-funded bounty was not established")
        return {**row["remote"], "job_id": row["id"]}

    def applications(self, row):
        response = self.call(
            "GET",
            "/api/bounties/" + self.ident(row["remote"]["bounty_id"]) + "/applications",
        )
        items = self.get(response, "applications")
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("Applicant coverage is unknown or oversized")
        return items

    def hire(self, row, application_id, operation_id):
        self.balance(time.time())
        bounty, application = (
            self.ident(row["remote"]["bounty_id"]),
            self.ident(application_id),
        )
        self.call(
            "PATCH",
            "/api/bounties/" + bounty + "/applications/" + application,
            {"action": "accept", "idempotencyKey": operation_id},
            operation_id,
        )
        return {**row["remote"], "job_id": row["id"], "application_id": application}

    def release(self, row, escrow_id, operation_id):
        self.balance(time.time())
        escrow = self.ident(escrow_id)
        self.call(
            "POST",
            "/api/escrow/" + escrow + "/release",
            {
                "applicationId": row["remote"]["application_id"],
                "acknowledgeRelease": True,
            },
            operation_id,
        )
        return {**row["remote"], "job_id": row["id"], "escrow_id": escrow}

    def payout(self, row, now):
        remote = row["remote"]
        escrow = self.call("GET", "/api/escrow/" + self.ident(remote["escrow_id"]))
        if (
            self.get(escrow, "escrow_bounty_id") != remote["bounty_id"]
            or self.get(escrow, "escrow_application_id") != remote["application_id"]
            or self.get(escrow, "worker_payout_status") != "paid"
        ):
            return None
        return {
            "job_id": row["id"],
            "account": row["account"],
            "external_id": remote["external_id"],
            "verified": True,
            "basis": "provider_confirmed_payout",
            "total_micro_usd": cents(self.get(escrow, "total_charged_cents")),
            "checked_at": now,
            "evidence_sha256": digest(escrow),
            "worker_id": self.get(escrow, "worker_id"),
            "recipient_wallet": None,
            "scope": "Authenticated platform payout status; not an independently verified worker bank or onchain transfer",
        }

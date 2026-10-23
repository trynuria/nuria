"""Connect neural task intents to enrolled providers and verified outcomes."""

import base64
import json
import os
import time
from pathlib import Path

from commerce.action_loop import ActionLoop
from commerce.ledger import canonical
from commerce.rentahuman import RentAHuman, field
from commerce.solana import usdc_balance
from commerce.tasks import contractor_brief, digest
from commerce.x402 import delivery as parse_delivery
from commerce.x402 import quote


class X402Tasks:
    def __init__(self, executor, provider, policy, signer, rpc_url):
        self.executor, self.provider, self.policy = executor, provider, policy
        self.signer, self.rpc_url = signer, rpc_url
        provider.validate()
        if provider.schema != "nuria.task-result.v1":
            raise ValueError("Provider is not an enrolled task service")

    def headers(self, row):
        raw = canonical(contractor_brief(row["contract"], row["id"])).encode()
        encoded = base64.b64encode(raw).decode()
        if len(encoded) > 12000:
            raise ValueError("Task brief exceeds its request bound")
        return {"X-Nuria-Task": encoded}

    def quote(self, row, now):
        status, headers, _ = self.executor.http(
            self.provider.endpoint, self.headers(row)
        )
        if status != 402:
            raise ValueError("Task provider did not offer an exact invoice")
        accepted = quote(headers.get("payment-required"), self.provider)
        return {
            "provider": row["provider"],
            "account": self.policy.spending_wallet,
            "offer_id": digest(accepted),
            "total_micro_usd": int(accepted["amount"]),
            "expires_at": now + min(60, accepted["maxTimeoutSeconds"]),
            "checked_at": now,
            "destination": {
                "basis": "solana_usdc",
                "wallet": accepted["payTo"],
                "fee_payer": accepted["extra"]["feePayer"],
            },
            "evidence_sha256": digest(accepted),
            "payment_basis": "exact_usdc",
        }

    def balance(self, now):
        balance = usdc_balance(
            self.policy.spending_wallet,
            self.executor.fetch,
            self.executor.ledger.minimum_balance_slot(),
        )
        return {
            "account": self.policy.spending_wallet,
            "available_micro_usd": balance["micro_usdc"],
            "checked_at": balance["checked_at"],
            "verified": True,
        }

    def dispatch(self, row, now, operation_id):
        # Existing exact-payment execution owns signing, pre-disclosure storage,
        # simulation and finalized-history recovery. It never executes artifacts.
        if self.signer is None:
            raise ValueError("Task signing is not configured")
        selected = {
            "provider": self.provider,
            "decision_hash": row["intent_id"],
            "action": row["contract"]["action"],
            "cursor": row["intent"]["source_cursor"],
            "baseline_p_buy": 0.5,
            "expected_value": row["intent"]["expected_utility"],
            "task": row["contract"],
            "action_task_id": row["id"],
            "invoice_sha256": row["quote"]["offer_id"],
        }
        ident = self.executor.purchase(
            self.policy, selected, self.signer, self.rpc_url, now
        )
        return {"job_id": row["id"], "external_id": ident}

    def observe(self, row, now):
        found = self.executor.ledger.db.execute(
            "SELECT id,status,delivery,settlement FROM jobs WHERE json_extract(terms,'$.action_task_id')=?",
            (row["id"],),
        ).fetchall()
        if len(found) != 1:
            return {}
        ident, state, artifact, raw_settlement = found[0]
        if not raw_settlement:
            return {}  # Unknown, expired or failed authorizations never become paid.
        paid = json.loads(raw_settlement)
        checks = json.loads(artifact) if artifact else None
        if checks and checks["schema"] == "nuria.task-ack.v1":
            if now <= checks["expires_at"]:
                status, _, raw = self.executor.http(
                    self.provider.endpoint.rstrip("/") + "/results/" + row["id"],
                    {"Authorization": "Bearer " + checks["result_token"]},
                )
                if status == 200:
                    parsed = parse_delivery(
                        raw,
                        self.policy.mint,
                        now,
                        "nuria.task-result.v1",
                        task=row["contract"],
                        task_id=row["id"],
                    )
                    if parsed["schema"] != "nuria.task-result.v1":
                        raise ValueError(
                            "A result fetch cannot replace the pending task"
                        )
                    self.executor.ledger.db.execute(
                        "UPDATE jobs SET delivery=? WHERE id=?",
                        (canonical(parsed), ident),
                    )
                    self.executor.ledger.transition(
                        ident,
                        "delivered",
                        {"schema": parsed["schema"], "result_sha256": parsed["sha256"]},
                        now,
                    )
                    checks = parsed
                elif status != 202:
                    raise ValueError("Task result is not available")
            if checks["schema"] == "nuria.task-ack.v1":
                checks = None
        return {
            "acknowledgment": {"job_id": row["id"], "external_id": ident},
            "delivery": checks["raw"].encode() if checks else None,
            "settlement": {
                "job_id": row["id"],
                "account": row["account"],
                "external_id": ident,
                "verified": True,
                "basis": "finalized_chain_transfer",
                "total_micro_usd": row["amount"],
                "checked_at": now,
                "evidence_sha256": digest(paid),
                "transaction": paid.get("transaction"),
                "slot": paid.get("slot"),
                "payer": self.policy.spending_wallet,
                "recipient_wallet": self.provider.recipient,
                "scope": "Finalized exact Solana USDC transfer; USDC amounts use nominal USD accounting",
            },
        }


class HumanTasks(RentAHuman):
    def tool(self, name, arguments):
        if name not in ("get_bounty_submissions", "get_submission_context"):
            raise ValueError("Unsupported marketplace read tool")
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        status, _, raw = self.http(
            "https://rentahuman.ai/api/mcp",
            {"X-API-Key": self.api_key, "Content-Type": "application/json"},
            method="POST",
            body=canonical(body).encode(),
        )
        envelope = json.loads(raw)
        if status != 200 or envelope.get("error"):
            raise ValueError("Marketplace read-tool contract is unavailable")
        result = envelope["result"]
        if result.get("isError"):
            raise ValueError("Marketplace tool did not establish success")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        parts = result.get("content", [])
        if len(parts) != 1 or parts[0].get("type") != "text":
            raise ValueError("Marketplace read-tool response is not structured")
        return json.loads(parts[0]["text"])

    def observe(self, row, now):
        remote = row["remote"]
        if not remote:
            return {}
        pending = row.get("pending_operation") or {}
        if pending.get("name") == "release" and pending.get("request"):
            row = {
                **row,
                "remote": {**remote, "escrow_id": pending["request"]["escrow_id"]},
            }
            paid = self.payout(row, now)
            return (
                {
                    "operation": "release",
                    "acknowledgment": {**row["remote"], "job_id": row["id"]},
                    "settlement": paid,
                }
                if paid
                else {}
            )
        if remote.get("escrow_id"):
            return {"settlement": self.payout(row, now)}
        bounty = self.call("GET", "/api/bounties/" + self.ident(remote["bounty_id"]))
        if pending.get("name") == "fund":
            report = self.call("GET", "/api/wallet/report")
            if self.get(report, "account") != self.profile["account"]:
                raise ValueError("Marketplace account identity differs")
            funded = [
                item
                for item in report.get("report", {}).get("byBounty", [])
                if item.get("bountyId") == remote["bounty_id"]
            ]
            if (
                len(funded) == 1
                and type(funded[0].get("funded")) is int
                and 0 < funded[0]["funded"] * 10000 <= row["amount"]
            ):
                return {
                    "operation": "fund",
                    "acknowledgment": {**remote, "job_id": row["id"]},
                }
            return {}
        if self.get(bounty, "bounty_status") == "pending_deposit":
            return {"operation": "fund", "request": {"bounty_id": remote["bounty_id"]}}
        if not remote.get("application_id"):
            candidates = []
            for item in self.applications(row):
                if (
                    pending.get("name") == "hire"
                    and pending.get("request", {}).get("application_id")
                    == item.get("id")
                    and item.get("status") == "accepted"
                ):
                    return {
                        "operation": "hire",
                        "acknowledgment": {
                            **remote,
                            "job_id": row["id"],
                            "application_id": item["id"],
                        },
                    }
                review = item.get("reviewSummary") or {}
                rating, completed = (
                    review.get("averageRating"),
                    item.get("completedTasksCount"),
                )
                if (
                    item.get("status") == "pending"
                    and item.get("isBlockedByOwner") is False
                    and type(rating) in (int, float)
                    and 0 <= rating <= 5
                    and type(completed) is int
                    and completed >= self.profile.get("minimum_completed_tasks", 1)
                    and rating >= self.profile.get("minimum_rating", 4)
                ):
                    candidates.append((rating, completed, self.ident(item["id"])))
            if candidates:
                return {
                    "operation": "hire",
                    "request": {"application_id": max(candidates)[2]},
                }
            return {}
        result = self.tool("get_bounty_submissions", {"bountyId": remote["bounty_id"]})
        submissions = self.get(result, "submissions")
        if not isinstance(submissions, list) or len(submissions) > 100:
            raise ValueError("Marketplace submission coverage is unknown")
        matches = [
            item
            for item in submissions
            if field(item, self.profile["fields"]["submission_application_id"])
            == remote["application_id"]
            and field(item, self.profile["fields"]["submission_status"]) == "pending"
        ]
        if len(matches) != 1:
            return {}
        item = matches[0]
        artifact = field(item, self.profile["fields"]["submission_text"])
        escrow = field(item, self.profile["fields"]["submission_escrow_id"])
        if not isinstance(artifact, str) or len(artifact.encode()) > 262144:
            raise ValueError("Marketplace task artifact is malformed")
        return {"delivery": artifact.encode(), "escrow_id": self.ident(escrow)}


def run_actions(
    ledger,
    executor,
    policy,
    signer,
    rpc_url,
    catalog,
    controls,
    cognition,
    decisions,
    now=None,
):
    now = time.time() if now is None else now
    loop = ActionLoop(ledger)
    tasks = catalog.get("tasks", [])
    if not isinstance(tasks, list) or len(tasks) > 16:
        raise ValueError("Task catalog is invalid")
    providers, readiness = {}, []
    for config in controls.get("adapters", []):
        try:
            if config["type"] == "x402":
                provider = next(
                    item for item in policy.providers if item.id == config["id"]
                )
                providers[config["id"]] = X402Tasks(
                    executor, provider, policy, signer, rpc_url
                )
            elif config["type"] == "rentahuman":
                key = os.environ.get(
                    config.get("api_key_env", "NURIA_RENTAHUMAN_API_KEY")
                )
                providers[config["id"]] = HumanTasks(config["enrollment"], key)
            else:
                raise ValueError("Provider capability is not implemented")
            readiness.append(
                {
                    "id": config["id"],
                    "phase": "configured_unverified",
                    "funded_delivery_test": config.get("funded_delivery_test") is True,
                }
            )
        except (KeyError, ValueError, StopIteration):
            readiness.append({"id": config.get("id"), "phase": "activation_required"})
    if catalog.get("planning_enabled") is True and cognition.get("phase") == "running":
        for decision in decisions[:1]:
            if decision.get("tick", 1) > cognition.get("committed_tick", 0):
                continue
            intent = decision.get("intent") or {}
            for task in tasks:
                if task["id"] == intent.get("goal_id"):
                    loop.propose(task, decision, now)
    errors = []
    for (ident,) in ledger.db.execute(
        "SELECT id FROM action_tasks WHERE state NOT IN ('evaluated','cancelled') ORDER BY created LIMIT 64"
    ).fetchall():
        try:
            row = loop.row(ident)
            if (
                row["state"] == "proposed"
                and now > row["created"] + row["contract"]["delivery_seconds"]
                or row["state"] == "reserved"
                and row["quote"]["expires_at"] <= now
            ):
                loop.cancel_unexecuted(ident, now)
                continue
            adapter = providers.get(row["provider"])
            if not adapter:
                continue
            if row["state"] == "proposed" and controls.get("enabled") is True:
                if row["contract"].get("outcome"):
                    loop.probe(ident, "baseline", now)
                loop.reserve(
                    ident,
                    adapter.quote(row, now),
                    adapter.balance(now),
                    policy,
                    controls,
                    now,
                )
                row = loop.row(ident)
            if (
                row["state"] == "reserved"
                and controls.get("enabled") is True
                and policy.enabled
            ):
                loop.operate(
                    ident,
                    "dispatch",
                    contractor_brief(row["contract"], ident),
                    lambda _, operation_id: adapter.dispatch(row, now, operation_id),
                    now,
                )
                row = loop.row(ident)
            observed = (
                adapter.observe(row, now)
                if row["remote"] or row["state"] == "uncertain"
                else {}
            )
            if row["state"] == "uncertain" and observed.get("acknowledgment"):
                loop.confirm_operation(
                    ident,
                    observed.get("operation", "dispatch"),
                    observed["acknowledgment"],
                    now,
                )
                row = loop.row(ident)
            if observed.get("delivery") and row["acceptance"] is None:
                loop.receive(ident, observed["delivery"], now)
                row = loop.row(ident)
            if observed.get("settlement"):
                loop.settle(ident, observed["settlement"], now)
            if row["acceptance"] and row["contract"].get("outcome"):
                loop.probe(ident, "after", now)
            if (
                isinstance(adapter, HumanTasks)
                and controls.get("enabled") is True
                and policy.enabled
            ):
                op, request = observed.get("operation"), observed.get("request")
                if op == "fund":
                    loop.operate(
                        ident,
                        "fund",
                        request,
                        lambda _, key: adapter.fund(row, key),
                        now,
                    )
                elif op == "hire":
                    loop.operate(
                        ident,
                        "hire",
                        request,
                        lambda _, key: adapter.hire(
                            row, request["application_id"], key
                        ),
                        now,
                    )
                elif row["state"] == "accepted" and observed.get("escrow_id"):
                    loop.operate(
                        ident,
                        "release",
                        {"escrow_id": observed["escrow_id"]},
                        lambda _, key: adapter.release(row, observed["escrow_id"], key),
                        now,
                    )
            loop.delivery_deadline(ident, now)
            loop.evaluate(ident, now)
        except (KeyError, ValueError, TypeError, OSError):
            errors.append({"id": ident, "phase": "evidence_or_activation_required"})
    return {
        "schema": "nuria.action-loop.v1",
        "execution_enabled": controls.get("enabled") is True and policy.enabled,
        "providers": readiness,
        "tasks": loop.public(),
        "errors": errors,
        "feedback": loop.feedback(
            cognition.get("agency", {}).get("feedback_cursor", 0)
        ),
        "scope": "Task delivery, payout and measured usefulness are distinct; no account or quote is inferred from fixtures",
    }


def load_action_files():
    catalog = Path(os.environ.get("NURIA_AGENCY_CATALOG", "/nonexistent"))
    controls = Path(os.environ.get("NURIA_ACTION_CONTROLS", "/nonexistent"))
    return (
        json.loads(catalog.read_text())
        if catalog.exists()
        else {"planning_enabled": False, "tasks": []},
        json.loads(controls.read_text())
        if controls.exists()
        else {"enabled": False, "adapters": []},
    )

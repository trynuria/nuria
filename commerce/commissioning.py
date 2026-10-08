"""Durable contractor requests, cost reservations and buyer-owned evaluation.

Planning has no signing or networking authority. A proposal is not an order;
payment, accepted delivery and measured usefulness are independent records.
"""

import hashlib
import json
import math
import re
from datetime import datetime, timezone

from commerce.ledger import canonical

STATES = {
    "proposed": {"reserved", "cancelled"},
    "reserved": {"dispatching", "cancelled"},
    "dispatching": {"submitted", "uncertain"},
    "uncertain": {"submitted"},
    "submitted": {"delivered", "overdue"},
    "overdue": {"delivered"},
    "delivered": {"accepted", "rejected"},
    "accepted": {"evaluated"},
    "rejected": {"evaluated"},
}
KINDS = ("agent", "human", "compute")
HASH = re.compile(r"[a-f0-9]{64}\Z")
IDENT = re.compile(r"[a-zA-Z0-9_-]{1,80}\Z")
ACTIONS = ("experiment", "compare", "explore", "predict")
BASE_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
ADDRESS = re.compile(r"0x[a-fA-F0-9]{40}\Z")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def public_text(value, maximum=2000):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("Invalid public task text")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("Task text contains control characters")
    # Neither authorization material nor credential-bearing links belong in briefs.
    if re.search(
        r"(?i)(?:private.key|wallet-auth:|api.key=|secret=|token=|BEGIN .*PRIVATE KEY)",
        value,
    ):
        raise ValueError("Task text may contain private authorization material")
    return value


def validate_goal(goal):
    required = {
        "id",
        "kind",
        "action",
        "title",
        "purpose",
        "brief",
        "schema",
        "evaluation",
    }
    if set(goal) != required or not IDENT.fullmatch(goal["id"]):
        raise ValueError("Invalid goal contract")
    if goal["kind"] not in KINDS or goal["action"] not in ACTIONS:
        raise ValueError("Unsupported goal kind or action")
    for key in ("title", "purpose", "brief"):
        public_text(goal[key], 8000 if key == "brief" else 500)
    if goal["schema"] != "nuria.predictions.v1":
        raise ValueError("Only non-executable structured predictions are supported")
    evaluation = goal["evaluation"]
    if set(evaluation) != {
        "dataset_sha256",
        "targets",
        "baseline",
        "minimum_improvement",
    }:
        raise ValueError("Evaluation must commit a local baseline and held-out targets")
    if not HASH.fullmatch(evaluation["dataset_sha256"]):
        raise ValueError("Invalid evaluation dataset hash")
    targets, baseline = evaluation["targets"], evaluation["baseline"]
    if (
        not isinstance(targets, list)
        or not 8 <= len(targets) <= 10000
        or len(baseline) != len(targets)
    ):
        raise ValueError("Evaluation size is invalid")
    if any(type(y) is not int or y not in (0, 1) for y in targets):
        raise ValueError("Held-out targets must be binary")
    if any(not finite(p) or not 0 <= p <= 1 for p in baseline):
        raise ValueError("Baseline probabilities are invalid")
    if (
        not finite(evaluation["minimum_improvement"])
        or not 0 <= evaluation["minimum_improvement"] <= 1
    ):
        raise ValueError("Invalid acceptance threshold")
    return goal


def inspect_decision(decision, now):
    payload = {
        k: v for k, v in decision.items() if k not in ("seq", "hash", "previous_hash")
    }
    parent = decision.get("previous_hash", "")
    if not HASH.fullmatch(parent) or not HASH.fullmatch(decision.get("hash", "")):
        raise ValueError("Decision hash is missing")
    if (
        hashlib.sha256((parent + canonical(payload)).encode()).hexdigest()
        != decision["hash"]
    ):
        raise ValueError("Cognitive decision integrity failed")
    stamp = datetime.fromisoformat(decision["utc"]).timestamp()
    if not finite(now) or not -2 <= now - stamp <= 30:
        raise ValueError("Decision is stale or future-dated")
    return decision["decision"]["action"]


def loss(probabilities, targets):
    return sum((p - y) ** 2 for p, y in zip(probabilities, targets, strict=True)) / len(
        targets
    )


class Commissioner:
    def __init__(self, ledger):
        self.ledger = ledger
        ledger.db.executescript("""
        CREATE TABLE IF NOT EXISTS commissions(
          id TEXT PRIMARY KEY, goal TEXT NOT NULL, kind TEXT NOT NULL,
          decision_hash TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
          state TEXT NOT NULL, contract TEXT NOT NULL, quote TEXT, dispatch TEXT,
          artifact TEXT, acceptance TEXT, settlement TEXT, outcome TEXT,
          amount INTEGER NOT NULL DEFAULT 0, day TEXT, deadline REAL);
        CREATE INDEX IF NOT EXISTS commissions_state ON commissions(state);
        CREATE INDEX IF NOT EXISTS commissions_created ON commissions(created);
        CREATE INDEX IF NOT EXISTS commissions_day ON commissions(day);
        CREATE INDEX IF NOT EXISTS commissions_goal ON commissions(goal,state);
        CREATE TABLE IF NOT EXISTS commission_payments(
          transaction_hash TEXT PRIMARY KEY, commission_id TEXT UNIQUE NOT NULL);
        CREATE INDEX IF NOT EXISTS events_commission ON events(json_extract(payload,'$.commission_id'));
        """)

    def _row(self, ident):
        columns = [
            item[1] for item in self.ledger.db.execute("PRAGMA table_info(commissions)")
        ]
        raw = self.ledger.db.execute(
            "SELECT * FROM commissions WHERE id=?", (ident,)
        ).fetchone()
        if not raw:
            raise ValueError("Commission is missing")
        row = dict(zip(columns, raw, strict=True))
        for key in (
            "contract",
            "quote",
            "dispatch",
            "artifact",
            "acceptance",
            "settlement",
            "outcome",
        ):
            row[key] = json.loads(row[key]) if row[key] else None
        commitment = self.ledger.db.execute(
            "SELECT json_extract(payload,'$.contract_sha256') FROM events WHERE json_extract(payload,'$.commission_id')=? AND json_extract(payload,'$.commission_state')='proposed' ORDER BY seq LIMIT 1",
            (ident,),
        ).fetchone()
        digest = hashlib.sha256(canonical(row["contract"]).encode()).hexdigest()
        if not commitment or commitment[0] != digest:
            raise ValueError(
                "Commission acceptance contract differs from its journal commitment"
            )
        row["contract_sha256"] = digest
        return row

    def _change(self, ident, state, fields, event, now):
        row = self._row(ident)
        if state not in STATES.get(row["state"], set()):
            raise ValueError("Invalid commission lifecycle transition")
        encoded = {
            k: canonical(v) if isinstance(v, dict) else v for k, v in fields.items()
        }
        assignments = ",".join(key + "=?" for key in encoded)
        self.ledger.db.execute(
            "UPDATE commissions SET state=?,updated=?"
            + ("," + assignments if assignments else "")
            + " WHERE id=?",
            (state, now, *encoded.values(), ident),
        )
        self.ledger.event(
            {"commission_id": ident, "commission_state": state, "at": now, **event}
        )

    def _transaction(self, operation):
        self.ledger.db.execute("BEGIN IMMEDIATE")
        try:
            result = operation()
            self.ledger.db.execute("COMMIT")
            return result
        except Exception:
            self.ledger.db.execute("ROLLBACK")
            raise

    def propose(self, goal, decision, now):
        validate_goal(goal)
        if inspect_decision(decision, now) != goal["action"]:
            return None
        ident = hashlib.sha256(
            (goal["id"] + ":" + decision["hash"]).encode()
        ).hexdigest()

        def operation():
            if self.ledger.db.execute(
                "SELECT 1 FROM commissions WHERE id=?", (ident,)
            ).fetchone():
                return ident
            current = self.ledger.db.execute(
                "SELECT id FROM commissions WHERE goal=? AND state NOT IN ('cancelled','rejected','evaluated') LIMIT 1",
                (goal["id"],),
            ).fetchone()
            if current:
                return current[0]
            last = self.ledger.db.execute(
                "SELECT max(created) FROM commissions"
            ).fetchone()[0]
            if last is not None and now - last < 60:
                return None
            self.ledger.db.execute(
                "INSERT INTO commissions(id,goal,kind,decision_hash,created,updated,state,contract) VALUES(?,?,?,?,?,?,'proposed',?)",
                (
                    ident,
                    goal["id"],
                    goal["kind"],
                    decision["hash"],
                    now,
                    now,
                    canonical(goal),
                ),
            )
            self.ledger.event(
                {
                    "commission_id": ident,
                    "commission_state": "proposed",
                    "at": now,
                    "goal": goal["id"],
                    "kind": goal["kind"],
                    "title": goal["title"],
                    "decision_hash": decision["hash"],
                    "action": goal["action"],
                    "dataset_sha256": goal["evaluation"]["dataset_sha256"],
                    "contract_sha256": hashlib.sha256(
                        canonical(goal).encode()
                    ).hexdigest(),
                    "scope": "A local proposal, not a marketplace order or payment",
                }
            )
            return ident

        return self._transaction(operation)

    def reserve(self, ident, quote, controls, payment_policy, balance, now):
        if controls.get("enabled") is not True or not payment_policy.enabled:
            raise ValueError(
                "Commissioning and financial execution must both be enabled"
            )
        if set(quote) != {
            "provider",
            "offer_id",
            "offer_sha256",
            "chain_id",
            "token",
            "amount_micro_usdc",
            "fees_micro_usdc",
            "verifier_micro_usdc",
            "gas_micro_usdc",
            "expires_at",
            "delivery_seconds",
            "recipient",
        }:
            raise ValueError("Quote is incomplete")
        if quote["chain_id"] != 8453 or quote["token"].lower() != BASE_USDC:
            raise ValueError("Commission quote uses an unsupported payment asset")
        if quote["provider"] not in controls.get("providers", []):
            raise ValueError("Provider is not enrolled")
        if not ADDRESS.fullmatch(controls.get("wallet") or "") or not ADDRESS.fullmatch(
            quote.get("recipient") or ""
        ):
            raise ValueError("Dedicated payer and verified recipient are required")
        if not IDENT.fullmatch(quote["offer_id"]) or not HASH.fullmatch(
            quote["offer_sha256"]
        ):
            raise ValueError("Offer commitment is invalid")
        prices = [
            quote[k]
            for k in (
                "amount_micro_usdc",
                "fees_micro_usdc",
                "verifier_micro_usdc",
                "gas_micro_usdc",
            )
        ]
        if any(type(v) is not int or v < 0 for v in prices) or not prices[0]:
            raise ValueError("Quote costs must be integer micro-USDC")
        if not prices[3]:
            raise ValueError(
                "A conservative, freshly valued gas commitment is required"
            )
        total = sum(prices)
        if (
            total > payment_policy.per_job_micro_usdc
            or not finite(quote["expires_at"])
            or not 0 < quote["expires_at"] - now <= 86400
        ):
            raise ValueError("Quote exceeds price or freshness limits")
        if (
            type(quote["delivery_seconds"]) is not int
            or not 60 <= quote["delivery_seconds"] <= 604800
        ):
            raise ValueError("Delivery deadline is invalid")
        if (
            balance.get("chain_id") != 8453
            or balance.get("wallet") != controls.get("wallet")
            or balance.get("token") != BASE_USDC
        ):
            raise ValueError(
                "Balance does not match the dedicated commissioning wallet"
            )
        if (
            not finite(balance.get("checked_at"))
            or not 0 <= now - balance["checked_at"] <= 30
        ):
            raise ValueError("Commissioning balance is stale")
        if type(balance.get("micro_usdc")) is not int or balance["micro_usdc"] < 0:
            raise ValueError("Commissioning balance is unknown")
        if (
            balance.get("finalized") is not True
            or type(balance.get("block")) is not int
        ):
            raise ValueError("Finalized commissioning balance is required")
        minimum_block = self.ledger.db.execute(
            "SELECT coalesce(max(json_extract(settlement,'$.block')),0) FROM commissions"
        ).fetchone()[0]
        if balance["block"] < minimum_block:
            raise ValueError("Balance precedes a recorded contractor payment")
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")

        def operation():
            row = self._row(ident)
            if row["state"] != "proposed":
                raise ValueError("Commission already committed; reconcile before retry")
            last_commission = self.ledger.db.execute(
                "SELECT max(json_extract(quote,'$.reserved_at')) FROM commissions"
            ).fetchone()[0]
            last_merchant = self.ledger.db.execute(
                "SELECT max(created) FROM jobs"
            ).fetchone()[0]
            last = max(
                (
                    value
                    for value in (last_commission, last_merchant)
                    if value is not None
                ),
                default=None,
            )
            if last is not None and now - last < payment_policy.cooldown_seconds:
                raise ValueError("Shared paid-job cooldown has not elapsed")
            pending = self.ledger.db.execute(
                "SELECT coalesce(sum(amount-coalesce(json_extract(settlement,'$.amount_micro_usdc'),0)),0),count(*) FROM commissions WHERE amount>coalesce(json_extract(settlement,'$.amount_micro_usdc'),0) AND state!='cancelled'"
            ).fetchone()
            merchant_pending = self.ledger.db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('reserved','authorized','uncertain')"
            ).fetchone()[0]
            daily = self.ledger.db.execute(
                "SELECT coalesce(sum(amount),0) FROM commissions WHERE day=?", (day,)
            ).fetchone()[0]
            merchant = self.ledger.db.execute(
                "SELECT coalesce(sum(amount),0) FROM jobs WHERE day=?", (day,)
            ).fetchone()[0]
            if (
                pending[1] + merchant_pending >= payment_policy.maximum_unresolved_jobs
                or total + daily + merchant > payment_policy.per_day_micro_usdc
            ):
                raise ValueError(
                    "Shared spending ceiling or unresolved-job breaker reached"
                )
            if (
                balance["micro_usdc"] - pending[0] - total
                < payment_policy.reserve_micro_usdc
            ):
                raise ValueError("Commission would consume the inventory floor")
            self._change(
                ident,
                "reserved",
                {
                    "quote": {**quote, "payer": controls["wallet"], "reserved_at": now},
                    "amount": total,
                    "day": day,
                    "deadline": now + quote["delivery_seconds"],
                },
                {
                    "provider": quote["provider"],
                    "offer_sha256": quote["offer_sha256"],
                    "committed_micro_usdc": total,
                },
                now,
            )

        self._transaction(operation)

    def dispatch(self, ident, prepare, send, now):
        """Persist an intent BEFORE disclosure. Ambiguous writes are never retried."""
        row = self._row(ident)
        if row["state"] != "reserved":
            raise ValueError("Order is not safely dispatchable")
        if row["quote"]["expires_at"] <= now:
            raise ValueError("Order quote expired before disclosure")
        intent = prepare(row)
        public_intent = {
            "endpoint": intent["endpoint"],
            "body_sha256": hashlib.sha256(
                canonical(intent["body"]).encode()
            ).hexdigest(),
            "request_id": ident,
        }
        self._transaction(
            lambda: self._change(
                ident, "dispatching", {"dispatch": public_intent}, public_intent, now
            )
        )
        try:
            response = send(intent)
            if (
                set(response) != {"external_id", "offer_sha256"}
                or not IDENT.fullmatch(response["external_id"])
                or response["offer_sha256"] != row["quote"]["offer_sha256"]
            ):
                raise ValueError("Marketplace acknowledgment does not match order")
        except Exception:
            self._transaction(
                lambda: self._change(
                    ident,
                    "uncertain",
                    {},
                    {
                        "reason": "External order outcome unknown; automatic resubmission refused"
                    },
                    now,
                )
            )
            return
        self.confirm(ident, response, now)

    def confirm(self, ident, evidence, now):
        row = self._row(ident)
        if (
            not IDENT.fullmatch(evidence.get("external_id", ""))
            or evidence.get("offer_sha256") != row["quote"]["offer_sha256"]
        ):
            raise ValueError("Order acknowledgment is not bound to the offer")
        dispatch = {**row["dispatch"], "external_id": evidence["external_id"]}
        self._transaction(
            lambda: self._change(
                ident,
                "submitted",
                {"dispatch": dispatch},
                {"external_id": evidence["external_id"]},
                now,
            )
        )

    def expire(self, now):
        for (ident,) in self.ledger.db.execute(
            "SELECT id FROM commissions WHERE state='submitted' AND deadline<? LIMIT 20",
            (now,),
        ).fetchall():
            self._transaction(
                lambda: self._change(
                    ident,
                    "overdue",
                    {},
                    {
                        "reason": "Delivery deadline passed; financial liability remains reserved"
                    },
                    now,
                )
            )

    def receive(self, ident, raw, now):
        if not isinstance(raw, bytes) or len(raw) > 262144:
            raise ValueError("Artifact exceeds the structured delivery limit")
        row = self._row(ident)
        value = json.loads(raw)
        expected = row["contract"]["evaluation"]
        if (
            set(value) != {"schema", "commission_id", "dataset_sha256", "predictions"}
            or value["schema"] != row["contract"]["schema"]
            or value["commission_id"] != ident
            or value["dataset_sha256"] != expected["dataset_sha256"]
        ):
            raise ValueError("Delivery is not bound to the committed task")
        probabilities = value["predictions"]
        if (
            not isinstance(probabilities, list)
            or len(probabilities) != len(expected["targets"])
            or any(not finite(v) or not 0 <= v <= 1 for v in probabilities)
        ):
            raise ValueError("Delivered predictions are malformed")
        artifact = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema": value["schema"],
            "dataset_sha256": value["dataset_sha256"],
            "predictions": probabilities,
            "received_at": now,
        }
        self._transaction(
            lambda: self._change(
                ident,
                "delivered",
                {"artifact": artifact},
                {k: artifact[k] for k in ("sha256", "schema", "dataset_sha256")},
                now,
            )
        )

    def accept(self, ident, now):
        row = self._row(ident)
        if row["state"] != "delivered":
            raise ValueError("No delivery to evaluate")
        evaluation = row["contract"]["evaluation"]
        baseline = loss(evaluation["baseline"], evaluation["targets"])
        candidate = loss(row["artifact"]["predictions"], evaluation["targets"])
        improvement = baseline - candidate
        accepted = improvement >= evaluation["minimum_improvement"]
        checks = {
            "passed": accepted,
            "metric": "brier",
            "baseline": baseline,
            "candidate": candidate,
            "improvement": improvement,
            "items": len(evaluation["targets"]),
            "dataset_sha256": evaluation["dataset_sha256"],
            "artifact_sha256": row["artifact"]["sha256"],
            "scope": "Buyer-owned held-out numerical check; no external code executed",
        }
        self._transaction(
            lambda: self._change(
                ident,
                "accepted" if accepted else "rejected",
                {"acceptance": checks},
                checks,
                now,
            )
        )
        return checks

    def settlement(self, ident, transaction, inspector, now):
        row = self._row(ident)
        if row["settlement"] or row["state"] not in (
            "submitted",
            "overdue",
            "delivered",
            "accepted",
            "rejected",
        ):
            raise ValueError("Payment already recorded")
        # The inspector is a trusted chain adapter, never a merchant-supplied flag.
        evidence = inspector(transaction, row)
        if (
            not isinstance(evidence, dict)
            or evidence.get("finalized") is not True
            or evidence.get("chain_id") != 8453
            or evidence.get("amount_micro_usdc") != row["quote"]["amount_micro_usdc"]
            or evidence.get("offer_sha256") != row["quote"]["offer_sha256"]
            or evidence.get("transaction") != transaction
            or not re.fullmatch(r"0x[a-fA-F0-9]{64}", transaction)
            or evidence.get("token") != BASE_USDC
            or evidence.get("sender") != row["quote"]["payer"]
            or not row["quote"]["recipient"]
            or evidence.get("recipient") != row["quote"]["recipient"]
            or type(evidence.get("block")) is not int
            or evidence["block"] < 0
        ):
            raise ValueError("Finalized exact payment evidence is incomplete")
        allowed = (
            "transaction",
            "chain_id",
            "token",
            "sender",
            "recipient",
            "amount_micro_usdc",
            "block",
            "block_hash",
            "log_index",
            "finalized",
            "offer_sha256",
            "scope",
        )
        evidence = {key: evidence[key] for key in allowed if key in evidence}

        def operation():
            if self._row(ident)["settlement"]:
                raise ValueError("Payment already recorded")
            self.ledger.db.execute(
                "INSERT INTO commission_payments VALUES(?,?)",
                (transaction.lower(), ident),
            )
            self.ledger.db.execute(
                "UPDATE commissions SET settlement=?,updated=? WHERE id=?",
                (canonical(evidence), now, ident),
            )
            self.ledger.event(
                {
                    "commission_id": ident,
                    "commission_payment": "settled",
                    "at": now,
                    **evidence,
                }
            )

        self._transaction(operation)

    def evaluate(self, ident, now):
        row = self._row(ident)
        if row["state"] not in ("accepted", "rejected") or not row["settlement"]:
            raise ValueError("Checked delivery and verified payment are required")
        checks = row["acceptance"]
        reward = max(
            -1, min(1, checks["improvement"] - row["amount"] / 1_000_000 * 0.01)
        )
        outcome = {
            "reward": reward,
            "improvement": checks["improvement"],
            "cost_micro_usdc": row["amount"],
            "metric": "brier",
            "accepted": checks["passed"],
            "artifact_sha256": row["artifact"]["sha256"],
            "dataset_sha256": checks["dataset_sha256"],
            "scope": "Task-specific improvement minus committed cost; not evidence of consciousness or general neural superiority",
        }
        self._transaction(
            lambda: self._change(ident, "evaluated", {"outcome": outcome}, outcome, now)
        )
        return outcome

    def cancel(self, ident, now):
        # An external order, ambiguous dispatch or deposit cannot be cancelled locally.
        self._transaction(
            lambda: self._change(
                ident,
                "cancelled",
                {"amount": 0},
                {"reason": "Closed before external disclosure"},
                now,
            )
        )

    def public(self, limit=40):
        if type(limit) is not int or not 1 <= limit <= 80:
            raise ValueError("Invalid public commission limit")
        result = []
        for (ident,) in self.ledger.db.execute(
            "SELECT id FROM commissions ORDER BY created DESC,id LIMIT ?", (limit,)
        ):
            row = self._row(ident)
            contract, quote = row["contract"], row["quote"]
            result.append(
                {
                    "id": ident,
                    "kind": row["kind"],
                    "title": contract["title"],
                    "purpose": contract["purpose"],
                    "action": contract["action"],
                    "state": row["state"],
                    "created_at": row["created"],
                    "updated_at": row["updated"],
                    "decision_hash": row["decision_hash"],
                    "contract_sha256": row["contract_sha256"],
                    "deadline": row["deadline"],
                    "provider": quote["provider"] if quote else None,
                    "chain_id": quote["chain_id"] if quote else None,
                    "offer_sha256": quote["offer_sha256"] if quote else None,
                    "recipient": quote["recipient"] if quote else None,
                    "payer": quote["payer"] if quote else None,
                    "committed_micro_usdc": row["amount"],
                    "external_id": (row["dispatch"] or {}).get("external_id"),
                    "artifact_sha256": (row["artifact"] or {}).get("sha256"),
                    "dataset_sha256": contract["evaluation"]["dataset_sha256"],
                    "acceptance": row["acceptance"],
                    "settlement": row["settlement"],
                    "outcome": row["outcome"],
                    "scope": "Proposal only; no order or payment"
                    if row["state"] == "proposed"
                    else "External work, payment and measured outcome remain separate",
                }
            )
        return result

    def summary(self):
        total = self.ledger.db.execute("SELECT count(*) FROM commissions").fetchone()[0]
        commitment = self.ledger.db.execute(
            "SELECT coalesce(sum(amount),0) FROM commissions"
        ).fetchone()[0]
        settled = self.ledger.db.execute(
            "SELECT coalesce(sum(json_extract(settlement,'$.amount_micro_usdc')),0) FROM commissions"
        ).fetchone()[0]
        return {
            "total": total,
            "committed_micro_usdc": commitment,
            "settled_micro_usdc": settled,
            "coverage": "At most 40 recent requests; complete events remain in the payment journal",
            "scope": "Base commissioning commitments, separate from the Solana inventory. Gas and other ceilings are retained until independently reconciled.",
        }

    def provider_memory(self):
        """Only measured paid outcomes inform task-specific contractor preference."""
        return [
            {
                "goal": goal,
                "provider": provider,
                "evaluated": count,
                "mean_reward": reward,
            }
            for goal, provider, count, reward in self.ledger.db.execute(
                "SELECT goal,json_extract(quote,'$.provider'),count(*),avg(json_extract(outcome,'$.reward')) "
                "FROM commissions WHERE state='evaluated' AND outcome IS NOT NULL "
                "GROUP BY goal,json_extract(quote,'$.provider') ORDER BY goal,2 LIMIT 80"
            )
        ]

    def rank_quotes(self, goal, quotes):
        """An inspectable hybrid preference, not automatic order authority."""
        memory = {
            item["provider"]: item
            for item in self.provider_memory()
            if item["goal"] == goal
        }
        ranked = []
        for quote in quotes:
            history = memory.get(quote["provider"])
            price = sum(
                quote[key]
                for key in (
                    "amount_micro_usdc",
                    "fees_micro_usdc",
                    "verifier_micro_usdc",
                    "gas_micro_usdc",
                )
            )
            reward = history["mean_reward"] if history else 0
            ranked.append(
                {
                    "quote": quote,
                    "score": reward - 0.01 * price / 1_000_000,
                    "evaluated": history["evaluated"] if history else 0,
                }
            )
        return sorted(
            ranked, key=lambda item: (-item["score"], item["quote"]["offer_id"])
        )

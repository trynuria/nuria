"""Crash-safe task execution, exact liabilities and delayed utility evidence."""

import copy
import json
import time
from datetime import datetime, timezone

from commerce.action_budget import commitments
from commerce.ledger import canonical
from commerce.tasks import HASH, digest, finite, validate_task, verify_delivery


class ActionLoop:
    def __init__(self, ledger):
        self.ledger = ledger
        ledger.db.executescript("""
        CREATE TABLE IF NOT EXISTS action_tasks(
          id TEXT PRIMARY KEY, intent_id TEXT UNIQUE NOT NULL, goal_id TEXT NOT NULL,
          contract TEXT NOT NULL, intent TEXT NOT NULL, provider TEXT NOT NULL,
          state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
          account TEXT, quote TEXT, amount INTEGER NOT NULL DEFAULT 0, day TEXT,
          remote TEXT, acceptance TEXT, settlement TEXT, outcome TEXT);
        CREATE INDEX IF NOT EXISTS action_tasks_state ON action_tasks(state);
        CREATE INDEX IF NOT EXISTS action_tasks_day ON action_tasks(day);
        CREATE TABLE IF NOT EXISTS action_operations(
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, operation TEXT NOT NULL,
          state TEXT NOT NULL, request_sha256 TEXT NOT NULL, response TEXT, request TEXT);
        CREATE TABLE IF NOT EXISTS action_measurements(
          task_id TEXT NOT NULL, phase TEXT NOT NULL, sample INTEGER NOT NULL,
          observed REAL NOT NULL, observation TEXT NOT NULL,
          PRIMARY KEY(task_id,phase,sample));
        """)
        if "request" not in {
            item[1]
            for item in ledger.db.execute("PRAGMA table_info(action_operations)")
        }:
            ledger.db.execute("ALTER TABLE action_operations ADD COLUMN request TEXT")

    def transaction(self, function):
        self.ledger.db.execute("BEGIN IMMEDIATE")
        try:
            result = function()
            self.ledger.db.execute("COMMIT")
            return result
        except Exception:
            self.ledger.db.execute("ROLLBACK")
            raise

    def row(self, ident):
        names = [
            r[1] for r in self.ledger.db.execute("PRAGMA table_info(action_tasks)")
        ]
        raw = self.ledger.db.execute(
            "SELECT * FROM action_tasks WHERE id=?", (ident,)
        ).fetchone()
        if not raw:
            raise ValueError("Task is missing")
        row = dict(zip(names, raw, strict=True))
        for key in (
            "contract",
            "intent",
            "quote",
            "remote",
            "acceptance",
            "settlement",
            "outcome",
        ):
            row[key] = json.loads(row[key]) if row[key] else None
        if digest(row["contract"]) != row["intent"]["contract_sha256"]:
            raise ValueError("Task contract commitment changed")
        original = self.ledger.db.execute(
            "SELECT json_extract(payload,'$.contract_sha256'),json_extract(payload,'$.intent_id') FROM events WHERE json_extract(payload,'$.action_task_id')=? AND json_extract(payload,'$.task_state')='proposed' ORDER BY seq LIMIT 1",
            (ident,),
        ).fetchone()
        if not original or original != (digest(row["contract"]), row["intent_id"]):
            raise ValueError("Task differs from its original journal commitment")
        operation = self.ledger.db.execute(
            "SELECT operation,request FROM action_operations WHERE task_id=? AND state='disclosed' ORDER BY rowid DESC LIMIT 1",
            (ident,),
        ).fetchone()
        row["pending_operation"] = (
            {
                "name": operation[0],
                "request": json.loads(operation[1]) if operation[1] else None,
            }
            if operation
            else None
        )
        return row

    def event(self, ident, state, detail, now):
        self.ledger.event(
            {"action_task_id": ident, "task_state": state, "at": now, **detail}
        )

    def propose(self, task, decision, now):
        validate_task(task)
        payload = {
            k: v
            for k, v in decision.items()
            if k not in ("seq", "hash", "previous_hash")
        }
        import hashlib

        if not HASH.fullmatch(decision.get("previous_hash", "")) or hashlib.sha256(
            (decision["previous_hash"] + canonical(payload)).encode()
        ).hexdigest() != decision.get("hash"):
            raise ValueError("Originating decision integrity failed")
        stamp = datetime.fromisoformat(decision["utc"]).timestamp()
        if not finite(now) or not 0 <= now - stamp <= 30:
            raise ValueError("Originating decision is stale")
        intent = decision.get("intent") or {}
        if (
            intent.get("schema") != "neural-task-policy-v1"
            or digest({k: v for k, v in intent.items() if k != "id"})
            != intent.get("id")
            or intent.get("contract_sha256") != digest(task)
            or intent.get("goal_id") != task["id"]
            or not HASH.fullmatch(intent.get("neural_state_sha256", ""))
        ):
            raise ValueError("Task is not bound to a neural intent")
        ident = digest({"intent_id": intent["id"], "contract": digest(task)})

        def create():
            if self.ledger.db.execute(
                "SELECT 1 FROM action_tasks WHERE id=?", (ident,)
            ).fetchone():
                return ident
            if self.ledger.db.execute(
                "SELECT 1 FROM action_tasks WHERE goal_id=? AND state NOT IN ('evaluated','cancelled')",
                (task["id"],),
            ).fetchone():
                return None
            self.ledger.db.execute(
                "INSERT INTO action_tasks(id,intent_id,goal_id,contract,intent,provider,state,created,updated) VALUES(?,?,?,?,?,?,'proposed',?,?)",
                (
                    ident,
                    intent["id"],
                    task["id"],
                    canonical(task),
                    canonical(intent),
                    task["provider"],
                    now,
                    now,
                ),
            )
            self.event(
                ident,
                "proposed",
                {
                    "intent_id": intent["id"],
                    "contract_sha256": digest(task),
                    "neural_state_sha256": intent["neural_state_sha256"],
                    "origin_decision_hash": decision["hash"],
                },
                now,
            )
            return ident

        return self.transaction(create)

    def reserve(self, ident, quote, balance, policy, controls, now):
        row = self.row(ident)
        required = {
            "provider",
            "account",
            "offer_id",
            "total_micro_usd",
            "expires_at",
            "checked_at",
            "destination",
            "evidence_sha256",
            "payment_basis",
        }
        if (
            set(quote) != required
            or quote["provider"] != row["provider"]
            or not isinstance(quote["account"], str)
            or not 1 <= len(quote["account"]) <= 160
            or not isinstance(quote["destination"], dict)
            or quote["payment_basis"]
            not in ("platform_wallet", "exact_usdc", "included_capacity")
        ):
            raise ValueError("Task quote identity or fields are invalid")
        amount = quote["total_micro_usd"]
        if (
            type(amount) is not int
            or amount < 0
            or amount > row["contract"]["maximum_micro_usd"]
            or not HASH.fullmatch(quote["evidence_sha256"])
        ):
            raise ValueError("Quote exceeds the committed task ceiling")
        if (
            not finite(quote["expires_at"])
            or not 0 < quote["expires_at"] - now <= 300
            or not finite(quote["checked_at"])
            or not 0 <= now - quote["checked_at"] <= 30
        ):
            raise ValueError("Task quote is stale")
        if (
            quote["provider"] not in controls.get("providers", [])
            or controls.get("enabled") is not True
        ):
            raise ValueError("Task execution is disabled or provider unenrolled")
        if amount and not policy.enabled:
            raise ValueError("Financial execution is disabled")
        if not amount and quote["payment_basis"] != "included_capacity":
            raise ValueError("Zero-cost quote needs an included-capacity contract")
        if (
            balance.get("account") != quote["account"]
            or balance.get("verified") is not True
            or not finite(balance.get("checked_at"))
            or not 0 <= now - balance["checked_at"] <= 30
            or type(balance.get("available_micro_usd")) is not int
            or balance["available_micro_usd"] < 0
        ):
            raise ValueError("Task funding account is unknown or stale")
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        if (
            row["contract"].get("outcome")
            and not self.ledger.db.execute(
                "SELECT 1 FROM action_measurements WHERE task_id=? AND phase='baseline'",
                (ident,),
            ).fetchone()
        ):
            raise ValueError("A pre-purchase outcome baseline is required")

        def commit():
            if self.row(ident)["state"] != "proposed":
                raise ValueError("Task was already committed")
            daily = self.ledger.db.execute(
                "SELECT coalesce(sum(amount),0) FROM action_tasks WHERE day=?", (day,)
            ).fetchone()[0]
            lifetime = self.ledger.db.execute(
                "SELECT coalesce(sum(amount),0) FROM action_tasks"
            ).fetchone()[0]
            pending, unresolved = self.ledger.db.execute(
                "SELECT coalesce(sum(amount),0),count(*) FROM action_tasks WHERE account=? AND amount>0 AND settlement IS NULL AND state!='cancelled'",
                (quote["account"],),
            ).fetchone()
            last = commitments(self.ledger.db, day, quote["account"])["last"]
            unresolved = commitments(self.ledger.db, day, quote["account"])[
                "unresolved"
            ]
            for table in ("jobs", "commissions"):
                if self.ledger.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone():
                    unlinked = (
                        " AND json_extract(terms,'$.action_task_id') IS NULL"
                        if table == "jobs"
                        else ""
                    )
                    daily += self.ledger.db.execute(
                        f"SELECT coalesce(sum(amount),0) FROM {table} WHERE day=?"
                        + unlinked,
                        (day,),
                    ).fetchone()[0]
                    lifetime += self.ledger.db.execute(
                        f"SELECT coalesce(sum(amount),0) FROM {table} WHERE 1=1"
                        + unlinked
                    ).fetchone()[0]
                    other_last = self.ledger.db.execute(
                        f"SELECT max(created) FROM {table} WHERE amount>0"
                    ).fetchone()[0]
                    last = max(
                        (v for v in (last, other_last) if v is not None), default=None
                    )
                    if table == "jobs":
                        unresolved += self.ledger.db.execute(
                            "SELECT count(*) FROM jobs WHERE status IN ('reserved','authorized','uncertain') AND json_extract(terms,'$.action_task_id') IS NULL"
                        ).fetchone()[0]
                        if quote["payment_basis"] == "exact_usdc":
                            pending += self.ledger.db.execute(
                                "SELECT coalesce(sum(amount),0) FROM jobs WHERE status IN ('reserved','authorized','uncertain') AND json_extract(terms,'$.action_task_id') IS NULL"
                            ).fetchone()[0]
                    else:
                        unresolved += self.ledger.db.execute(
                            "SELECT count(*) FROM commissions WHERE amount>coalesce(json_extract(settlement,'$.amount_micro_usdc'),0) AND state!='cancelled'"
                        ).fetchone()[0]
            if amount and last is not None and now - last < policy.cooldown_seconds:
                raise ValueError("Shared paid-task cooldown has not elapsed")
            if amount and (
                amount > policy.per_job_micro_usdc
                or daily + amount > policy.per_day_micro_usdc
                or lifetime + amount > controls.get("lifetime_micro_usd", 0)
                or unresolved >= policy.maximum_unresolved_jobs
                or balance["available_micro_usd"] - pending - amount
                < policy.reserve_micro_usdc
            ):
                raise ValueError(
                    "Shared budget, inventory floor or liability breaker reached"
                )
            self.ledger.db.execute(
                "UPDATE action_tasks SET state='reserved',quote=?,account=?,amount=?,day=?,updated=? WHERE id=?",
                (canonical(quote), quote["account"], amount, day, now, ident),
            )
            self.event(
                ident,
                "reserved",
                {
                    "quote_sha256": digest(quote),
                    "committed_micro_usd": amount,
                    "payment_basis": quote["payment_basis"],
                    "destination": quote["destination"],
                },
                now,
            )

        self.transaction(commit)

    def operate(self, ident, operation, request, send, now):
        """Persist disclosure before the write; an ambiguous operation is not replayed."""
        if operation not in ("dispatch", "fund", "hire", "release"):
            raise ValueError("Unsupported task write")
        allowed = {
            "dispatch": ("reserved",),
            "fund": ("submitted",),
            "hire": ("submitted",),
            "release": ("accepted",),
        }
        op_id = digest({"job": ident, "operation": operation})

        def begin():
            row = self.row(ident)
            if (
                row["state"] not in allowed[operation]
                or self.ledger.db.execute(
                    "SELECT 1 FROM action_operations WHERE id=?", (op_id,)
                ).fetchone()
            ):
                raise ValueError("Task write already disclosed or state invalid")
            if operation == "dispatch" and row["quote"]["expires_at"] <= now:
                raise ValueError("Task quote expired before disclosure")
            self.ledger.db.execute(
                "INSERT INTO action_operations(id,task_id,operation,state,request_sha256,response,request) VALUES(?,?,?,'disclosed',?,NULL,?)",
                (op_id, ident, operation, digest(request), canonical(request)),
            )
            self.ledger.db.execute(
                "UPDATE action_tasks SET state='uncertain',updated=? WHERE id=?",
                (now, ident),
            )
            self.event(
                ident,
                "uncertain",
                {
                    "operation": operation,
                    "operation_id": op_id,
                    "request_sha256": digest(request),
                },
                now,
            )

        self.transaction(begin)
        try:
            response = send(request, op_id)
            self.confirm_operation(ident, operation, response, now)
        except Exception:
            # The private adapter may retain diagnostics. Never publish arbitrary
            # provider errors, credentials, briefs or human submission contents.
            return False
        return True

    def confirm_operation(self, ident, operation, response, now):
        if (
            not isinstance(response, dict)
            or response.get("job_id") != ident
            or not isinstance(response.get("external_id"), str)
            or not 1 <= len(response["external_id"]) <= 128
        ):
            raise ValueError("Provider acknowledgment is not bound to the task")
        op_id = digest({"job": ident, "operation": operation})

        def confirm():
            op = self.ledger.db.execute(
                "SELECT state FROM action_operations WHERE id=? AND task_id=? AND operation=?",
                (op_id, ident, operation),
            ).fetchone()
            if (
                not op
                or op[0] != "disclosed"
                or self.row(ident)["state"] != "uncertain"
            ):
                raise ValueError("No unresolved task operation to confirm")
            if self.ledger.db.execute(
                "SELECT 1 FROM action_tasks WHERE provider=? AND id!=? AND json_extract(remote,'$.external_id')=?",
                (self.row(ident)["provider"], ident, response["external_id"]),
            ).fetchone():
                raise ValueError("External order is already bound to another task")
            state = "release_pending" if operation == "release" else "submitted"
            self.ledger.db.execute(
                "UPDATE action_operations SET state='confirmed',response=? WHERE id=?",
                (canonical(response), op_id),
            )
            self.ledger.db.execute(
                "UPDATE action_tasks SET state=?,remote=?,updated=? WHERE id=?",
                (
                    state,
                    canonical({**(self.row(ident)["remote"] or {}), **response}),
                    now,
                    ident,
                ),
            )
            self.event(
                ident,
                state,
                {"external_id": response["external_id"], "operation": operation},
                now,
            )

        self.transaction(confirm)

    def receive(self, ident, raw, now, *, analysis_resolver=None):
        row = self.row(ident)
        if row["state"] not in ("submitted", "release_pending"):
            raise ValueError("Task cannot receive a delivery in this state")
        acceptance = verify_delivery(
            row["contract"], ident, raw, analysis_resolver=analysis_resolver
        )
        analysis = row["contract"]["acceptance"]["type"] == "registered_analysis"
        if now > row["created"] + row["contract"]["delivery_seconds"]:
            if analysis:
                acceptance.update(passed=False, delivery_passed=False, late=True)
            else:
                acceptance.update(
                    passed=False,
                    utility=min(-0.1, acceptance["utility"] or 0),
                    late=True,
                )
        state = (
            "delivered"
            if analysis
            else "accepted"
            if acceptance["passed"]
            else "rejected"
        )

        def accept():
            self.ledger.db.execute(
                "UPDATE action_tasks SET acceptance=?,state=?,updated=? WHERE id=?",
                (
                    canonical(acceptance),
                    state,
                    now,
                    ident,
                ),
            )
            self.event(
                ident,
                state,
                acceptance,
                now,
            )

        self.transaction(accept)
        return acceptance

    def grade_analysis(self, ident, resolve_grade, now):
        """Host-only transition from delivered evidence to qualified semantics.

        The callable resolves a separately retained, enrolled checker receipt.
        No JSON action or public request can supply its own accepted flag.
        """
        from commerce.grounded_analysis import GRADE_FIELDS

        row = self.row(ident)
        if (
            row["contract"]["acceptance"]["type"] != "registered_analysis"
            or row["acceptance"] is None
            or not callable(resolve_grade)
            or row["state"] not in ("delivered", "accepted", "rejected", "evaluated")
        ):
            raise ValueError("Analytical grading needs a delivered registered job")
        grade = resolve_grade(copy.deepcopy(row))
        if grade is None:
            return None
        if (
            type(grade) is not dict
            or set(grade) != GRADE_FIELDS
            or type(grade["semantic_accepted"]) is not bool
            or type(grade["utility"]) is not int
            or grade["utility"] != int(grade["semantic_accepted"])
            or any(
                type(grade[key]) is not str or not HASH.fullmatch(grade[key])
                for key in GRADE_FIELDS - {"semantic_accepted", "utility"}
            )
            or grade["analysis_artifact_sha256"]
            != row["acceptance"]["analysis_artifact_sha256"]
        ):
            raise ValueError("Analytical checker evidence is malformed or unbound")
        if row["acceptance"]["semantic_accepted"] is not None:
            if any(row["acceptance"][key] != grade[key] for key in GRADE_FIELDS):
                raise ValueError("A committed analytical grade cannot change")
            return row["acceptance"]
        acceptance = {
            **row["acceptance"],
            **grade,
            "passed": grade["semantic_accepted"]
            and row["acceptance"]["delivery_passed"],
            "metric": "registered_sql_answer_correctness",
            "scope": "A separately registered host checker qualified the retained "
            "answer on its stated source. Binary correctness is not a "
            "claim of improvement, neural advantage or general usefulness.",
        }
        state = "accepted" if acceptance["passed"] else "rejected"

        def commit():
            current = self.row(ident)
            if current["acceptance"] != row["acceptance"]:
                raise ValueError("Analytical delivery changed before qualification")
            self.ledger.db.execute(
                "UPDATE action_tasks SET acceptance=?,state=?,updated=? WHERE id=?",
                (canonical(acceptance), state, now, ident),
            )
            self.event(ident, state, acceptance, now)

        self.transaction(commit)
        return acceptance

    def settle(self, ident, evidence, now):
        row = self.row(ident)
        if row["settlement"]:
            if any(
                row["settlement"].get(key) != evidence.get(key)
                for key in (
                    "job_id",
                    "account",
                    "external_id",
                    "total_micro_usd",
                    "basis",
                    "transaction",
                )
            ):
                raise ValueError("Settlement evidence changed")
            return
        if (
            not row["quote"]
            or row["state"]
            not in ("submitted", "delivered", "accepted", "rejected", "release_pending")
            or evidence.get("job_id") != ident
            or evidence.get("account") != row["account"]
            or evidence.get("external_id") != (row["remote"] or {}).get("external_id")
            or evidence.get("verified") is not True
            or evidence.get("basis")
            not in (
                "provider_confirmed_payout",
                "finalized_chain_transfer",
                "included_capacity",
            )
            or type(evidence.get("total_micro_usd")) is not int
            or not 0 <= evidence["total_micro_usd"] <= row["amount"]
            or not finite(evidence.get("checked_at"))
            or not 0 <= now - evidence["checked_at"] <= 30
            or not HASH.fullmatch(evidence.get("evidence_sha256", ""))
        ):
            raise ValueError("Settlement evidence is missing, stale or unbound")
        expected_basis = {
            "exact_usdc": "finalized_chain_transfer",
            "platform_wallet": "provider_confirmed_payout",
            "included_capacity": "included_capacity",
        }[row["quote"]["payment_basis"]]
        if evidence["basis"] != expected_basis:
            raise ValueError("Payout evidence uses a different payment rail")
        if row["amount"] and evidence["basis"] == "included_capacity":
            raise ValueError("A paid job needs actual payout evidence")
        if evidence["basis"] == "finalized_chain_transfer":
            from solders.signature import Signature

            Signature.from_string(evidence.get("transaction", ""))
            if (
                evidence.get("recipient_wallet")
                != row["quote"]["destination"].get("wallet")
                or evidence.get("payer") != row["account"]
                or evidence["total_micro_usd"] != row["amount"]
                or type(evidence.get("slot")) is not int
                or evidence["slot"] < 0
            ):
                raise ValueError(
                    "Finalized transfer does not match the exact task invoice"
                )
        if evidence["basis"] == "provider_confirmed_payout" and not evidence.get(
            "worker_id"
        ):
            raise ValueError("Worker payout identity is unknown")
        if (
            evidence.get("transaction")
            and self.ledger.db.execute(
                "SELECT 1 FROM action_tasks WHERE id!=? AND json_extract(settlement,'$.transaction')=?",
                (ident, evidence["transaction"]),
            ).fetchone()
        ):
            raise ValueError("A payout cannot settle two action tasks")

        def save():
            self.ledger.db.execute(
                "UPDATE action_tasks SET settlement=?,updated=? WHERE id=?",
                (canonical(evidence), now, ident),
            )
            self.event(
                ident,
                "settled",
                {k: v for k, v in evidence.items() if k != "account"},
                now,
            )

        self.transaction(save)

    def evaluate(self, ident, now):
        row = self.row(ident)
        if row["outcome"]:
            return row["outcome"]
        if not row["acceptance"] or not row["settlement"]:
            return None
        utility = row["acceptance"]["utility"]
        measurement = None
        if (
            row["contract"].get("outcome")
            and row["acceptance"]["metric"] != "delivery_deadline"
        ):
            samples = row["contract"]["outcome"]["samples"]
            before = self.measurements(ident, "baseline")
            after = self.measurements(ident, "after")
            if len(before) != 1 or len(after) != samples:
                return None
            baseline = float(before[0]["passed"])
            observed = sum(item["passed"] for item in after) / samples
            utility = observed - baseline
            measurement = {
                "metric": "http_availability_gain",
                "baseline_success": baseline,
                "delivered_success": observed,
                "samples": samples,
                "observations_sha256": digest(after),
                "scope": "Buyer-observed resource availability and latency; an operational comparison, not randomized causal attribution",
            }
        if utility is None:
            return None  # Structure or payment alone does not establish usefulness.
        if not row["acceptance"]["passed"]:
            utility = min(0, utility)
        cost = row["settlement"]["total_micro_usd"]
        reward = max(-1, min(1, utility - 0.01 * cost / 1_000_000))
        result = {
            "intent_id": row["intent_id"],
            "goal_id": row["goal_id"],
            "action": row["contract"]["action"],
            "contract_sha256": digest(row["contract"]),
            "reward": reward,
            "utility": utility,
            "cost_micro_usd": cost,
            "result_sha256": row["acceptance"]["artifact_sha256"]
            or row["acceptance"].get("evidence_sha256"),
            "evidence_level": "verified_outcome",
            "metric": measurement["metric"]
            if measurement
            else row["acceptance"]["metric"],
            "settlement_basis": row["settlement"]["basis"],
            "measurement": measurement,
        }

        def measure():
            self.ledger.db.execute(
                "UPDATE action_tasks SET state='evaluated',outcome=?,updated=? WHERE id=?",
                (canonical(result), now, ident),
            )
            self.event(ident, "evaluated", result, now)

        self.transaction(measure)
        return result

    def delivery_deadline(self, ident, now):
        row = self.row(ident)
        if (
            row["acceptance"]
            or not row["settlement"]
            or now <= row["created"] + row["contract"]["delivery_seconds"]
        ):
            return False
        evidence = {
            "job_id": ident,
            "deadline": row["created"] + row["contract"]["delivery_seconds"],
            "checked_at": now,
            "finding": "No valid delivery verified by the committed deadline",
        }
        acceptance = {
            "passed": False,
            "utility": -1.0,
            "metric": "delivery_deadline",
            "artifact_sha256": None,
            "evidence_sha256": digest(evidence),
            "scope": "Missing verified delivery by deadline; no assumed cancellation or refund",
        }

        def fail():
            self.ledger.db.execute(
                "UPDATE action_tasks SET state='rejected',acceptance=?,updated=? WHERE id=?",
                (canonical(acceptance), now, ident),
            )
            self.event(ident, "rejected", {**acceptance, **evidence}, now)

        self.transaction(fail)
        return True

    def measurements(self, ident, phase):
        return [
            json.loads(raw)
            for (raw,) in self.ledger.db.execute(
                "SELECT observation FROM action_measurements WHERE task_id=? AND phase=? ORDER BY sample",
                (ident, phase),
            )
        ]

    def cancel_unexecuted(self, ident, now):
        def close():
            row = self.row(ident)
            if (
                row["state"] not in ("proposed", "reserved")
                or self.ledger.db.execute(
                    "SELECT 1 FROM action_operations WHERE task_id=?", (ident,)
                ).fetchone()
            ):
                raise ValueError(
                    "Disclosed work needs independent remote reconciliation"
                )
            self.ledger.db.execute(
                "UPDATE action_tasks SET state='cancelled',amount=0,updated=? WHERE id=?",
                (now, ident),
            )
            self.event(
                ident,
                "cancelled",
                {
                    "intent_id": row["intent_id"],
                    "goal_id": row["goal_id"],
                    "contract_sha256": digest(row["contract"]),
                    "evidence_level": "not_executed",
                    "reason": "Closed before external disclosure; no utility claimed",
                },
                now,
            )

        self.transaction(close)

    def probe(self, ident, phase, now, http=None):
        """Measure a precommitted resource in the buyer process, never a seller score."""
        from commerce.transport import request

        row = self.row(ident)
        rule = row["contract"].get("outcome")
        if not rule or phase not in ("baseline", "after"):
            return False
        if (
            phase == "baseline"
            and row["state"] != "proposed"
            or phase == "after"
            and not row["acceptance"]
        ):
            raise ValueError("Outcome observation is outside its committed phase")
        prior = self.measurements(ident, phase)
        limit = 1 if phase == "baseline" else rule["samples"]
        if (
            len(prior) >= limit
            or prior
            and now - prior[-1]["observed_at"] < rule["interval_seconds"]
        ):
            return False
        start = time.monotonic()
        # DNS/TLS/transport failures stay unknown; an explicit completed HTTP
        # failure is an observed failure. Unknown data is never a zero baseline.
        status, _, raw = (http or request)(rule["endpoint"])
        elapsed = (time.monotonic() - start) * 1000
        import hashlib

        response_hash = hashlib.sha256(raw).hexdigest()
        observed = {
            "observed_at": now,
            "status": status,
            "response_sha256": response_hash,
            "latency_ms": elapsed,
            "passed": status == 200
            and response_hash == rule["expected_sha256"]
            and elapsed <= rule["maximum_latency_ms"],
            "source": "buyer_pinned_https",
        }

        def save():
            self.ledger.db.execute(
                "INSERT INTO action_measurements VALUES(?,?,?,?,?)",
                (ident, phase, len(prior), now, canonical(observed)),
            )
            self.event(
                ident,
                "observation",
                {"phase": phase, "sample": len(prior), **observed},
                now,
            )

        self.transaction(save)
        return True

    def feedback(self, after=0, limit=64):
        if type(after) is not int or after < 0 or not 1 <= limit <= 64:
            raise ValueError("Feedback cursor or limit is invalid")
        return [
            {"seq": seq, **json.loads(raw)}
            for seq, raw in self.ledger.db.execute(
                "SELECT seq,payload FROM events WHERE seq>? AND json_extract(payload,'$.task_state') IN ('evaluated','cancelled') ORDER BY seq LIMIT ?",
                (after, limit),
            )
        ]

    def public(self):
        result = []
        for (ident,) in self.ledger.db.execute(
            "SELECT id FROM action_tasks ORDER BY created DESC,id LIMIT 40"
        ):
            row = self.row(ident)
            result.append(
                {
                    "id": ident,
                    "intent_id": row["intent_id"],
                    "title": row["contract"]["title"],
                    "purpose": row["contract"]["purpose"],
                    "kind": row["contract"]["kind"],
                    "provider": row["provider"],
                    "state": row["state"],
                    "created_at": row["created"],
                    "contract_sha256": digest(row["contract"]),
                    "neural_state_sha256": row["intent"]["neural_state_sha256"],
                    "committed_micro_usd": row["amount"],
                    "destination": (row["quote"] or {}).get("destination"),
                    "acceptance": row["acceptance"],
                    "settlement": {
                        k: v for k, v in row["settlement"].items() if k != "account"
                    }
                    if row["settlement"]
                    else None,
                    "outcome": row["outcome"],
                    "scope": "Provider payout, delivery and measured utility are separate evidence",
                }
            )
        return copy.deepcopy(result)

"""Included-capacity skill jobs with separate, finite compute authority.

The adapter runs only the reviewed data-only transform DSL. It has no network,
wallet, subprocess or arbitrary-code tool. Buyer-held evaluation rows never enter
the synthesis request; admission follows the buyer's exact delivery check.
"""

import json
import time
from datetime import datetime, timezone

from commerce.ledger import canonical
from commerce.tasks import (
    IDENT,
    contractor_brief,
    digest,
    execution_verifier_ceiling,
    finite,
    transform_verifier_ceiling,
    transform_work,
)


class LocalSkills:
    def __init__(self, ledger, config):
        from cognition.skills import SkillLibrary, normalize_config

        required = {
            "type",
            "id",
            "source_id",
            "enabled",
            "config",
            "mode",
            "maximum_work_per_job",
            "maximum_work_per_day",
            "maximum_jobs_per_day",
            "cooldown_seconds",
        }
        if (
            type(config) is not dict
            or set(config) != required
            or config["type"] != "local_skills"
            or type(config["id"]) is not str
            or not IDENT.fullmatch(config["id"])
            or type(config["enabled"]) is not bool
            or type(config["mode"]) is not str
            or config["mode"]
            not in (
                "fresh",
                "retrieval",
                "macro",
                "contextual",
                "factored",
                "retained_factored",
                "active",
                "active_audited",
            )
        ):
            raise ValueError("Local skill authority is malformed")
        for name, maximum in (
            ("maximum_work_per_job", 10_000_000),
            ("maximum_work_per_day", 100_000_000),
            ("maximum_jobs_per_day", 1000),
        ):
            if type(config[name]) is not int or not 1 <= config[name] <= maximum:
                raise ValueError(
                    "Local skill authority needs finite compute and job ceilings"
                )
        if (
            type(config["cooldown_seconds"]) is not int
            or not 0 <= config["cooldown_seconds"] <= 86400
        ):
            raise ValueError("Local skill cooldown is invalid")
        self.config, self.ledger = config, ledger
        self.id, self.account = config["id"], "included:" + config["id"]
        if config["mode"] in ("active", "active_audited"):
            from cognition.skill_language import library_factory, normalize_config

            SkillLibrary = library_factory
        self.dsl_config = normalize_config(config["config"])
        if (
            config["mode"] in ("factored", "retained_factored")
            and self.dsl_config["version"] != "safe-expression-v1"
        ):
            raise ValueError(
                "Factored expression search is not a v1 pipeline capability"
            )
        self.factory = SkillLibrary
        ledger.db.executescript("""
          CREATE TABLE IF NOT EXISTS local_skill_state(
            provider TEXT PRIMARY KEY, source_id TEXT NOT NULL,
            config_sha256 TEXT NOT NULL,state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS local_skill_jobs(
            job_id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,day TEXT NOT NULL,created REAL NOT NULL,
            budget INTEGER NOT NULL,state TEXT NOT NULL,proposal TEXT,delivery TEXT,
            admission TEXT,work INTEGER NOT NULL DEFAULT 0);
          CREATE INDEX IF NOT EXISTS local_skill_jobs_provider_day
            ON local_skill_jobs(provider,day);
        """)
        row = ledger.db.execute(
            "SELECT source_id,config_sha256,state FROM local_skill_state WHERE provider=?",
            (self.id,),
        ).fetchone()
        if row and (row[0] != config["source_id"] or row[1] != digest(self.dsl_config)):
            raise ValueError("A retained skill source or DSL cannot change identity")
        self.library = SkillLibrary(
            config["source_id"], self.dsl_config, json.loads(row[2]) if row else None
        )
        self.active = None
        self.active_policy = (
            "audited_retention"
            if config["mode"] == "active_audited"
            else "disagreement"
        )
        if config["mode"] in ("active", "active_audited"):
            from commerce.active_skill_runtime import ActiveSkillRuntime

            self.active = ActiveSkillRuntime(self)

    @property
    def enabled(self):
        return self.config["enabled"] is True

    def _transaction(self, operation):
        self.ledger.db.execute("BEGIN IMMEDIATE")
        try:
            result = operation()
            self.ledger.db.execute("COMMIT")
            return result
        except BaseException:
            self.ledger.db.execute("ROLLBACK")
            self._reload_library()
            raise

    def _reload_library(self):
        """Discard speculative Python objects after a failed durable commit."""
        saved = self.ledger.db.execute(
            "SELECT state FROM local_skill_state WHERE provider=?", (self.id,)
        ).fetchone()
        self.library = self.factory(
            self.config["source_id"],
            self.dsl_config,
            json.loads(saved[0]) if saved else None,
        )

    def _save_library(self):
        self.ledger.db.execute(
            "INSERT INTO local_skill_state VALUES(?,?,?,?) ON CONFLICT(provider) DO UPDATE SET state=excluded.state",
            (
                self.id,
                self.config["source_id"],
                digest(self.dsl_config),
                canonical(self.library.dump()),
            ),
        )

    def _terms(self, row):
        task = row["contract"]
        rule = task["acceptance"]
        if (
            not self.enabled
            or task["provider"] != self.id
            or task["kind"] != "compute"
            or task["maximum_micro_usd"] != 0
            or rule["type"]
            not in ("transform_program", "transform_execution", "transform_active")
            or (rule["type"] == "transform_active") != (self.active is not None)
            or normalize(rule["config"], active=self.active is not None)
            != self.dsl_config
            or rule["maximum_work"] > self.config["maximum_work_per_job"]
            or task.get("outcome")
            or rule["type"] == "transform_execution"
            and rule["source_id"] != self.config["source_id"]
        ):
            raise ValueError("Task exceeds the explicit nonfinancial skill authority")
        return rule

    def _capacity(self, now, budget):
        if not finite(now):
            raise ValueError("Local skill clock is invalid")
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        jobs, reserved, last = self.ledger.db.execute(
            "SELECT count(*),coalesce(sum(budget),0),max(created) FROM local_skill_jobs WHERE provider=? AND day=?",
            (self.id, day),
        ).fetchone()
        if (
            jobs >= self.config["maximum_jobs_per_day"]
            or reserved + budget > self.config["maximum_work_per_day"]
            or last is not None
            and now - last < self.config["cooldown_seconds"]
        ):
            raise ValueError("Included compute ceiling or cooldown reached")
        return day

    def quote(self, row, now):
        rule = self._terms(row)
        self._capacity(now, rule["maximum_work"])
        self._active_idle(row["id"])

        terms = {
            "provider": self.id,
            "source_id": self.config["source_id"],
            "job_id": row["id"],
            "config_sha256": digest(self.dsl_config),
            "maximum_work": rule["maximum_work"],
            "mode": self.config["mode"],
        }
        return {
            "provider": self.id,
            "account": self.account,
            "offer_id": digest(terms),
            "total_micro_usd": 0,
            "expires_at": now + 60,
            "checked_at": now,
            "destination": {"basis": "included_capacity", "provider": self.id},
            "evidence_sha256": digest(terms),
            "payment_basis": "included_capacity",
        }

    def _active_idle(self, job_id):
        if (
            self.active is not None
            and self.ledger.db.execute(
                "SELECT 1 FROM local_skill_jobs WHERE provider=? AND job_id!=? AND state!='evaluated' LIMIT 1",
                (self.id, job_id),
            ).fetchone()
        ):
            raise ValueError("The active provider already has an unfinished episode")

    def balance(self, now):
        return {
            "account": self.account,
            "available_micro_usd": 0,
            "checked_at": now,
            "verified": True,
        }

    def dispatch(self, row, now, operation_id):
        rule = self._terms(row)
        brief = contractor_brief(row["contract"], row["id"])
        safe = brief["acceptance"]
        external = "local-skill-" + row["id"][:48]
        request_sha256 = digest(brief)
        old = self.ledger.db.execute(
            "SELECT request_sha256,external_id FROM local_skill_jobs WHERE job_id=?",
            (row["id"],),
        ).fetchone()
        if old:
            if old[0] != request_sha256:
                raise ValueError("A disclosed local skill request cannot change")
            return {"job_id": row["id"], "external_id": old[1]}

        def reserve():
            day = self._capacity(now, rule["maximum_work"])
            self._active_idle(row["id"])
            self.ledger.db.execute(
                "INSERT INTO local_skill_jobs(job_id,provider,external_id,request_sha256,day,created,budget,state) VALUES(?,?,?,?,?,?,?,'disclosed')",
                (
                    row["id"],
                    self.id,
                    external,
                    request_sha256,
                    day,
                    now,
                    rule["maximum_work"],
                ),
            )
            self.ledger.event(
                {
                    "local_skill_job_id": row["id"],
                    "provider": self.id,
                    "phase": "compute_reserved",
                    "maximum_work": rule["maximum_work"],
                    "financial_transactions": 0,
                }
            )

        self._transaction(reserve)
        if rule["type"] == "transform_active":
            self.active.start(row, now)
            return {"job_id": row["id"], "external_id": external}
        # Solve sees training examples only. The bounded library contains no
        # external tools; an interrupted disclosure is never silently rerun.
        try:
            if rule["type"] == "transform_execution":
                proposal = self.library.serve(
                    safe["skill_id"],
                    safe["inputs"],
                    rule["maximum_work"] - execution_verifier_ceiling(self.dsl_config),
                )
                result = {
                    key: proposal[key]
                    for key in (
                        "status",
                        "outputs",
                        "skill_id",
                        "program_sha256",
                        "source_id",
                        "inputs_sha256",
                        "work",
                    )
                }
            else:
                proposal = self._synthesize(row, rule, safe)
                result = {
                    "program": proposal["program"],
                    "proposal_id": proposal["proposal_id"],
                    "work": proposal["work"],
                }
            delivery = canonical(
                {
                    "schema": "nuria.task-result.v1",
                    "job_id": row["id"],
                    "contract_sha256": digest(row["contract"]),
                    "result": result,
                }
            )
        except BaseException:
            self._reload_library()
            raise

        def persist():
            self._save_library()
            self.ledger.db.execute(
                "UPDATE local_skill_jobs SET state='delivered',proposal=?,delivery=?,work=? WHERE job_id=?",
                (
                    canonical(proposal),
                    delivery,
                    total_work(proposal["work"]),
                    row["id"],
                ),
            )
            self.ledger.event(
                {
                    "local_skill_job_id": row["id"],
                    "provider": self.id,
                    "phase": "computed",
                    "proposal_id": proposal.get("proposal_id"),
                    "program_sha256": proposal.get("program_sha256"),
                    "work": proposal["work"],
                    "financial_transactions": 0,
                }
            )

        self._transaction(persist)
        return {"job_id": row["id"], "external_id": external}

    def _synthesize(self, row, rule, safe):
        """Buyer-controlled, two-round counterexample exchange.

        Only a failing validation example may be appended to the next synthesis
        request. The final acceptance partition is not accessed here.
        """
        from cognition.skills import execute_checked

        inputs, outputs = list(safe["training_inputs"]), list(safe["training_outputs"])
        ceiling = transform_verifier_ceiling(self.dsl_config)
        used = 0
        attempts = []
        all_work = {
            "total": 0,
            "primitive_attempts": 0,
            "candidates": 0,
            "byte_blocks": 0,
            "errors": 0,
            "semantic_duplicates": 0,
        }

        def charge(work):
            nonlocal used
            used += total_work(work)
            for key in all_work:
                all_work[key] += work[key]

        for index in range(2):
            # Reserve the current CE check and untouched final check before
            # synthesis consumes any finite local compute allowance.
            budget = rule["maximum_work"] - used - 2 * ceiling
            if budget < 1:
                break
            proposal = self.library.solve(
                inputs,
                outputs,
                mode=self.config["mode"],
                budget=budget,
                features=row["intent"]["neural_context"],
            )
            charge(proposal["work"])
            attempt = {
                "proposal_id": proposal["proposal_id"],
                "program_sha256": proposal["program_sha256"],
                "search_work": proposal["work"],
                "validation_work": 0,
                "validation_correct": 0,
                "validation_rows": len(rule["validation_inputs"]),
            }
            attempts.append(attempt)
            if proposal["program"] is None:
                break
            observed, correct, validation_work, counterexample = [], 0, 0, None
            for value, target in zip(
                rule["validation_inputs"], rule["validation_outputs"], strict=True
            ):
                checked = execute_checked(proposal["program"], value, self.dsl_config)
                charge(checked["work"])
                validation_work += total_work(checked["work"])
                observed.append(checked["output"])
                correct += int(checked["output"] == target)
                if checked["output"] != target and counterexample is None:
                    counterexample = (value, target)
            attempt.update(validation_work=validation_work, validation_correct=correct)
            if counterexample is None:
                proposal["work"] = all_work
                proposal["attempts"] = attempts
                return proposal
            self.library.admit_verified(
                proposal["proposal_id"],
                {
                    "source_id": self.config["source_id"],
                    "proposal_id": proposal["proposal_id"],
                    "program_sha256": proposal["program_sha256"],
                    "evaluation_id": digest(
                        {"job": row["id"], "phase": "counterexample", "round": index}
                    ),
                    "accepted": False,
                    "correct_rows": correct,
                    "total_rows": len(observed),
                    "evidence_sha256": digest(
                        {
                            "inputs": rule["validation_inputs"],
                            "outputs": rule["validation_outputs"],
                            "observed": observed,
                        }
                    ),
                    "work": validation_work,
                },
            )
            inputs.append(counterexample[0])
            outputs.append(counterexample[1])
        return {
            "proposal_id": None,
            "program": None,
            "program_sha256": None,
            "work": all_work,
            "attempts": attempts,
            "source_id": self.config["source_id"],
            "config_sha256": digest(self.dsl_config),
            "solved_training": False,
            "mode": self.config["mode"],
            "exhausted": True,
        }

    def observe(self, row, now):
        if self.active is not None and self.enabled:
            deadline = time.monotonic() + 0.25
            maximum = 4 if self.active_policy == "audited_retention" else 1
            for step in range(maximum):
                # The deadline is checked between independently reserved
                # operations; it does not interrupt a bounded in-flight search.
                if step and time.monotonic() >= deadline:
                    break
                self.active.advance(row, now)
                phase = self.ledger.db.execute(
                    "SELECT state FROM active_skill_episodes WHERE job_id=?",
                    (row["id"],),
                ).fetchone()
                if phase is None or phase[0] != "ready":
                    break
        found = self.ledger.db.execute(
            "SELECT external_id,state,delivery,proposal FROM local_skill_jobs WHERE job_id=? AND provider=?",
            (row["id"], self.id),
        ).fetchone()
        if not found or found[1] in (
            "disclosed",
            "active",
            "active_executing",
            "active_uncertain",
        ):
            return {}
        external, _, delivery, proposal = found
        if self.active is not None:
            self.active.verify_selection(row, json.loads(proposal), delivery)
        evidence = digest(
            {
                "job_id": row["id"],
                "provider": self.id,
                "delivery_sha256": digest(delivery),
                "work": json.loads(proposal)["work"],
            }
        )
        return {
            "acknowledgment": {"job_id": row["id"], "external_id": external},
            "delivery": delivery.encode(),
            "settlement": {
                "job_id": row["id"],
                "account": self.account,
                "external_id": external,
                "verified": True,
                "basis": "included_capacity",
                "total_micro_usd": 0,
                "checked_at": now,
                "evidence_sha256": evidence,
                "scope": "Bounded local compute already included in project hosting; no financial payment or outside provider is implied",
            },
        }

    def admit(self, row):
        """Buyer-side admission after independently checked ActionLoop delivery."""
        found = self.ledger.db.execute(
            "SELECT proposal,admission FROM local_skill_jobs WHERE job_id=? AND provider=?",
            (row["id"], self.id),
        ).fetchone()
        if not found or not found[0] or not row["acceptance"]:
            return None
        if found[1]:
            return json.loads(found[1])
        proposal = json.loads(found[0])
        if row["contract"]["acceptance"]["type"] == "transform_active":
            return self.active.admit(row, proposal)
        if row["contract"]["acceptance"]["type"] == "transform_execution":
            return self._confirm_execution(row, proposal)
        if proposal["proposal_id"] is None:

            def failed():
                self.ledger.db.execute(
                    "UPDATE local_skill_jobs SET state='evaluated',work=? WHERE job_id=?",
                    (row["acceptance"]["total_work"], row["id"]),
                )

            self._transaction(failed)
            return None
        acceptance = row["acceptance"]
        receipt = {
            "source_id": self.config["source_id"],
            "proposal_id": proposal["proposal_id"],
            "program_sha256": proposal["program_sha256"],
            "evaluation_id": digest(
                {"contract": digest(row["contract"]), "job": row["id"]}
            ),
            "accepted": acceptance["exact_rows"] == acceptance["evaluation_rows"],
            "correct_rows": acceptance["exact_rows"],
            "total_rows": acceptance["evaluation_rows"],
            "evidence_sha256": acceptance["evaluation_sha256"],
            "work": acceptance["verifier_work"]
            + proposal["attempts"][-1]["validation_work"],
        }
        result = self.library.admit_verified(proposal["proposal_id"], receipt)

        def persist():
            self._save_library()
            self.ledger.db.execute(
                "UPDATE local_skill_jobs SET admission=?,state='evaluated',work=? WHERE job_id=?",
                (canonical(result), acceptance["total_work"], row["id"]),
            )
            self.ledger.event(
                {
                    "local_skill_job_id": row["id"],
                    "provider": self.id,
                    "phase": "skill_checked",
                    "admission": result,
                    "financial_transactions": 0,
                }
            )

        self._transaction(persist)
        return result

    def _confirm_execution(self, row, proposal):
        acceptance = row["acceptance"]
        receipt = {
            "kind": "transform_execution",
            "evaluation_id": digest(
                {"job": row["id"], "contract": digest(row["contract"])}
            ),
            "source_id": self.config["source_id"],
            "skill_id": proposal["skill_id"],
            "inputs_sha256": proposal["inputs_sha256"],
            "accepted": acceptance["passed"],
            "correct_rows": acceptance["exact_rows"],
            "total_rows": acceptance["evaluation_rows"],
            "evidence_sha256": acceptance["evaluation_sha256"],
            "work": acceptance["verifier_work"],
        }

        def persist():
            self.ledger.db.execute(
                "UPDATE local_skill_jobs SET admission=?,state='evaluated',work=? WHERE job_id=?",
                (canonical(receipt), acceptance["total_work"], row["id"]),
            )
            self.ledger.event(
                {
                    "local_skill_job_id": row["id"],
                    "provider": self.id,
                    "phase": "retained_skill_executed",
                    "admission": receipt,
                    "financial_transactions": 0,
                }
            )

        self._transaction(persist)
        return receipt

    def public(self):
        return {
            "id": self.id,
            "source_id": self.config["source_id"],
            "enabled": self.enabled,
            "payment_basis": "included_capacity",
            "financial_authority": False,
            "verified_programs": [
                {
                    "id": ident,
                    "program": record["program"],
                    "uses": record["uses"],
                    "uses_scope": "verified_learning_admissions",
                    "verified_serving_uses": self.ledger.db.execute(
                        "SELECT count(*) FROM local_skill_jobs j JOIN action_tasks a ON a.id=j.job_id WHERE j.provider=? AND a.state='evaluated' AND json_extract(j.admission,'$.kind')='transform_execution' AND json_extract(j.admission,'$.skill_id')=? AND json_extract(j.admission,'$.accepted')=1 AND json_extract(a.outcome,'$.evidence_level')='verified_outcome'",
                        (self.id, ident),
                    ).fetchone()[0],
                    "verified_active_uses": self.ledger.db.execute(
                        "SELECT count(*) FROM local_skill_jobs j JOIN action_tasks a ON a.id=j.job_id WHERE j.provider=? AND a.state='evaluated' AND json_extract(j.admission,'$.kind')='transform_active' AND json_extract(j.admission,'$.skill_id')=? AND json_extract(j.admission,'$.accepted')=1 AND json_extract(a.outcome,'$.evidence_level')='verified_outcome'",
                        (self.id, ident),
                    ).fetchone()[0],
                    "evidence_sha256": record["evidence_sha256"],
                }
                for ident, record in self.library.dump()["programs"].items()
            ],
            "jobs": [
                {
                    "job_id": ident,
                    "state": state,
                    "work": None
                    if state in ("disclosed", "active_executing", "active_uncertain")
                    else work,
                    "work_accounting": "unknown_after_disclosure"
                    if state in ("disclosed", "active_executing", "active_uncertain")
                    else "measured",
                    "proposal_id": json.loads(proposal).get("proposal_id")
                    if proposal
                    else None,
                    "admission": json.loads(admission) if admission else None,
                    "attempts": json.loads(proposal).get("attempts", [])
                    if proposal
                    else [],
                }
                for ident, state, work, proposal, admission in self.ledger.db.execute(
                    "SELECT job_id,state,work,proposal,admission FROM local_skill_jobs WHERE provider=? ORDER BY created DESC LIMIT 40",
                    (self.id,),
                )
            ],
            "active_episodes": self.active.public() if self.active is not None else [],
            "scope": "Typed programs learned from examples and applied to unlabelled requests. Withheld buyer checks gate retention and verified serving counts. Uses means verified learning admissions; serving counts come only from evaluated jobs. This provider has finite compute authority and no financial or network authority.",
        }


def normalize(config, *, active=False):
    if active:
        from cognition.skill_language import normalize_config
    else:
        from cognition.skills import normalize_config

    return normalize_config(config)


def total_work(report):
    return transform_work(report)

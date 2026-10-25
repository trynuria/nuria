"""Explicit included-capacity analytical jobs and separately qualified outcomes.

Enrollment belongs to the host. Questions, source identity and inference bounds
are fixed before a job; the model receives no checker or financial capability.
Full analytical artifacts remain in the private ledger. Delivery alone supplies
neither semantic acceptance nor learning credit.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

from commerce.ledger import canonical
from commerce.tasks import HASH, IDENT, contractor_brief, digest, finite, validate_task

VERSION = "nuria.grounded-analysis.v1"
PROOF_HASHES = {
    "analysis_artifact_sha256",
    "execution_receipt_sha256",
    "registry_sha256",
    "source_sha256",
    "model_sha256",
    "episode_sha256",
    "proposal_sha256",
}
PROOF_COUNTS = {"row_count", "column_count", "prompt_tokens", "completion_tokens"}
GRADE_FIELDS = {
    "semantic_accepted",
    "utility",
    "analysis_artifact_sha256",
    "checker_sha256",
    "checker_receipt_sha256",
    "grade_evidence_sha256",
}


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Analytical authority requires finite integer bounds")
    return value


def _hash(value):
    if type(value) is not str or not HASH.fullmatch(value):
        raise ValueError("Analytical evidence needs a full SHA256 identity")
    return value


def _identifier(value):
    if type(value) is not str or not IDENT.fullmatch(value):
        raise ValueError("Analytical identity is invalid")
    return value


def _workspace_digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def verify_registered_delivery(task, job_id, raw, resolver):
    """Resolve proof through an enrolled host, never a provider's self-score."""
    if not callable(resolver):
        raise ValueError("Registered analysis needs a trusted execution resolver")
    proof = resolver(task, job_id, raw)
    if type(proof) is not dict or set(proof) != PROOF_HASHES | PROOF_COUNTS:
        raise ValueError("Analytical execution proof has unexpected fields")
    for key in PROOF_HASHES:
        _hash(proof[key])
    for key in PROOF_COUNTS:
        _integer(proof[key], 0, 100_000_000)
    if (
        proof["registry_sha256"] != task["acceptance"]["registry_sha256"]
        or proof["model_sha256"] != task["acceptance"]["model_sha256"]
    ):
        raise ValueError("Analytical delivery uses another enrolled source or model")
    return {
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        **proof,
        "passed": True,
        "delivery_passed": True,
        "semantic_accepted": None,
        "utility": None,
        "metric": "registered_analysis_delivery",
        "scope": "A host-retained own query completed on the registered source. "
        "Semantic correctness and usefulness remain unknown until a "
        "separate registered host checker resolves the outcome.",
    }


class GroundedAnalysis:
    """Optional host-bound provider; absent runtime binding cannot execute."""

    def __init__(self, ledger, config, *, analyst=None, checkers=None):
        required = {
            "type",
            "id",
            "enabled",
            "registry_sha256",
            "source_id",
            "model_id",
            "memory_mode",
            "max_turns",
            "max_output_tokens",
            "max_tokens_per_turn",
            "max_tool_calls",
            "maximum_jobs_per_day",
            "maximum_completion_tokens_per_day",
            "cooldown_seconds",
            "questions",
        }
        if (
            type(config) is not dict
            or set(config) != required
            or config["type"] != "grounded_analysis"
            or type(config["enabled"]) is not bool
            or config["memory_mode"] not in ("none", "episode", "procedure")
        ):
            raise ValueError("Analytical enrollment is malformed")
        self.id = _identifier(config["id"])
        _hash(config["registry_sha256"])
        for key in ("source_id", "model_id"):
            if (
                type(config[key]) is not str
                or not 1 <= len(config[key].encode()) <= 256
            ):
                raise ValueError("Analytical source and model identities are required")
        for key, high in (
            ("max_turns", 24),
            ("max_output_tokens", 12000),
            ("max_tokens_per_turn", 4096),
            ("max_tool_calls", 24),
            ("maximum_jobs_per_day", 1000),
            ("maximum_completion_tokens_per_day", 10_000_000),
        ):
            _integer(config[key], 1, high)
        _integer(config["cooldown_seconds"], 0, 86400)
        if config["maximum_completion_tokens_per_day"] < config["max_output_tokens"]:
            raise ValueError("Daily analytical authority cannot cover one reservation")
        questions = config["questions"]
        if type(questions) is not dict or not 1 <= len(questions) <= 16:
            raise ValueError("Analytical questions must be explicitly enrolled")
        for goal_id, question in questions.items():
            _identifier(goal_id)
            if type(question) is not dict or set(question) != {
                "question",
                "db_id",
                "checker_id",
            }:
                raise ValueError("Analytical question fields are invalid")
            if (
                type(question["question"]) is not str
                or not 1 <= len(question["question"].encode()) <= 5000
            ):
                raise ValueError("An enrolled analytical question is required")
            _identifier(question["db_id"])
            if question["checker_id"] is not None:
                _identifier(question["checker_id"])
        if checkers is not None and (
            type(checkers) is not dict
            or any(
                type(key) is not str or not IDENT.fullmatch(key) or not callable(value)
                for key, value in checkers.items()
            )
        ):
            raise ValueError("Analytical checkers must be host-owned callables")
        self.ledger = ledger
        self.config = copy.deepcopy(config)
        self.analyst = analyst
        self.checkers = dict(checkers or {})
        self.account = "included:" + self.id
        self.registration_sha256 = digest(
            {k: v for k, v in config.items() if k != "enabled"}
        )
        self._check_binding()
        ledger.db.executescript("""
        CREATE TABLE IF NOT EXISTS grounded_analysis_providers(
          provider TEXT PRIMARY KEY, registration_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS grounded_analysis_jobs(
          job_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
          operation_id TEXT UNIQUE NOT NULL, external_id TEXT NOT NULL,
          contract_sha256 TEXT NOT NULL, goal_id TEXT NOT NULL,
          day TEXT NOT NULL, created REAL NOT NULL, reserved_completion INTEGER NOT NULL,
          state TEXT NOT NULL, artifact TEXT, delivery TEXT, grade TEXT);
        CREATE INDEX IF NOT EXISTS grounded_analysis_daily
          ON grounded_analysis_jobs(provider,day);
        """)
        found = ledger.db.execute(
            "SELECT registration_sha256 FROM grounded_analysis_providers WHERE provider=?",
            (self.id,),
        ).fetchone()
        if found and found[0] != self.registration_sha256:
            raise ValueError("An analytical provider cannot change its enrollment")
        ledger.db.execute(
            "INSERT OR IGNORE INTO grounded_analysis_providers VALUES(?,?)",
            (self.id, self.registration_sha256),
        )

    def _check_binding(self):
        if self.analyst is None:
            return
        from cognition.analyst_workspace import AnalystWorkspace
        from cognition.grounded_agent import GroundedAnalyst

        if not isinstance(self.analyst, GroundedAnalyst) or not isinstance(
            self.analyst.workspace, AnalystWorkspace
        ):
            raise ValueError("Analytical execution needs the reviewed host analyst")
        actual = self.analyst.configuration
        expected_binding = {
            "schema": "nuria.procedural-memory.v1",
            "source_id": self.config["source_id"],
            "model_id": self.config["model_id"],
            "tool_version": "nuria.analyst-workspace.v1",
        }
        if (
            actual["model_binding"] != expected_binding
            or actual["registry_sha256"] != self.config["registry_sha256"]
            or actual["memory_mode"] != self.config["memory_mode"]
            or any(
                actual[key] != self.config[key]
                for key in (
                    "max_turns",
                    "max_output_tokens",
                    "max_tokens_per_turn",
                    "max_tool_calls",
                )
            )
            or self.analyst.workspace._manifest["revision"] != self.config["source_id"]
            or any(
                question["db_id"] not in self.analyst.workspace._manifest["databases"]
                for question in self.config["questions"].values()
            )
        ):
            raise ValueError("Analytical runtime differs from its enrollment")

    @property
    def enabled(self):
        return self.config["enabled"] and self.analyst is not None

    def contract(
        self,
        goal_id,
        *,
        title,
        purpose,
        action,
        evaluation_nonce,
        delivery_seconds=3600,
    ):
        question = self.config["questions"][_identifier(goal_id)]
        return validate_task(
            {
                "id": goal_id,
                "title": title,
                "purpose": purpose,
                "brief": question["question"],
                "kind": "compute",
                "provider": self.id,
                "action": action,
                "maximum_micro_usd": 0,
                "delivery_seconds": delivery_seconds,
                "evaluation_nonce": evaluation_nonce,
                "acceptance": {
                    "type": "registered_analysis",
                    "registration_sha256": self.registration_sha256,
                    "registry_sha256": self.config["registry_sha256"],
                    "model_sha256": digest(self.config["model_id"]),
                    "db_id": question["db_id"],
                },
            }
        )

    def _terms(self, row):
        self._check_binding()
        task = validate_task(row["contract"])
        question = self.config["questions"].get(task["id"])
        if (
            not self.enabled
            or question is None
            or task["provider"] != self.id
            or task["kind"] != "compute"
            or task["maximum_micro_usd"] != 0
            or task.get("outcome")
            or task["brief"] != question["question"]
            or task["acceptance"]
            != {
                "type": "registered_analysis",
                "registration_sha256": self.registration_sha256,
                "registry_sha256": self.config["registry_sha256"],
                "model_sha256": digest(self.config["model_id"]),
                "db_id": question["db_id"],
            }
        ):
            raise ValueError("Task exceeds enrolled analytical authority")
        return question

    def _transaction(self, function):
        self.ledger.db.execute("BEGIN IMMEDIATE")
        try:
            value = function()
            self.ledger.db.execute("COMMIT")
            return value
        except BaseException:
            self.ledger.db.execute("ROLLBACK")
            raise

    def _capacity(self, now):
        if not finite(now):
            raise ValueError("Analytical clock is invalid")
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        jobs, reserved, last = self.ledger.db.execute(
            "SELECT count(*),coalesce(sum(reserved_completion),0),max(created) "
            "FROM grounded_analysis_jobs WHERE provider=? AND day=?",
            (self.id, day),
        ).fetchone()
        if (
            jobs >= self.config["maximum_jobs_per_day"]
            or reserved + self.config["max_output_tokens"]
            > self.config["maximum_completion_tokens_per_day"]
            or last is not None
            and now - last < self.config["cooldown_seconds"]
        ):
            raise ValueError("Analytical daily authority or cooldown reached")
        if self.ledger.db.execute(
            "SELECT 1 FROM grounded_analysis_jobs WHERE provider=? "
            "AND state IN ('reserved','uncertain') LIMIT 1",
            (self.id,),
        ).fetchone():
            raise ValueError("Unresolved analytical work needs explicit recovery")
        return day

    def quote(self, row, now):
        self._terms(row)
        self._capacity(now)
        terms = {
            "provider": self.id,
            "job_id": row["id"],
            "registration_sha256": self.registration_sha256,
            "reserved_completion_tokens": self.config["max_output_tokens"],
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

    def balance(self, now):
        return {
            "account": self.account,
            "available_micro_usd": 0,
            "checked_at": now,
            "verified": True,
        }

    def _job(self, job_id):
        found = self.ledger.db.execute(
            "SELECT external_id,contract_sha256,goal_id,state,artifact,delivery,grade "
            "FROM grounded_analysis_jobs WHERE job_id=? AND provider=?",
            (job_id, self.id),
        ).fetchone()
        if found is None:
            raise ValueError("Analytical job is missing")
        names = (
            "external_id",
            "contract_sha256",
            "goal_id",
            "state",
            "artifact",
            "delivery",
            "grade",
        )
        return dict(zip(names, found, strict=True))

    def dispatch(self, row, now, operation_id):
        question = self._terms(row)
        old = self.ledger.db.execute(
            "SELECT contract_sha256,external_id FROM grounded_analysis_jobs "
            "WHERE job_id=?",
            (row["id"],),
        ).fetchone()
        if old:
            if old[0] != digest(row["contract"]):
                raise ValueError("An analytical disclosure cannot change")
            return {"job_id": row["id"], "external_id": old[1]}
        operation = self.ledger.db.execute(
            "SELECT task_id,operation,state,request_sha256 FROM action_operations "
            "WHERE id=?",
            (operation_id,),
        ).fetchone()
        if operation != (
            row["id"],
            "dispatch",
            "disclosed",
            digest(contractor_brief(row["contract"], row["id"])),
        ):
            raise ValueError("Analysis requires a durably disclosed task operation")
        external = "grounded-" + row["id"][:48]

        def reserve():
            day = self._capacity(now)
            self.ledger.db.execute(
                "INSERT INTO grounded_analysis_jobs(job_id,provider,operation_id,"
                "external_id,contract_sha256,goal_id,day,created,reserved_completion,"
                "state) VALUES(?,?,?,?,?,?,?,?,?,'reserved')",
                (
                    row["id"],
                    self.id,
                    operation_id,
                    external,
                    digest(row["contract"]),
                    row["contract"]["id"],
                    day,
                    now,
                    self.config["max_output_tokens"],
                ),
            )
            self.ledger.event(
                {
                    "grounded_analysis_job_id": row["id"],
                    "provider": self.id,
                    "phase": "compute_reserved",
                    "reserved_completion_tokens": self.config["max_output_tokens"],
                    "financial_transactions": 0,
                }
            )

        self._transaction(reserve)
        try:
            report = self.analyst.run(
                job_id=row["id"],
                question=question["question"],
                db_id=question["db_id"],
                seed=0,
            )
            raw_artifact = canonical(report)
            if len(raw_artifact.encode()) > 4 * 1024 * 1024:
                raise ValueError("Analytical artifact exceeds its retained bound")
            if report.get("status") == "proposed":
                self._verify_execution(row, report)
                delivery = canonical(
                    {
                        "schema": "nuria.task-result.v1",
                        "job_id": row["id"],
                        "contract_sha256": digest(row["contract"]),
                        "result": {
                            "artifact_sha256": hashlib.sha256(
                                raw_artifact.encode()
                            ).hexdigest(),
                            "episode_id": report["episode_id"],
                            "proposal_sha256": report["proposal_sha256"],
                            "execution_receipt_sha256": report["selected_receipt"][
                                "result_sha256"
                            ],
                        },
                    }
                )
                state = "delivered"
            else:
                delivery = None
                state = (
                    "uncertain"
                    if report.get("model_usage", {}).get("unknown_additional_usage")
                    is True
                    else "unanswered"
                )

            def persist():
                self.ledger.db.execute(
                    "UPDATE grounded_analysis_jobs SET state=?,artifact=?,delivery=? "
                    "WHERE job_id=? AND state='reserved'",
                    (state, raw_artifact, delivery, row["id"]),
                )
                self.ledger.event(
                    {
                        "grounded_analysis_job_id": row["id"],
                        "provider": self.id,
                        "phase": state,
                        "analysis_artifact_sha256": hashlib.sha256(
                            raw_artifact.encode()
                        ).hexdigest(),
                        "financial_transactions": 0,
                    }
                )

            self._transaction(persist)
        except BaseException:
            self.ledger.db.execute(
                "UPDATE grounded_analysis_jobs SET state='uncertain' "
                "WHERE job_id=? AND state='reserved'",
                (row["id"],),
            )
            raise
        return {"job_id": row["id"], "external_id": external}

    def _verify_execution(self, row, report):
        question = self._terms(row)
        from cognition.analyst_workspace import MAX_DATABASE_BYTES, MAX_DOCUMENT_BYTES

        workspace = self.analyst.workspace
        registered = workspace._manifest["databases"][question["db_id"]]
        workspace._registered_file(registered["database"], MAX_DATABASE_BYTES)
        for document in registered["documents"].values():
            workspace._registered_file(document, MAX_DOCUMENT_BYTES)
        if type(report) is not dict or report.get("result_sha256") != digest(
            {k: v for k, v in report.items() if k != "result_sha256"}
        ):
            raise ValueError("Retained analytical result changed")
        if (
            report.get("job_id") != row["id"]
            or report.get("status") != "proposed"
            or report.get("accepted") is not None
            or report.get("configuration") != self.analyst.configuration
            or report.get("model_usage", {}).get("unknown_additional_usage")
            is not False
        ):
            raise ValueError("Analytical execution is not a complete bound proposal")
        usage = report["model_usage"]
        _integer(usage["prompt_tokens"], 0, 100_000_000)
        _integer(usage["completion_tokens"], 0, self.config["max_output_tokens"])
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            raise ValueError("Analytical accounting changed")
        self.analyst.memory.verify()
        state = self.analyst.memory.episode_state(report["episode_id"])
        origin, proposal = state["origin"], state["proposal"]
        receipt, sql = report.get("selected_receipt"), report.get("final_sql")
        if (
            origin["job_id"] != row["id"]
            or origin["question"] != question["question"]
            or origin["context_sha256"] != report.get("context_sha256")
            or proposal is None
            or proposal["final_sql"] != sql
            or proposal["proposal_sha256"] != report.get("proposal_sha256")
            or type(receipt) is not dict
            or receipt.get("status") != "ok"
            or receipt.get("complete") is not True
            or receipt.get("tool") != "query"
            or receipt.get("result_sha256")
            != _workspace_digest(
                {k: v for k, v in receipt.items() if k != "result_sha256"}
            )
            or receipt.get("source") != report.get("source")
            or receipt["source"].get("registry_sha256")
            != self.config["registry_sha256"]
            or receipt["source"].get("dataset_revision") != self.config["source_id"]
            or receipt["source"].get("db_id") != question["db_id"]
            or receipt["source"].get("db_sha256") != registered["database"]["sha256"]
            or not any(
                a["ok"]
                and a["sql"] == sql
                and a["result_sha256"] == receipt["result_sha256"]
                for a in state["actions"]
            )
            or not any(
                item.get("kind") == "tool_receipt" and item.get("receipt") == receipt
                for item in report.get("trace", [])
            )
        ):
            raise ValueError("Final answer lacks matching own retained query execution")
        if not isinstance(receipt.get("rows"), list) or not isinstance(
            receipt.get("columns"), list
        ):
            raise ValueError("Analytical query data is malformed")
        return receipt

    def resolve_delivery(self, task, job_id, raw):
        row = {"id": job_id, "contract": task}
        job = self._job(job_id)
        if (
            job["contract_sha256"] != digest(task)
            or job["delivery"] is None
            or raw != job["delivery"].encode()
        ):
            raise ValueError("Analytical delivery differs from its durable job")
        report = json.loads(job["artifact"])
        committed = self.ledger.db.execute(
            "SELECT json_extract(payload,'$.analysis_artifact_sha256') FROM events "
            "WHERE json_extract(payload,'$.grounded_analysis_job_id')=? "
            "AND json_extract(payload,'$.phase')='delivered'",
            (job_id,),
        ).fetchall()
        artifact_sha256 = hashlib.sha256(job["artifact"].encode()).hexdigest()
        if committed != [(artifact_sha256,)]:
            raise ValueError(
                "Analytical artifact differs from its original journal commitment"
            )
        receipt = self._verify_execution(row, report)
        envelope = json.loads(raw)
        if envelope["result"] != {
            "artifact_sha256": artifact_sha256,
            "episode_id": report["episode_id"],
            "proposal_sha256": report["proposal_sha256"],
            "execution_receipt_sha256": receipt["result_sha256"],
        }:
            raise ValueError("Analytical artifact commitment changed")
        return {
            "analysis_artifact_sha256": envelope["result"]["artifact_sha256"],
            "execution_receipt_sha256": receipt["result_sha256"],
            "registry_sha256": self.config["registry_sha256"],
            "source_sha256": digest(receipt["source"]),
            "model_sha256": digest(self.config["model_id"]),
            "episode_sha256": report["episode_id"],
            "proposal_sha256": report["proposal_sha256"],
            "row_count": len(receipt["rows"]),
            "column_count": len(receipt["columns"]),
            "prompt_tokens": report["model_usage"]["prompt_tokens"],
            "completion_tokens": report["model_usage"]["completion_tokens"],
        }

    def observe(self, row, now):
        try:
            job = self._job(row["id"])
        except ValueError:
            return {}
        result = {
            "acknowledgment": {"job_id": row["id"], "external_id": job["external_id"]}
        }
        if job["delivery"] is None:
            return result
        self.resolve_delivery(row["contract"], row["id"], job["delivery"].encode())
        result["delivery"] = job["delivery"].encode()
        result["settlement"] = {
            "job_id": row["id"],
            "account": self.account,
            "external_id": job["external_id"],
            "verified": True,
            "basis": "included_capacity",
            "total_micro_usd": 0,
            "checked_at": now,
            "evidence_sha256": digest(
                {"job_id": row["id"], "delivery_sha256": digest(job["delivery"])}
            ),
            "scope": "Previously enrolled local computation; no payment or external purchase.",
        }
        return result

    def grade(self, row):
        """Host-only semantic qualification. The model cannot register a checker."""
        question = self._terms(row)
        job = self._job(row["id"])
        if job["delivery"] is None:
            return None
        if job["state"] == "grade_unresolved":
            return None
        proof = self.resolve_delivery(
            row["contract"], row["id"], job["delivery"].encode()
        )
        if job["grade"] is not None:
            grade = json.loads(job["grade"])
            report = json.loads(job["artifact"])
            settled = self.analyst.memory.feedback(report["episode_id"])
            committed_grades = [
                {key: json.loads(raw)[key] for key in GRADE_FIELDS}
                for (raw,) in self.ledger.db.execute(
                    "SELECT payload FROM events "
                    "WHERE json_extract(payload,'$.grounded_analysis_job_id')=? "
                    "AND json_extract(payload,'$.phase')='host_graded'",
                    (row["id"],),
                )
            ]
            if (
                settled is None
                or settled.get("checker_id") != question["checker_id"]
                or grade != self._grade_receipt(proof, settled)
                or committed_grades != [grade]
            ):
                raise ValueError("A qualified analytical artifact cannot change")
            return grade
        checker_id = question["checker_id"]
        checker = self.checkers.get(checker_id)
        if checker is None:
            return None
        report = json.loads(job["artifact"])

        def verify(request):
            if request["proposal_sha256"] != report["proposal_sha256"]:
                raise ValueError("Analytical checker proposal differs")
            result = checker(
                copy.deepcopy(
                    {
                        "job_id": row["id"],
                        "question": question["question"],
                        "db_id": question["db_id"],
                        "artifact_sha256": proof["analysis_artifact_sha256"],
                        "selected_receipt": report["selected_receipt"],
                        "proposal_sha256": report["proposal_sha256"],
                        "source": report["source"],
                    }
                )
            )
            if type(result) is not dict or result.get("checker_id") != checker_id:
                raise ValueError("Checker identity differs from host enrollment")
            return result

        settled = self.analyst.memory.settle(
            report["episode_id"], verify_terminal=verify
        )
        if "accepted" not in settled:

            def unresolved():
                self.ledger.db.execute(
                    "UPDATE grounded_analysis_jobs SET state='grade_unresolved' "
                    "WHERE job_id=? AND grade IS NULL",
                    (row["id"],),
                )
                self.ledger.event(
                    {
                        "grounded_analysis_job_id": row["id"],
                        "provider": self.id,
                        "phase": "grade_unresolved",
                    }
                )

            self._transaction(unresolved)
            return None
        if settled["checker_id"] != checker_id:
            raise ValueError("Analytical memory was qualified by another checker")
        grade = self._grade_receipt(proof, settled)

        def persist():
            previous = self._job(row["id"])["grade"]
            if previous is not None and json.loads(previous) != grade:
                raise ValueError("An analytical grade cannot change")
            if previous is None:
                self.ledger.db.execute(
                    "UPDATE grounded_analysis_jobs SET state='graded',grade=? WHERE job_id=?",
                    (canonical(grade), row["id"]),
                )
                self.ledger.event(
                    {
                        "grounded_analysis_job_id": row["id"],
                        "provider": self.id,
                        "phase": "host_graded",
                        **grade,
                    }
                )

        self._transaction(persist)
        return grade

    def _grade_receipt(self, proof, settled):
        return {
            "semantic_accepted": settled["accepted"],
            "utility": int(settled["accepted"]),
            "analysis_artifact_sha256": proof["analysis_artifact_sha256"],
            "checker_sha256": digest(settled["checker_id"]),
            "checker_receipt_sha256": settled["receipt_sha256"],
            "grade_evidence_sha256": settled["evidence_sha256"],
        }

    def public(self):
        states = dict(
            self.ledger.db.execute(
                "SELECT state,count(*) FROM grounded_analysis_jobs WHERE provider=? GROUP BY state",
                (self.id,),
            )
        )
        return {
            "schema": VERSION,
            "provider": self.id,
            "enabled": self.enabled,
            "registration_sha256": self.registration_sha256,
            "registry_sha256": self.config["registry_sha256"],
            "model_sha256": digest(self.config["model_id"]),
            "job_states": states,
            "scope": "Included analytical compute with separately registered host grading. "
            "Input tokens are reported; completion limits are not equal-compute budgets.",
        }

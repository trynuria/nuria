"""Durable, metered diagnostic exchanges for local skill selection.

The buyer owns diagnostic labels and final checks. The routing session receives
only requested diagnostic labels; it never receives final rows or their targets.
Each physical operation has a durable reservation before execution. An
interrupted reservation is retained as uncertain and is not silently repeated.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from commerce.ledger import canonical
from commerce.tasks import (
    active_probe_receipt,
    active_verifier_ceiling,
    digest,
    transform_work,
)

MAX_ACTIONS = 256


class ActiveSkillRuntime:
    def __init__(self, adapter):
        self.adapter = adapter
        self.ledger = adapter.ledger
        self.ledger.db.executescript("""
          CREATE TABLE IF NOT EXISTS active_skill_episodes(
            job_id TEXT PRIMARY KEY,provider TEXT NOT NULL,terms_sha256 TEXT NOT NULL,
            initial_library_sha256 TEXT NOT NULL,initial_library TEXT NOT NULL,
            session TEXT NOT NULL,
            session_sha256 TEXT NOT NULL,state TEXT NOT NULL,next_action TEXT,
            work INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS active_skill_actions(
            id TEXT PRIMARY KEY,job_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL,request TEXT NOT NULL,request_sha256 TEXT NOT NULL,
            state TEXT NOT NULL,reserved_work INTEGER NOT NULL,response TEXT,
            work INTEGER,UNIQUE(job_id,ordinal));
        """)

    def _terms(self, row):
        import cognition.active_skills as router
        import cognition.skill_expressions as expressions
        import cognition.skill_factored as factored
        import cognition.skill_language as language
        import cognition.skills as skills
        import cognition.text_skills as text_skills
        import commerce.local_skills as adapter
        import commerce.tasks as tasks

        return digest(
            {
                "schema": "nuria.active-skill-runtime.v1",
                "provider": self.adapter.id,
                "source_id": self.adapter.config["source_id"],
                "policy": self.adapter.active_policy,
                "contract_sha256": digest(row["contract"]),
                "implementation": {
                    module.__name__: hashlib.sha256(
                        Path(module.__file__).read_bytes()
                    ).hexdigest()
                    for module in (
                        router,
                        language,
                        skills,
                        expressions,
                        factored,
                        text_skills,
                        adapter,
                        tasks,
                    )
                }
                | {"runtime": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            }
        )

    def _session(self, row, *, state=None, library_state=None):
        from cognition.active_skills import ActiveSkillSession
        from cognition.skill_language import (
            execute_checked,
            library_factory,
            normalize_config,
        )

        rule = row["contract"]["acceptance"]
        # The only data passed to the learner is the diagnostic input pool.
        return ActiveSkillSession(
            self.adapter.config["source_id"],
            self.adapter.dsl_config,
            list(rule["diagnostic_inputs"]),
            library_state=library_state,
            maximum_work=rule["maximum_work"]
            - active_verifier_ceiling(self.adapter.dsl_config),
            maximum_probes=rule["maximum_probes"],
            synthesis_work=rule["synthesis_work"],
            minimum_evidence=rule["minimum_evidence"],
            policy=self.adapter.active_policy,
            state=state,
            library_factory=library_factory,
            executor=execute_checked,
            config_validator=normalize_config,
        )

    def start(self, row, now):
        """Initialize only after the enclosing job's full budget is reserved."""
        initial = self.adapter.library.dump()
        session = self._session(row, library_state=initial)
        state = session.dump()
        state_hash = digest(state)

        def persist():
            self.ledger.db.execute(
                "INSERT INTO active_skill_episodes VALUES(?,?,?,?,?,?,?,?,NULL,?,?,?)",
                (
                    row["id"],
                    self.adapter.id,
                    self._terms(row),
                    digest(initial),
                    canonical(initial),
                    canonical(state),
                    state_hash,
                    "ready",
                    transform_work(session.work),
                    now,
                    now,
                ),
            )
            self.ledger.db.execute(
                "UPDATE local_skill_jobs SET state='active',work=? WHERE job_id=?",
                (transform_work(session.work), row["id"]),
            )
            self._event(row["id"], "initialized", state_hash)

        self.adapter._transaction(persist)

    def _event(self, job_id, phase, session_hash, **detail):
        self.ledger.event(
            {
                "active_job_id": job_id,
                "provider": self.adapter.id,
                "active_phase": phase,
                "active_session_sha256": session_hash,
                "financial_transactions": 0,
                **detail,
            }
        )

    def _read(self, row):
        found = self.ledger.db.execute(
            "SELECT terms_sha256,initial_library_sha256,initial_library,session,session_sha256,state,next_action,work FROM active_skill_episodes WHERE job_id=? AND provider=?",
            (row["id"], self.adapter.id),
        ).fetchone()
        if found is None:
            return None
        terms, initial, initial_raw, raw, state_hash, phase, action, work = found
        initial_state = json.loads(initial_raw)
        state = json.loads(raw)
        last = self.ledger.db.execute(
            "SELECT json_extract(payload,'$.active_session_sha256') FROM events WHERE json_extract(payload,'$.active_job_id')=? ORDER BY seq DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if (
            terms != self._terms(row)
            or digest(initial_state) != initial
            or digest(state) != state_hash
            or last != (state_hash,)
            or phase not in ("ready", "executing", "uncertain", "selected", "evaluated")
            or type(work) is not int
            or work < 0
        ):
            raise ValueError("Active episode differs from its committed checkpoint")
        session = self._session(row, state=state, library_state=initial_state)
        if transform_work(session.work) != work:
            raise ValueError("Active episode work differs from its checkpoint")
        next_action = json.loads(action) if action else None
        if next_action != state["pending"]:
            raise ValueError(
                "Active operation differs from the router's pending action"
            )
        completed_work = 0
        for (
            op_id,
            kind,
            request,
            request_hash,
            op_state,
            reserved,
            response,
            used,
        ) in self.ledger.db.execute(
            "SELECT id,kind,request,request_sha256,state,reserved_work,response,work FROM active_skill_actions WHERE job_id=? ORDER BY ordinal",
            (row["id"],),
        ):
            reserved_event = self.ledger.db.execute(
                "SELECT json_extract(payload,'$.request_sha256'),json_extract(payload,'$.maximum_work') FROM events WHERE json_extract(payload,'$.active_action_id')=? AND json_extract(payload,'$.active_phase')='operation_reserved' ORDER BY seq LIMIT 1",
                (op_id,),
            ).fetchone()
            if (
                digest(json.loads(request)) != request_hash
                or reserved_event != (request_hash, reserved)
                or kind not in ("decide", "probe", "synthesize")
                or op_state not in ("disclosed", "completed")
            ):
                raise ValueError("Active operation differs from its reservation")
            if op_state == "completed":
                completion = self.ledger.db.execute(
                    "SELECT json_extract(payload,'$.response_sha256'),json_extract(payload,'$.work') FROM events WHERE json_extract(payload,'$.active_action_id')=? AND json_extract(payload,'$.active_phase') IN ('operation_completed','selected') ORDER BY seq LIMIT 1",
                    (op_id,),
                ).fetchone()
                if (
                    type(used) is not int
                    or not 0 <= used <= reserved
                    or response is None
                    or completion != (digest(json.loads(response)), used)
                ):
                    raise ValueError("Completed active operation evidence changed")
                completed_work += used
            elif response is not None or used is not None:
                raise ValueError("Unresolved active operation claims a measured result")
        if completed_work != work:
            raise ValueError("Active checkpoint omits completed operation work")
        return {
            "session": session,
            "sha256": state_hash,
            "phase": phase,
            "next_action": next_action,
            "initial_library_sha256": initial,
        }

    def _reserve(self, row, saved, kind, request, now, reservation):
        ordinal = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (row["id"],)
        ).fetchone()[0]
        if ordinal >= MAX_ACTIONS:
            raise ValueError("Active episode reached its operation bound")
        ident = digest(
            {
                "job_id": row["id"],
                "ordinal": ordinal,
                "kind": kind,
                "request": request,
                "session_sha256": saved["sha256"],
            }
        )

        def persist():
            self.ledger.db.execute(
                "INSERT INTO active_skill_actions VALUES(?,?,?,?,?,?,'disclosed',?,NULL,NULL)",
                (
                    ident,
                    row["id"],
                    ordinal,
                    kind,
                    canonical(request),
                    digest(request),
                    reservation,
                ),
            )
            changed = self.ledger.db.execute(
                "UPDATE active_skill_episodes SET state='executing',updated=? WHERE job_id=? AND state='ready'",
                (now, row["id"]),
            )
            if changed.rowcount != 1:
                raise ValueError("Another operation owns this active episode")
            self.ledger.db.execute(
                "UPDATE local_skill_jobs SET state='active_executing' WHERE job_id=?",
                (row["id"],),
            )
            self._event(
                row["id"],
                "operation_reserved",
                saved["sha256"],
                active_action_id=ident,
                kind=kind,
                request_sha256=digest(request),
                maximum_work=reservation,
            )

        self.adapter._transaction(persist)
        return ident

    def _uncertain(self, row, saved, ident, now):
        def persist():
            self.ledger.db.execute(
                "UPDATE active_skill_episodes SET state='uncertain',updated=? WHERE job_id=?",
                (now, row["id"]),
            )
            self.ledger.db.execute(
                "UPDATE local_skill_jobs SET state='active_uncertain' WHERE job_id=?",
                (row["id"],),
            )
            self._event(
                row["id"],
                "execution_uncertain",
                saved["sha256"],
                active_action_id=ident,
                work_accounting="unknown_after_reservation",
            )

        self.adapter._transaction(persist)

    def advance(self, row, now):
        """Perform at most one disclosed operation, never replay an open one."""
        saved = self._read(row)
        if saved is None or saved["phase"] in ("uncertain", "selected", "evaluated"):
            return
        if saved["phase"] == "executing":
            pending = self.ledger.db.execute(
                "SELECT id FROM active_skill_actions WHERE job_id=? AND state='disclosed' ORDER BY ordinal DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            if pending is None:
                raise ValueError("Active execution reservation is missing")
            self._uncertain(row, saved, pending[0], now)
            return
        session = saved["session"]
        action = saved["next_action"]
        kind = action["kind"] if action else "decide"
        request = action or {"session_sha256": saved["sha256"]}
        remaining = (
            row["contract"]["acceptance"]["maximum_work"]
            - active_verifier_ceiling(self.adapter.dsl_config)
            - transform_work(session.work)
        )
        # The entire job ceiling stays reserved. Sub-reservations identify the
        # bounded liability of an interrupted individual operation.
        reservation = max(0, remaining)
        if kind == "probe":
            reservation = min(
                reservation,
                1 + (2 * self.adapter.dsl_config["max_input_bytes"] + 31) // 32,
            )
        elif kind == "synthesize":
            reservation = min(
                reservation, row["contract"]["acceptance"]["synthesis_work"]
            )
        ident = self._reserve(row, saved, kind, request, now, reservation)
        before = transform_work(session.work)
        try:
            if kind == "decide":
                response = session.next_action()
                next_action = (
                    response if response["kind"] in ("probe", "synthesize") else None
                )
            elif kind == "probe":
                response = active_probe_receipt(row["contract"], row["id"], action)
                response["source_id"] = self.adapter.config["source_id"]
                session.observe_probe(action["id"], response)
                next_action = None
            elif kind == "synthesize":
                response = session.perform_synthesis(action["id"])
                next_action = None
            else:
                raise ValueError("Active episode requested an unsupported operation")
            after = transform_work(session.work)
            if not 0 <= after - before <= reservation:
                raise ValueError("Active operation exceeded its disclosed reservation")
            state, result = session.dump(), session.result()
            state_hash = digest(state)
            terminal = result["status"] in ("proposed", "abstained")
            if terminal:
                self._selection(row, session, state_hash, result)

            def persist():
                self.ledger.db.execute(
                    "UPDATE active_skill_actions SET state='completed',response=?,work=? WHERE id=? AND state='disclosed'",
                    (canonical(response), after - before, ident),
                )
                self.ledger.db.execute(
                    "UPDATE active_skill_episodes SET session=?,session_sha256=?,state=?,next_action=?,work=?,updated=? WHERE job_id=?",
                    (
                        canonical(state),
                        state_hash,
                        "selected" if terminal else "ready",
                        canonical(next_action) if next_action else None,
                        after,
                        now,
                        row["id"],
                    ),
                )
                if terminal:
                    self._persist_selection(row, result)
                else:
                    self.ledger.db.execute(
                        "UPDATE local_skill_jobs SET state='active',work=? WHERE job_id=?",
                        (after, row["id"]),
                    )
                self._event(
                    row["id"],
                    "selected" if terminal else "operation_completed",
                    state_hash,
                    active_action_id=ident,
                    response_sha256=digest(response),
                    work=after - before,
                )

            self.adapter._transaction(persist)
        except BaseException:
            self._uncertain(row, saved, ident, now)
            raise

    def _selection(self, row, session, state_hash, result):
        # There is no final-row execution here. Only an immutable selection is
        # exported; ActionLoop.receive performs the later buyer check.
        probes = session.dump()["probe_receipts"]
        result.update(
            source_id=self.adapter.config["source_id"],
            session_sha256=state_hash,
            probe_receipts=[
                {
                    "action": self._probe_action(row["id"], ident),
                    "receipt": record["receipt"],
                }
                for ident, record in sorted(
                    probes.items(), key=lambda item: item[1]["action"]["sequence"]
                )
            ],
        )
        if result["selection"] == "retained":
            initial = session.library.dump()["programs"].get(result["program_sha256"])
            if initial is None or initial["program"] != result["program"]:
                raise ValueError("Selected retained program has no verified origin")

    def _probe_action(self, job_id, action_id):
        for (raw,) in self.ledger.db.execute(
            "SELECT request FROM active_skill_actions WHERE job_id=? AND kind='probe' AND state='completed' ORDER BY ordinal",
            (job_id,),
        ):
            action = json.loads(raw)
            if action["id"] == action_id:
                return action
        raise ValueError("Selected probe has no completed operation evidence")

    def _persist_selection(self, row, result):
        delivery = canonical(
            {
                "schema": "nuria.task-result.v1",
                "job_id": row["id"],
                "contract_sha256": digest(row["contract"]),
                "result": result,
            }
        )
        self.ledger.db.execute(
            "UPDATE local_skill_jobs SET state='delivered',proposal=?,delivery=?,work=? WHERE job_id=?",
            (canonical(result), delivery, transform_work(result["work"]), row["id"]),
        )

    def verify_selection(self, row, proposal, delivery):
        """A mutable delivery row cannot replace the checkpointed choice."""
        saved = self._read(row)
        if saved is None or saved["phase"] not in ("selected", "evaluated"):
            raise ValueError("Active delivery has no committed selection")
        expected = saved["session"].result()
        self._selection(row, saved["session"], saved["sha256"], expected)
        envelope = canonical(
            {
                "schema": "nuria.task-result.v1",
                "job_id": row["id"],
                "contract_sha256": digest(row["contract"]),
                "result": expected,
            }
        )
        if proposal != expected or delivery != envelope:
            raise ValueError("Active delivery differs from its committed selection")
        return saved

    def admit(self, row, proposal):
        """Commit one final outcome after the independent buyer acceptance."""
        delivery = self.ledger.db.execute(
            "SELECT delivery FROM local_skill_jobs WHERE job_id=? AND provider=?",
            (row["id"], self.adapter.id),
        ).fetchone()[0]
        saved = self.verify_selection(row, proposal, delivery)
        if saved["phase"] != "selected":
            raise ValueError("Active outcome was already consumed")
        if digest(self.adapter.library.dump()) != saved["initial_library_sha256"]:
            raise ValueError(
                "Another library update cannot be overwritten by an episode"
            )
        acceptance = row["acceptance"]
        if (
            acceptance.get("program_sha256") != proposal["program_sha256"]
            or acceptance.get("session_sha256") != saved["sha256"]
        ):
            raise ValueError("Buyer outcome names another active selection")
        checked = self.ledger.db.execute(
            "SELECT payload FROM events WHERE json_extract(payload,'$.action_task_id')=? AND json_extract(payload,'$.task_state') IN ('accepted','rejected') ORDER BY seq LIMIT 1",
            (row["id"],),
        ).fetchone()
        if (
            checked is None
            or {
                key: value
                for key, value in json.loads(checked[0]).items()
                if key not in ("action_task_id", "task_state", "at")
            }
            != acceptance
        ):
            raise ValueError("Active buyer acceptance changed after its commitment")
        session = saved["session"]
        core_receipt = None
        if proposal["proposal_id"] is not None:
            core_receipt = session.library.admit_verified(
                proposal["proposal_id"],
                {
                    "source_id": self.adapter.config["source_id"],
                    "proposal_id": proposal["proposal_id"],
                    "program_sha256": proposal["program_sha256"],
                    "evaluation_id": digest(
                        {"job": row["id"], "contract": digest(row["contract"])}
                    ),
                    "accepted": acceptance["exact_rows"]
                    == acceptance["evaluation_rows"],
                    "correct_rows": acceptance["exact_rows"],
                    "total_rows": acceptance["evaluation_rows"],
                    "evidence_sha256": acceptance["evaluation_sha256"],
                    "work": acceptance["verifier_work"],
                },
                retain=acceptance["passed"],
            )
        receipt = {
            "kind": "transform_active",
            "source_id": self.adapter.config["source_id"],
            "skill_id": proposal["program_sha256"],
            "selection": proposal["selection"],
            "accepted": acceptance["passed"],
            "correct_rows": acceptance["exact_rows"],
            "total_rows": acceptance["evaluation_rows"],
            "evidence_sha256": acceptance["evaluation_sha256"],
            "work": acceptance["total_work"],
            "admission": core_receipt,
        }
        self.adapter.library = session.library

        def persist():
            self.adapter._save_library()
            self.ledger.db.execute(
                "UPDATE local_skill_jobs SET state='evaluated',admission=?,work=? WHERE job_id=?",
                (canonical(receipt), acceptance["total_work"], row["id"]),
            )
            self.ledger.db.execute(
                "UPDATE active_skill_episodes SET state='evaluated' WHERE job_id=?",
                (row["id"],),
            )
            self._event(
                row["id"],
                "buyer_checked",
                saved["sha256"],
                accepted=acceptance["passed"],
                total_work=acceptance["total_work"],
                admission_sha256=digest(receipt),
            )

        self.adapter._transaction(persist)
        return receipt

    def public(self):
        """Bounded progress commitments; diagnostic values remain private."""
        records = []
        for ident, phase, raw, state_hash, work in self.ledger.db.execute(
            "SELECT job_id,state,session,session_sha256,work FROM active_skill_episodes WHERE provider=? ORDER BY created DESC LIMIT 40",
            (self.adapter.id,),
        ):
            state = json.loads(raw)
            records.append(
                {
                    "job_id": ident,
                    "state": phase,
                    "session_sha256": state_hash,
                    "probes_returned": len(state["probe_receipts"]),
                    "maximum_probes": state["terms"]["limits"]["maximum_probes"],
                    "known_work": work,
                    "reserved_job_work": self.ledger.db.execute(
                        "SELECT budget FROM local_skill_jobs WHERE job_id=?", (ident,)
                    ).fetchone()[0],
                    "unresolved_operation_reservation": self.ledger.db.execute(
                        "SELECT coalesce(sum(reserved_work),0) FROM active_skill_actions WHERE job_id=? AND state='disclosed'",
                        (ident,),
                    ).fetchone()[0],
                    "work_accounting": "unknown_after_reservation"
                    if phase in ("executing", "uncertain")
                    else "measured",
                    "selection": (state["selected"] or {}).get("selection"),
                    "program_sha256": (state["selected"] or {}).get("program_sha256"),
                }
            )
        return records

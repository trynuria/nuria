"""Transactional bridge from disclosed task evidence to learned search profiles.

This opt-in wrapper is not enrolled by the legacy task validator or worker. It
does not execute a tool or authenticate an external counterparty. Its caller
must enroll the separate source and execute the selected profile through the
metered skill-operation ledger. Only an independently committed local buyer
acceptance can train the policy; a caller-supplied success flag cannot.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
from pathlib import Path

from cognition.routed_text_search import (
    PROFILES,
    SEARCH_MODES,
    RoutedTextLibrary,
    implementation_hash,
)
from cognition.spiking_policy import SpikingDecisionPolicy
from cognition.text_skills import fields, normalize_config, text
from commerce.action_loop import ActionLoop
from commerce.ledger import canonical
from commerce.tasks import HASH, IDENT, active_probe_receipt, digest, transform_work

VERSION = "nuria.neural-routing-runtime.v1"
POLICY = "neural_routing_audited_v1"
MAX_WORK = 10_000_000
SOLVE_MODES = ("factored", "fresh", "retained_factored", "active")


def _hash(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError("Routing evidence requires complete SHA256 commitments")
    return value


def _vector(value, size):
    if (
        type(value) is not list
        or len(value) != size
        or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
            for v in value
        )
    ):
        raise ValueError(
            "Routing observations require the declared normalized dimensions"
        )
    # The numerical policy canonically stores float64 values. Normalize the
    # integer endpoints too so their commitment is identical at both layers.
    return [float(v) for v in value]


class RoutingDecision:
    """Keep policy, RNG, pending choices and verified feedback in one ledger.

    Feature extraction is a trusted source callable pinned by implementation
    hash. It sees disclosed audits, the source library, public language limits,
    profile caps and the 64 actual neural features retained in the task intent.
    It receives no current final rows, targets, task name or evaluation nonce.
    """

    def __init__(
        self,
        ledger,
        *,
        source_id,
        provider_id,
        feature_dimension,
        feature_encoder,
        profiles=None,
        solve_mode="retained_factored",
        search_mode="deduplicated",
        policy_configuration=None,
        reviewed_state=None,
    ):
        from cognition.spiking_policy import SOURCE

        if (
            not isinstance(source_id, str)
            or not SOURCE.fullmatch(source_id)
            or not isinstance(provider_id, str)
            or not IDENT.fullmatch(provider_id)
        ):
            raise ValueError("An explicit routing source and provider are required")
        if (
            source_id == "nuria:composed-record-repair-v1"
            or provider_id == "local-composed-records-v1"
        ):
            raise ValueError(
                "Neural routing cannot adopt the historical composed source"
            )
        if not inspect.isfunction(feature_encoder) or feature_encoder.__closure__:
            raise ValueError("A reviewed source feature encoder is required")
        if solve_mode not in SOLVE_MODES or search_mode not in SEARCH_MODES:
            raise ValueError("Routing execution modes require explicit reviewed values")
        self.ledger, self.loop = ledger, ActionLoop(ledger)
        self.source_id, self.provider_id = source_id, provider_id
        self.feature_dimension, self.feature_encoder = (
            feature_dimension,
            feature_encoder,
        )
        self.solve_mode, self.search_mode = solve_mode, search_mode
        self.profiles = dict(
            {name: 50000 for name in PROFILES} if profiles is None else profiles
        )
        if not 2 <= len(self.profiles) <= 16 or any(
            name not in PROFILES or type(cap) is not int or not 1 <= cap <= 50000
            for name, cap in self.profiles.items()
        ):
            raise ValueError(
                "Routing profiles require reviewed names and finite synthesis caps"
            )
        options = copy.deepcopy(policy_configuration or {})
        identity = {
            "source_id": source_id,
            "feature_dimension": feature_dimension,
            "arm_ids": list(self.profiles),
        }
        if any(
            key in options and options[key] != value for key, value in identity.items()
        ):
            raise ValueError("Policy configuration differs from the routing authority")
        options.update(identity)
        self.policy = SpikingDecisionPolicy(**options, state=reviewed_state)
        self.configuration = copy.deepcopy(self.policy.configuration)
        initial = self.policy.state()
        if initial["pending"]:
            raise ValueError(
                "A new routing authority cannot import orphan pending choices"
            )
        self.terms = {
            "schema": VERSION,
            "source_id": source_id,
            "provider_id": provider_id,
            "policy": POLICY,
            "configuration": self.configuration,
            "profiles": self.profiles,
            "solve_mode": self.solve_mode,
            "search_mode": self.search_mode,
            "initial_state_sha256": digest(initial),
            "initial_feedback_cursor": initial["feedback_cursor"],
            "feature_encoder_sha256": hashlib.sha256(
                Path(inspect.getfile(feature_encoder)).read_bytes()
            ).hexdigest(),
            "feature_encoder_name": feature_encoder.__qualname__,
            "feature_encoder_module": feature_encoder.__module__,
            "model_implementation_sha256": hashlib.sha256(
                Path(inspect.getfile(SpikingDecisionPolicy)).read_bytes()
            ).hexdigest(),
            "execution_implementation_sha256": implementation_hash(),
            "runtime_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "reward": "int(exact_buyer_acceptance) - 0.1 * committed_total_work / contract_total_ceiling",
            "financial_transactions": 0,
        }
        ledger.db.executescript("""
          CREATE TABLE IF NOT EXISTS neural_routing_state(
            provider TEXT PRIMARY KEY,source_id TEXT NOT NULL,terms TEXT NOT NULL,
            state TEXT NOT NULL,state_sha256 TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS neural_routing_choices(
            job_id TEXT PRIMARY KEY,provider TEXT NOT NULL,source_id TEXT NOT NULL,
            origin TEXT NOT NULL,origin_sha256 TEXT NOT NULL,decision TEXT NOT NULL,
            decision_sha256 TEXT NOT NULL,phase TEXT NOT NULL,feedback TEXT);
        """)
        saved = ledger.db.execute(
            "SELECT terms FROM neural_routing_state WHERE provider=?", (provider_id,)
        ).fetchone()
        if saved:
            if json.loads(saved[0]) != self.terms:
                raise ValueError(
                    "Existing routing policy authority or implementation differs"
                )
        else:
            self._transaction(
                lambda: ledger.db.execute(
                    "INSERT INTO neural_routing_state VALUES(?,?,?,?,?)",
                    (
                        provider_id,
                        source_id,
                        canonical(self.terms),
                        canonical(initial),
                        digest(initial),
                    ),
                )
            )
        self._reload()

    def _reviewed_implementation(self):
        """Refuse changed source or hidden callable state within this authority."""
        if (
            not inspect.isfunction(self.feature_encoder)
            or self.feature_encoder.__closure__
        ):
            raise ValueError("Routing requires its reviewed module feature function")
        current = {
            "feature_encoder_sha256": hashlib.sha256(
                Path(inspect.getfile(self.feature_encoder)).read_bytes()
            ).hexdigest(),
            "feature_encoder_name": self.feature_encoder.__qualname__,
            "feature_encoder_module": self.feature_encoder.__module__,
            "model_implementation_sha256": hashlib.sha256(
                Path(inspect.getfile(SpikingDecisionPolicy)).read_bytes()
            ).hexdigest(),
            "execution_implementation_sha256": implementation_hash(),
            "runtime_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "profiles": self.profiles,
            "solve_mode": self.solve_mode,
            "search_mode": self.search_mode,
        }
        if any(
            canonical(value) != canonical(self.terms[key])
            for key, value in current.items()
        ):
            raise ValueError("Routing implementation or execution authority changed")

    def _reload(self):
        row = self.ledger.db.execute(
            "SELECT source_id,terms,state,state_sha256 FROM neural_routing_state WHERE provider=?",
            (self.provider_id,),
        ).fetchone()
        if (
            row is None
            or row[0] != self.source_id
            or json.loads(row[1]) != self.terms
            or digest(json.loads(row[2])) != row[3]
        ):
            raise ValueError("Routing policy checkpoint integrity failed")
        self.policy = SpikingDecisionPolicy(
            **self.configuration, state=json.loads(row[2])
        )

    def _commit(self):
        self.ledger.db.execute("COMMIT")

    def _transaction(self, operation):
        if self.ledger.db.in_transaction:
            raise RuntimeError(
                "Routing policy changes require their own joint transaction"
            )
        self.ledger.db.execute("BEGIN IMMEDIATE")
        try:
            result = operation()
            self._commit()
            return result
        except BaseException:
            if self.ledger.db.in_transaction:
                self.ledger.db.execute("ROLLBACK")
            if self.ledger.db.execute(
                "SELECT 1 FROM neural_routing_state WHERE provider=?",
                (self.provider_id,),
            ).fetchone():
                self._reload()
            raise

    def _save(self):
        state = self.policy.state()
        self.ledger.db.execute(
            "UPDATE neural_routing_state SET state=?,state_sha256=? WHERE provider=?",
            (canonical(state), digest(state), self.provider_id),
        )

    def _event(self, **fields):
        self.ledger.event(
            {
                "schema": VERSION,
                "source_id": self.source_id,
                "provider": self.provider_id,
                **fields,
            }
        )

    def _find(self, *, key, value, phase_key, phase):
        rows = self.ledger.db.execute(
            "SELECT seq,hash,payload FROM events WHERE json_extract(payload,?)=? AND json_extract(payload,?)=? ORDER BY seq",
            ("$." + key, value, "$." + phase_key, phase),
        ).fetchall()
        if len(rows) != 1:
            raise ValueError(
                "Routing evidence requires one exact committed ledger event"
            )
        return rows[0][0], rows[0][1], json.loads(rows[0][2])

    def _task(self, job_id):
        _hash(job_id)
        row = self.loop.row(job_id)
        task, intent = row["contract"], row["intent"]
        rule = task.get("acceptance", {})
        if (
            task.get("provider") != self.provider_id
            or row["provider"] != self.provider_id
            or task.get("kind") != "compute"
            or type(task.get("maximum_micro_usd")) is not int
            or task.get("maximum_micro_usd") != 0
            or task.get("outcome")
            or rule.get("type") != "transform_active"
            or rule.get("policy") != POLICY
            or rule.get("source_id") != self.source_id
            or rule.get("config", {}).get("version") != "safe-text-v1"
        ):
            raise ValueError("Task is outside the separate neural-routing authority")
        config = normalize_config(rule["config"])
        for input_key, output_key in (
            ("diagnostic_inputs", "diagnostic_outputs"),
            ("heldout_inputs", "heldout_outputs"),
        ):
            inputs, outputs = rule.get(input_key), rule.get(output_key)
            if (
                type(inputs) is not list
                or type(outputs) is not list
                or not 1 <= len(inputs) <= config["max_rows"]
                or len(inputs) != len(outputs)
            ):
                raise ValueError("Routing records must retain the bounded text schema")
            for value, output in zip(inputs, outputs, strict=True):
                fields(value, config)
                text(output, config["max_input_bytes"])
        count = rule.get("maximum_probes")
        if (
            type(count) is not int
            or not 1 <= count <= 4
            or type(rule.get("minimum_evidence")) is not int
            or rule.get("minimum_evidence") != count
            or type(rule.get("maximum_work")) is not int
            or not 1 <= rule["maximum_work"] <= MAX_WORK
            or type(rule.get("synthesis_work")) is not int
            or not 1 <= rule["synthesis_work"] <= 50000
            or type(rule.get("diagnostic_inputs")) is not list
            or len(rule["diagnostic_inputs"]) < count
            or len(set(rule["diagnostic_inputs"][:count])) != count
            or type(rule.get("heldout_inputs")) is not list
            or type(rule.get("heldout_outputs")) is not list
            or not 1 <= len(rule["heldout_inputs"]) <= config["max_rows"]
            or len(rule["heldout_inputs"]) != len(rule["heldout_outputs"])
            or set(rule["diagnostic_inputs"][:count]) & set(rule["heldout_inputs"])
        ):
            raise ValueError("Routing audit count or work contract is not precommitted")
        if (
            intent.get("contract_sha256") != digest(task)
            or intent.get("provider") != self.provider_id
            or intent.get("goal_id") != task["id"]
            or intent.get("action") != task["action"]
            or intent.get("id") != row["intent_id"]
            or digest({key: value for key, value in intent.items() if key != "id"})
            != intent.get("id")
        ):
            raise ValueError("Task intent no longer binds its exact contract")
        _hash(intent.get("neural_state_sha256"))
        neural = _vector(intent.get("neural_features64"), 64)
        _, _, event = self._find(
            key="action_task_id", value=job_id, phase_key="task_state", phase="proposed"
        )
        if (
            event.get("intent_id") != row["intent_id"]
            or event.get("contract_sha256") != digest(task)
            or event.get("neural_state_sha256") != intent["neural_state_sha256"]
        ):
            raise ValueError("Task origin differs from its committed neural intent")
        return row, config, neural

    def _audits(self, row):
        rule = row["contract"]["acceptance"]
        actions = self.ledger.db.execute(
            "SELECT id,request,request_sha256,state,reserved_work,response,work FROM active_skill_actions WHERE job_id=? AND kind='probe' ORDER BY ordinal",
            (row["id"],),
        ).fetchall()
        if len(actions) != rule["maximum_probes"]:
            raise ValueError(
                "Exactly the declared paid audit count must precede routing"
            )
        audits, last_sequence = [], 0
        for index, (
            ident,
            request,
            request_hash,
            phase,
            reserved,
            response,
            work,
        ) in enumerate(actions):
            request = json.loads(request)
            if (
                phase != "completed"
                or digest(request) != request_hash
                or request.get("id") != ident
                or request.get("input_index") != index
                or response is None
            ):
                raise ValueError("Routing audit has no exact completed operation")
            response = json.loads(response)
            expected = {
                **active_probe_receipt(row["contract"], row["id"], request),
                "source_id": self.source_id,
            }
            before, _, disclosed = self._find(
                key="active_action_id",
                value=ident,
                phase_key="active_phase",
                phase="operation_reserved",
            )
            after, _, completed = self._find(
                key="active_action_id",
                value=ident,
                phase_key="active_phase",
                phase="operation_completed",
            )
            if (
                canonical(response) != canonical(expected)
                or work != expected["work"]
                or reserved != request["maximum_work"]
                or disclosed.get("request_sha256") != request_hash
                or disclosed.get("maximum_work") != reserved
                or completed.get("response_sha256") != digest(response)
                or completed.get("work") != work
                or after <= before
            ):
                raise ValueError(
                    "Routing audit differs from its paid disclosure evidence"
                )
            audits.append(
                {
                    "input": request["input"],
                    "output": response["output"],
                    "receipt_sha256": digest(response),
                    "work": work,
                }
            )
            last_sequence = max(last_sequence, after)
        return audits, last_sequence

    def _saved(self, job_id):
        row = self.ledger.db.execute(
            "SELECT provider,source_id,origin,origin_sha256,decision,decision_sha256,phase,feedback FROM neural_routing_choices WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        origin, decision = json.loads(row[2]), json.loads(row[4])
        if (
            row[0] != self.provider_id
            or row[1] != self.source_id
            or digest(origin) != row[3]
            or digest(decision) != row[5]
            or row[6] not in ("selected", "learned")
            or decision.get("schema") != VERSION
            or decision.get("source_id") != self.source_id
            or decision.get("provider") != self.provider_id
            or decision.get("job_id") != job_id
            or canonical(decision.get("origin")) != canonical(origin)
        ):
            raise ValueError("Routing choice checkpoint integrity failed")
        execution = decision.get("execution")
        if (
            type(execution) is not dict
            or set(execution)
            != {"config", "library_before", "inputs", "outputs", "mode", "search_mode"}
            or digest(execution["config"]) != origin.get("config_sha256")
            or execution["mode"] != origin.get("solve_mode")
            or execution["mode"] != self.solve_mode
            or execution["search_mode"] != origin.get("search_mode")
            or execution["search_mode"] != self.search_mode
            or digest(execution["library_before"])
            != origin.get("library_before_sha256")
            or digest([execution["inputs"], execution["outputs"]])
            != origin.get("training_sha256")
            or origin.get("profile_caps_sha256") != digest(self.profiles)
            or any(
                origin.get(key) != self.terms[key]
                for key in (
                    "feature_encoder_sha256",
                    "feature_encoder_name",
                    "feature_encoder_module",
                )
            )
        ):
            raise ValueError(
                "Routing execution snapshot differs from its originating authority"
            )
        RoutedTextLibrary(
            self.source_id,
            execution["config"],
            state=execution["library_before"],
            search_mode=self.search_mode,
        )
        _, _, selected = self._find(
            key="job_id", value=job_id, phase_key="routing_phase", phase="selected"
        )
        if (
            selected.get("source_id") != self.source_id
            or selected.get("provider") != self.provider_id
            or selected.get("decision_sha256") != digest(decision)
        ):
            raise ValueError("Routing decision differs from its retained journal")
        feedback = json.loads(row[7]) if row[7] else None
        if row[6] == "learned":
            _, _, learned = self._find(
                key="job_id", value=job_id, phase_key="routing_phase", phase="learned"
            )
            if (
                feedback is None
                or learned.get("source_id") != self.source_id
                or learned.get("provider") != self.provider_id
                or learned.get("feedback_sha256") != digest(feedback)
                or learned.get("receipt_id") != feedback.get("buyer_identity_sha256")
                or feedback.get("job_id") != job_id
                or feedback.get("source_id") != self.source_id
                or feedback.get("receipt", {}).get("decision_id")
                != decision["choice"]["decision_id"]
            ):
                raise ValueError("Routing feedback differs from its retained journal")
        elif feedback is not None:
            raise ValueError("An untrained routing choice cannot contain feedback")
        return {
            "origin": origin,
            "decision": decision,
            "phase": row[6],
            "feedback": feedback,
        }

    def decide(self, job_id):
        """Commit the actual spike-derived profile before any synthesis call."""
        return copy.deepcopy(self._transaction(lambda: self._decide(job_id)))

    def _decide(self, job_id):
        self.ledger.verify(full=True)
        self._reviewed_implementation()
        self._reload()
        row, config, neural = self._task(job_id)
        audits, audit_sequence = self._audits(row)
        origin = {
            "contract_sha256": digest(row["contract"]),
            "intent_id": row["intent_id"],
            "neural_state_sha256": row["intent"]["neural_state_sha256"],
            "neural_features_sha256": digest(neural),
            "audits_sha256": digest(audits),
            "last_audit_sequence": audit_sequence,
            "config_sha256": digest(config),
            "solve_mode": self.solve_mode,
            "search_mode": self.search_mode,
            "profile_caps_sha256": digest(self.profiles),
            "feature_encoder_sha256": self.terms["feature_encoder_sha256"],
            "feature_encoder_name": self.terms["feature_encoder_name"],
            "feature_encoder_module": self.terms["feature_encoder_module"],
            "training_sha256": digest(
                [
                    [item["input"] for item in audits],
                    [item["output"] for item in audits],
                ]
            ),
        }
        previous = self._saved(job_id)
        if previous:
            if any(previous["origin"][key] != value for key, value in origin.items()):
                raise ValueError(
                    "An already committed routing observation cannot change"
                )
            return copy.deepcopy(previous["decision"])
        state_row = self.ledger.db.execute(
            "SELECT source_id,config_sha256,state FROM local_skill_state WHERE provider=?",
            (self.provider_id,),
        ).fetchone()
        if (
            state_row is None
            or state_row[0] != self.source_id
            or state_row[1] != digest(config)
        ):
            raise ValueError("Routing requires its own source-specific skill library")
        library_state = json.loads(state_row[2])
        RoutedTextLibrary(
            self.source_id, config, state=library_state, search_mode=self.search_mode
        )
        legal = [
            name
            for name, cap in self.profiles.items()
            if cap <= row["contract"]["acceptance"]["synthesis_work"]
        ]
        if not legal:
            raise ValueError("The task has no legally budgeted routing profile")
        features = _vector(
            self.feature_encoder(
                [
                    {key: item[key] for key in ("input", "output", "receipt_sha256")}
                    for item in audits
                ],
                copy.deepcopy(library_state),
                copy.deepcopy(config),
                copy.deepcopy(neural),
                copy.deepcopy(self.profiles),
            ),
            self.feature_dimension,
        )
        origin.update(
            library_before_sha256=digest(library_state), feature_sha256=digest(features)
        )
        decision_id = digest(
            {"schema": VERSION, "source_id": self.source_id, "job_id": job_id, **origin}
        )
        before = self.policy.state_hash()

        def commit():
            choice = self.policy.choose(features, decision_id, legal)
            if (
                choice.get("decision_id") != decision_id
                or choice.get("arm") not in legal
                or choice.get("source_id") != self.source_id
                or choice.get("features_sha256") != digest(features)
            ):
                raise ValueError(
                    "The numerical policy returned an unbound routing choice"
                )
            decision = {
                "schema": VERSION,
                "job_id": job_id,
                "source_id": self.source_id,
                "provider": self.provider_id,
                "origin": origin,
                "choice": choice,
                "profile_maximum_work": self.profiles[choice["arm"]],
                "execution_implementation_sha256": self.terms[
                    "execution_implementation_sha256"
                ],
                "execution": {
                    "config": copy.deepcopy(config),
                    "library_before": copy.deepcopy(library_state),
                    "inputs": [item["input"] for item in audits],
                    "outputs": [item["output"] for item in audits],
                    "mode": self.solve_mode,
                    "search_mode": self.search_mode,
                },
                "before_policy_state_sha256": before,
                "after_policy_state_sha256": self.policy.state_hash(),
            }
            self.ledger.db.execute(
                "INSERT INTO neural_routing_choices VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    self.provider_id,
                    self.source_id,
                    canonical(origin),
                    digest(origin),
                    canonical(decision),
                    digest(decision),
                    "selected",
                    None,
                ),
            )
            self._save()
            self._event(
                routing_phase="selected",
                job_id=job_id,
                decision_id=decision_id,
                decision_sha256=digest(decision),
            )
            return decision

        return commit()

    def _buyer(self, row, saved):
        acceptance = row["acceptance"]
        if acceptance is None:
            return None
        decision, choice = saved["decision"], saved["decision"]["choice"]
        selected_seq, _, selected_event = self._find(
            key="job_id", value=row["id"], phase_key="routing_phase", phase="selected"
        )
        if selected_event.get("decision_sha256") != digest(decision):
            raise ValueError(
                "Routing selection differs from its pre-execution commitment"
            )
        phase = "accepted" if acceptance.get("passed") is True else "rejected"
        if row["state"] != phase:
            raise ValueError("Buyer task phase and acceptance disagree")
        buyer_seq, buyer_hash, event = self._find(
            key="action_task_id", value=row["id"], phase_key="task_state", phase=phase
        )
        if canonical(
            {
                key: value
                for key, value in event.items()
                if key not in ("action_task_id", "task_state", "at")
            }
        ) != canonical(acceptance):
            raise ValueError("Buyer acceptance differs from its committed evidence")
        found = self.ledger.db.execute(
            "SELECT provider,proposal,delivery FROM local_skill_jobs WHERE job_id=?",
            (row["id"],),
        ).fetchone()
        if (
            found is None
            or found[0] != self.provider_id
            or found[1] is None
            or found[2] is None
        ):
            raise ValueError("The routing outcome has no actual committed delivery")
        proposal, raw = json.loads(found[1]), found[2].encode()
        if proposal.get("config_sha256") != saved["origin"]["config_sha256"]:
            raise ValueError(
                "The executed configuration differs from the committed task"
            )
        if proposal.get("mode") != saved["origin"]["solve_mode"]:
            raise ValueError(
                "The executed solve mode differs from the committed authority"
            )
        if proposal.get("search_mode") != saved["origin"]["search_mode"]:
            raise ValueError(
                "The executed search mode differs from the committed authority"
            )
        if (
            proposal.get("routing_library_before_sha256")
            != saved["origin"]["library_before_sha256"]
        ):
            raise ValueError(
                "The executed library snapshot differs from its originating decision"
            )
        if (
            proposal.get("routing_training_sha256")
            != saved["origin"]["training_sha256"]
        ):
            raise ValueError(
                "The executed diagnostic examples differ from the paid disclosures"
            )
        action = self.ledger.db.execute(
            "SELECT id,request,request_sha256,state,reserved_work,response,work FROM active_skill_actions WHERE job_id=? AND kind='synthesize'",
            (row["id"],),
        ).fetchall()
        if len(action) != 1:
            raise ValueError(
                "Routing feedback requires exactly one selected-profile synthesis"
            )
        ident, request, request_hash, state, reserved, response, work = action[0]
        request = json.loads(request)
        started, _, disclosure = self._find(
            key="active_action_id",
            value=ident,
            phase_key="active_phase",
            phase="operation_reserved",
        )
        finished, _, completion = self._find(
            key="active_action_id",
            value=ident,
            phase_key="active_phase",
            phase="operation_completed",
        )
        if (
            state != "completed"
            or digest(request) != request_hash
            or request.get("id") != ident
            or request.get("maximum_work") != decision["profile_maximum_work"]
            or reserved != decision["profile_maximum_work"]
            or response is None
            or canonical(json.loads(response)) != canonical(proposal)
            or proposal.get("source_id") != self.source_id
            or proposal.get("routing_profile") != choice["arm"]
            or proposal.get("routing_implementation_sha256")
            != decision["execution_implementation_sha256"]
            or proposal.get("routing_execution_sha256")
            != digest(
                {
                    key: value
                    for key, value in proposal.items()
                    if key != "routing_execution_sha256"
                }
            )
            or transform_work(proposal["work"]) != work
            or not 0 <= work <= reserved
            or disclosure.get("request_sha256") != request_hash
            or disclosure.get("maximum_work") != reserved
            or completion.get("response_sha256") != digest(proposal)
            or completion.get("work") != work
            or not selected_seq < started < finished < buyer_seq
        ):
            raise ValueError(
                "The actual metered profile execution does not bind the neural choice"
            )
        rule = row["contract"]["acceptance"]
        audits, _ = self._audits(row)
        total, ceiling = acceptance.get("total_work"), rule["maximum_work"]
        count, correct = acceptance.get("evaluation_rows"), acceptance.get("exact_rows")
        completed_operations = self.ledger.db.execute(
            "SELECT count(*),coalesce(sum(work),0),sum(state!='completed') "
            "FROM active_skill_actions WHERE job_id=?",
            (row["id"],),
        ).fetchone()
        if (
            acceptance.get("source_id") != self.source_id
            or acceptance.get("program_sha256") != proposal.get("program_sha256")
            or acceptance.get("artifact_sha256") != hashlib.sha256(raw).hexdigest()
            or acceptance.get("maximum_work") != ceiling
            or type(total) is not int
            or not 0 <= total <= ceiling
            or type(count) is not int
            or count < 1
            or count != len(rule["heldout_inputs"])
            or type(correct) is not int
            or not 0 <= correct <= count
            or type(acceptance.get("passed")) is not bool
            or acceptance["passed"]
            != (proposal.get("program") is not None and correct == count)
            or acceptance.get("probe_work") != sum(item["work"] for item in audits)
            or sorted(acceptance.get("probe_receipt_sha256s", []))
            != sorted(item["receipt_sha256"] for item in audits)
            or completed_operations[0] != len(audits) + 1
            or completed_operations[2] != 0
            or type(acceptance.get("solver_work")) is not int
            or acceptance["solver_work"] != completed_operations[1]
            or acceptance["solver_work"] != work + sum(item["work"] for item in audits)
            or type(acceptance.get("verifier_work")) is not int
            or acceptance["verifier_work"] < 0
            or total != acceptance.get("solver_work", -1) + acceptance["verifier_work"]
        ):
            raise ValueError(
                "Buyer acceptance does not bind actual result and complete work"
            )
        result = json.loads(found[2])
        if (
            result.get("job_id") != row["id"]
            or result.get("contract_sha256") != digest(row["contract"])
            or result.get("result", {}).get("program_sha256")
            != proposal.get("program_sha256")
            or canonical(result.get("result", {}).get("program"))
            != canonical(proposal.get("program"))
            or proposal.get("program_sha256")
            != (
                digest(proposal["program"])
                if proposal.get("program") is not None
                else None
            )
        ):
            raise ValueError("Buyer delivery belongs to another result or contract")
        return {
            "buyer_event_seq": buyer_seq,
            "buyer_event_sha256": buyer_hash,
            "acceptance_sha256": digest(acceptance),
            "artifact_sha256": acceptance["artifact_sha256"],
            "execution_sha256": proposal["routing_execution_sha256"],
            "passed": acceptance["passed"],
            "outcome": (
                "abstained"
                if proposal.get("program") is None
                else "success"
                if acceptance["passed"]
                else "incorrect"
            ),
            "correct_rows": correct,
            "total_rows": count,
            "total_work": total,
            "maximum_work": ceiling,
            "reward": int(acceptance["passed"]) - 0.1 * total / ceiling,
        }

    def complete(self, job_id):
        """Train only the selected origin, using the authoritative buyer ledger."""
        return copy.deepcopy(self._transaction(lambda: self._complete(job_id)))

    def _complete(self, job_id):
        self.ledger.verify(full=True)
        self._reviewed_implementation()
        self._reload()
        row, _, _ = self._task(job_id)
        saved = self._saved(job_id)
        if saved is None:
            raise ValueError("A committed routing decision must precede feedback")
        buyer = self._buyer(row, saved)
        if buyer is None:
            return None
        identity = {
            "source_id": self.source_id,
            "job_id": job_id,
            "decision_id": saved["decision"]["choice"]["decision_id"],
            "contract_sha256": digest(row["contract"]),
            "evaluation_nonce": row["contract"]["evaluation_nonce"],
            **buyer,
        }
        if saved["phase"] == "learned":
            if saved["feedback"]["buyer_identity_sha256"] != digest(identity):
                raise ValueError("An already trained buyer outcome changed")
            return copy.deepcopy(saved["feedback"])

        def commit():
            before = self.policy.state_hash()
            receipt = {
                "seq": self.policy.feedback_cursor + 1,
                "decision_id": identity["decision_id"],
                "arm": saved["decision"]["choice"]["arm"],
                "receipt_id": digest(identity),
                "reward": buyer["reward"],
            }
            result = self.policy.learn(receipt)
            feedback = {
                "schema": VERSION,
                "job_id": job_id,
                "source_id": self.source_id,
                "buyer_identity_sha256": digest(identity),
                "buyer": buyer,
                "receipt": receipt,
                "policy_result": result,
                "before_policy_state_sha256": before,
                "after_policy_state_sha256": self.policy.state_hash(),
                "financial_transactions": 0,
            }
            self.ledger.db.execute(
                "UPDATE neural_routing_choices SET phase='learned',feedback=? WHERE job_id=?",
                (canonical(feedback), job_id),
            )
            self._save()
            self._event(
                routing_phase="learned",
                job_id=job_id,
                receipt_id=receipt["receipt_id"],
                feedback_sha256=digest(feedback),
            )
            return feedback

        return commit()

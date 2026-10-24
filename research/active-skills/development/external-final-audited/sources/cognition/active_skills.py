"""Bounded active routing over buyer-verified executable skills.

Only visible diagnostic inputs and explicitly returned probe labels enter this
controller. The experimental disagreement policy evaluates a retained portfolio.
The audited-retention policy requests two distinct labels, then uses the existing
solver's direct retained lookup and factored fallback without a full portfolio. New synthesis requires at least two distinct labelled diagnostic inputs;
reusing an already verified program may use one audit label. A proposal is not a
verification result; untouched final acceptance and
library admission belong to the buyer. Tool units count declared execution,
routing, disclosure and search operations, not currency or all process CPU.

A caller must reserve a job allowance before construction and persist the
pending action before invoking its buyer or synthesis tool. Dumped state is
private and source-bound. Its checksum detects accidental changes; it is not a
signature or protection against a writer who can replace the whole checkpoint.
"""

from __future__ import annotations

import copy
import json
import re

from cognition.skills import fingerprint

VERSION = "active-skill-session-v1"
HASH = re.compile(r"[a-f0-9]{64}\Z")
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
COSTS = ("portfolio_work", "routing_work", "probe_work", "synthesis_work")
COUNTERS = (
    "candidate_evaluations",
    "planning_operations",
    "probe_requests",
    "primitive_attempts",
    "candidates",
    "byte_blocks",
    "errors",
    "semantic_duplicates",
    "constraint_operations",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def bounded(value, low, high):
    require(
        type(value) is int and low <= value <= high,
        "Active-skill integer is outside its bound",
    )
    return value


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def blocks(value):
    return (len(value.encode()) + 31) // 32


def probe_cost(value, output):
    """Declared buyer disclosure units, charged once per returned label."""
    return 1 + (len(value.encode()) + len(output.encode()) + 31) // 32


def defaults():
    # A new language dispatcher may extend the registry without changing the
    # frozen expression learner. Do not hide an import failure inside it.
    try:
        from cognition.skill_language import (
            execute_checked,
            library_factory,
            normalize_config,
        )

        return library_factory, execute_checked, normalize_config
    except ModuleNotFoundError as error:
        if error.name != "cognition.skill_language":
            raise
        from cognition.skills import SkillLibrary, execute_checked, normalize_config

        return SkillLibrary, execute_checked, normalize_config


class ActiveSkillSession:
    def __init__(
        self,
        source_id,
        config,
        diagnostic_inputs,
        library_state=None,
        *,
        maximum_work=100000,
        maximum_probes=4,
        synthesis_work=10000,
        minimum_evidence=1,
        policy="disagreement",
        state=None,
        library_factory=None,
        executor=None,
        config_validator=None,
    ):
        factory, checked, normalize = defaults()
        self.factory = library_factory or factory
        self.executor = executor or checked
        self.config = (config_validator or normalize)(config)
        require(
            type(source_id) is str and SOURCE.fullmatch(source_id),
            "Active source identity is invalid",
        )
        require(
            type(diagnostic_inputs) is list
            and 1 <= len(diagnostic_inputs) <= self.config["max_rows"],
            "Diagnostic inputs are not bounded",
        )
        require(
            all(
                type(value) is str
                and len(value.encode()) <= self.config["max_input_bytes"]
                for value in diagnostic_inputs
            ),
            "Diagnostic input text is invalid",
        )
        require(
            policy in ("disagreement", "audited_retention"),
            "Active routing policy is unsupported",
        )
        self.policy = policy
        self.source_id = source_id
        self.inputs = list(diagnostic_inputs)
        self.limits = {
            "maximum_work": bounded(maximum_work, 1, 10000000),
            "maximum_probes": bounded(maximum_probes, 0, 64),
            "synthesis_work": bounded(synthesis_work, 1, 10000000),
            "minimum_evidence": bounded(minimum_evidence, 1, 64),
        }
        initial = self.factory(source_id, self.config, state=library_state)
        terms = {
            "version": VERSION,
            "policy": self.policy,
            "source_id": source_id,
            "config_sha256": fingerprint(self.config),
            "inputs_sha256": fingerprint(self.inputs),
            "limits": self.limits,
            "initial_library_sha256": fingerprint(initial.dump()),
        }
        if state is None:
            self.library = initial
            self._state = {
                "terms": terms,
                "session_id": fingerprint(terms),
                "inputs": self.inputs,
                "stage": "portfolio",
                "reason": None,
                "work": {key: 0 for key in ("total", *COSTS, *COUNTERS)},
                "evaluations": {},
                "candidates": [],
                "labels": {},
                "probe_receipts": {},
                "pending": None,
                "action_sequence": 0,
                "synthesis": None,
                "synthesis_action": None,
                "selected": None,
                "history": [],
                "accounting_uncertain": False,
                "uncertain_reservation": 0,
            }
        else:
            self._restore(state, terms)

    @property
    def work(self):
        return copy.deepcopy(self._state["work"])

    @property
    def remaining_work(self):
        return self.limits["maximum_work"] - self._state["work"]["total"]

    def _restore(self, state, terms):
        require(
            type(state) is dict and "state_sha256" in state,
            "Active checkpoint is malformed",
        )
        saved = copy.deepcopy(state)
        checksum = saved.pop("state_sha256")
        require(
            HASH.fullmatch(checksum or "") and fingerprint(saved) == checksum,
            "Active checkpoint integrity failed",
        )
        library = saved.pop("library")
        require(
            set(saved)
            == {
                "terms",
                "session_id",
                "inputs",
                "stage",
                "reason",
                "work",
                "evaluations",
                "candidates",
                "labels",
                "probe_receipts",
                "pending",
                "action_sequence",
                "synthesis",
                "synthesis_action",
                "selected",
                "history",
                "accounting_uncertain",
                "uncertain_reservation",
            },
            "Active checkpoint fields changed",
        )
        require(
            saved["terms"] == terms
            and saved["session_id"] == fingerprint(terms)
            and saved["inputs"] == self.inputs,
            "Active checkpoint source, inputs or terms changed",
        )
        self._state = saved
        self.library = self.factory(self.source_id, self.config, state=library)
        work = saved["work"]
        require(set(work) == {"total", *COSTS, *COUNTERS}, "Active work schema changed")
        for value in work.values():
            bounded(value, 0, 2**53 - 1)
        require(
            sum(work[key] for key in COSTS)
            == work["total"]
            <= self.limits["maximum_work"],
            "Active work components disagree",
        )
        require(
            saved["stage"] in ("portfolio", "routing", "proposed", "abstained"),
            "Active stage is invalid",
        )
        require(
            type(saved["probe_receipts"]) is dict
            and len(saved["probe_receipts"]) <= self.limits["maximum_probes"],
            "Active probe collection exceeds its bound",
        )
        labels = {}
        receipt_ids = set()
        for ident, record in saved["probe_receipts"].items():
            self._validate_action(record["action"])
            require(
                record["action"]["kind"] == "probe" and record["action"]["id"] == ident,
                "Probe action identity changed",
            )
            receipt = record["receipt"]
            self._validate_probe(record["action"], receipt)
            require(
                record["sha256"] == fingerprint(receipt)
                and receipt["receipt_id"] not in receipt_ids,
                "Probe receipt changed or repeated",
            )
            receipt_ids.add(receipt["receipt_id"])
            labels[str(receipt["input_index"])] = receipt["output"]
        require(
            labels == saved["labels"], "Returned labels changed outside their receipts"
        )
        require(
            sum(
                record["receipt"]["work"] for record in saved["probe_receipts"].values()
            )
            == work["probe_work"],
            "Probe disclosure work changed",
        )
        require(
            type(saved["evaluations"]) is dict
            and len(saved["evaluations"]) <= self.config["max_programs"],
            "Portfolio state exceeds its bound",
        )
        for ident, evaluation in saved["evaluations"].items():
            require(
                ident in self.library.dump()["programs"]
                and len(evaluation["outputs"]) <= len(self.inputs),
                "Portfolio identity changed",
            )
            require(
                all(
                    value is None
                    or type(value) is str
                    and len(value.encode()) <= self.config["max_input_bytes"]
                    for value in evaluation["outputs"]
                ),
                "Portfolio output is invalid",
            )
        require(
            type(saved["candidates"]) is list
            and len(set(saved["candidates"])) == len(saved["candidates"])
            and all(ident in saved["evaluations"] for ident in saved["candidates"]),
            "Candidate state changed",
        )
        if saved["pending"] is not None:
            self._validate_action(saved["pending"])
            require(
                saved["pending"]["kind"] in ("probe", "synthesize"),
                "Pending action is not executable",
            )
        bounded(saved["action_sequence"], 0, 66)
        require(
            type(saved["history"]) is list and len(saved["history"]) <= 66,
            "Action history exceeds its bound",
        )
        require(
            (saved["selected"] is not None) == (saved["stage"] == "proposed"),
            "Terminal selection is missing or premature",
        )
        require(
            type(saved["accounting_uncertain"]) is bool,
            "Active uncertainty marker is invalid",
        )
        bounded(saved["uncertain_reservation"], 0, self.limits["maximum_work"])
        if saved["selected"] is not None:
            chosen = saved["selected"]
            require(
                saved["stage"] == "proposed"
                and chosen["program_sha256"] == fingerprint(chosen["program"]),
                "Selected program changed",
            )
            require(
                len(saved["labels"]) >= self.limits["minimum_evidence"],
                "Selected program lacks target evidence",
            )
            if chosen["selection"] == "retained":
                require(
                    chosen["program_sha256"] in self.library.dump()["programs"],
                    "Selected retained skill is absent",
                )
                if chosen["proposal_id"] is not None:
                    require(
                        self.policy == "audited_retention"
                        and saved["synthesis"] is not None
                        and chosen["proposal_id"] == saved["synthesis"]["proposal_id"]
                        and chosen["program"] == saved["synthesis"]["program"],
                        "Selected retained audit proposal changed",
                    )
            else:
                require(
                    chosen["selection"] == "synthesized"
                    and saved["synthesis"] is not None
                    and chosen["proposal_id"] == saved["synthesis"]["proposal_id"]
                    and chosen["program"] == saved["synthesis"]["program"],
                    "Selected synthesis changed",
                )

    def dump(self):
        saved = {**copy.deepcopy(self._state), "library": self.library.dump()}
        require(
            len(serial(saved).encode()) <= 8 * 1024 * 1024,
            "Active checkpoint exceeds its bound",
        )
        return {**saved, "state_sha256": fingerprint(saved)}

    def _charge(self, category, amount, counters=None):
        bounded(amount, 0, 10000000)
        if amount > self.remaining_work:
            self._abstain("work_limit")
            return False
        self._state["work"][category] += amount
        self._state["work"]["total"] += amount
        for key, value in (counters or {}).items():
            if key in COUNTERS:
                self._state["work"][key] += bounded(value, 0, 10000000)
        return True

    def _plan(self, amount=1):
        return self._charge("routing_work", amount, {"planning_operations": amount})

    def _abstain(self, reason):
        self._state.update(stage="abstained", reason=reason, pending=None)

    def _portfolio(self):
        if not self._plan(1 + blocks(serial(self.inputs))):
            return
        for ident, record in sorted(self.library.dump()["programs"].items()):
            if not self._charge("portfolio_work", 1, {"candidates": 1}):
                return
            evaluation = {"outputs": [], "errors": [], "work": 1, "complete": False}
            self._state["evaluations"][ident] = evaluation
            for value in self.inputs:
                if self.remaining_work < 1:
                    self._abstain("work_limit")
                    return
                checked = self.executor(
                    record["program"], value, self.config, budget=self.remaining_work
                )
                actual = checked["work"]
                bounded(actual["total"], 0, self.remaining_work)
                self._charge("portfolio_work", actual["total"], actual)
                self._state["work"]["candidate_evaluations"] += 1
                evaluation["work"] += actual["total"]
                evaluation["outputs"].append(checked["output"])
                evaluation["errors"].append(checked["error"])
                if checked["error"] == "work_limit":
                    self._abstain("work_limit")
                    return
            evaluation["complete"] = True
        self._state["stage"] = "routing"

    def _compatible(self):
        candidates = []
        for ident, evaluation in sorted(self._state["evaluations"].items()):
            if not self._plan():
                return []
            if not evaluation["complete"] or any(evaluation["errors"]):
                continue
            compatible = True
            for index, label in self._state["labels"].items():
                observed = evaluation["outputs"][int(index)]
                if not self._plan(1 + blocks(observed) + blocks(label)):
                    return []
                if observed != label:
                    compatible = False
            if compatible:
                candidates.append(ident)
        self._state["candidates"] = candidates
        return candidates

    def _unlabelled(self):
        known = {self.inputs[int(index)] for index in self._state["labels"]}
        return [index for index, value in enumerate(self.inputs) if value not in known]

    def _probe_index(self, candidates, unlabelled):
        best, score = None, -1
        for index in unlabelled:
            groups = {}
            for ident in candidates:
                value = self._state["evaluations"][ident]["outputs"][index]
                if not self._plan(1 + blocks(value)):
                    return None
                groups[value] = groups.get(value, 0) + 1
            if not self._plan(1 + len(groups)):
                return None
            disagreement = len(candidates) ** 2 - sum(
                count * count for count in groups.values()
            )
            if disagreement > score:
                best, score = index, disagreement
        return best

    def _action(self, kind, **fields):
        self._state["action_sequence"] += 1
        body = {
            "session_id": self._state["session_id"],
            "sequence": self._state["action_sequence"],
            "kind": kind,
            **fields,
        }
        action = {**body, "id": fingerprint(body)}
        self._state["pending"] = action
        return copy.deepcopy(action)

    def _validate_action(self, action):
        require(
            type(action) is dict
            and action.get("session_id") == self._state["session_id"]
            and action.get("id")
            == fingerprint(
                {key: value for key, value in action.items() if key != "id"}
            ),
            "Active action commitment changed",
        )
        bounded(action["sequence"], 1, 66)
        if action["kind"] == "probe":
            index = bounded(action["input_index"], 0, len(self.inputs) - 1)
            require(
                action["input"] == self.inputs[index]
                and action["input_sha256"] == fingerprint(self.inputs[index])
                and action["maximum_output_bytes"] == self.config["max_input_bytes"],
                "Active probe input commitment changed",
            )

    def next_action(self):
        state = self._state
        if state["pending"] is not None:
            return copy.deepcopy(state["pending"])
        if state["stage"] == "portfolio":
            if self.policy == "audited_retention":
                if self._plan(1 + blocks(serial(self.inputs))):
                    state["stage"] = "routing"
            else:
                self._portfolio()
        if state["stage"] in ("proposed", "abstained"):
            return self._terminal_action()
        if self.policy == "audited_retention":
            return self._next_audited()
        candidates = self._compatible()
        if state["stage"] == "abstained":
            return self._terminal_action()
        evidence = len(state["labels"])
        if candidates and evidence >= self.limits["minimum_evidence"]:
            signatures = set()
            for ident in candidates:
                values = state["evaluations"][ident]["outputs"]
                if not self._plan(1 + blocks(serial(values))):
                    return self._terminal_action()
                signatures.add(tuple(values))
            if len(signatures) == 1:
                ident = candidates[0]
                state["selected"] = {
                    "program": self.library.dump()["programs"][ident]["program"],
                    "program_sha256": ident,
                    "proposal_id": None,
                    "selection": "retained",
                }
                state.update(
                    stage="proposed", reason="target_confirmed_retained_program"
                )
                return self._terminal_action()
        unlabelled = self._unlabelled()
        acquisition = min(4, len(set(self.inputs)), self.limits["maximum_probes"])
        if not candidates and evidence >= max(
            2, acquisition, self.limits["minimum_evidence"]
        ):
            return self._request_synthesis()
        if not unlabelled or evidence >= self.limits["maximum_probes"]:
            self._abstain(
                "unresolved_ambiguity" if candidates else "insufficient_target_evidence"
            )
            return self._terminal_action()
        index = self._probe_index(candidates, unlabelled)
        if index is None:
            return self._terminal_action()
        return self._request_probe(index)

    def _next_audited(self):
        if len(self._state["labels"]) >= max(2, self.limits["minimum_evidence"]):
            return self._request_synthesis()
        unlabelled = self._unlabelled()
        if (
            not unlabelled
            or len(self._state["labels"]) >= self.limits["maximum_probes"]
        ):
            self._abstain("insufficient_target_evidence")
            return self._terminal_action()
        if not self._plan():
            return self._terminal_action()
        return self._request_probe(unlabelled[0])

    def _request_synthesis(self):
        if not self._plan(3):
            return self._terminal_action()
        # Both solver modes produce a pending buyer-admission proposal. Check
        # known storage/counter limits before reserving the bounded tool call.
        budget = min(self.limits["synthesis_work"], self.remaining_work, 10000000)
        if budget < 1:
            self._abstain("work_limit")
            return self._terminal_action()
        library = self.library.dump()
        if (
            len(library["pending"]) >= 64
            or library["sequence"] >= 2**53 - 1
            or library["total_work"] + budget > 2**53 - 1
        ):
            self._abstain("library_capacity_unavailable")
            return self._terminal_action()
        return self._action("synthesize", maximum_work=budget)

    def _request_probe(self, index):
        # Reserve for the largest permitted label before asking the buyer.
        maximum = (
            1
            + (len(self.inputs[index].encode()) + self.config["max_input_bytes"] + 31)
            // 32
        )
        if maximum > self.remaining_work:
            self._abstain("probe_allowance_unavailable")
            return self._terminal_action()
        return self._action(
            "probe",
            input_index=index,
            input=self.inputs[index],
            input_sha256=fingerprint(self.inputs[index]),
            maximum_output_bytes=self.config["max_input_bytes"],
            maximum_work=maximum,
        )

    def _terminal_action(self):
        kind = "propose" if self._state["stage"] == "proposed" else "abstain"
        body = {
            "session_id": self._state["session_id"],
            "sequence": self._state["action_sequence"],
            "kind": kind,
            "reason": self._state["reason"],
        }
        return {**body, "id": fingerprint(body)}

    def _validate_probe(self, action, receipt):
        require(
            type(receipt) is dict
            and set(receipt)
            == {
                "source_id",
                "input_index",
                "input_sha256",
                "output",
                "receipt_id",
                "work",
            },
            "Probe receipt schema is invalid",
        )
        require(
            type(receipt["input_index"]) is int
            and receipt["source_id"] == self.source_id
            and receipt["input_index"] == action["input_index"]
            and receipt["input_sha256"] == action["input_sha256"],
            "Probe receipt belongs to another source or input",
        )
        output = receipt["output"]
        require(
            type(output) is str
            and len(output.encode()) <= self.config["max_input_bytes"]
            and type(receipt["receipt_id"]) is str
            and HASH.fullmatch(receipt["receipt_id"]),
            "Probe label or receipt identity is invalid",
        )
        require(
            type(receipt["work"]) is int
            and receipt["work"]
            == probe_cost(action["input"], output)
            <= action["maximum_work"],
            "Probe disclosure work is invalid",
        )

    def observe_probe(self, action_id, receipt):
        prior = self._state["probe_receipts"].get(action_id)
        if prior is not None:
            require(
                prior["sha256"] == fingerprint(receipt),
                "An already returned probe label changed",
            )
            return copy.deepcopy(prior)
        action = self._state["pending"]
        require(
            action is not None
            and action["id"] == action_id
            and action["kind"] == "probe",
            "Probe has no matching pending action",
        )
        self._validate_probe(action, receipt)
        require(
            receipt["receipt_id"]
            not in {
                record["receipt"]["receipt_id"]
                for record in self._state["probe_receipts"].values()
            },
            "Probe receipt identity was reused",
        )
        require(
            str(receipt["input_index"]) not in self._state["labels"],
            "An observed diagnostic row cannot be relabelled",
        )
        require(
            self._charge("probe_work", receipt["work"], {"probe_requests": 1}),
            "Probe exceeded its reserved work",
        )
        record = {
            "action": copy.deepcopy(action),
            "receipt": copy.deepcopy(receipt),
            "sha256": fingerprint(receipt),
        }
        self._state["probe_receipts"][action_id] = record
        self._state["labels"][str(receipt["input_index"])] = receipt["output"]
        self._state["history"].append(
            {
                "action_id": action_id,
                "kind": "probe",
                "status": "observed",
                "work": receipt["work"],
                "binding_sha256": record["sha256"],
            }
        )
        self._state["pending"] = None
        return copy.deepcopy(record)

    def perform_synthesis(self, action_id):
        if self._state["synthesis_action"] is not None:
            require(
                self._state["synthesis_action"]["id"] == action_id,
                "Synthesis action changed after execution",
            )
            return copy.deepcopy(self._state["synthesis"])
        action = self._state["pending"]
        require(
            action is not None
            and action["id"] == action_id
            and action["kind"] == "synthesize",
            "Synthesis has no matching pending action",
        )
        require(
            len(self._state["labels"]) >= self.limits["minimum_evidence"],
            "Synthesis lacks target evidence",
        )
        indices = sorted(int(index) for index in self._state["labels"])
        initial = self.library.dump()
        try:
            proposal = self.library.solve(
                [self.inputs[index] for index in indices],
                [self._state["labels"][str(index)] for index in indices],
                mode="retained_factored"
                if self.policy == "audited_retention"
                else "factored",
                budget=action["maximum_work"],
            )
            bounded(proposal["work"]["total"], 0, action["maximum_work"])
        except BaseException:
            self.library = self.factory(self.source_id, self.config, state=initial)
            self._state.update(
                accounting_uncertain=True, uncertain_reservation=action["maximum_work"]
            )
            self._state["history"].append(
                {
                    "action_id": action_id,
                    "kind": "synthesize",
                    "status": "accounting_unknown",
                    "work": None,
                    "reserved_work": action["maximum_work"],
                }
            )
            self._abstain("synthesis_accounting_unknown")
            raise
        self._charge("synthesis_work", proposal["work"]["total"], proposal["work"])
        self._state["synthesis_action"] = copy.deepcopy(action)
        self._state["synthesis"] = copy.deepcopy(proposal)
        self._state["pending"] = None
        self._state["history"].append(
            {
                "action_id": action_id,
                "kind": "synthesize",
                "status": "proposed" if proposal["program"] is not None else "failed",
                "work": proposal["work"]["total"],
                "binding_sha256": fingerprint(proposal),
            }
        )
        if proposal["program"] is None:
            self._abstain("no_program_found")
        else:
            retained = (
                self.policy == "audited_retention"
                and proposal["program_sha256"] in initial["programs"]
            )
            self._state["selected"] = {
                "program": proposal["program"],
                "program_sha256": proposal["program_sha256"],
                "proposal_id": proposal["proposal_id"],
                "selection": "retained" if retained else "synthesized",
            }
            self._state.update(
                stage="proposed",
                reason="audited_examples_matched_retained_program"
                if retained
                else "acquired_examples_produced_program",
            )
        return copy.deepcopy(proposal)

    def result(self):
        status = {"proposed": "proposed", "abstained": "abstained"}.get(
            self._state["stage"], "pending"
        )
        selected = self._state["selected"] or {
            "program": None,
            "program_sha256": None,
            "proposal_id": None,
            "selection": None,
        }
        return {
            "status": status,
            **copy.deepcopy(selected),
            "reason": self._state["reason"],
            "source_id": self.source_id,
            "session_id": self._state["session_id"],
            "work": self.work,
            "probes_used": len(self._state["labels"]),
            "probe_receipt_sha256s": [
                record["sha256"]
                for record in sorted(
                    self._state["probe_receipts"].values(),
                    key=lambda value: value["action"]["sequence"],
                )
            ],
            "accounting_uncertain": self._state["accounting_uncertain"],
            "uncertain_reservation": self._state["uncertain_reservation"],
        }

"""Ambiguity-aware, bounded reuse with a protected fresh-search allowance.

The controller obtains two distinct diagnostic answers before checking retained
programs. It checks every retained candidate within a separate lookup quota,
then requests a distinguishing answer when surviving programs disagree on the
remaining visible inputs. A truncated lookup cannot establish consensus: the
controller falls back to the same conventional factored solver instead.

All declared work, including checkpoint serialization, shares ``maximum_work``.
The fresh-search quota is reserved while lookup runs; it is not extra work or
free compute. Diagnostic answers are training evidence, never final acceptance.
The caller must persist a pending action before performing a physical tool call.
Checksums bind accidental recovery changes, not an adversarial checkpoint writer.
"""

from __future__ import annotations

import copy
import json

from cognition.active_skills import (
    HASH,
    SOURCE,
    blocks,
    bounded,
    defaults,
    probe_cost,
    require,
    serial,
)
from cognition.skills import fingerprint

VERSION = "guarded-skill-session-v2"
COSTS = (
    "portfolio_work",
    "routing_work",
    "probe_work",
    "synthesis_work",
    "audit_work",
    "checkpoint_work",
)
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
    "parse_operations",
    "checkpoint_operations",
)
STAGES = ("routing", "synthesis_audit", "proposed", "abstained")


def _shape(value):
    """A necessary field-count guard, not a prediction of the intended rule."""
    try:
        parsed = json.loads(value)
    except (ValueError, RecursionError):
        return None
    return (
        len(parsed)
        if type(parsed) is list and all(type(item) is str for item in parsed)
        else None
    )


def _required_fields(program):
    maximum, nodes = 0, 0
    pending = [program.get("expr")]
    while pending:
        node = pending.pop()
        if type(node) is not dict:
            continue
        nodes += 1
        if node.get("op") == "field":
            maximum = max(maximum, node["index"] + 1)
        elif node.get("op") == "if_equal":
            maximum = max(maximum, node["field"] + 1)
        for key in ("arg", "left", "right", "then", "else"):
            if key in node:
                pending.append(node[key])
        pending.extend(node.get("parts", []))
    return maximum, nodes


def _validate_work(work, maximum):
    require(
        type(work) is dict
        and {"total", "primitive_attempts", "candidates", "byte_blocks"} <= set(work)
        and all(
            type(value) is int and 0 <= value <= 2**53 - 1 for value in work.values()
        )
        and work["total"]
        == sum(
            work[key] for key in ("primitive_attempts", "candidates", "byte_blocks")
        ),
        "Guarded interpreter work components disagree",
    )
    bounded(work["total"], 0, maximum)


class GuardedSkillSession:
    """Private source-bound session with idempotent reserved tool actions.

    ``factory`` and ``executor`` are trusted constructor dependencies. They are
    never loaded from checkpoint text. A custom solver must provide its own
    reviewed ``solver_id``; that identity is sealed into every checkpoint.
    """

    def __init__(
        self,
        source_id,
        config,
        inputs,
        library_state=None,
        *,
        maximum_work=100000,
        maximum_probes=4,
        synthesis_work=10000,
        minimum_evidence=2,
        policy="guarded",
        lookup_work=None,
        factory=None,
        solver_id="factored-v1",
        executor=None,
        state=None,
    ):
        default_factory, checked, normalize = defaults()
        self.factory = factory or default_factory
        self.executor = executor or checked
        require(
            callable(self.factory) and callable(self.executor), "Invalid trusted tools"
        )
        self.config = normalize(config)
        require(
            type(source_id) is str and SOURCE.fullmatch(source_id),
            "Guarded source identity is invalid",
        )
        require(
            type(solver_id) is str and SOURCE.fullmatch(solver_id),
            "Guarded solver identity is invalid",
        )
        require(policy == "guarded", "Guarded policy is unsupported")
        require(
            type(inputs) is list
            and 1 <= len(inputs) <= self.config["max_rows"]
            and all(
                type(value) is str
                and len(value.encode()) <= self.config["max_input_bytes"]
                for value in inputs
            ),
            "Guarded diagnostic inputs are invalid",
        )
        self.source_id, self.inputs, self.policy = source_id, list(inputs), policy
        self.limits = {
            "maximum_work": bounded(maximum_work, 1, 10000000),
            "maximum_probes": bounded(maximum_probes, 0, 64),
            "synthesis_work": bounded(synthesis_work, 1, 10000000),
            "minimum_evidence": bounded(minimum_evidence, 2, 64),
            "lookup_work": bounded(
                min(maximum_work // 4, 25000) if lookup_work is None else lookup_work,
                0,
                maximum_work,
            ),
        }
        initial = self.factory(source_id, self.config, state=library_state)
        self._initial_programs = initial.dump()["programs"]
        terms = {
            "version": VERSION,
            "policy": policy,
            "solver_id": solver_id,
            "source_id": source_id,
            "config_sha256": fingerprint(self.config),
            "inputs_sha256": fingerprint(self.inputs),
            "initial_library_sha256": fingerprint(initial.dump()),
            "limits": self.limits,
        }
        if state is not None:
            self._restore(state, terms)
            return
        self.library = initial
        self._state = {
            "terms": terms,
            "session_id": fingerprint(terms),
            "inputs": self.inputs,
            "stage": "routing",
            "reason": None,
            "work": {key: 0 for key in ("total", *COSTS, *COUNTERS)},
            "charges": [],
            "shapes": [_shape(value) for value in self.inputs],
            "requirements": {
                ident: _required_fields(record["program"])[0]
                for ident, record in self._initial_programs.items()
            },
            "evaluations": {},
            "candidates": [],
            "labels": {},
            "probe_receipts": {},
            "pending": None,
            "action_sequence": 0,
            "lookup_complete": False,
            "lookup_truncated": False,
            "retained_audit_index": None,
            "retained_audit_candidates": [],
            "synthesis": None,
            "synthesis_action": None,
            "synthesis_attempts": [],
            "synthesis_audit_index": None,
            "diagnostic_rejection": None,
            "diagnostic_rejections": [],
            "selected": None,
            "history": [],
            "accounting_uncertain": False,
            "uncertain_reservation": 0,
        }
        for value in self.inputs:
            if not self._charge(
                "routing_work", 1 + blocks(value), {"parse_operations": 1}
            ):
                return
        for _, record in sorted(self._initial_programs.items()):
            _, nodes = _required_fields(record["program"])
            if not self._charge("routing_work", 1 + nodes):
                return
        self._charge("routing_work", 1 + blocks(serial(terms)))

    @property
    def work(self):
        return copy.deepcopy(self._state["work"])

    @property
    def remaining_work(self):
        return self.limits["maximum_work"] - self._state["work"]["total"]

    @property
    def lookup_remaining(self):
        """Remaining lookup quota inside the one shared whole-job allowance."""
        return max(
            0,
            min(
                self.limits["lookup_work"] - self._state["work"]["portfolio_work"],
                self.remaining_work - self.limits["synthesis_work"] - 32,
            ),
        )

    def _charge(self, category, amount, counters=None):
        require(category in COSTS, "Unknown guarded cost category")
        bounded(amount, 0, 10000000)
        counters = dict(counters or {})
        require(set(counters) <= set(COUNTERS), "Unknown guarded work counter")
        for value in counters.values():
            bounded(value, 0, 10000000)
        if amount > self.remaining_work:
            self._abstain("work_limit")
            return False
        if len(self._state["charges"]) >= 10000:
            self._abstain("accounting_record_limit")
            return False
        work = self._state["work"]
        work[category] += amount
        work["total"] += amount
        for key, value in counters.items():
            work[key] += value
        self._state["charges"].append(
            {"category": category, "amount": amount, "counters": counters}
        )
        return True

    def _plan(self, amount=1):
        return self._charge("routing_work", amount, {"planning_operations": amount})

    def _checkpoint_cost(self):
        # Serialized bytes are declared units, not a claim about system calls or
        # every machine instruction. Read-only dump/recovery is not a new job.
        return 1 + blocks(serial({**self._state, "library": self.library.dump()}))

    def _checkpoint(self):
        return self._charge(
            "checkpoint_work",
            self._checkpoint_cost(),
            {"checkpoint_operations": 1},
        )

    def _abstain(self, reason):
        self._state.update(
            stage="abstained", reason=reason, selected=None, pending=None
        )

    def _unlabelled(self):
        known = {self.inputs[int(index)] for index in self._state["labels"]}
        return [index for index, value in enumerate(self.inputs) if value not in known]

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
        if not self._checkpoint():
            return self._terminal_action()
        return copy.deepcopy(action)

    def _terminal_action(self):
        body = {
            "session_id": self._state["session_id"],
            "sequence": self._state["action_sequence"],
            "kind": "propose" if self._state["stage"] == "proposed" else "abstain",
            "reason": self._state["reason"],
        }
        return {**body, "id": fingerprint(body)}

    def _request_probe(self, index):
        maximum = probe_cost(self.inputs[index], "x" * self.config["max_input_bytes"])
        # Reserve for the label and two state writes before exposing an action.
        if maximum + 2 * self._checkpoint_cost() + 64 > self.remaining_work:
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

    def _checked(self, program, index, *, category="portfolio_work"):
        available = (
            self.lookup_remaining
            if category == "portfolio_work"
            else self.remaining_work
        )
        if available < 1:
            return None
        try:
            checked = self.executor(
                program, self.inputs[index], self.config, budget=available
            )
            require(
                type(checked) is dict and set(checked) == {"output", "error", "work"},
                "Guarded execution receipt is invalid",
            )
            _validate_work(checked["work"], available)
            require(
                checked["error"] in (None, "invalid_input", "work_limit")
                and (checked["error"] is not None or type(checked["output"]) is str)
                and (
                    checked["output"] is None
                    or type(checked["output"]) is str
                    and len(checked["output"].encode())
                    <= self.config["max_input_bytes"]
                ),
                "Guarded execution output is invalid",
            )
        except BaseException:
            self._state.update(
                accounting_uncertain=True, uncertain_reservation=available
            )
            self._abstain("execution_accounting_unknown")
            raise
        counters = {
            key: value for key, value in checked["work"].items() if key in COUNTERS
        }
        counters["candidate_evaluations"] = 1
        self._charge(category, checked["work"]["total"], counters)
        return checked

    def _evaluate_retained(self, ident, program, index):
        cache = self._state["evaluations"].setdefault(ident, {})
        if str(index) in cache:
            return cache[str(index)]
        checked = self._checked(program, index)
        if checked is None or checked["error"] == "work_limit":
            self._state["lookup_truncated"] = True
            return None
        cache[str(index)] = copy.deepcopy(checked)
        return checked

    def _lookup(self):
        programs = self.library.dump()["programs"]
        candidates = []
        for ident, record in sorted(programs.items()):
            if not self._plan():
                return
            required = self._state["requirements"][ident]
            if any(
                shape is not None and shape < required
                for shape in self._state["shapes"]
            ):
                continue
            if self.lookup_remaining < 1 or not self._charge(
                "portfolio_work", 1, {"candidates": 1}
            ):
                self._state["lookup_truncated"] = True
                break
            compatible = True
            for index, label in self._state["labels"].items():
                checked = self._evaluate_retained(ident, record["program"], int(index))
                if checked is None:
                    break
                if checked["error"] is not None or checked["output"] != label:
                    compatible = False
                    break
            if self._state["lookup_truncated"]:
                break
            if compatible:
                candidates.append(ident)
        self._state["candidates"] = candidates
        self._state["lookup_complete"] = True

    def _retained_choice(self):
        programs = self.library.dump()["programs"]
        compatible = []
        for ident in self._state["candidates"]:
            if not self._plan():
                return self._terminal_action()
            if all(
                self._state["evaluations"][ident][index]["error"] is None
                and self._state["evaluations"][ident][index]["output"] == output
                for index, output in self._state["labels"].items()
            ):
                compatible.append(ident)
        self._state["candidates"] = compatible
        if not compatible:
            return self._request_synthesis()
        unlabelled = self._unlabelled()
        for index in unlabelled:
            retained = []
            for ident in compatible:
                checked = self._evaluate_retained(
                    ident, programs[ident]["program"], index
                )
                if checked is None:
                    return self._request_synthesis()
                if checked["error"] is None:
                    retained.append(ident)
            compatible = retained
        self._state["candidates"] = compatible
        if not compatible:
            return self._request_synthesis()
        scores = []
        for index in unlabelled:
            groups = {}
            for ident in compatible:
                checked = self._evaluate_retained(
                    ident, programs[ident]["program"], index
                )
                if checked is None:
                    return self._request_synthesis()
                # An error is a distinct predicted outcome, never an oracle label.
                key = (checked["error"], checked["output"])
                groups[key] = groups.get(key, 0) + 1
                if not self._plan(1 + blocks(serial(key))):
                    return self._terminal_action()
            scores.append(
                (
                    len(compatible) ** 2
                    - sum(value * value for value in groups.values()),
                    index,
                )
            )
        best = (
            max(scores, key=lambda pair: (pair[0], -pair[1])) if scores else (0, None)
        )
        if best[0] > 0:
            if len(self._state["labels"]) >= self.limits["maximum_probes"]:
                self._abstain("unresolved_ambiguity")
                return self._terminal_action()
            return self._request_probe(best[1])
        audit_index = self._state["retained_audit_index"]
        audit_passed = (
            audit_index is not None
            and str(audit_index) in self._state["labels"]
            and sorted(compatible) == self._state["retained_audit_candidates"]
            and all(
                self._state["evaluations"][ident][str(audit_index)]["error"] is None
                and self._state["evaluations"][ident][str(audit_index)]["output"]
                == self._state["labels"][str(audit_index)]
                for ident in compatible
            )
        )
        if not audit_passed:
            if (
                not unlabelled
                or len(self._state["labels"]) >= self.limits["maximum_probes"]
            ):
                self._abstain("independent_retained_audit_unavailable")
                return self._terminal_action()
            # Even one candidate can fit two examples by coincidence. A fresh
            # answer must test applicability; portfolio consensus cannot do so.
            self._state["retained_audit_index"] = unlabelled[0]
            self._state["retained_audit_candidates"] = sorted(compatible)
            return self._request_probe(unlabelled[0])
        # Consensus is limited to this visible pool, never universal correctness.
        ident = min(
            compatible, key=lambda key: (len(serial(programs[key]["program"])), key)
        )
        self._state["selected"] = {
            "program": copy.deepcopy(programs[ident]["program"]),
            "program_sha256": ident,
            "proposal_id": None,
            "selection": "retained",
        }
        self._state.update(stage="proposed", reason="audited_retained_pool_consensus")
        self._checkpoint()
        return self._terminal_action()

    def _request_synthesis(self):
        if len(self._state["synthesis_attempts"]) >= 2:
            self._abstain("fresh_search_already_consumed")
            return self._terminal_action()
        state = self.library.dump()
        if (
            len(state["pending"]) >= 64
            or state["sequence"] >= 2**53 - 1
            or state["total_work"] + self.limits["synthesis_work"] > 2**53 - 1
        ):
            self._abstain("library_capacity_unavailable")
            return self._terminal_action()
        available = self.remaining_work - 2 * self._checkpoint_cost() - 64
        budget = min(
            self.limits["synthesis_work"] - self._state["work"]["synthesis_work"],
            available,
        )
        if budget < 1:
            self._abstain("synthesis_allowance_unavailable")
            return self._terminal_action()
        return self._action("synthesize", maximum_work=budget)

    def next_action(self):
        if self._state["pending"] is not None:
            return copy.deepcopy(self._state["pending"])
        if self._state["stage"] in ("proposed", "abstained"):
            return self._terminal_action()
        if self._state["stage"] == "synthesis_audit":
            return self._audit_synthesis()
        if len(self._state["labels"]) < self.limits["minimum_evidence"]:
            unlabelled = self._unlabelled()
            if (
                not unlabelled
                or len(self._state["labels"]) >= self.limits["maximum_probes"]
            ):
                self._abstain("insufficient_target_evidence")
                return self._terminal_action()
            return self._request_probe(unlabelled[0])
        if not self._state["lookup_complete"]:
            self._lookup()
        if self._state["stage"] == "abstained":
            return self._terminal_action()
        if self._state["lookup_truncated"]:
            return self._request_synthesis()
        return self._retained_choice()

    def _validate_action(self, action):
        require(
            type(action) is dict
            and action.get("session_id") == self._state["session_id"]
            and action.get("id")
            == fingerprint(
                {key: value for key, value in action.items() if key != "id"}
            ),
            "Guarded action commitment changed",
        )
        bounded(action.get("sequence"), 1, 66)
        kind = action.get("kind")
        if kind == "probe":
            require(
                set(action)
                == {
                    "session_id",
                    "sequence",
                    "kind",
                    "input_index",
                    "input",
                    "input_sha256",
                    "maximum_output_bytes",
                    "maximum_work",
                    "id",
                },
                "Guarded probe action fields changed",
            )
            index = bounded(action["input_index"], 0, len(self.inputs) - 1)
            require(
                action["input"] == self.inputs[index]
                and action["input_sha256"] == fingerprint(self.inputs[index])
                and action["maximum_output_bytes"] == self.config["max_input_bytes"]
                and action["maximum_work"]
                == probe_cost(self.inputs[index], "x" * self.config["max_input_bytes"]),
                "Guarded probe reservation changed",
            )
        elif kind == "synthesize":
            require(
                set(action) == {"session_id", "sequence", "kind", "maximum_work", "id"},
                "Guarded synthesis action fields changed",
            )
            bounded(action["maximum_work"], 1, self.limits["synthesis_work"])
        else:
            raise ValueError("Guarded pending action kind is invalid")

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
            "Guarded probe receipt fields changed",
        )
        require(
            type(receipt["input_index"]) is int
            and receipt["source_id"] == self.source_id
            and receipt["input_index"] == action["input_index"]
            and receipt["input_sha256"] == action["input_sha256"],
            "Guarded probe belongs to another source or input",
        )
        require(
            type(receipt["output"]) is str
            and len(receipt["output"].encode()) <= self.config["max_input_bytes"]
            and type(receipt["receipt_id"]) is str
            and HASH.fullmatch(receipt["receipt_id"])
            and type(receipt["work"]) is int
            and receipt["work"] == probe_cost(action["input"], receipt["output"])
            and receipt["work"] <= action["maximum_work"],
            "Guarded probe cost or output is invalid",
        )

    def observe_probe(self, action_id, receipt):
        prior = self._state["probe_receipts"].get(action_id)
        if prior is not None:
            require(
                prior["sha256"] == fingerprint(receipt),
                "An already returned guarded label changed",
            )
            return copy.deepcopy(prior)
        action = self._state["pending"]
        require(
            action is not None
            and action["kind"] == "probe"
            and action["id"] == action_id,
            "Guarded probe has no matching pending action",
        )
        self._validate_probe(action, receipt)
        require(
            receipt["receipt_id"]
            not in {
                record["receipt"]["receipt_id"]
                for record in self._state["probe_receipts"].values()
            },
            "Guarded probe receipt identity repeats",
        )
        require(
            self.inputs[action["input_index"]]
            not in {self.inputs[int(index)] for index in self._state["labels"]},
            "Guarded diagnostic input cannot be relabelled",
        )
        require(
            self._charge("probe_work", receipt["work"], {"probe_requests": 1}),
            "Guarded probe exceeded its reservation",
        )
        record = {
            "action": copy.deepcopy(action),
            "receipt": copy.deepcopy(receipt),
            "sha256": fingerprint(receipt),
        }
        self._state["probe_receipts"][action_id] = record
        self._state["labels"][str(action["input_index"])] = receipt["output"]
        self._state["history"].append(
            {
                "action_id": action_id,
                "kind": "probe",
                "binding_sha256": record["sha256"],
                "work": receipt["work"],
            }
        )
        self._state["pending"] = None
        self._checkpoint()
        return copy.deepcopy(record)

    def perform_synthesis(self, action_id):
        for attempt in self._state["synthesis_attempts"]:
            if attempt["action"]["id"] == action_id:
                return copy.deepcopy(attempt["proposal"])
        action = self._state["pending"]
        require(
            action is not None
            and action["kind"] == "synthesize"
            and action["id"] == action_id,
            "Guarded synthesis has no matching pending action",
        )
        require(
            len(self._state["labels"]) >= self.limits["minimum_evidence"],
            "Guarded synthesis lacks target evidence",
        )
        indices = sorted(int(index) for index in self._state["labels"])
        initial = self.library.dump()
        try:
            proposal = self.library.solve(
                [self.inputs[index] for index in indices],
                [self._state["labels"][str(index)] for index in indices],
                mode="factored",
                budget=action["maximum_work"],
            )
            _validate_work(proposal["work"], action["maximum_work"])
            require(
                proposal["source_id"] == self.source_id
                and proposal["config_sha256"] == fingerprint(self.config)
                and (
                    proposal["program"] is None
                    or proposal["program_sha256"] == fingerprint(proposal["program"])
                ),
                "Guarded solver program commitment is invalid",
            )
            if proposal["program"] is not None:
                pending = self.library.dump()["pending"].get(proposal["proposal_id"])
                require(
                    pending is not None
                    and pending["program_sha256"] == proposal["program_sha256"]
                    and pending["program"] == proposal["program"],
                    "Guarded solver proposal is not reserved in its library",
                )
        except BaseException:
            self.library = self.factory(self.source_id, self.config, state=initial)
            self._state.update(
                accounting_uncertain=True, uncertain_reservation=action["maximum_work"]
            )
            self._state["history"].append(
                {
                    "action_id": action_id,
                    "kind": "synthesize",
                    "binding_sha256": None,
                    "work": None,
                    "reserved_work": action["maximum_work"],
                }
            )
            self._abstain("synthesis_accounting_unknown")
            raise
        counters = {
            key: value for key, value in proposal["work"].items() if key in COUNTERS
        }
        self._charge("synthesis_work", proposal["work"]["total"], counters)
        self._state["synthesis_action"] = copy.deepcopy(action)
        self._state["synthesis"] = copy.deepcopy(proposal)
        self._state["synthesis_attempts"].append(
            {
                "action": copy.deepcopy(action),
                "proposal": copy.deepcopy(proposal),
                "training_indices": indices,
                "training_sha256": fingerprint(
                    [
                        [self.inputs[index] for index in indices],
                        [self._state["labels"][str(index)] for index in indices],
                    ]
                ),
            }
        )
        self._state["synthesis_audit_index"] = None
        self._state["pending"] = None
        self._state["history"].append(
            {
                "action_id": action_id,
                "kind": "synthesize",
                "binding_sha256": fingerprint(proposal),
                "work": proposal["work"]["total"],
            }
        )
        if proposal["program"] is None:
            self._abstain("no_program_found")
        else:
            self._state["stage"] = "synthesis_audit"
        self._checkpoint()
        return copy.deepcopy(proposal)

    def _audit_synthesis(self):
        proposal = self._state["synthesis"]
        index = self._state["synthesis_audit_index"]
        if index is None:
            unlabelled = self._unlabelled()
            if (
                unlabelled
                and len(self._state["labels"]) < self.limits["maximum_probes"]
            ):
                index = unlabelled[0]
                self._state["synthesis_audit_index"] = index
                return self._request_probe(index)
        elif str(index) not in self._state["labels"]:
            return self._request_probe(index)
        if index is not None:
            checked = self._checked(proposal["program"], index, category="audit_work")
            if checked is None or checked["error"] == "work_limit":
                self._abstain("diagnostic_audit_allowance_unavailable")
                return self._terminal_action()
            expected = self._state["labels"][str(index)]
            if checked["error"] is not None or checked["output"] != expected:
                evidence = {
                    "input": self.inputs[index],
                    "expected": expected,
                    "observed": checked["output"],
                    "error": checked["error"],
                }
                receipt = {
                    "source_id": self.source_id,
                    "proposal_id": proposal["proposal_id"],
                    "program_sha256": proposal["program_sha256"],
                    "evaluation_id": fingerprint(
                        {
                            "session_id": self._state["session_id"],
                            "diagnostic_audit_index": index,
                            "proposal_id": proposal["proposal_id"],
                        }
                    ),
                    "accepted": False,
                    "correct_rows": 0,
                    "total_rows": 1,
                    "evidence_sha256": fingerprint(evidence),
                    "work": checked["work"]["total"],
                }
                # This consumes only a genuinely failed diagnostic proposal.
                # It never positively admits a skill or touches final answers.
                self.library.admit_verified(
                    proposal["proposal_id"], receipt, retain=False
                )
                self._state["diagnostic_rejection"] = {
                    "proposal_id": proposal["proposal_id"],
                    "program_sha256": proposal["program_sha256"],
                    "input_index": index,
                    "input_sha256": fingerprint(self.inputs[index]),
                    "expected_sha256": fingerprint(expected),
                    "observed_sha256": fingerprint(checked["output"]),
                    "error": checked["error"],
                    "work": checked["work"],
                    "receipt": receipt,
                    "evidence": evidence,
                }
                self._state["diagnostic_rejections"].append(
                    copy.deepcopy(self._state["diagnostic_rejection"])
                )
                if (
                    len(self._state["synthesis_attempts"]) < 2
                    and self._state["work"]["synthesis_work"]
                    < self.limits["synthesis_work"]
                ):
                    self._state["candidates"] = []
                    self._state["stage"] = "routing"
                    self._checkpoint()
                    return self._request_synthesis()
                self._abstain("synthesized_program_failed_diagnostic_audit")
                self._checkpoint()
                return self._terminal_action()
        self._state["selected"] = {
            "program": copy.deepcopy(proposal["program"]),
            "program_sha256": proposal["program_sha256"],
            "proposal_id": proposal["proposal_id"],
            "selection": "synthesized",
        }
        self._state.update(
            stage="proposed",
            reason="synthesized_program_passed_extra_diagnostic"
            if index is not None
            else "synthesized_program_matches_available_diagnostics",
        )
        self._checkpoint()
        return self._terminal_action()

    def dump(self):
        saved = {**copy.deepcopy(self._state), "library": self.library.dump()}
        require(
            len(serial(saved).encode()) <= 8 * 1024 * 1024,
            "Guarded checkpoint exceeds its bound",
        )
        return {**saved, "state_sha256": fingerprint(saved)}

    def _restore(self, state, terms):
        require(
            type(state) is dict and "state_sha256" in state,
            "Guarded checkpoint is malformed",
        )
        saved = copy.deepcopy(state)
        checksum = saved.pop("state_sha256")
        require(
            type(checksum) is str
            and HASH.fullmatch(checksum)
            and fingerprint(saved) == checksum,
            "Guarded checkpoint integrity failed",
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
                "charges",
                "shapes",
                "requirements",
                "evaluations",
                "candidates",
                "labels",
                "probe_receipts",
                "pending",
                "action_sequence",
                "lookup_complete",
                "lookup_truncated",
                "retained_audit_index",
                "retained_audit_candidates",
                "synthesis",
                "synthesis_action",
                "synthesis_attempts",
                "synthesis_audit_index",
                "diagnostic_rejection",
                "diagnostic_rejections",
                "selected",
                "history",
                "accounting_uncertain",
                "uncertain_reservation",
            },
            "Guarded checkpoint fields changed",
        )
        require(
            saved["terms"] == terms
            and saved["session_id"] == fingerprint(terms)
            and saved["inputs"] == self.inputs,
            "Guarded checkpoint source, inputs, solver or limits changed",
        )
        self.library = self.factory(self.source_id, self.config, state=library)
        self._state = saved
        require(saved["stage"] in STAGES, "Guarded stage is invalid")
        for name in ("lookup_complete", "lookup_truncated", "accounting_uncertain"):
            require(type(saved[name]) is bool, "Guarded state marker is invalid")
        bounded(saved["uncertain_reservation"], 0, self.limits["maximum_work"])
        require(
            type(saved["charges"]) is list and len(saved["charges"]) <= 10000,
            "Guarded charge collection is invalid",
        )
        total = {key: 0 for key in ("total", *COSTS, *COUNTERS)}
        for charge in saved["charges"]:
            require(
                type(charge) is dict
                and set(charge) == {"category", "amount", "counters"}
                and charge["category"] in COSTS
                and type(charge["counters"]) is dict
                and set(charge["counters"]) <= set(COUNTERS),
                "Guarded charge fields changed",
            )
            amount = bounded(charge["amount"], 0, 10000000)
            total["total"] += amount
            total[charge["category"]] += amount
            for key, value in charge["counters"].items():
                total[key] += bounded(value, 0, 10000000)
        require(
            saved["work"] == total
            and total["total"] <= self.limits["maximum_work"]
            and total["portfolio_work"] <= self.limits["lookup_work"],
            "Guarded work ledger disagrees",
        )
        require(
            type(saved["work"]) is dict
            and set(saved["work"]) == set(total)
            and all(type(value) is int for value in saved["work"].values())
            and total["synthesis_work"] <= self.limits["synthesis_work"],
            "Guarded work types or search quota changed",
        )
        require(
            saved["shapes"] == [_shape(value) for value in self.inputs],
            "Guarded parsed input shapes changed",
        )
        programs = self.library.dump()["programs"]
        require(
            saved["requirements"]
            == {
                ident: _required_fields(record["program"])[0]
                for ident, record in self._initial_programs.items()
            },
            "Guarded applicability index changed",
        )
        require(
            type(saved["probe_receipts"]) is dict
            and len(saved["probe_receipts"]) <= self.limits["maximum_probes"],
            "Guarded probe collection exceeds its bound",
        )
        labels, receipt_ids, action_sequences = {}, set(), set()
        for ident, record in saved["probe_receipts"].items():
            require(
                type(record) is dict and set(record) == {"action", "receipt", "sha256"},
                "Guarded returned probe record changed",
            )
            action = record["action"]
            self._validate_action(action)
            require(
                action["kind"] == "probe" and action["id"] == ident,
                "Guarded returned action identity changed",
            )
            self._validate_probe(action, record["receipt"])
            receipt = record["receipt"]
            require(
                record["sha256"] == fingerprint(receipt)
                and receipt["receipt_id"] not in receipt_ids
                and action["sequence"] not in action_sequences
                and str(receipt["input_index"]) not in labels,
                "Guarded probe receipt changed or repeats",
            )
            labels[str(receipt["input_index"])] = receipt["output"]
            receipt_ids.add(receipt["receipt_id"])
            action_sequences.add(action["sequence"])
        require(
            labels == saved["labels"]
            and len({self.inputs[int(index)] for index in labels}) == len(labels),
            "Guarded labels changed outside distinct receipts",
        )
        require(
            sum(
                record["receipt"]["work"] for record in saved["probe_receipts"].values()
            )
            == total["probe_work"]
            and len(labels) == total["probe_requests"],
            "Guarded probe accounting changed",
        )
        require(
            type(saved["evaluations"]) is dict
            and set(saved["evaluations"]) <= set(programs),
            "Guarded retained evaluation identity changed",
        )
        for ident, evaluations in saved["evaluations"].items():
            require(
                type(evaluations) is dict and len(evaluations) <= len(self.inputs),
                "Guarded retained evaluation bound changed",
            )
            for index, checked in evaluations.items():
                require(
                    type(index) is str
                    and index.isdigit()
                    and 0 <= int(index) < len(self.inputs)
                    and str(int(index)) == index
                    and type(checked) is dict
                    and set(checked) == {"output", "error", "work"},
                    "Guarded retained execution changed",
                )
                require(
                    checked["output"] is None
                    or type(checked["output"]) is str
                    and len(checked["output"].encode())
                    <= self.config["max_input_bytes"],
                    "Guarded retained output is invalid",
                )
                _validate_work(checked["work"], self.limits["lookup_work"])
        require(
            type(saved["candidates"]) is list
            and len(set(saved["candidates"])) == len(saved["candidates"])
            and all(ident in saved["evaluations"] for ident in saved["candidates"]),
            "Guarded candidates changed",
        )
        audit_index = saved["retained_audit_index"]
        audit_candidates = saved["retained_audit_candidates"]
        require(
            type(audit_candidates) is list
            and audit_candidates == sorted(set(audit_candidates))
            and all(
                ident in programs and ident in saved["evaluations"]
                for ident in audit_candidates
            ),
            "Guarded retained audit candidate identity changed",
        )
        if audit_index is not None:
            bounded(audit_index, 0, len(self.inputs) - 1)
            require(
                bool(audit_candidates)
                and all(
                    str(audit_index) in saved["evaluations"][ident]
                    for ident in audit_candidates
                )
                and (
                    str(audit_index) in labels
                    or saved["stage"] == "abstained"
                    or saved["pending"] is not None
                    and saved["pending"]["kind"] == "probe"
                    and saved["pending"]["input_index"] == audit_index
                ),
                "Guarded retained audit lost its execution or probe binding",
            )
        else:
            require(not audit_candidates, "Guarded retained audit lacks an input")
        bounded(saved["action_sequence"], 0, 66)
        if saved["pending"] is not None:
            self._validate_action(saved["pending"])
            require(
                saved["pending"]["sequence"] == saved["action_sequence"],
                "Guarded pending sequence changed",
            )
        require(
            type(saved["history"]) is list and len(saved["history"]) <= 66,
            "Guarded history exceeds its bound",
        )
        require(
            type(saved["synthesis_attempts"]) is list
            and len(saved["synthesis_attempts"]) <= 2,
            "Guarded synthesis attempt bound changed",
        )
        synthesis_total = 0
        for attempt in saved["synthesis_attempts"]:
            require(
                type(attempt) is dict
                and set(attempt)
                == {"action", "proposal", "training_indices", "training_sha256"},
                "Guarded synthesis attempt fields changed",
            )
            self._validate_action(attempt["action"])
            require(
                attempt["action"]["kind"] == "synthesize"
                and attempt["action"]["sequence"] not in action_sequences,
                "Guarded synthesis sequence changed",
            )
            action_sequences.add(attempt["action"]["sequence"])
            indices = attempt["training_indices"]
            require(
                type(indices) is list
                and len(indices) >= self.limits["minimum_evidence"]
                and indices == sorted(set(indices))
                and all(
                    type(index) is int and str(index) in labels for index in indices
                ),
                "Guarded synthesis training indices changed",
            )
            training_sha = fingerprint(
                [
                    [self.inputs[index] for index in indices],
                    [labels[str(index)] for index in indices],
                ]
            )
            require(
                attempt["training_sha256"] == training_sha,
                "Guarded synthesis training commitment changed",
            )
            proposal = attempt["proposal"]
            require(
                type(proposal) is dict
                and (
                    proposal["program"] is None
                    or proposal["program_sha256"] == fingerprint(proposal["program"])
                ),
                "Guarded synthesized proposal changed",
            )
            _validate_work(proposal["work"], attempt["action"]["maximum_work"])
            cost = proposal["work"]["total"]
            require(
                attempt["action"]["maximum_work"]
                <= self.limits["synthesis_work"] - synthesis_total,
                "Guarded synthesis reserved extra quota",
            )
            synthesis_total += cost
            if proposal["program"] is not None:
                entry = library["pending"].get(proposal["proposal_id"]) or library[
                    "receipts"
                ].get(proposal["proposal_id"])
                require(
                    entry is not None
                    and entry["program_sha256"] == proposal["program_sha256"],
                    "Guarded synthesis proposal lost its library binding",
                )
                if "training_sha256" in entry:
                    library_training = (
                        fingerprint(
                            [
                                [self.inputs[index] for index in indices],
                                [labels[str(index)] for index in indices],
                            ]
                        )
                        if self.config["version"] == "safe-text-v1"
                        else fingerprint(
                            {
                                "inputs": [self.inputs[index] for index in indices],
                                "outputs": [labels[str(index)] for index in indices],
                            }
                        )
                    )
                    require(
                        entry["training_sha256"] == library_training,
                        "Guarded library training identity changed",
                    )
        require(
            synthesis_total == total["synthesis_work"],
            "Guarded cumulative synthesis work changed",
        )
        if saved["synthesis_attempts"]:
            latest = saved["synthesis_attempts"][-1]
            require(
                saved["synthesis_action"] == latest["action"]
                and saved["synthesis"] == latest["proposal"],
                "Guarded latest synthesis identity changed",
            )
        else:
            require(
                saved["synthesis_action"] is None and saved["synthesis"] is None,
                "Guarded synthesis is unbound",
            )
        require(
            type(saved["diagnostic_rejections"]) is list
            and len(saved["diagnostic_rejections"]) <= 2,
            "Guarded diagnostic rejection bound changed",
        )
        for rejection in saved["diagnostic_rejections"]:
            require(
                type(rejection) is dict
                and set(rejection)
                == {
                    "proposal_id",
                    "program_sha256",
                    "input_index",
                    "input_sha256",
                    "expected_sha256",
                    "observed_sha256",
                    "error",
                    "work",
                    "receipt",
                    "evidence",
                },
                "Guarded diagnostic rejection fields changed",
            )
            index = bounded(rejection["input_index"], 0, len(self.inputs) - 1)
            require(str(index) in labels, "Guarded rejected diagnostic label is absent")
            evidence = rejection["evidence"]
            receipt = rejection["receipt"]
            require(
                evidence
                == {
                    "input": self.inputs[index],
                    "expected": labels[str(index)],
                    "observed": evidence["observed"],
                    "error": rejection["error"],
                }
                and (
                    evidence["observed"] != evidence["expected"]
                    or evidence["error"] is not None
                ),
                "Guarded diagnostic rejection is not an observed failure",
            )
            require(
                rejection["input_sha256"] == fingerprint(self.inputs[index])
                and rejection["expected_sha256"] == fingerprint(labels[str(index)])
                and rejection["observed_sha256"] == fingerprint(evidence["observed"]),
                "Guarded diagnostic rejection commitments changed",
            )
            require(
                receipt == library["receipts"].get(rejection["proposal_id"])
                and receipt["source_id"] == self.source_id
                and receipt["accepted"] is False
                and receipt["correct_rows"] == 0
                and receipt["total_rows"] == 1
                and receipt["evidence_sha256"] == fingerprint(evidence)
                and receipt["work"] == rejection["work"]["total"],
                "Guarded diagnostic rejection lost its negative library receipt",
            )
        require(
            saved["diagnostic_rejection"]
            == (
                saved["diagnostic_rejections"][-1]
                if saved["diagnostic_rejections"]
                else None
            ),
            "Guarded latest rejection changed",
        )
        if saved["synthesis_audit_index"] is not None:
            bounded(saved["synthesis_audit_index"], 0, len(self.inputs) - 1)
            require(saved["synthesis"] is not None, "Guarded audit lacks synthesis")
        chosen = saved["selected"]
        require(
            (chosen is not None) == (saved["stage"] == "proposed"),
            "Guarded terminal selection is missing or premature",
        )
        if chosen is not None:
            require(
                type(chosen) is dict
                and set(chosen)
                == {"program", "program_sha256", "proposal_id", "selection"}
                and chosen["program_sha256"] == fingerprint(chosen["program"])
                and len(labels) >= self.limits["minimum_evidence"],
                "Guarded selection commitment changed",
            )
            if chosen["selection"] == "retained":
                require(
                    chosen["proposal_id"] is None
                    and chosen["program_sha256"] in programs
                    and chosen["program"]
                    == programs[chosen["program_sha256"]]["program"],
                    "Guarded retained selection changed",
                )
                require(
                    audit_index is not None
                    and str(audit_index) in labels
                    and chosen["program_sha256"] in audit_candidates
                    and saved["evaluations"][chosen["program_sha256"]][
                        str(audit_index)
                    ]["output"]
                    == labels[str(audit_index)]
                    and saved["evaluations"][chosen["program_sha256"]][
                        str(audit_index)
                    ]["error"]
                    is None,
                    "Guarded retained selection lacks its independent audit",
                )
            else:
                proposal = saved["synthesis"]
                require(
                    chosen["selection"] == "synthesized"
                    and proposal is not None
                    and chosen["program"] == proposal["program"]
                    and chosen["proposal_id"] == proposal["proposal_id"],
                    "Guarded synthesized selection changed",
                )

    def result(self):
        selected = self._state["selected"] or {
            "program": None,
            "program_sha256": None,
            "proposal_id": None,
            "selection": None,
        }

        def safe_rejection(rejection):
            return (
                {
                    key: copy.deepcopy(value)
                    for key, value in rejection.items()
                    if key != "evidence"
                }
                if rejection is not None
                else None
            )

        pending = self.library.dump()["pending"]
        unresolved = [
            {
                "proposal_id": attempt["proposal"]["proposal_id"],
                "program_sha256": attempt["proposal"]["program_sha256"],
                "training_sha256": attempt["training_sha256"],
                "status": "requires_buyer_disposition",
                "reason": self._state["reason"],
                "synthesis_work": attempt["proposal"]["work"]["total"],
            }
            for attempt in self._state["synthesis_attempts"]
            if self._state["stage"] == "abstained"
            and attempt["proposal"]["proposal_id"] in pending
        ]

        return {
            "status": {"proposed": "proposed", "abstained": "abstained"}.get(
                self._state["stage"], "pending"
            ),
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
            "lookup_truncated": self._state["lookup_truncated"],
            "retained_audit_index": self._state["retained_audit_index"],
            "diagnostic_rejection": safe_rejection(self._state["diagnostic_rejection"]),
            "diagnostic_rejections": [
                safe_rejection(rejection)
                for rejection in self._state["diagnostic_rejections"]
            ],
            "synthesis_attempts": [
                {
                    "action_id": attempt["action"]["id"],
                    "proposal_id": attempt["proposal"]["proposal_id"],
                    "program_sha256": attempt["proposal"]["program_sha256"],
                    "training_sha256": attempt["training_sha256"],
                    "work": attempt["proposal"]["work"],
                }
                for attempt in self._state["synthesis_attempts"]
            ],
            "accounting_uncertain": self._state["accounting_uncertain"],
            "uncertain_reservation": self._state["uncertain_reservation"],
            "unresolved_proposals": unresolved,
        }

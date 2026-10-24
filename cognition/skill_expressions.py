"""Typed multi-field arithmetic and reusable, verified normalization programs.

The source is a bounded JSON array of four strings. Leaves select fields;
typed unary operations normalize them; binary arithmetic composes independent
subexpressions. The same grammar and interpreter serve every search branch.
Learned fragments change search order and grammar depth, never the underlying
operation language or the charged work of executing their primitive bodies.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal, InvalidOperation, localcontext

from cognition.skills import (
    HASH,
    SOURCE,
    WorkLimit,
    _features,
    _input,
    _int,
    _Meter,
    _operation,
    _types,
    fingerprint,
)
from cognition.skills import (
    SkillLibrary as _ReceiptBase,
)

VERSION = "safe-expression-v1"
STATE_VERSION = "verified-expression-library-v1"
MAX_DEPTH = 8
MAX_STATES = 4096
BINARY = ("add", "subtract", "multiply")
DEFAULT_EXPRESSION_CONFIG = {
    "version": VERSION,
    "operations": [
        *[{"op": "field", "index": index} for index in range(4)],
        {"op": "strip"},
        {"op": "prefix", "value": "value="},
        {"op": "prefix", "value": "n="},
        {"op": "suffix", "value": " units"},
        {"op": "suffix", "value": "%"},
        {"op": "decimal"},
        {"op": "scale", "numerator": 1, "denominator": 100},
        {"op": "scale", "numerator": 100, "denominator": 1},
        {"op": "scale", "numerator": 1, "denominator": 1000},
        {"op": "scale", "numerator": 1000, "denominator": 1},
        {"op": "format"},
        *[{"op": name} for name in BINARY],
    ],
    "max_ops": 15,
    "max_candidates": 20000,
    "max_rows": 32,
    "max_input_bytes": 512,
    "max_programs": 64,
    "max_macros": 32,
}


def _registered(operation):
    if type(operation) is not dict:
        raise ValueError("Expression primitive must be an object")
    if operation.get("op") == "field":
        if set(operation) != {"op", "index"}:
            raise ValueError("Expression field primitive is malformed")
        _int(operation["index"], 0, 3)
        return copy.deepcopy(operation)
    if operation.get("op") in BINARY:
        if set(operation) != {"op"}:
            raise ValueError("Expression arithmetic primitive is malformed")
        return dict(operation)
    clean = _operation(operation)
    if clean["op"] not in ("strip", "prefix", "suffix", "decimal", "scale", "format"):
        raise ValueError("Expression unary primitive is not supported")
    return clean


def normalize_config(config=DEFAULT_EXPRESSION_CONFIG):
    if (
        type(config) is not dict
        or set(config) != set(DEFAULT_EXPRESSION_CONFIG)
        or config["version"] != VERSION
    ):
        raise ValueError("Expression configuration fields are invalid")
    bounds = {
        "max_ops": (1, 31),
        "max_candidates": (1, 100000),
        "max_rows": (1, 64),
        "max_input_bytes": (1, 512),
        "max_programs": (1, 128),
        "max_macros": (0, 64),
    }
    for key, pair in bounds.items():
        _int(config[key], *pair)
    if (
        type(config["operations"]) is not list
        or not 1 <= len(config["operations"]) <= 32
    ):
        raise ValueError("Expression primitive registry exceeds its bound")
    operations = [_registered(op) for op in config["operations"]]
    if len({fingerprint(op) for op in operations}) != len(operations):
        raise ValueError("Expression primitive registry repeats an operation")
    clean = copy.deepcopy(config)
    clean["operations"] = operations
    return clean


def _shape(node, allowed, *, depth=1):
    if depth > MAX_DEPTH or type(node) is not dict or type(node.get("op")) is not str:
        raise ValueError("Expression syntax or depth is invalid")
    name = node["op"]
    if name == "field":
        primitive = _registered(node)
        kind, size = "text", 1
    elif name in BINARY:
        if set(node) != {"op", "left", "right"}:
            raise ValueError("Expression arithmetic node is malformed")
        primitive = {"op": name}
        left_kind, left_size = _shape(node["left"], allowed, depth=depth + 1)
        right_kind, right_size = _shape(node["right"], allowed, depth=depth + 1)
        if left_kind != "number" or right_kind != "number":
            raise ValueError("Expression arithmetic needs numeric children")
        kind, size = "number", 1 + left_size + right_size
    else:
        if "arg" not in node:
            raise ValueError("Expression unary child is missing")
        primitive = _registered(
            {key: value for key, value in node.items() if key != "arg"}
        )
        child_kind, child_size = _shape(node["arg"], allowed, depth=depth + 1)
        kind = _types([primitive], child_kind)
        size = 1 + child_size
    if fingerprint(primitive) not in allowed:
        raise ValueError("Expression uses an unenrolled primitive")
    return kind, size


def validate_program(program, config=DEFAULT_EXPRESSION_CONFIG):
    config = normalize_config(config)
    if (
        type(program) is not dict
        or set(program) != {"version", "expr"}
        or program["version"] != VERSION
    ):
        raise ValueError("Expression program envelope is invalid")
    allowed = {fingerprint(op) for op in config["operations"]}
    kind, size = _shape(program["expr"], allowed)
    if kind != "text" or size > config["max_ops"]:
        raise ValueError("Expression result type or node count is invalid")
    return copy.deepcopy(program)


def _number(value):
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or value.copy_abs() > Decimal("1e30")
        or abs(value.as_tuple().exponent) > 64
    ):
        raise ValueError("Expression number exceeds its declared bounds")
    return value


def _formatted(value):
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if result in ("-0", "") else result


def _primitive(operation, operands, meter, config):
    meter.charge("primitive_attempts")
    for value in operands:
        meter.data(value)
    try:
        name = operation["op"]
        value = operands[0]
        if name == "field":
            record = json.loads(value)
            if (
                type(record) is not list
                or len(record) != 4
                or any(type(item) is not str for item in record)
            ):
                raise ValueError("Expression input must be four text fields")
            result = record[operation["index"]]
        elif name == "strip":
            result = value.strip()
        elif name in ("prefix", "suffix"):
            affix = operation["value"]
            if name == "prefix" and value.startswith(affix):
                result = value[len(affix) :]
            elif name == "suffix" and value.endswith(affix):
                result = value[: -len(affix)]
            else:
                raise ValueError("Expression affix is absent")
        elif name == "decimal":
            result = Decimal(value)
        elif name == "format":
            result = _formatted(value)
        else:
            with localcontext() as context:
                context.prec = 64
                if name == "scale":
                    result = value * operation["numerator"] / operation["denominator"]
                elif name == "add":
                    result = value + operands[1]
                elif name == "subtract":
                    result = value - operands[1]
                elif name == "multiply":
                    result = value * operands[1]
                else:
                    raise ValueError("Unregistered expression primitive")
        return _number(result) if type(result) is Decimal else _input(result, config)
    except (ValueError, TypeError, IndexError, InvalidOperation):
        meter.values["errors"] += 1
        raise ValueError("Expression primitive failed on supplied data") from None


def _evaluate(node, source, meter, config):
    if node["op"] == "field":
        return _primitive(node, [source], meter, config)
    if node["op"] in BINARY:
        left = _evaluate(node["left"], source, meter, config)
        right = _evaluate(node["right"], source, meter, config)
        return _primitive({"op": node["op"]}, [left, right], meter, config)
    value = _evaluate(node["arg"], source, meter, config)
    operation = {key: item for key, item in node.items() if key != "arg"}
    return _primitive(operation, [value], meter, config)


def execute_checked(program, value, config=DEFAULT_EXPRESSION_CONFIG, budget=10000):
    config = normalize_config(config)
    program = validate_program(program, config)
    value = _input(value, config)
    meter = _Meter(budget)
    try:
        result = _evaluate(program["expr"], value, meter, config)
    except ValueError as error:
        return {
            "output": None,
            "error": "work_limit"
            if isinstance(error, WorkLimit)
            else "operation_failed",
            "work": copy.deepcopy(meter.values),
        }
    return {"output": result, "error": None, "work": copy.deepcopy(meter.values)}


def _fragments(node):
    """Extract parameterized unary chains, preserving their typed child hole."""
    if node["op"] in BINARY:
        yield from _fragments(node["left"])
        yield from _fragments(node["right"])
    elif node["op"] != "field":
        ops, current = [], node
        while current["op"] not in (*BINARY, "field") and len(ops) < 4:
            ops.insert(
                0, {key: value for key, value in current.items() if key != "arg"}
            )
            if len(ops) >= 2:
                yield copy.deepcopy(ops)
            current = current["arg"]
        yield from _fragments(node["arg"])


def _size(node):
    if node["op"] == "field":
        return 1
    if node["op"] in BINARY:
        return 1 + _size(node["left"]) + _size(node["right"])
    return 1 + _size(node["arg"])


def _depth(node):
    if node["op"] == "field":
        return 1
    if node["op"] in BINARY:
        return 1 + max(_depth(node["left"]), _depth(node["right"]))
    return 1 + _depth(node["arg"])


class ExpressionLibrary:
    """A source-isolated library of exact-checked, composable expression skills."""

    dump = _ReceiptBase.dump
    _rank = _ReceiptBase._rank
    _receipt = _ReceiptBase._receipt
    admit_verified = _ReceiptBase.admit_verified
    serve = _ReceiptBase.serve

    def __init__(self, source_id, config=DEFAULT_EXPRESSION_CONFIG, state=None):
        if type(source_id) is not str or not SOURCE.fullmatch(source_id):
            raise ValueError("Expression source identity is invalid")
        self.source_id = source_id
        self.config = normalize_config(config)
        self.config_sha256 = fingerprint(self.config)
        self.state = (
            copy.deepcopy(state)
            if state is not None
            else {
                "version": STATE_VERSION,
                "source_id": source_id,
                "config_sha256": self.config_sha256,
                "sequence": 0,
                "total_work": 0,
                "programs": {},
                "macros": {},
                "pending": {},
                "receipts": {},
            }
        )
        self._validate_state()

    def _validate_state(self):
        state = self.state
        required = {
            "version",
            "source_id",
            "config_sha256",
            "sequence",
            "total_work",
            "programs",
            "macros",
            "pending",
            "receipts",
        }
        if (
            type(state) is not dict
            or set(state) != required
            or state["version"] != STATE_VERSION
            or state["source_id"] != self.source_id
            or state["config_sha256"] != self.config_sha256
        ):
            raise ValueError("Expression checkpoint terms are incompatible")
        _int(state["sequence"], 0, 2**53 - 1)
        _int(state["total_work"], 0, 2**53 - 1)
        for key, maximum in (
            ("programs", self.config["max_programs"]),
            ("macros", self.config["max_macros"]),
            ("pending", 64),
            ("receipts", 256),
        ):
            if type(state[key]) is not dict or len(state[key]) > maximum:
                raise ValueError("Expression checkpoint collection exceeds its bound")
        for key, record in state["programs"].items():
            if type(record) is not dict or set(record) != {
                "program",
                "uses",
                "features",
                "evidence_sha256",
            }:
                raise ValueError("Retained expression record is invalid")
            if (
                key != fingerprint(validate_program(record["program"], self.config))
                or type(record["evidence_sha256"]) is not str
                or not HASH.fullmatch(record["evidence_sha256"])
            ):
                raise ValueError("Retained expression commitment is invalid")
            _features(record["features"])
            _int(record["uses"], 1, 2**53 - 1)
        for key, record in state["macros"].items():
            if (
                type(record) is not dict
                or set(record)
                != {"ops", "uses", "parent_program_sha256", "evidence_sha256"}
                or key != fingerprint(record["ops"])
            ):
                raise ValueError("Retained expression fragment is invalid")
            parent = state["programs"].get(record["parent_program_sha256"])
            if parent is None or record["ops"] not in list(
                _fragments(parent["program"]["expr"])
            ):
                raise ValueError("Expression fragment has no verified parent")
            if type(record["evidence_sha256"]) is not str or not HASH.fullmatch(
                record["evidence_sha256"]
            ):
                raise ValueError("Expression fragment evidence is invalid")
            _int(record["uses"], 1, 2**53 - 1)
        for key, record in state["pending"].items():
            if type(record) is not dict or set(record) != {
                "program",
                "program_sha256",
                "training_sha256",
                "features",
                "work",
                "sequence",
            }:
                raise ValueError("Pending expression record is invalid")
            if record["program_sha256"] != fingerprint(
                validate_program(record["program"], self.config)
            ):
                raise ValueError("Pending expression program commitment changed")
            if type(record["training_sha256"]) is not str or not HASH.fullmatch(
                record["training_sha256"]
            ):
                raise ValueError("Pending expression training commitment changed")
            _features(record["features"])
            _int(record["work"], 0, 2**53 - 1)
            _int(record["sequence"], 1, state["sequence"])
            if key != fingerprint(
                {
                    "source_id": self.source_id,
                    "config_sha256": self.config_sha256,
                    **record,
                }
            ):
                raise ValueError("Pending expression identity changed")
        for key, receipt in state["receipts"].items():
            self._receipt(receipt)
            if key != receipt["proposal_id"] or key in state["pending"]:
                raise ValueError("Expression buyer receipt identity changed")
        if len({r["evaluation_id"] for r in state["receipts"].values()}) != len(
            state["receipts"]
        ):
            raise ValueError("Expression checkpoint repeats an evaluation")
        if state["total_work"] < sum(
            r["work"] for r in state["pending"].values()
        ) + sum(r["work"] for r in state["receipts"].values()):
            raise ValueError("Expression checkpoint omits charged work")

    def _resolve(self, proposal_id, receipt, retain):
        receipt = copy.deepcopy(self._receipt(receipt))
        if proposal_id != receipt["proposal_id"]:
            raise ValueError("Expression receipt names another proposal")
        old = self.state["receipts"].get(proposal_id)
        if old is not None:
            if old != receipt:
                raise ValueError("A committed expression receipt changed")
            return copy.deepcopy(old)
        pending = self.state["pending"].get(proposal_id)
        if pending is None or pending["program_sha256"] != receipt["program_sha256"]:
            raise ValueError("Expression receipt has no matching proposal")
        if any(
            item["evaluation_id"] == receipt["evaluation_id"]
            for item in self.state["receipts"].values()
        ):
            raise ValueError("Expression evaluation identity was already consumed")
        if receipt["accepted"] and retain:
            programs = self.state["programs"]
            key = pending["program_sha256"]
            if key in programs:
                programs[key]["uses"] += 1
                programs[key]["features"] = pending["features"]
                programs[key]["evidence_sha256"] = receipt["evidence_sha256"]
            elif len(programs) < self.config["max_programs"]:
                programs[key] = {
                    "program": pending["program"],
                    "uses": 1,
                    "features": pending["features"],
                    "evidence_sha256": receipt["evidence_sha256"],
                }
            if key in programs:
                for ops in _fragments(pending["program"]["expr"]):
                    identity = fingerprint(ops)
                    macros = self.state["macros"]
                    if identity in macros:
                        macros[identity]["uses"] += 1
                    elif len(macros) < self.config["max_macros"]:
                        macros[identity] = {
                            "ops": ops,
                            "uses": 1,
                            "parent_program_sha256": key,
                            "evidence_sha256": receipt["evidence_sha256"],
                        }
        self.state["total_work"] += receipt["work"]
        self.state["pending"].pop(proposal_id)
        self.state["receipts"][proposal_id] = receipt
        while len(self.state["receipts"]) > 256:
            self.state["receipts"].pop(next(iter(self.state["receipts"])))
        return copy.deepcopy(receipt)

    def validate_and_admit(
        self,
        proposal_id,
        inputs,
        outputs,
        evaluation_id,
        *,
        retain=True,
        reveal_counterexample=False,
    ):
        inputs, outputs = self._examples(inputs, outputs)
        if (
            type(retain) is not bool
            or type(reveal_counterexample) is not bool
            or type(evaluation_id) is not str
            or not HASH.fullmatch(evaluation_id)
        ):
            raise ValueError("Expression buyer evaluation terms are invalid")
        pending = self.state["pending"].get(proposal_id)
        if pending is None:
            raise ValueError("Expression buyer evaluation needs a pending proposal")
        observed, work = [], 0
        for value in inputs:
            checked = execute_checked(pending["program"], value, self.config)
            observed.append(checked["output"])
            work += checked["work"]["total"]
        correct = sum(
            value == target for value, target in zip(observed, outputs, strict=True)
        )
        receipt = {
            "source_id": self.source_id,
            "proposal_id": proposal_id,
            "program_sha256": pending["program_sha256"],
            "evaluation_id": evaluation_id,
            "accepted": correct == len(inputs),
            "correct_rows": correct,
            "total_rows": len(inputs),
            "evidence_sha256": fingerprint(
                {"inputs": inputs, "outputs": outputs, "observed": observed}
            ),
            "work": work,
        }
        result = self._resolve(proposal_id, receipt, retain)
        if reveal_counterexample:
            result["counterexample"] = next(
                (
                    {"input": value, "expected": target, "observed": actual}
                    for value, target, actual in zip(
                        inputs, outputs, observed, strict=True
                    )
                    if target != actual
                ),
                None,
            )
        return result

    def _examples(self, inputs, outputs):
        if (
            type(inputs) is not list
            or type(outputs) is not list
            or not 1 <= len(inputs) <= self.config["max_rows"]
            or len(inputs) != len(outputs)
        ):
            raise ValueError("Expression example rows are not bounded and equal")
        return [_input(x, self.config) for x in inputs], [
            _input(x, self.config) for x in outputs
        ]

    def solve(self, inputs, outputs, mode="fresh", budget=20000, features=None):
        inputs, outputs = self._examples(inputs, outputs)
        features = _features(features)
        if (
            mode
            not in (
                "fresh",
                "retrieval",
                "macro",
                "contextual",
                "factored",
                "retained_factored",
            )
            or len(self.state["pending"]) >= 64
        ):
            raise ValueError("Expression search mode or pending count is invalid")
        meter = _Meter(budget)
        if mode in ("factored", "retained_factored"):
            from cognition.skill_factored import solve_factored

            retained = []
            if mode == "retained_factored":
                records = sorted(
                    self.state["programs"].values(),
                    key=lambda record: (
                        -record["uses"],
                        fingerprint(record["program"]),
                    ),
                )
                retained = [record["program"] for record in records]
            result = solve_factored(inputs, outputs, self.config, meter, retained)
            return self._report(
                result["program"],
                meter,
                mode,
                features,
                inputs,
                outputs,
                result["exhausted"],
                result["semantic_states"],
            )
        found, exhausted = None, False
        target = ("text", tuple(outputs))
        states, layers = {}, {}
        operations = self.config["operations"]
        fields = [op for op in operations if op["op"] == "field"]
        unary = [[op] for op in operations if op["op"] not in (*BINARY, "field")]
        binary = [op for op in operations if op["op"] in BINARY]
        programs = list(self.state["programs"].values()) if mode != "fresh" else []
        programs.sort(
            key=lambda r: (
                self._rank(r, features if mode == "contextual" else None),
                fingerprint(r["program"]),
            ),
            reverse=True,
        )
        if mode in ("macro", "contextual"):
            macros = list(self.state["macros"].values())
            macros.sort(key=lambda r: (-r["uses"], fingerprint(r["ops"])))
            if mode == "contextual":
                preference = {
                    fingerprint(ops): rank
                    for rank, record in reversed(list(enumerate(programs)))
                    for ops in _fragments(record["program"]["expr"])
                }
                macros.sort(
                    key=lambda r: (
                        preference.get(fingerprint(r["ops"]), 100000),
                        -r["uses"],
                        fingerprint(r["ops"]),
                    )
                )
            unary = [r["ops"] for r in macros] + unary

        def admit(node, kind, values, cost):
            nonlocal found
            size, depth = _size(node), _depth(node)
            if size > self.config["max_ops"] or depth > MAX_DEPTH:
                return
            rendered = [
                _formatted(value) if type(value) is Decimal else value
                for value in values
            ]
            meter.data(json.dumps(rendered, separators=(",", ":")))
            key = kind, tuple(rendered)
            if key == target:
                found = {"version": VERSION, "expr": node}
                return
            previous = states.get(key, [])
            if any(
                record["cost"] <= cost and record["size"] <= size for record in previous
            ):
                meter.values["semantic_duplicates"] += 1
                return
            if key not in states and len(states) >= MAX_STATES:
                raise WorkLimit("Expression semantic state allowance is exhausted")
            record = {
                "node": node,
                "kind": kind,
                "values": values,
                "size": size,
                "depth": depth,
                "cost": cost,
            }
            states[key] = [
                old
                for old in previous
                if not (old["cost"] >= cost and old["size"] >= size)
            ] + [record]
            layers.setdefault(cost, []).append(record)

        def attempt_unary(child, chunk, cost):
            kind = _types(chunk, child["kind"])
            if (
                child["size"] + len(chunk) > self.config["max_ops"]
                or child["depth"] + len(chunk) > MAX_DEPTH
            ):
                return
            meter.charge("candidates")
            values, node = child["values"], child["node"]
            for operation in chunk:
                values = [
                    _primitive(operation, [value], meter, self.config)
                    for value in values
                ]
                node = {**operation, "arg": node}
            admit(node, kind, values, cost)

        def attempt_binary(left, right, operation, cost):
            if (
                left["size"] + right["size"] + 1 > self.config["max_ops"]
                or max(left["depth"], right["depth"]) + 1 > MAX_DEPTH
            ):
                return
            if operation["op"] in ("add", "multiply") and fingerprint(
                left["node"]
            ) > fingerprint(right["node"]):
                return
            meter.charge("candidates")
            values = [
                _primitive(operation, [a, b], meter, self.config)
                for a, b in zip(left["values"], right["values"], strict=True)
            ]
            admit(
                {"op": operation["op"], "left": left["node"], "right": right["node"]},
                "number",
                values,
                cost,
            )

        try:
            for record in programs:
                meter.charge("candidates")
                try:
                    values = [
                        _evaluate(record["program"]["expr"], value, meter, self.config)
                        for value in inputs
                    ]
                except ValueError as error:
                    if isinstance(error, WorkLimit):
                        raise
                    continue
                if values == outputs:
                    found = record["program"]
                    break
            if found is None:
                for operation in fields:
                    meter.charge("candidates")
                    try:
                        values = [
                            _primitive(operation, [value], meter, self.config)
                            for value in inputs
                        ]
                        admit(operation, "text", values, 1)
                    except ValueError as error:
                        if isinstance(error, WorkLimit):
                            raise
                for cost in range(2, self.config["max_ops"] + 1):
                    if found is not None:
                        break
                    for child in list(layers.get(cost - 1, [])):
                        if found is not None:
                            break
                        for chunk in unary:
                            if (
                                meter.values["candidates"]
                                >= self.config["max_candidates"]
                            ):
                                raise WorkLimit(
                                    "Expression candidate allowance is exhausted"
                                )
                            try:
                                attempt_unary(child, chunk, cost)
                            except ValueError as error:
                                if isinstance(error, WorkLimit):
                                    raise
                            if found is not None:
                                break
                    if found is not None:
                        break
                    for left_cost in range(1, cost - 1):
                        right_cost = cost - left_cost - 1
                        for left in list(layers.get(left_cost, [])):
                            if left["kind"] != "number":
                                continue
                            for right in list(layers.get(right_cost, [])):
                                if right["kind"] != "number":
                                    continue
                                for operation in binary:
                                    if (
                                        meter.values["candidates"]
                                        >= self.config["max_candidates"]
                                    ):
                                        raise WorkLimit(
                                            "Expression candidate allowance is exhausted"
                                        )
                                    try:
                                        attempt_binary(left, right, operation, cost)
                                    except ValueError as error:
                                        if isinstance(error, WorkLimit):
                                            raise
                                    if found is not None:
                                        break
                                if found is not None:
                                    break
                            if found is not None:
                                break
                        if found is not None:
                            break
        except WorkLimit:
            exhausted = True
        return self._report(
            found, meter, mode, features, inputs, outputs, exhausted, len(states)
        )

    def _report(
        self, found, meter, mode, features, inputs, outputs, exhausted, semantic_states
    ):
        self.state["sequence"] = _int(self.state["sequence"] + 1, 1, 2**53 - 1)
        self.state["total_work"] = _int(
            self.state["total_work"] + meter.values["total"], 0, 2**53 - 1
        )
        proposal_id = None
        if found is not None:
            found = validate_program(found, self.config)
            pending = {
                "program": found,
                "program_sha256": fingerprint(found),
                "training_sha256": fingerprint({"inputs": inputs, "outputs": outputs}),
                "features": features,
                "work": meter.values["total"],
                "sequence": self.state["sequence"],
            }
            proposal_id = fingerprint(
                {
                    "source_id": self.source_id,
                    "config_sha256": self.config_sha256,
                    **pending,
                }
            )
            self.state["pending"][proposal_id] = pending
        return {
            "proposal_id": proposal_id,
            "program": copy.deepcopy(found),
            "program_sha256": fingerprint(found) if found is not None else None,
            "solved_training": found is not None,
            "mode": mode,
            "exhausted": exhausted,
            "work": copy.deepcopy(meter.values),
            "semantic_states": semantic_states,
            "source_id": self.source_id,
            "config_sha256": self.config_sha256,
        }

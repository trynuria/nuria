"""Bounded typed transformation synthesis and buyer-verified reusable programs.

Programs are data interpreted by a fixed operation registry. No operation can
open a file, start a process, access a network or execute supplied source code.
Library learning and guided enumeration are conventional program-synthesis
mechanisms; their existence does not establish a neural advantage.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import json
import math
import re
from decimal import Decimal, InvalidOperation, localcontext

VERSION = "safe-transform-v1"
STATE_VERSION = "verified-skill-library-v1"
HASH = re.compile(r"[a-f0-9]{64}\Z")
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
MAX_SEMANTIC_STATES = 4096
DEFAULT_CONFIG = {
    "version": VERSION,
    "operations": [
        {"op": "strip"},
        {"op": "prefix", "value": "value="},
        {"op": "prefix", "value": "n="},
        {"op": "suffix", "value": " units"},
        {"op": "suffix", "value": "%"},
        {"op": "split", "separator": "|", "index": 1},
        {"op": "split", "separator": ":", "index": 1},
        {"op": "json"},
        {"op": "decimal"},
        {"op": "scale", "numerator": 1, "denominator": 100},
        {"op": "scale", "numerator": 100, "denominator": 1},
        {"op": "scale", "numerator": 1, "denominator": 10},
        {"op": "scale", "numerator": 10, "denominator": 1},
        {"op": "scale", "numerator": 1, "denominator": 1000},
        {"op": "scale", "numerator": 1000, "denominator": 1},
        {"op": "format"},
    ],
    "max_ops": 8,
    "max_candidates": 20000,
    "max_rows": 32,
    "max_input_bytes": 256,
    "max_programs": 64,
    "max_macros": 32,
}


def fingerprint(value):
    """Canonical JSON commitment, matching project artifact commitments."""
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Skill integer is outside its bound")
    return value


def _operation(operation):
    if type(operation) is not dict or type(operation.get("op")) is not str:
        raise ValueError("Skill operation is malformed")
    name = operation["op"]
    if name in ("strip", "json", "decimal", "format"):
        required = {"op"}
    elif name in ("prefix", "suffix"):
        required = {"op", "value"}
        value = operation.get("value")
        if type(value) is not str or not 1 <= len(value.encode()) <= 32:
            raise ValueError("Skill affix is invalid")
    elif name == "split":
        required = {"op", "separator", "index"}
        separator = operation.get("separator")
        if type(separator) is not str or not 1 <= len(separator.encode()) <= 8:
            raise ValueError("Skill delimiter is invalid")
        _int(operation.get("index"), 0, 7)
    elif name == "scale":
        required = {"op", "numerator", "denominator"}
        _int(operation.get("numerator"), 1, 10000)
        _int(operation.get("denominator"), 1, 10000)
    else:
        raise ValueError("Skill operation is not registered")
    if set(operation) != required:
        raise ValueError("Skill operation fields are invalid")
    return copy.deepcopy(operation)


def validate_config(config=DEFAULT_CONFIG):
    if type(config) is not dict or set(config) != set(DEFAULT_CONFIG):
        raise ValueError("Skill configuration fields are invalid")
    if config["version"] != VERSION:
        raise ValueError("Skill configuration version is incompatible")
    bounds = {
        "max_ops": (1, 10),
        "max_candidates": (1, 100000),
        "max_rows": (1, 64),
        "max_input_bytes": (1, 512),
        "max_programs": (1, 128),
        "max_macros": (0, 64),
    }
    for key, (low, high) in bounds.items():
        _int(config[key], low, high)
    operations = config["operations"]
    if type(operations) is not list or not 1 <= len(operations) <= 32:
        raise ValueError("Skill primitive registry is not bounded")
    clean = [_operation(operation) for operation in operations]
    if len({fingerprint(operation) for operation in clean}) != len(clean):
        raise ValueError("Skill primitive registry repeats an operation")
    result = copy.deepcopy(config)
    result["operations"] = clean
    return result


normalize_config = validate_config


def _types(ops, start="text"):
    kind = start
    for operation in ops:
        name = operation["op"]
        required = "number" if name in ("scale", "format") else "text"
        if kind != required:
            raise ValueError("Skill operation input type does not match")
        kind = "number" if name in ("decimal", "scale") else "text"
    return kind


def validate_program(program, config=DEFAULT_CONFIG):
    config = validate_config(config)
    if (
        type(program) is not dict
        or set(program) != {"version", "ops"}
        or program["version"] != VERSION
        or type(program["ops"]) is not list
        or len(program["ops"]) > config["max_ops"]
    ):
        raise ValueError("Skill program structure is invalid")
    allowed = {fingerprint(op) for op in config["operations"]}
    ops = [_operation(op) for op in program["ops"]]
    if any(fingerprint(op) not in allowed for op in ops):
        raise ValueError("Skill program uses an unenrolled primitive")
    if _types(ops) != "text":
        raise ValueError("Skill program must produce canonical text")
    return {"version": VERSION, "ops": ops}


class WorkLimit(ValueError):
    """A finite work allowance was exhausted before another operation."""


class _Meter:
    def __init__(self, limit):
        self.limit = _int(limit, 1, 10000000)
        self.values = {
            "total": 0,
            "primitive_attempts": 0,
            "candidates": 0,
            "byte_blocks": 0,
            "errors": 0,
            "semantic_duplicates": 0,
        }

    def charge(self, key, amount=1):
        if self.values["total"] + amount > self.limit:
            raise WorkLimit("Skill work allowance is exhausted")
        self.values[key] += amount
        self.values["total"] += amount

    def data(self, value):
        self.charge("byte_blocks", max(1, (len(str(value).encode()) + 31) // 32))


def _input(value, config):
    if type(value) is not str or len(value.encode()) > config["max_input_bytes"]:
        raise ValueError("Skill input must be bounded text")
    return value


def _apply(operation, value, meter, config):
    meter.charge("primitive_attempts")
    meter.data(value)
    try:
        name = operation["op"]
        if name == "strip":
            result = value.strip()
        elif name == "prefix":
            if not value.startswith(operation["value"]):
                raise ValueError("Required prefix is absent")
            result = value[len(operation["value"]) :]
        elif name == "suffix":
            if not value.endswith(operation["value"]):
                raise ValueError("Required suffix is absent")
            result = value[: -len(operation["value"])]
        elif name == "split":
            result = value.split(operation["separator"])[operation["index"]]
        elif name == "json":
            result = json.loads(value)
            if type(result) is not str:
                raise ValueError("JSON operation accepts only a quoted string")
        elif name == "decimal":
            result = Decimal(value)
        elif name == "scale":
            with localcontext() as context:
                context.prec = 64
                result = value * operation["numerator"] / operation["denominator"]
        elif name == "format":
            result = format(value, "f")
            if "." in result:
                result = result.rstrip("0").rstrip(".")
            if result in ("-0", ""):
                result = "0"
        else:
            raise ValueError("Unregistered operation")
        if type(result) is Decimal:
            if not result.is_finite() or abs(result) > Decimal("1e30"):
                raise ValueError("Skill numeric result is outside its bound")
            if abs(result.as_tuple().exponent) > 64:
                raise ValueError("Skill numeric precision is outside its bound")
        else:
            _input(result, config)
        return result
    except (ValueError, IndexError, TypeError, InvalidOperation):
        meter.values["errors"] += 1
        raise ValueError("Skill operation failed on supplied input") from None


def execute(program, value, config=DEFAULT_CONFIG, budget=10000):
    result = execute_checked(program, value, config, budget)
    if result["error"] is not None:
        error = ValueError("Skill program failed on supplied input")
        error.work = result["work"]
        raise error
    return result["output"], result["work"]


def execute_checked(program, value, config=DEFAULT_CONFIG, budget=10000):
    """Return measured attempted work even when a valid program rejects a row."""
    config = validate_config(config)
    program = validate_program(program, config)
    value = _input(value, config)
    meter = _Meter(budget)
    try:
        for operation in program["ops"]:
            value = _apply(operation, value, meter, config)
    except ValueError as error:
        return {
            "output": None,
            "error": "work_limit"
            if isinstance(error, WorkLimit)
            else "operation_failed",
            "work": copy.deepcopy(meter.values),
        }
    return {"output": value, "error": None, "work": copy.deepcopy(meter.values)}


def _examples(inputs, outputs, config):
    if (
        type(inputs) is not list
        or type(outputs) is not list
        or not 1 <= len(inputs) <= config["max_rows"]
        or len(inputs) != len(outputs)
    ):
        raise ValueError("Skill examples must have equal bounded row counts")
    return (
        [_input(value, config) for value in inputs],
        [_input(value, config) for value in outputs],
    )


def _features(features):
    if features is None:
        return None
    if (
        type(features) is not list
        or not 1 <= len(features) <= 64
        or any(
            type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1
            for x in features
        )
    ):
        raise ValueError("Skill ranking features are invalid")
    return list(map(float, features))


class SkillLibrary:
    """Experience can retain executable programs and bounded reusable fragments.

    Hidden validation is a distinct buyer operation after search has returned.
    Admission receipts are trusted buyer records, not independent cryptographic
    attestations. The solver never receives hidden rows through ``solve``.
    """

    def __init__(self, source_id, config=DEFAULT_CONFIG, state=None):
        if type(source_id) is not str or not SOURCE.fullmatch(source_id):
            raise ValueError("Skill source identity is invalid")
        self.source_id = source_id
        self.config = validate_config(config)
        self.config_sha256 = fingerprint(self.config)
        if state is None:
            state = {
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
        self.state = copy.deepcopy(state)
        self._validate_state()

    def _validate_state(self):
        expected = {
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
        state = self.state
        if (
            type(state) is not dict
            or set(state) != expected
            or state["version"] != STATE_VERSION
            or state["source_id"] != self.source_id
            or state["config_sha256"] != self.config_sha256
        ):
            raise ValueError("Skill checkpoint terms are incompatible")
        _int(state["sequence"], 0, 2**53 - 1)
        _int(state["total_work"], 0, 2**53 - 1)
        for name, maximum in (
            ("programs", self.config["max_programs"]),
            ("macros", self.config["max_macros"]),
            ("pending", 64),
            ("receipts", 256),
        ):
            if type(state[name]) is not dict or len(state[name]) > maximum:
                raise ValueError("Skill checkpoint collection exceeds its bound")
        for key, record in state["programs"].items():
            if type(record) is not dict or set(record) != {
                "program",
                "uses",
                "features",
                "evidence_sha256",
            }:
                raise ValueError("Retained skill fields are invalid")
            program = validate_program(record["program"], self.config)
            if (
                key != fingerprint(program)
                or type(record["evidence_sha256"]) is not str
                or not HASH.fullmatch(record["evidence_sha256"])
            ):
                raise ValueError("Retained skill commitment is invalid")
            _int(record["uses"], 1, 2**53 - 1)
            _features(record["features"])
        allowed = {fingerprint(op) for op in self.config["operations"]}
        for key, record in state["macros"].items():
            if (
                type(record) is not dict
                or set(record)
                != {"ops", "uses", "parent_program_sha256", "evidence_sha256"}
                or key != fingerprint(record["ops"])
            ):
                raise ValueError("Retained fragment commitment is invalid")
            if type(record["ops"]) is not list or not 2 <= len(record["ops"]) <= 4:
                raise ValueError("Retained fragment size is invalid")
            parent = state["programs"].get(record["parent_program_sha256"])
            if (
                parent is None
                or type(record["evidence_sha256"]) is not str
                or not HASH.fullmatch(record["evidence_sha256"])
                or not any(
                    parent["program"]["ops"][start : start + len(record["ops"])]
                    == record["ops"]
                    for start in range(
                        len(parent["program"]["ops"]) - len(record["ops"]) + 1
                    )
                )
            ):
                raise ValueError("Retained fragment has no verified parent program")
            for op in record["ops"]:
                if fingerprint(_operation(op)) not in allowed:
                    raise ValueError("Retained fragment uses an unenrolled primitive")
            start = (
                "number" if record["ops"][0]["op"] in ("scale", "format") else "text"
            )
            _types(record["ops"], start)
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
                raise ValueError("Pending skill proposal is malformed")
            if (
                type(key) is not str
                or not HASH.fullmatch(key)
                or record["program_sha256"]
                != fingerprint(validate_program(record["program"], self.config))
            ):
                raise ValueError("Pending skill program commitment is invalid")
            if type(record["training_sha256"]) is not str or not HASH.fullmatch(
                record["training_sha256"]
            ):
                raise ValueError("Pending training commitment is invalid")
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
                raise ValueError("Pending skill proposal identity is invalid")
        for key, receipt in state["receipts"].items():
            self._receipt(receipt)
            if key != receipt["proposal_id"]:
                raise ValueError("Skill receipt identity is invalid")
            if key in state["pending"]:
                raise ValueError("Skill receipt is still marked pending")
        if len(
            {receipt["evaluation_id"] for receipt in state["receipts"].values()}
        ) != len(state["receipts"]):
            raise ValueError("Skill checkpoint repeats a buyer evaluation")
        if state["total_work"] < sum(
            record["work"] for record in state["pending"].values()
        ) + sum(record["work"] for record in state["receipts"].values()):
            raise ValueError("Skill checkpoint omits charged work")

    def dump(self):
        return copy.deepcopy(self.state)

    def _rank(self, record, features):
        prior = record["features"]
        similarity = 0.0
        if features is not None and prior is not None and len(prior) == len(features):
            similarity = -sum(
                (x - y) ** 2 for x, y in zip(features, prior, strict=True)
            )
        return (similarity, record["uses"])

    def solve(self, inputs, outputs, mode="fresh", budget=10000, features=None):
        inputs, outputs = _examples(inputs, outputs, self.config)
        features = _features(features)
        if mode not in ("fresh", "retrieval", "macro", "contextual"):
            raise ValueError("Skill search mode is unknown")
        if len(self.state["pending"]) >= 64:
            raise ValueError("Skill library has too many pending buyer evaluations")
        meter = _Meter(budget)
        target = ("text", tuple(outputs))
        seen = {}
        found = None
        exhausted = False
        queue = []
        serial = 0

        def vector_key(kind, values):
            rendered = [str(value) for value in values]
            meter.data(json.dumps(rendered, separators=(",", ":")))
            return kind, tuple(rendered)

        def expand(ops, values):
            current = list(values)
            for operation in ops:
                current = [
                    _apply(operation, value, meter, self.config) for value in current
                ]
            return current

        programs = list(self.state["programs"].values()) if mode != "fresh" else []
        programs.sort(
            key=lambda record: (
                self._rank(record, features if mode == "contextual" else None),
                fingerprint(record["program"]),
            ),
            reverse=True,
        )
        try:
            for record in programs:
                meter.charge("candidates")
                try:
                    values = expand(record["program"]["ops"], inputs)
                except ValueError as error:
                    if isinstance(error, WorkLimit):
                        raise
                    continue
                if values == outputs:
                    found = record["program"]
                    break
            chunks = [[op] for op in self.config["operations"]]
            if mode in ("macro", "contextual"):
                macros = list(self.state["macros"].values())
                macros.sort(
                    key=lambda record: (-record["uses"], fingerprint(record["ops"]))
                )
                if mode == "contextual":
                    preferred = {
                        fingerprint(
                            record["program"]["ops"][start : start + length]
                        ): rank
                        for rank, record in reversed(list(enumerate(programs)))
                        for length in range(2, 5)
                        for start in range(len(record["program"]["ops"]) - length + 1)
                    }
                    macros.sort(
                        key=lambda record: (
                            preferred.get(fingerprint(record["ops"]), 100000),
                            -record["uses"],
                            fingerprint(record["ops"]),
                        )
                    )
                chunks = [record["ops"] for record in macros] + chunks
            initial = vector_key("text", inputs)
            seen[initial] = 0
            heapq.heappush(queue, (0, serial, [], "text", inputs))
            while (
                found is None
                and queue
                and meter.values["candidates"] < self.config["max_candidates"]
                and len(seen) < MAX_SEMANTIC_STATES
            ):
                depth, _, ops, kind, values = heapq.heappop(queue)
                if (kind, tuple(map(str, values))) == target:
                    found = {"version": VERSION, "ops": ops}
                    break
                for chunk in chunks:
                    if meter.values["candidates"] >= self.config["max_candidates"]:
                        break
                    if len(ops) + len(chunk) > self.config["max_ops"]:
                        continue
                    try:
                        next_kind = _types(chunk, kind)
                    except ValueError:
                        continue
                    meter.charge("candidates")
                    try:
                        next_values = expand(chunk, values)
                    except ValueError as error:
                        if isinstance(error, WorkLimit):
                            raise
                        continue
                    key = vector_key(next_kind, next_values)
                    next_ops = ops + chunk
                    if key in seen and seen[key] <= len(next_ops):
                        meter.values["semantic_duplicates"] += 1
                        continue
                    if key not in seen and len(seen) >= MAX_SEMANTIC_STATES:
                        exhausted = True
                        break
                    seen[key] = len(next_ops)
                    if key == target:
                        found = {"version": VERSION, "ops": next_ops}
                        break
                    serial += 1
                    heapq.heappush(
                        queue, (depth + 1, serial, next_ops, next_kind, next_values)
                    )
        except WorkLimit:
            exhausted = True
        if found is None and len(seen) >= MAX_SEMANTIC_STATES:
            exhausted = True
        self.state["sequence"] += 1
        self.state["total_work"] += meter.values["total"]
        proposal_id = None
        if found is not None:
            found = validate_program(found, self.config)
            proposal = {
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
                    **proposal,
                }
            )
            self.state["pending"][proposal_id] = proposal
        return {
            "proposal_id": proposal_id,
            "program": copy.deepcopy(found),
            "program_sha256": fingerprint(found) if found is not None else None,
            "solved_training": found is not None,
            "mode": mode,
            "exhausted": exhausted,
            "work": copy.deepcopy(meter.values),
            "semantic_states": len(seen),
            "source_id": self.source_id,
            "config_sha256": self.config_sha256,
        }

    def _receipt(self, receipt):
        required = {
            "source_id",
            "proposal_id",
            "program_sha256",
            "evaluation_id",
            "accepted",
            "correct_rows",
            "total_rows",
            "evidence_sha256",
            "work",
        }
        if (
            type(receipt) is not dict
            or set(receipt) != required
            or receipt["source_id"] != self.source_id
        ):
            raise ValueError("Buyer skill receipt fields are invalid")
        for key in (
            "proposal_id",
            "program_sha256",
            "evaluation_id",
            "evidence_sha256",
        ):
            if type(receipt[key]) is not str or not HASH.fullmatch(receipt[key]):
                raise ValueError("Buyer skill receipt commitment is invalid")
        _int(receipt["total_rows"], 1, self.config["max_rows"])
        _int(receipt["correct_rows"], 0, receipt["total_rows"])
        _int(receipt["work"], 0, 10000000)
        if type(receipt["accepted"]) is not bool or receipt["accepted"] != (
            receipt["correct_rows"] == receipt["total_rows"]
        ):
            raise ValueError("Buyer skill acceptance disagrees with exact outputs")
        return receipt

    def admit_verified(self, proposal_id, receipt, *, retain=True):
        """Consume a trusted buyer receipt; no hidden rows enter provider search."""
        if type(retain) is not bool:
            raise ValueError("Buyer retention control must be boolean")
        return self._resolve(proposal_id, receipt, retain=retain)

    def _resolve(self, proposal_id, receipt, retain):
        receipt = copy.deepcopy(self._receipt(receipt))
        if proposal_id != receipt["proposal_id"]:
            raise ValueError("Buyer skill receipt names another proposal")
        previous = self.state["receipts"].get(proposal_id)
        if previous is not None:
            if previous != receipt:
                raise ValueError("Buyer skill receipt changed after commitment")
            return copy.deepcopy(previous)
        pending = self.state["pending"].get(proposal_id)
        if pending is None or receipt["program_sha256"] != pending["program_sha256"]:
            raise ValueError("Buyer skill receipt does not match a pending program")
        if any(
            item["evaluation_id"] == receipt["evaluation_id"]
            for item in self.state["receipts"].values()
        ):
            raise ValueError("Buyer evaluation identity was already consumed")
        if receipt["accepted"] and retain:
            key = receipt["program_sha256"]
            programs = self.state["programs"]
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
            for length in range(2, 5):
                for start in range(len(pending["program"]["ops"]) - length + 1):
                    if pending["program_sha256"] not in programs:
                        continue
                    ops = pending["program"]["ops"][start : start + length]
                    key = fingerprint(ops)
                    macros = self.state["macros"]
                    if key in macros:
                        macros[key]["uses"] += 1
                    elif len(macros) < self.config["max_macros"]:
                        macros[key] = {
                            "ops": copy.deepcopy(ops),
                            "uses": 1,
                            "parent_program_sha256": pending["program_sha256"],
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
        if type(retain) is not bool or type(reveal_counterexample) is not bool:
            raise ValueError("Buyer validation controls must be boolean")
        inputs, outputs = _examples(inputs, outputs, self.config)
        if type(evaluation_id) is not str or not HASH.fullmatch(evaluation_id):
            raise ValueError("Buyer evaluation identity must be a SHA-256 commitment")
        pending = self.state["pending"].get(proposal_id)
        if pending is None:
            raise ValueError("Buyer evaluation needs a pending skill proposal")
        meter = _Meter(10000000)
        observed = []
        correct = 0
        counterexample = None
        for value, target in zip(inputs, outputs, strict=True):
            try:
                for operation in pending["program"]["ops"]:
                    value = _apply(operation, value, meter, self.config)
                observed.append(value)
                correct += int(value == target)
            except ValueError:
                observed.append(None)
            if observed[-1] != target and counterexample is None:
                counterexample = {
                    "input": inputs[len(observed) - 1],
                    "expected": target,
                    "observed": observed[-1],
                }
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
            "work": meter.values["total"],
        }
        result = self._resolve(proposal_id, receipt, retain)
        if reveal_counterexample:
            result["counterexample"] = counterexample
        return result

"""Bounded text programs inferred from examples and admitted by buyer receipts.

The language has no Python evaluation, arbitrary regular expressions, filesystem,
network or process operations. Search is conventional programming by example:
enumerate text projections, then find a shared concatenation through the example
strings. Equality branches are considered only after unconditional search fails.
"""

from __future__ import annotations

import copy
import heapq
import json
import re
from decimal import Decimal, InvalidOperation, localcontext

from cognition.skills import HASH, SOURCE, WorkLimit, _features, _Meter, fingerprint

VERSION = "safe-text-v1"
STATE_VERSION = "verified-text-library-v1"
DEFAULT_TEXT_CONFIG = {
    "version": VERSION,
    "max_ops": 24,
    "max_candidates": 30000,
    "max_rows": 32,
    "max_input_bytes": 2048,
    "max_programs": 128,
    "max_macros": 0,
    "max_fields": 8,
    "max_parts": 4,
}
PATTERNS = {
    "word": r"\w+",
    "digits": r"[0-9]+",
    "alpha": r"[A-Za-z]+",
    "alnum": r"[A-Za-z0-9]+",
    "upper": r"[A-Z]+",
    "lower": r"[a-z]+",
    "nonspace": r"\S+",
}
CASES = {"strip", "lower", "upper", "title", "capitalize", "compact"}
SCALES = ("1", "100", "0.01", "1000", "0.001")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Text-program integer exceeds its bound")
    return value


def text(value, maximum=2048):
    if type(value) is not str or len(value.encode()) > maximum:
        raise ValueError("Text-program string exceeds its bound")
    return value


def normalize_config(config=DEFAULT_TEXT_CONFIG):
    if type(config) is not dict or set(config) != set(DEFAULT_TEXT_CONFIG):
        raise ValueError("Text language configuration is malformed")
    if config["version"] != VERSION or config["max_macros"] != 0:
        raise ValueError("Unsupported text language or fragment authority")
    for name, low, high in (
        ("max_ops", 1, 32),
        ("max_candidates", 1, 100000),
        ("max_rows", 1, 64),
        ("max_input_bytes", 1, 4096),
        ("max_programs", 1, 128),
        ("max_fields", 1, 8),
        ("max_parts", 1, 6),
    ):
        integer(config[name], low, high)
    return copy.deepcopy(config)


def fields(value, config):
    try:
        value = json.loads(text(value, config["max_input_bytes"]))
    except (ValueError, RecursionError) as error:
        raise ValueError("Text input is not a bounded JSON record") from error
    if type(value) is not list or not 1 <= len(value) <= config["max_fields"]:
        raise ValueError("Text input needs a bounded array of string fields")
    return [text(item, config["max_input_bytes"]) for item in value]


def shape(node, config, depth=0, _budget=None):
    if _budget is None:
        _budget = [config["max_ops"]]
    if _budget[0] <= 0:
        raise ValueError("Text program exceeds its node allowance")
    _budget[0] -= 1
    if depth > 8 or type(node) is not dict or type(node.get("op")) is not str:
        raise ValueError("Text program syntax exceeds its bound")
    name = node["op"]
    keys = {"op"}
    children = []
    if name == "field":
        keys.add("index")
        integer(node.get("index"), 0, config["max_fields"] - 1)
    elif name == "constant":
        keys.add("value")
        text(node.get("value"), 256)
    elif name in CASES:
        keys.add("arg")
        children = [node.get("arg")]
    elif name == "slice":
        keys |= {"arg", "start", "end"}
        integer(node.get("start"), -512, 512)
        if node.get("end") is not None:
            integer(node["end"], -512, 512)
        children = [node.get("arg")]
    elif name == "token":
        keys |= {"arg", "pattern", "index"}
        if node.get("pattern") not in PATTERNS:
            raise ValueError("Only registered linear text patterns are allowed")
        integer(node.get("index"), -8, 7)
        children = [node.get("arg")]
    elif name == "split":
        keys |= {"arg", "separator", "index"}
        if not text(node.get("separator"), 8):
            raise ValueError("Text separator cannot be empty")
        integer(node.get("index"), -8, 7)
        children = [node.get("arg")]
    elif name == "replace":
        keys |= {"arg", "old", "new"}
        if not text(node.get("old"), 16):
            raise ValueError("Text replacement needs a nonempty source")
        text(node.get("new"), 16)
        children = [node.get("arg")]
    elif name == "number":
        keys |= {"arg", "scale", "places"}
        if node.get("scale") not in SCALES:
            raise ValueError("Numeric text scale is unregistered")
        if node.get("places") is not None:
            integer(node["places"], 0, 6)
        children = [node.get("arg")]
    elif name == "concat":
        keys.add("parts")
        if type(node.get("parts")) is not list:
            raise ValueError("Text concatenation needs bounded parts")
        integer(len(node["parts"]), 2, config["max_parts"])
        children = node["parts"]
    elif name == "if_equal":
        keys |= {"field", "value", "then", "else"}
        integer(node.get("field"), 0, config["max_fields"] - 1)
        text(node.get("value"), 128)
        children = [node.get("then"), node.get("else")]
    else:
        raise ValueError("Text program operation is unregistered")
    if set(node) != keys:
        raise ValueError("Text program operation fields are malformed")
    return 1 + sum(shape(child, config, depth + 1, _budget) for child in children)


def validate_program(program, config=DEFAULT_TEXT_CONFIG):
    config = normalize_config(config)
    if (
        type(program) is not dict
        or set(program) != {"version", "expr"}
        or program["version"] != VERSION
        or shape(program["expr"], config) > config["max_ops"]
    ):
        raise ValueError("Text program exceeds its language authority")
    return copy.deepcopy(program)


def evaluate(node, values, meter, config):
    meter.charge("primitive_attempts")
    meter.data(node["op"])
    name = node["op"]
    if name == "field":
        result = values[node["index"]]
    elif name == "constant":
        result = node["value"]
    elif name == "concat":
        result = "".join(evaluate(p, values, meter, config) for p in node["parts"])
    elif name == "if_equal":
        meter.data(values[node["field"]])
        result = evaluate(
            node["then"] if values[node["field"]] == node["value"] else node["else"],
            values,
            meter,
            config,
        )
    else:
        value = evaluate(node["arg"], values, meter, config)
        meter.data(value)
        if name in CASES:
            result = (
                "".join(value.split()) if name == "compact" else getattr(value, name)()
            )
        elif name == "slice":
            result = value[node["start"] : node["end"]]
        elif name == "token":
            result = re.findall(PATTERNS[node["pattern"]], value)[node["index"]]
        elif name == "split":
            result = value.split(node["separator"])[node["index"]]
        elif name == "replace":
            result = value.replace(node["old"], node["new"])
        elif name == "number":
            if len(value) > 80:
                raise ValueError("Numeric text exceeds its bound")
            number = Decimal(value.replace(",", ""))
            if (
                not number.is_finite()
                or number.copy_abs() > Decimal("1e18")
                or not -24 <= number.as_tuple().exponent <= 24
                or len(number.as_tuple().digits) > 32
            ):
                raise ValueError("Numeric text magnitude is unsafe")
            with localcontext() as context:
                context.prec = 64
                number *= Decimal(node["scale"])
                if node["places"] is not None:
                    number = number.quantize(Decimal(1).scaleb(-node["places"]))
                    result = format(number, "f")
                else:
                    result = format(number, "f")
                    if "." in result:
                        result = result.rstrip("0").rstrip(".")
        else:
            raise ValueError("Unknown text execution operation")
    meter.data(result)
    return text(result, config["max_input_bytes"])


def execute_checked(program, value, config=DEFAULT_TEXT_CONFIG, budget=10000):
    config = normalize_config(config)
    program = validate_program(program, config)
    meter = _Meter(budget)
    output, error = None, None
    try:
        meter.data(value)
        output = evaluate(program["expr"], fields(value, config), meter, config)
    except WorkLimit:
        error = "work_limit"
    except (ValueError, IndexError, InvalidOperation, KeyError):
        meter.values["errors"] += 1
        error = "invalid_input"
    return {"output": output, "error": error, "work": copy.deepcopy(meter.values)}


def infer(inputs, outputs, config, meter, *, conditional=True):
    """Find a common bounded projection/concatenation; every candidate is charged."""
    rows = [fields(value, config) for value in inputs]
    atoms, signatures = [], set()
    numeric = all(re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", out) for out in outputs)

    def attempt(node):
        if meter.values["candidates"] >= config["max_candidates"]:
            raise WorkLimit("Text search candidate limit reached")
        meter.charge("candidates")
        try:
            shape(node, config)
        except ValueError:
            meter.values["errors"] += 1
            return None
        observed = []
        try:
            for row in rows:
                observed.append(evaluate(node, row, meter, config))
        except (ValueError, IndexError, KeyError, InvalidOperation) as error:
            if isinstance(error, WorkLimit):
                raise
            meter.values["errors"] += 1
            return None
        meter.data(json.dumps(observed, separators=(",", ":")))
        return tuple(observed)

    def add(node):
        values = attempt(node)
        if values == tuple(outputs):
            return node
        if values is not None and values not in signatures and any(values):
            signatures.add(values)
            atoms.append((node, values, shape(node, config)))
        elif values is not None:
            meter.values["semantic_duplicates"] += 1
        return None

    if len(set(outputs)) == 1 and len(outputs[0].encode()) <= 256:
        node = {"op": "constant", "value": outputs[0]}
        if add(node) is not None:
            return node
    for index in range(min(map(len, rows))):
        field = {"op": "field", "index": index}
        for node in [field] + [{"op": case, "arg": field} for case in sorted(CASES)]:
            if add(node) is not None:
                return node
        for pattern in PATTERNS:
            for position in (0, -1, 1, -2, 2, -3, 3, -4):
                node = {
                    "op": "token",
                    "arg": field,
                    "pattern": pattern,
                    "index": position,
                }
                if add(node) is not None:
                    return node
        separators = set(" ,-_/.@:;=|\\\t()[]")
        separators.update(
            char for row in rows for char in row[index] if not char.isalnum()
        )
        for separator in sorted(separators)[:32]:
            for position in (0, -1, 1, -2, 2, -3, 3, -4):
                node = {
                    "op": "split",
                    "arg": field,
                    "separator": separator,
                    "index": position,
                }
                if add(node) is not None:
                    return node
        for start in (0, 1, 2, 3, 4, 5, 6, 7, 8, -1, -2, -3, -4, -5, -6, -7, -8):
            for end in (None, -1, -2, -3, -4, -5, -6, -7, -8, 1, 2, 3, 4, 5, 6, 7, 8):
                if start == 0 and end is None:
                    continue
                node = {"op": "slice", "arg": field, "start": start, "end": end}
                if add(node) is not None:
                    return node
        for old in sorted({char for row in rows for char in row[index]})[:64]:
            node = {"op": "replace", "arg": field, "old": old, "new": ""}
            if add(node) is not None:
                return node
        if numeric:
            for scale in SCALES:
                for places in (None, 0, 1, 2, 3, 4, 5, 6):
                    node = {
                        "op": "number",
                        "arg": field,
                        "scale": scale,
                        "places": places,
                    }
                    if add(node) is not None:
                        return node

    # A shortest shared path through all example outputs. Whole input vectors
    # form semantic edges; only a common literal can become a constant edge.
    initial = (0,) * len(outputs)
    queue, serial, visited = [(0, 0, initial, ())], 0, {}
    while queue:
        cost, _, positions, parts = heapq.heappop(queue)
        if positions == tuple(map(len, outputs)):
            node = (
                parts[0] if len(parts) == 1 else {"op": "concat", "parts": list(parts)}
            )
            if add(node) is not None:
                return node
            continue
        if len(parts) >= config["max_parts"]:
            continue
        prefix = outputs[0][positions[0] :]
        for value, position in zip(outputs[1:], positions[1:], strict=True):
            tail = value[position:]
            length = 0
            while (
                length < min(len(prefix), len(tail)) and prefix[length] == tail[length]
            ):
                meter.charge("primitive_attempts")
                length += 1
            prefix = prefix[:length]
        edges = atoms + [
            (
                {"op": "constant", "value": prefix[:length]},
                (prefix[:length],) * len(outputs),
                1,
            )
            for length in range(min(len(prefix), 128), 0, -1)
        ]
        for node, values, size in edges:
            if meter.values["candidates"] >= config["max_candidates"]:
                raise WorkLimit("Text search candidate limit reached")
            meter.charge("candidates")
            meter.data(json.dumps(values, separators=(",", ":")))
            if cost + size + 1 > config["max_ops"] or not any(values):
                continue
            if not all(
                target.startswith(value, position)
                for target, value, position in zip(
                    outputs, values, positions, strict=True
                )
            ):
                continue
            future = tuple(
                position + len(value)
                for position, value in zip(positions, values, strict=True)
            )
            key = (future, len(parts) + 1)
            if key in visited and visited[key] <= cost + size:
                continue
            visited[key] = cost + size
            serial += 1
            heapq.heappush(queue, (cost + size, serial, future, (*parts, node)))
    if conditional and len(rows) >= 3:
        for index in range(min(map(len, rows))):
            for marker in sorted(
                {row[index] for row in rows if len(row[index].encode()) <= 128},
                key=lambda value: (len(value), value),
            ):
                chosen = [i for i, row in enumerate(rows) if row[index] == marker]
                other = [i for i in range(len(rows)) if i not in chosen]
                if not other or len({outputs[i] for i in chosen}) != 1:
                    continue
                branch = infer(
                    [inputs[i] for i in other],
                    [outputs[i] for i in other],
                    config,
                    meter,
                    conditional=False,
                )
                if branch is not None:
                    node = {
                        "op": "if_equal",
                        "field": index,
                        "value": marker,
                        "then": {"op": "constant", "value": outputs[chosen[0]]},
                        "else": branch,
                    }
                    if add(node) is not None:
                        return node
    return None


class TextSkillLibrary:
    def __init__(self, source_id, config=DEFAULT_TEXT_CONFIG, state=None):
        if type(source_id) is not str or not SOURCE.fullmatch(source_id):
            raise ValueError("Text library source identity is invalid")
        self.source_id, self.config = source_id, normalize_config(config)
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
                "pending": {},
                "receipts": {},
                "macros": {},
            }
        )
        expected = {
            "version",
            "source_id",
            "config_sha256",
            "sequence",
            "total_work",
            "programs",
            "pending",
            "receipts",
            "macros",
        }
        if (
            type(self.state) is not dict
            or set(self.state) != expected
            or self.state["version"] != STATE_VERSION
            or self.state["source_id"] != source_id
            or self.state["config_sha256"] != self.config_sha256
            or self.state["macros"]
        ):
            raise ValueError("Text library checkpoint identity is incompatible")
        for key in ("sequence", "total_work"):
            integer(self.state[key], 0, 2**53 - 1)
        for key, maximum in (
            ("programs", self.config["max_programs"]),
            ("pending", 64),
            ("receipts", 256),
        ):
            if type(self.state[key]) is not dict or len(self.state[key]) > maximum:
                raise ValueError("Text library state exceeds its bound")
        for key, record in self.state["programs"].items():
            if (
                type(record) is not dict
                or set(record) != {"program", "uses", "features", "evidence_sha256"}
                or key != fingerprint(validate_program(record["program"], self.config))
                or type(record["evidence_sha256"]) is not str
                or not HASH.fullmatch(record["evidence_sha256"])
            ):
                raise ValueError("Retained text program commitment is invalid")
            integer(record["uses"], 1, 2**53 - 1)
            _features(record["features"])
        for key, record in self.state["pending"].items():
            if (
                type(key) is not str
                or not HASH.fullmatch(key)
                or type(record) is not dict
                or set(record)
                != {
                    "program",
                    "program_sha256",
                    "features",
                    "training_sha256",
                    "work",
                    "sequence",
                }
                or record["program_sha256"]
                != fingerprint(validate_program(record["program"], self.config))
                or type(record["training_sha256"]) is not str
                or not HASH.fullmatch(record["training_sha256"])
            ):
                raise ValueError("Pending text proposal commitment is invalid")
            _features(record["features"])
            integer(record["work"], 0, self.state["total_work"])
            integer(record["sequence"], 1, self.state["sequence"])
            if key != fingerprint(
                {
                    "source_id": self.source_id,
                    "config_sha256": self.config_sha256,
                    **record,
                }
            ):
                raise ValueError("Pending text proposal identity is invalid")
        for key, receipt in self.state["receipts"].items():
            self._receipt(receipt)
            if key != receipt["proposal_id"] or key in self.state["pending"]:
                raise ValueError("Text buyer receipt identity is invalid")
        if len({r["evaluation_id"] for r in self.state["receipts"].values()}) != len(
            self.state["receipts"]
        ):
            raise ValueError("Text buyer evaluation identity repeats")
        if self.state["total_work"] < sum(
            r["work"] for r in self.state["pending"].values()
        ) + sum(r["work"] for r in self.state["receipts"].values()):
            raise ValueError("Text checkpoint omits charged work")

    def solve(self, inputs, outputs, mode="factored", budget=100000, features=None):
        integer(budget, 1, 10_000_000)
        if (
            len(self.state["pending"]) >= 64
            or self.state["sequence"] >= 2**53 - 1
            or self.state["total_work"] + budget > 2**53 - 1
        ):
            raise ValueError("Text library cannot reserve another bounded search")
        if mode not in ("factored", "fresh", "retained_factored", "active"):
            raise ValueError("Text search mode is unsupported")
        if (
            type(inputs) is not list
            or type(outputs) is not list
            or len(inputs) != len(outputs)
        ):
            raise ValueError("Text search needs paired examples")
        integer(len(inputs), 1, self.config["max_rows"])
        features = _features(features)
        for value in inputs:
            fields(value, self.config)
        outputs = [text(value, self.config["max_input_bytes"]) for value in outputs]
        meter, node, status = _Meter(budget), None, "not_found"
        try:
            if mode in ("retained_factored", "active"):
                for record in sorted(
                    self.state["programs"].values(),
                    key=lambda r: (-r["uses"], fingerprint(r["program"])),
                ):
                    if meter.values["candidates"] >= self.config["max_candidates"]:
                        raise WorkLimit("Text search candidate limit reached")
                    meter.charge("candidates")
                    try:
                        observed = [
                            evaluate(
                                record["program"]["expr"],
                                fields(value, self.config),
                                meter,
                                self.config,
                            )
                            for value in inputs
                        ]
                    except (
                        ValueError,
                        IndexError,
                        KeyError,
                        InvalidOperation,
                    ) as error:
                        if isinstance(error, WorkLimit):
                            raise
                        meter.values["errors"] += 1
                        continue
                    if observed == outputs:
                        node = record["program"]["expr"]
                        break
            if node is None:
                node = infer(inputs, outputs, self.config, meter)
            if node is not None:
                status = "solved"
        except WorkLimit:
            status = "work_limit"
        except (ValueError, IndexError, InvalidOperation, KeyError):
            meter.values["errors"] += 1
        program = (
            validate_program({"version": VERSION, "expr": node}, self.config)
            if node is not None
            else None
        )
        self.state["sequence"] += 1
        self.state["total_work"] += meter.values["total"]
        proposal_id = None
        program_sha = fingerprint(program) if program is not None else None
        if program is not None:
            if len(self.state["pending"]) >= 64:
                raise ValueError("Unresolved text proposals exceed their bound")
            pending = {
                "program": program,
                "program_sha256": program_sha,
                "features": copy.deepcopy(features),
                "training_sha256": fingerprint([inputs, outputs]),
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
            "status": status,
            "proposal_id": proposal_id if program is not None else None,
            "program": program,
            "program_sha256": program_sha,
            "source_id": self.source_id,
            "config_sha256": self.config_sha256,
            "solved_training": program is not None,
            "exhausted": status == "work_limit",
            "mode": mode,
            "work": copy.deepcopy(meter.values),
        }

    def _receipt(self, receipt):
        """Check finite buyer evidence without widening the search-row allowance.

        A benchmark file can contain more scoring rows than the small diagnostic
        pool. The buyer may verify up to 2,048 original rows; search and serving
        retain their independent ``max_rows`` limit.
        """
        expected = {
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
            or set(receipt) != expected
            or receipt["source_id"] != self.source_id
        ):
            raise ValueError("Text buyer receipt fields are invalid")
        for name in (
            "proposal_id",
            "program_sha256",
            "evaluation_id",
            "evidence_sha256",
        ):
            if type(receipt[name]) is not str or not HASH.fullmatch(receipt[name]):
                raise ValueError("Text buyer receipt commitment is invalid")
        integer(receipt["total_rows"], 1, 2048)
        integer(receipt["correct_rows"], 0, receipt["total_rows"])
        integer(receipt["work"], 0, 10_000_000)
        if type(receipt["accepted"]) is not bool or receipt["accepted"] != (
            receipt["correct_rows"] == receipt["total_rows"]
        ):
            raise ValueError("Text buyer acceptance disagrees with exact outputs")
        return receipt

    def admit_verified(self, proposal_id, receipt, *, retain=True):

        if type(retain) is not bool:
            raise ValueError("Text retention choice is invalid")
        receipt = copy.deepcopy(self._receipt(receipt))
        if proposal_id != receipt["proposal_id"]:
            raise ValueError("Text buyer receipt names another proposal")
        prior = self.state["receipts"].get(proposal_id)
        if prior is not None:
            if prior != receipt:
                raise ValueError("Text buyer receipt changed after commitment")
            return copy.deepcopy(prior)
        pending = self.state["pending"].get(proposal_id)
        if pending is None or receipt["program_sha256"] != pending["program_sha256"]:
            raise ValueError("Text buyer receipt does not bind its pending program")
        if any(
            r["evaluation_id"] == receipt["evaluation_id"]
            for r in self.state["receipts"].values()
        ):
            raise ValueError("Text buyer evaluation was already consumed")
        integer(self.state["total_work"] + receipt["work"], 0, 2**53 - 1)
        if receipt["program_sha256"] in self.state["programs"]:
            integer(
                self.state["programs"][receipt["program_sha256"]]["uses"] + 1,
                1,
                2**53 - 1,
            )
        if receipt["accepted"] and retain:
            key, programs = receipt["program_sha256"], self.state["programs"]
            if key in programs:
                programs[key]["uses"] += 1
                programs[key]["evidence_sha256"] = receipt["evidence_sha256"]
            elif len(programs) < self.config["max_programs"]:
                programs[key] = {
                    "program": pending["program"],
                    "uses": 1,
                    "features": pending["features"],
                    "evidence_sha256": receipt["evidence_sha256"],
                }
        self.state["total_work"] += receipt["work"]
        self.state["pending"].pop(proposal_id)
        self.state["receipts"][proposal_id] = receipt
        while len(self.state["receipts"]) > 256:
            self.state["receipts"].pop(next(iter(self.state["receipts"])))
        return copy.deepcopy(receipt)

    def serve(self, skill_id, inputs, budget=10000):
        integer(budget, 1, 10_000_000)
        integer(self.state["total_work"] + budget, 0, 2**53 - 1)
        if type(skill_id) is not str or not HASH.fullmatch(skill_id):
            raise ValueError("Text execution needs a retained program identity")
        if type(inputs) is not list:
            raise ValueError("Text serving input rows must be a bounded list")
        integer(len(inputs), 1, self.config["max_rows"])
        for value in inputs:
            fields(value, self.config)
        meter, observed, status = _Meter(budget), [], "served"
        record = self.state["programs"].get(skill_id)
        try:
            meter.charge("candidates")
            for value in inputs:
                if record is None:
                    status = "skill_missing"
                    break
                try:
                    observed.append(
                        evaluate(
                            record["program"]["expr"],
                            fields(value, self.config),
                            meter,
                            self.config,
                        )
                    )
                except (ValueError, IndexError, InvalidOperation, KeyError) as error:
                    if isinstance(error, WorkLimit):
                        raise
                    meter.values["errors"] += 1
                    observed.append(None)
                    status = "failed"
        except WorkLimit:
            status = "work_limit"
        observed += [None] * (len(inputs) - len(observed))
        self.state["total_work"] += meter.values["total"]
        return {
            "status": status,
            "outputs": observed,
            "skill_id": skill_id,
            "program_sha256": skill_id if record is not None else None,
            "source_id": self.source_id,
            "inputs_sha256": fingerprint(inputs),
            "work": copy.deepcopy(meter.values),
        }

    def dump(self):
        return copy.deepcopy(self.state)

    def summary(self):
        return {
            "version": STATE_VERSION,
            "source_id": self.source_id,
            "programs": len(self.state["programs"]),
            "pending": len(self.state["pending"]),
            "work": self.state["total_work"],
            "sequence": self.state["sequence"],
        }

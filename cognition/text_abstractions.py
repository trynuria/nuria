"""Bounded reusable subprograms derived from accepted text-program records.

The caller authenticates acceptance. This pure utility checks record structure,
program identities and expansion provenance; a hash is not buyer authentication.
Private hole markers are never executable operations. Every application expands
to an ordinary, fully validated ``safe-text-v1`` program, whose primitive body
must still be charged by the normal interpreter. Conditional field selectors
and operation parameters remain literal; only text-expression holes vary.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
from dataclasses import dataclass

from cognition.skills import HASH, SOURCE, fingerprint
from cognition.text_skills import (
    CASES,
    VERSION,
    normalize_config,
    shape,
    validate_program,
)

SCHEMA = "nuria.text-abstraction.v1"
LIBRARY_SCHEMA = "nuria.text-abstraction-library.v1"
MAX_SUPPORT = 8
MAX_PATTERNS = 4096
CHILDREN = ("arg", "then", "else")
COUNTERS = (
    "node_visits",
    "serialization_blocks",
    "candidate_attempts",
    "pair_attempts",
    "validation_nodes",
    "ordering_units",
)


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Abstraction integer exceeds its declared bound")
    return value


def _config(config):
    if (
        type(config) is not dict
        or type(config.get("version")) is not str
        or any(
            type(value) is not int for key, value in config.items() if key != "version"
        )
    ):
        raise ValueError("Abstraction configuration requires strict integers")
    return normalize_config(config)


def _hash(value):
    if type(value) is not str or not HASH.fullmatch(value):
        raise ValueError("Abstraction provenance requires complete SHA256 hashes")
    return value


def _source(value):
    if type(value) is not str or not SOURCE.fullmatch(value):
        raise ValueError("Abstraction source identity is invalid")
    return value


def _serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class _Limit(Exception):
    pass


class _Work:
    def __init__(self, maximum):
        self.maximum = maximum
        self.limit = maximum
        self.values = dict.fromkeys(COUNTERS, 0)

    @property
    def total(self):
        return sum(self.values.values())

    def charge(self, counter, amount=1):
        if self.total + amount > self.limit:
            raise _Limit
        self.values[counter] += amount

    def data(self, value):
        encoded = _serial(value).encode()
        self.charge("serialization_blocks", (len(encoded) + 31) // 32)
        return encoded


def _children(node):
    for key in CHILDREN:
        if key in node:
            yield (key,), node[key]
    for index, child in enumerate(node.get("parts", [])):
        yield ("parts", index), child


def _nodes(node):
    return 1 + sum(_nodes(child) for _, child in _children(node))


def _walk(node, path=()):
    yield path, node
    for step, child in _children(node):
        yield from _walk(child, path + step)


def _parameters(node):
    return {
        key: value for key, value in node.items() if key not in (*CHILDREN, "parts")
    }


def _pattern(left, right=None, *, cuts=(), meter):
    """Preserve shared operators; canonical holes preserve paired aliases."""
    holes, first, second = {}, [], []

    def hole(a, b):
        key = fingerprint(a), fingerprint(b)
        if key not in holes:
            holes[key] = len(holes)
            first.append(copy.deepcopy(a))
            second.append(copy.deepcopy(b))
        return {"op": "hole", "index": holes[key]}

    def visit(a, b):
        meter.charge("node_visits")
        if (
            fingerprint(a) in cuts
            or a["op"] == "field"
            or b["op"] == "field"
            or _parameters(a) != _parameters(b)
            or len(a.get("parts", [])) != len(b.get("parts", []))
        ):
            return hole(a, b)
        result = copy.deepcopy(_parameters(a))
        for key in CHILDREN:
            if key in a:
                result[key] = visit(a[key], b[key])
        if "parts" in a:
            result["parts"] = [
                visit(x, y) for x, y in zip(a["parts"], b["parts"], strict=True)
            ]
        return result

    body = visit(left, left if right is None else right)
    return body, first, second


def _body(body, arity, config):
    seen, occurrences = [], [0] * arity
    remaining = [config["max_ops"]]

    def expand(node, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 8 or type(node) is not dict:
            raise ValueError("Abstraction body exceeds its syntax bound")
        if node.get("op") == "hole":
            if set(node) != {"op", "index"}:
                raise ValueError("Abstraction hole fields are malformed")
            index = _integer(node["index"], 0, arity - 1)
            if index not in seen:
                if index != len(seen):
                    raise ValueError("Abstraction holes are not in canonical order")
                seen.append(index)
            occurrences[index] += 1
            return {"op": "field", "index": 0}
        # Native validation below checks exact operation fields and parameters.
        if type(node.get("op")) is not str:
            raise ValueError("Abstraction operation is malformed")
        if len(node) > 5 or node["op"] not in (
            "field",
            "constant",
            "slice",
            "token",
            "split",
            "replace",
            "number",
            "concat",
            "if_equal",
            *CASES,
        ):
            raise ValueError("Abstraction native operation is unsupported")
        for key, value in node.items():
            if key in (*CHILDREN, "parts"):
                continue
            if type(value) not in (str, int, type(None)) or (
                type(value) is str and len(value.encode()) > 256
            ):
                raise ValueError("Abstraction parameter is not a bounded scalar")
        if "parts" in node and (
            type(node["parts"]) is not list
            or not 2 <= len(node["parts"]) <= config["max_parts"]
        ):
            raise ValueError("Abstraction concatenation exceeds its bound")
        result = {
            k: copy.deepcopy(v)
            for k, v in node.items()
            if k not in (*CHILDREN, "parts")
        }
        for key in CHILDREN:
            if key in node:
                result[key] = expand(node[key], depth + 1)
        if "parts" in node:
            result["parts"] = [expand(x, depth + 1) for x in node["parts"]]
        return result

    native = expand(body)
    validate_program({"version": VERSION, "expr": native}, config)
    if seen != list(range(arity)):
        raise ValueError("Abstraction has an unused text argument")
    nodes = config["max_ops"] - remaining[0]
    fixed = nodes - sum(occurrences)
    if fixed < 2:
        raise ValueError("Abstraction must preserve two native operation nodes")
    return nodes, fixed, occurrences


def _expand(body, arguments):
    if body["op"] == "hole":
        return copy.deepcopy(arguments[body["index"]])
    result = copy.deepcopy(_parameters(body))
    for key in CHILDREN:
        if key in body:
            result[key] = _expand(body[key], arguments)
    if "parts" in body:
        result["parts"] = [_expand(child, arguments) for child in body["parts"]]
    return result


def _arguments(arguments, arity, config):
    if type(arguments) is not list or len(arguments) != arity:
        raise ValueError("Abstraction needs its exact bounded text arguments")
    return [
        validate_program({"version": VERSION, "expr": node}, config)["expr"]
        for node in arguments
    ]


def _support(entry, node, arguments):
    record, path = entry["record"], entry["path"]
    return {
        "origin_sha256": record["origin_sha256"],
        "program_sha256": record["program_sha256"],
        "evidence_sha256": record["evidence_sha256"],
        "subtree_sha256": fingerprint(node),
        "parent_program": copy.deepcopy(record["program"]),
        "path": list(path),
        "arguments": copy.deepcopy(arguments),
    }


def _compression(body, support):
    expanded = [_expand(body, item["arguments"]) for item in support]
    expanded_nodes = sum(_nodes(node) for node in expanded)
    stored_nodes = _nodes(body) + sum(
        1 + sum(_nodes(node) for node in item["arguments"]) for item in support
    )
    expanded_bytes = sum(len(_serial(node).encode()) for node in expanded)
    stored_bytes = len(_serial(body).encode()) + sum(
        len(_serial(item["arguments"]).encode()) for item in support
    )
    return {
        "expanded_nodes": expanded_nodes,
        "definition_and_bindings_nodes": stored_nodes,
        "node_gain": expanded_nodes - stored_nodes,
        "expanded_bytes": expanded_bytes,
        "definition_and_bindings_bytes": stored_bytes,
        "byte_gain": expanded_bytes - stored_bytes,
    }


def _identity(source_id, config_hash, body, arity):
    return fingerprint(
        {
            "schema": SCHEMA,
            "source_id": source_id,
            "config_sha256": config_hash,
            "body": body,
            "arity": arity,
        }
    )


def _validate(abstraction, config):
    expected = {
        "schema",
        "source_id",
        "config_sha256",
        "body",
        "arity",
        "body_nodes",
        "fixed_nodes",
        "hole_occurrences",
        "support",
        "compression",
        "pattern_sha256",
        "sha256",
    }
    if type(abstraction) is not dict or set(abstraction) != expected:
        raise ValueError("Abstraction envelope is malformed")
    if type(abstraction["schema"]) is not str or abstraction["schema"] != SCHEMA:
        raise ValueError("Abstraction schema is unsupported")
    source = _source(abstraction["source_id"])
    if _hash(abstraction["config_sha256"]) != fingerprint(config):
        raise ValueError("Abstraction language authority changed")
    arity = _integer(abstraction["arity"], 1, 3)
    nodes, fixed, occurrences = _body(abstraction["body"], arity, config)
    if (
        type(abstraction["body_nodes"]) is not int
        or type(abstraction["fixed_nodes"]) is not int
        or type(abstraction["hole_occurrences"]) is not list
        or len(abstraction["hole_occurrences"]) != arity
        or any(type(n) is not int for n in abstraction["hole_occurrences"])
        or (nodes, fixed, occurrences)
        != (
            abstraction["body_nodes"],
            abstraction["fixed_nodes"],
            abstraction["hole_occurrences"],
        )
    ):
        raise ValueError("Abstraction expansion sizes changed")
    support = abstraction["support"]
    if type(support) is not list or not 2 <= len(support) <= MAX_SUPPORT:
        raise ValueError("Abstraction needs bounded distinct accepted parents")
    compression_keys = {
        "expanded_nodes",
        "definition_and_bindings_nodes",
        "node_gain",
        "expanded_bytes",
        "definition_and_bindings_bytes",
        "byte_gain",
    }
    compression = abstraction["compression"]
    if (
        type(compression) is not dict
        or set(compression) != compression_keys
        or any(
            type(value) is not int or abs(value) > 20_000_000
            for value in compression.values()
        )
    ):
        raise ValueError("Abstraction compression evidence is malformed")
    for item in support:
        if type(item) is not dict or set(item) != {
            "origin_sha256",
            "program_sha256",
            "evidence_sha256",
            "subtree_sha256",
            "parent_program",
            "path",
            "arguments",
        }:
            raise ValueError("Abstraction support is malformed")
        for key in (
            "origin_sha256",
            "program_sha256",
            "evidence_sha256",
            "subtree_sha256",
        ):
            _hash(item[key])
        path = item["path"]
        if type(path) is not list or len(path) > 16:
            raise ValueError("Abstraction support path exceeds its bound")
        index = 0
        while index < len(path):
            step = path[index]
            if type(step) is not str or step not in (*CHILDREN, "parts"):
                raise ValueError("Abstraction support path is malformed")
            if step == "parts":
                index += 1
                if index == len(path):
                    raise ValueError("Abstraction part index is missing")
                _integer(path[index], 0, config["max_parts"] - 1)
            index += 1
        args = _arguments(item["arguments"], arity, config)
        parent = validate_program(item["parent_program"], config)
        if fingerprint(parent) != item["program_sha256"]:
            raise ValueError("Abstraction parent program identity changed")
        subtree = parent["expr"]
        try:
            for step in path:
                subtree = subtree[step]
        except (KeyError, IndexError, TypeError):
            raise ValueError(
                "Abstraction path does not name a parent subtree"
            ) from None
        if type(subtree) is not dict or fingerprint(subtree) != item["subtree_sha256"]:
            raise ValueError("Abstraction parent subtree identity changed")
        program = validate_program(
            {"version": VERSION, "expr": _expand(abstraction["body"], args)}, config
        )
        if fingerprint(program["expr"]) != item["subtree_sha256"]:
            raise ValueError("Abstraction support expansion changed")
    if (
        len({s["program_sha256"] for s in support}) != len(support)
        or len({s["origin_sha256"] for s in support}) != len(support)
        or support != sorted(support, key=lambda s: s["program_sha256"])
        or abstraction["compression"] != _compression(abstraction["body"], support)
        or _hash(abstraction["pattern_sha256"])
        != _identity(source, abstraction["config_sha256"], abstraction["body"], arity)
    ):
        raise ValueError("Abstraction support, compression or pattern identity changed")
    if _hash(abstraction["sha256"]) != fingerprint(
        {key: value for key, value in abstraction.items() if key != "sha256"}
    ):
        raise ValueError("Abstraction record integrity failed")
    return nodes, fixed, occurrences


@dataclass(frozen=True, slots=True, init=False)
class PreparedAbstraction:
    """Validated immutable body for repeated bounded native applications.

    Construct through :func:`prepare_abstraction`; do not load instances from
    untrusted object serialization. Canonical JSON strings and integer tuples
    retain no mutable reference to the source record. Parent evidence is checked
    and charged once. Each application checks its own argument/output authority.
    """

    _body_json: str
    _config_json: str
    source_id: str
    config_sha256: str
    abstraction_sha256: str
    pattern_sha256: str
    arity: int
    body_nodes: int
    fixed_nodes: int
    hole_occurrences: tuple
    preparation_work: int

    def __init__(self):
        raise TypeError("Use prepare_abstraction to construct a validated body")

    @property
    def body(self):
        return json.loads(self._body_json)

    def apply(self, arguments):
        """Expand and validate arguments, charging the native primitive size.

        Counters declare bounded validation/assembly/immutable-read units, not
        complete hardware instructions or CPU. Normal interpreter execution is
        additional: a repeated macro never becomes one cheap primitive call.
        """
        config, body = json.loads(self._config_json), self.body
        args = _arguments(arguments, self.arity, config)
        expanded_nodes = self.fixed_nodes + sum(
            count * _nodes(arg)
            for count, arg in zip(self.hole_occurrences, args, strict=True)
        )
        if expanded_nodes > config["max_ops"]:
            raise ValueError("Expanded abstraction exceeds primitive node authority")
        program = validate_program(
            {"version": VERSION, "expr": _expand(body, args)}, config
        )
        counters = {
            "argument_validation_nodes": sum(map(_nodes, args)),
            "body_read_nodes": self.body_nodes,
            "expanded_assembly_nodes": expanded_nodes,
            "expanded_validation_nodes": expanded_nodes,
            "immutable_read_blocks": (
                len(self._body_json.encode()) + len(self._config_json.encode()) + 31
            )
            // 32,
        }
        return {
            "program": program,
            "expanded_nodes": expanded_nodes,
            "construction_work": sum(counters.values()),
            "construction_counters": counters,
            "program_sha256": fingerprint(program),
            "abstraction_sha256": self.abstraction_sha256,
        }


def prepare_abstraction(abstraction, config):
    """Check complete support once, returning immutable data and declared cost."""
    config = _config(config)
    body_nodes, fixed, occurrences = _validate(abstraction, config)
    support_nodes = sum(
        _nodes(item["parent_program"]["expr"])
        + sum(map(_nodes, item["arguments"]))
        + fixed
        + sum(
            count * _nodes(arg)
            for count, arg in zip(occurrences, item["arguments"], strict=True)
        )
        for item in abstraction["support"]
    )
    values = {
        "_body_json": _serial(abstraction["body"]),
        "_config_json": _serial(config),
        "source_id": abstraction["source_id"],
        "config_sha256": abstraction["config_sha256"],
        "abstraction_sha256": abstraction["sha256"],
        "pattern_sha256": abstraction["pattern_sha256"],
        "arity": abstraction["arity"],
        "body_nodes": body_nodes,
        "fixed_nodes": fixed,
        "hole_occurrences": tuple(occurrences),
        "preparation_work": body_nodes
        + support_nodes
        + (len(_serial(abstraction).encode()) + 31) // 32,
    }
    prepared = object.__new__(PreparedAbstraction)
    for key, value in values.items():
        object.__setattr__(prepared, key, value)
    return prepared


def instantiate_with_report(abstraction, arguments, config):
    """Fully check a record and expand; preparation is charged on every call.

    For repeated trusted use, :func:`prepare_abstraction` checks parent evidence
    once and its immutable result reports per-application construction separately.
    Physical CPU and complete helper hashing are not certified by these units.
    """
    prepared = prepare_abstraction(abstraction, config)
    report = prepared.apply(arguments)
    report["preparation_work"] = prepared.preparation_work
    report["construction_work"] += prepared.preparation_work
    return report


def instantiate(abstraction, arguments, config):
    """Return an ordinary validated text program, with no executable holes."""
    return instantiate_with_report(abstraction, arguments, config)["program"]


def extract_abstractions(
    records,
    config,
    *,
    source_id,
    max_programs=128,
    max_abstractions=64,
    max_arity=3,
    max_pairs=4096,
    maximum_work=2_500_000,
):
    """Extract canonical fields, proper-subtree cuts and paired abstractions.

    Records have exactly ``program``, ``program_sha256``, ``origin_sha256`` and
    ``evidence_sha256``. Acceptance is the caller's responsibility. Two distinct
    program hashes and origins must support every emitted abstraction. Support
    examples are deterministically capped at eight. Generation and finalization
    share a finite declared node/serialization work meter; this is not CPU work.
    Reaching a generation bound returns explicit partial/exhausted evidence.
    """
    config, source_id = _config(config), _source(source_id)
    for value, low, high in (
        (max_programs, 1, 128),
        (max_abstractions, 1, 64),
        (max_arity, 1, 3),
        (max_pairs, 0, 4096),
        (maximum_work, 1, 20_000_000),
    ):
        _integer(value, low, high)
    if type(records) is not list or len(records) > max_programs:
        raise ValueError("Accepted program history exceeds its bound")
    meter, valid, entries, patterns = _Work(maximum_work), [], [], {}
    exhausted, pairs_limited, candidates_limited, record_hash = (
        False,
        False,
        False,
        None,
    )
    config_hash = fingerprint(config)
    record_keys = {"program", "program_sha256", "origin_sha256", "evidence_sha256"}

    def put(body, entry, arguments):
        nonlocal candidates_limited
        meter.charge("candidate_attempts")
        if not 1 <= len(arguments) <= max_arity:
            return
        try:
            body_nodes, fixed, occurrences = _body(body, len(arguments), config)
        except ValueError:
            return
        meter.charge("validation_nodes", body_nodes)
        encoded = meter.data(body)
        identity = fingerprint(body)
        if identity not in patterns and len(patterns) == MAX_PATTERNS:
            candidates_limited = True
            return
        item = patterns.setdefault(
            identity,
            {
                "body": body,
                "arity": len(arguments),
                "body_nodes": body_nodes,
                "fixed_nodes": fixed,
                "hole_occurrences": occurrences,
                "support": {},
                "encoded_bytes": len(encoded),
            },
        )
        support = _support(entry, entry["node"], arguments)
        key = support["program_sha256"]
        previous = item["support"].get(key)
        if previous is None or _serial(support) < _serial(previous):
            item["support"][key] = support
        if len(item["support"]) > MAX_SUPPORT:
            del item["support"][max(item["support"])]

    try:
        for record in records:
            if type(record) is not dict or set(record) != record_keys:
                raise ValueError("Accepted program record is malformed")
            for key in record_keys - {"program"}:
                _hash(record[key])
            program = validate_program(record["program"], config)
            meter.charge("validation_nodes", shape(program["expr"], config))
            meter.data(program)
            if fingerprint(program) != record["program_sha256"]:
                raise ValueError("Accepted program identity changed")
            valid.append({**record, "program": program})
        if len({item["origin_sha256"] for item in valid}) != len(valid):
            raise ValueError("Accepted history repeats an origin")
        valid.sort(key=lambda r: (r["program_sha256"], r["origin_sha256"]))
        meter.charge(
            "ordering_units", len(valid) * math.ceil(math.log2(max(1, len(valid))))
        )
        record_hash = fingerprint(valid)
        # Reserve part of the same allowance for honest final validation. A
        # generation limit is not a second or renewed work allowance.
        reserve = min(maximum_work // 2, max_abstractions * 6144)
        meter.limit = max(meter.total, maximum_work - reserve)
        for record in valid:
            for path, node in _walk(record["program"]["expr"]):
                meter.charge("node_visits")
                if _nodes(node) < 3:
                    continue
                entry = {"record": record, "path": path, "node": node}
                entries.append(entry)
                body, args, _ = _pattern(node, meter=meter)
                put(body, entry, args)
                cuts = sorted({fingerprint(child) for p, child in _walk(node) if p})
                # Two distinct subtree cuts complement pair anti-unification;
                # repeated occurrences share an argument by full AST identity.
                for count in (1, 2):
                    for chosen in itertools.combinations(cuts, count):
                        body, args, _ = _pattern(node, cuts=chosen, meter=meter)
                        put(body, entry, args)
        pair_count = 0
        for left_index, left in enumerate(entries):
            for right in entries[left_index + 1 :]:
                meter.charge("ordering_units")
                if (
                    left["record"]["program_sha256"]
                    == right["record"]["program_sha256"]
                ):
                    continue
                if _parameters(left["node"]) != _parameters(right["node"]):
                    continue
                if pair_count == max_pairs:
                    pairs_limited = True
                    break
                pair_count += 1
                meter.charge("pair_attempts")
                body, first, second = _pattern(left["node"], right["node"], meter=meter)
                put(body, left, first)
                put(body, right, second)
            if pairs_limited:
                break
    except _Limit:
        exhausted = True
    meter.limit = maximum_work
    eligible = [item for item in patterns.values() if len(item["support"]) >= 2]
    try:
        meter.charge(
            "ordering_units",
            len(eligible) * math.ceil(math.log2(max(1, len(eligible)))),
        )
    except _Limit:
        exhausted = True
        eligible = []
    # Definition node savings are a deterministic heuristic, not demonstrated
    # future utility. Canonical identities break every remaining tie.
    eligible.sort(
        key=lambda item: (
            -((len(item["support"]) - 1) * item["fixed_nodes"] - len(item["support"])),
            -len(item["support"]),
            -item["fixed_nodes"],
            item["arity"],
            fingerprint(item["body"]),
        )
    )
    abstractions = []
    try:
        for item in eligible[:max_abstractions]:
            support = [item["support"][key] for key in sorted(item["support"])]
            abstraction = {
                "schema": SCHEMA,
                "source_id": source_id,
                "config_sha256": config_hash,
                **{
                    key: item[key]
                    for key in (
                        "body",
                        "arity",
                        "body_nodes",
                        "fixed_nodes",
                        "hole_occurrences",
                    )
                },
                "support": support,
                "compression": _compression(item["body"], support),
                "pattern_sha256": _identity(
                    source_id, config_hash, item["body"], item["arity"]
                ),
            }
            meter.data(abstraction)
            meter.charge(
                "validation_nodes",
                sum(
                    _nodes(_expand(item["body"], s["arguments"]))
                    + _nodes(s["parent_program"]["expr"])
                    + sum(map(_nodes, s["arguments"]))
                    for s in support
                )
                + item["body_nodes"],
            )
            abstraction["sha256"] = fingerprint(abstraction)
            _validate(abstraction, config)
            abstractions.append(abstraction)
    except _Limit:
        exhausted = True
    return {
        "schema": LIBRARY_SCHEMA,
        "source_id": source_id,
        "config_sha256": config_hash,
        "records_sha256": record_hash,
        "validated_records": len(valid),
        "bounds": {
            "max_programs": max_programs,
            "max_abstractions": max_abstractions,
            "max_arity": max_arity,
            "max_pairs": max_pairs,
            "maximum_work": maximum_work,
            "max_support_per_abstraction": MAX_SUPPORT,
            "max_candidate_patterns": MAX_PATTERNS,
        },
        "abstractions": abstractions,
        "candidate_count": len(patterns),
        "eligible_count": len(eligible),
        "exhausted": exhausted,
        "pair_limit_reached": pairs_limited,
        "candidate_limit_reached": candidates_limited,
        "work": {"total": meter.total, **meter.values},
        "serialized_abstraction_bytes": sum(
            len(_serial(a).encode()) for a in abstractions
        ),
        "compression_scope": "AST definition/bindings only; provenance storage is included in serialized_abstraction_bytes",
        "accounting_scope": "Declared validation, generation, explicit serialization and ordering units; not complete helper hashing or physical CPU",
    }

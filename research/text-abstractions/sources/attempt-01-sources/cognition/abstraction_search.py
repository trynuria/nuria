"""Finite search that composes admitted text abstractions as ordinary ASTs.

Learned components affect visitation, not interpreter authority. Every proposal
is expanded into the unchanged text language and charged at primitive cost.
The caller owns buyer verification; fitting disclosed examples is not proof of
general correctness. Neural ranking is optional and needs matched evaluation.
"""

from __future__ import annotations

import copy
import heapq
import itertools
import math
import time
from dataclasses import dataclass
from decimal import InvalidOperation

import numpy as np

from cognition.skills import WorkLimit, _Meter, fingerprint
from cognition.text_skills import (
    VERSION,
    evaluate,
    fields,
    integer,
    normalize_config,
    shape,
    text,
    validate_program,
)

SEARCH_VERSION = "verified-text-abstraction-search-v1"
FEATURE_DIMENSION = 64
OPERATIONS = (
    "field",
    "constant",
    "strip",
    "lower",
    "upper",
    "title",
    "capitalize",
    "compact",
    "slice",
    "token",
    "split",
    "replace",
    "number",
    "concat",
    "if_equal",
)
_FACTORY = object()


@dataclass(frozen=True, slots=True, init=False)
class PreparedGrammar:
    """Immutable in-memory preparation; not an untrusted serialized authority."""

    macros: tuple
    base_costs: tuple
    frequencies: tuple
    source_id: str
    config_sha256: str
    library_sha256: str
    preparation_work: int
    _factory: object

    def __init__(self):
        raise TypeError("Use prepare_grammar for a validated immutable grammar")


def prepare_grammar(library, config):
    """Validate parent proofs once and share identical bodies with every arm."""
    from cognition.text_abstractions import LIBRARY_SCHEMA, prepare_abstraction

    config = normalize_config(config)
    if (
        type(library) is not dict
        or library.get("schema") != LIBRARY_SCHEMA
        or library.get("config_sha256") != fingerprint(config)
        or type(library.get("source_id")) is not str
        or type(library.get("abstractions")) is not list
        or len(library["abstractions"]) > 64
    ):
        raise ValueError("Malformed source/config-bound abstraction library")
    macros, base_costs, frequencies, work = [], [], [], 0
    ordered = sorted(
        enumerate(library["abstractions"]), key=lambda item: (item[1]["arity"], item[0])
    )
    for _, record in ordered:
        if record.get("source_id") != library["source_id"]:
            raise ValueError("Abstractions from another source cannot enter a grammar")
        prepared = prepare_abstraction(record, config)
        sample = prepared.apply([{"op": "field", "index": 0}] * prepared.arity)
        base_costs.append(
            sample["construction_work"] - prepared.arity - 2 * sample["expanded_nodes"]
        )
        work += prepared.preparation_work + sample["construction_work"]
        macros.append(prepared)
        frequencies.append(len(record["support"]) + 1)
    result = object.__new__(PreparedGrammar)
    for key, value in {
        "macros": tuple(macros),
        "base_costs": tuple(base_costs),
        "frequencies": tuple(frequencies),
        "source_id": library["source_id"],
        "config_sha256": fingerprint(config),
        "library_sha256": fingerprint(library),
        "preparation_work": work,
        "_factory": _FACTORY,
    }.items():
        object.__setattr__(result, key, value)
    return result


def _depth(node):
    children = [node[k] for k in ("arg", "then", "else") if k in node]
    children += node.get("parts", [])
    return 1 + max((_depth(child) for child in children), default=0)


def observations(node, values, targets, config):
    """Encode executed partial values and disclosed targets, never buyer rows."""
    if (
        not isinstance(values, (list, tuple))
        or not isinstance(targets, (list, tuple))
        or not 1 <= len(values) == len(targets) <= config["max_rows"]
    ):
        raise ValueError("Partial observations need bounded paired strings")
    values = [text(v, config["max_input_bytes"]) for v in values]
    targets = [text(v, config["max_input_bytes"]) for v in targets]
    size = shape(node, config)
    relations = (
        lambda v, y: v == y,
        lambda v, y: v in y,
        lambda v, y: y.startswith(v),
        lambda v, y: y.endswith(v),
        lambda v, y: y in v,
        lambda v, y: v.startswith(y),
        lambda v, y: v.endswith(y),
        lambda v, y: v.casefold() == y.casefold(),
        lambda v, y: v.casefold() in y.casefold(),
        lambda v, y: y.casefold() in v.casefold(),
    )
    result = []
    for relation in relations:
        flags = [relation(v, y) for v, y in zip(values, targets, strict=True)]
        result.extend((float(all(flags)), sum(flags) / len(flags)))
    for predicate in (str.isalpha, str.isdigit, str.isupper, str.islower, str.isspace):
        for strings in (values, targets):
            total = sum(map(len, strings))
            result.append(sum(predicate(c) for v in strings for c in v) / max(1, total))
    result.extend(float(node["op"] == op) for op in OPERATIONS)
    result.extend((size / config["max_ops"], _depth(node) / 9))
    for strings in (values, targets):
        result.extend(
            (
                min(1.0, sum(map(len, strings)) / (len(strings) * 128)),
                len(set(strings)) / len(strings),
                float(all(strings)),
            )
        )
    result.extend([0.0] * (FEATURE_DIMENSION - len(result)))
    return result


def conventional_priority(features):
    """Target-relatedness rule over the same partial observations as learners."""
    x = np.asarray(features, dtype=np.float64)
    return 4 * x[:, 0] + 2 * x[:, 2] + x[:, 4] + x[:, 6] + x[:, 16] - x[:, 45]


def solve_abstractions(
    inputs,
    outputs,
    config,
    library,
    *,
    budget=50000,
    scorer=None,
    ordering="relatedness",
    maximum_states=256,
    argument_beam=8,
    maximum_frontier=4096,
    collect=False,
):
    """Return the first example-fitting program under a finite shared allowance.

    A typed multi-hole invocation binds at least one argument to the expanded
    frontier state and the others to a bounded observed argument beam. This is
    deliberately incomplete grammar search, not an exhaustive synthesizer.
    Equal diagnostic vectors share a representative; the buyer must check the
    consequences of this finite-example equivalence on independent rows.
    """
    started = time.process_time()
    meter = _Meter(budget)
    config = normalize_config(config)
    integer(maximum_states, 1, 1024)
    integer(argument_beam, 1, 16)
    integer(maximum_frontier, 1, 16384)
    if ordering not in ("relatedness", "frequency", "uniform", "guided"):
        raise ValueError("Unknown abstraction ordering")
    if (ordering == "guided") != (scorer is not None) or type(collect) is not bool:
        raise ValueError("Only explicit guided ordering accepts a scorer")
    if (
        type(inputs) is not list
        or type(outputs) is not list
        or not 1 <= len(inputs) == len(outputs) <= config["max_rows"]
    ):
        raise ValueError("Malformed disclosed abstraction-search examples")
    rows = [fields(v, config) for v in inputs]
    targets = tuple(text(v, config["max_input_bytes"]) for v in outputs)
    shared_preparation = isinstance(library, PreparedGrammar)
    grammar = library if shared_preparation else prepare_grammar(library, config)
    if grammar._factory is not _FACTORY or grammar.config_sha256 != fingerprint(config):
        raise ValueError("Prepared grammar source or configuration changed")
    if (
        any(
            m.source_id != grammar.source_id or m.config_sha256 != grammar.config_sha256
            for m in grammar.macros
        )
        or len(grammar.base_costs) != len(grammar.macros)
        or len(grammar.frequencies) != len(grammar.macros)
        or any(type(v) is not int or v < 0 for v in grammar.base_costs)
        or any(type(v) is not int or not 1 <= v <= 9 for v in grammar.frequencies)
    ):
        raise ValueError("Prepared grammar bindings or construction ledger changed")
    macros = grammar.macros
    frequencies = {
        macro.abstraction_sha256: count
        for macro, count in zip(macros, grammar.frequencies, strict=True)
    }
    total_frequency = sum(frequencies.values())
    grammar_costs = {
        key: -math.log(value / total_frequency) for key, value in frequencies.items()
    }
    records, signatures, pending, attempted = [], {}, [], set()
    result, status, sequence = None, "exhausted", 0
    score_cpu = 0.0
    scored = 0
    trace = []
    used_abstractions = []
    frontier_limited = False
    preparation_debit = 0

    def rank(record):
        nonlocal score_cpu, scored
        if ordering == "uniform":
            return -record["size"]
        if ordering == "frequency":
            return (
                -sum(grammar_costs[a] for a in record["abstractions"])
                - 0.025 * record["size"]
            )
        start = time.process_time()
        scores = (scorer if scorer is not None else conventional_priority)(
            np.asarray([record["features"]], dtype=np.float64)
        )
        score_cpu += time.process_time() - start
        scored += 1
        if np.shape(scores) != (1,) or not math.isfinite(float(scores[0])):
            raise ValueError("A ranker must return one finite score per observation")
        return float(scores[0]) - 0.025 * record["size"]

    def attempt(node, ancestry):
        nonlocal result, status, sequence, used_abstractions
        meter.charge("candidates")
        meter.data(node)
        key = fingerprint(node)
        if key in attempted:
            meter.values["semantic_duplicates"] += 1
            return
        if len(attempted) >= config["max_candidates"]:
            raise WorkLimit("Candidate authority exhausted")
        attempted.add(key)
        try:
            size = shape(node, config)
            observed = tuple(evaluate(node, row, meter, config) for row in rows)
        except (ValueError, IndexError, KeyError, InvalidOperation) as error:
            if isinstance(error, WorkLimit):
                raise
            meter.values["errors"] += 1
            return
        if observed == targets:
            result = validate_program({"version": VERSION, "expr": node}, config)
            status = "proposed"
            used_abstractions = list(ancestry)
            return
        prior = signatures.get(observed)
        if prior is not None or len(records) >= maximum_states:
            meter.values["semantic_duplicates"] += 1
            return
        features = observations(node, observed, targets, config)
        meter.charge("primitive_attempts", FEATURE_DIMENSION)
        meter.data(observed)
        record = {
            "node": node,
            "values": observed,
            "size": size,
            "features": features,
            "abstractions": tuple(ancestry),
        }
        record["priority"] = rank(record)
        signatures[observed] = len(records)
        records.append(record)
        heapq.heappush(pending, (-record["priority"], sequence, len(records) - 1))
        sequence += 1
        if collect:
            trace.append({"node": node, "values": list(observed), "features": features})

    def concatenate():
        """Assemble observed pieces; literal characters come only from targets."""
        nonlocal frontier_limited
        atoms = [
            r
            for r in records
            if any(r["values"])
            and all(v in y for v, y in zip(r["values"], targets, strict=True))
        ]
        queue = [(0, 0, (0,) * len(targets), (), ())]
        seen, serial = {}, 0
        while queue:
            _, _, positions, parts, ancestry = heapq.heappop(queue)
            meter.charge("primitive_attempts")
            if positions == tuple(map(len, targets)) and parts:
                node = (
                    parts[0]
                    if len(parts) == 1
                    else {"op": "concat", "parts": list(parts)}
                )
                attempt(node, ancestry)
                if result is not None:
                    return
            if len(parts) >= config["max_parts"]:
                continue
            prefix = targets[0][positions[0] :]
            for target, position in zip(targets[1:], positions[1:], strict=True):
                tail, length = target[position:], 0
                while (
                    length < min(len(prefix), len(tail))
                    and prefix[length] == tail[length]
                ):
                    meter.charge("primitive_attempts")
                    length += 1
                prefix = prefix[:length]
            choices = [(r["node"], r["values"], r["abstractions"]) for r in atoms]
            choices += [
                (
                    {"op": "constant", "value": prefix[:n]},
                    (prefix[:n],) * len(targets),
                    (),
                )
                for n in range(min(len(prefix), 128), 0, -1)
            ]
            for node, values, used in choices:
                meter.charge("candidates")
                if not all(
                    y.startswith(v, p)
                    for y, v, p in zip(targets, values, positions, strict=True)
                ):
                    continue
                future = tuple(
                    p + len(v) for p, v in zip(positions, values, strict=True)
                )
                key = (future, len(parts) + 1)
                cost = sum(shape(p, config) for p in (*parts, node))
                if cost + (1 if parts else 0) > config["max_ops"]:
                    continue
                if key in seen and seen[key] <= cost:
                    continue
                if key not in seen and len(seen) >= maximum_frontier:
                    frontier_limited = True
                    continue
                if len(queue) >= maximum_frontier:
                    frontier_limited = True
                    continue
                seen[key] = cost
                serial += 1
                heapq.heappush(
                    queue,
                    (
                        cost,
                        serial,
                        future,
                        (*parts, node),
                        (*ancestry, *used),
                    ),
                )

    try:
        if not shared_preparation:
            meter.charge("primitive_attempts", grammar.preparation_work)
            preparation_debit = grammar.preparation_work
        for value in inputs + outputs:
            meter.data(value)
        for index in range(min(map(len, rows))):
            attempt({"op": "field", "index": index}, ())
            if result is not None:
                break
        if (
            len(set(targets)) == 1
            and result is None
            and len(targets[0].encode()) <= 256
        ):
            attempt({"op": "constant", "value": targets[0]}, ())
        while pending and result is None:
            _, _, current = heapq.heappop(pending)
            beam = sorted(
                range(len(records)), key=lambda i: (-records[i]["priority"], i)
            )[:argument_beam]
            for macro_index, macro in enumerate(macros):
                arity = macro.arity
                for position in range(arity):
                    choices = [
                        [current] if i == position else beam for i in range(arity)
                    ]
                    for indices in itertools.product(*choices):
                        meter.charge("primitive_attempts")
                        args = [records[i]["node"] for i in indices]
                        argument_sizes = [shape(arg, config) for arg in args]
                        expanded_size = macro.fixed_nodes + sum(
                            count * size
                            for count, size in zip(
                                macro.hole_occurrences, argument_sizes, strict=True
                            )
                        )
                        proof_nodes = grammar.base_costs[macro_index]
                        meter.charge(
                            "primitive_attempts",
                            proof_nodes
                            + sum(argument_sizes)
                            + (
                                2 * expanded_size
                                if expanded_size <= config["max_ops"]
                                else 0
                            ),
                        )
                        meter.data(args)
                        try:
                            report = macro.apply(args)
                            if (
                                report["construction_work"]
                                != proof_nodes + sum(argument_sizes) + 2 * expanded_size
                            ):
                                raise RuntimeError(
                                    "Prepared construction accounting changed"
                                )
                            program = report["program"]
                        except ValueError:
                            meter.values["errors"] += 1
                            continue
                        ancestry = tuple(
                            a for i in indices for a in records[i]["abstractions"]
                        )
                        ancestry += (macro.abstraction_sha256,)
                        attempt(program["expr"], ancestry)
                        if result is not None:
                            break
                    if result is not None:
                        break
                if result is not None:
                    break
            if result is None:
                concatenate()
    except WorkLimit:
        status = "work_limit"
    return {
        "program": result,
        "status": status,
        "work": copy.deepcopy(meter.values),
        "states": len(records),
        "attempted": len(attempted),
        "frontier_limited": frontier_limited,
        "preparation": {
            "shared": shared_preparation,
            "work": grammar.preparation_work,
            "charged_to_search": preparation_debit,
            "uncharged_preparation_work": 0
            if shared_preparation
            else grammar.preparation_work - preparation_debit,
        },
        "abstractions_used": used_abstractions,
        "candidate_records": trace,
        "ranking": {"ordering": ordering, "calls": scored, "cpu_seconds": score_cpu},
        "cpu_seconds": time.process_time() - started,
        "search_version": SEARCH_VERSION,
        "commitment": fingerprint(
            {
                "inputs": inputs,
                "outputs": outputs,
                "config": config,
                "library_sha256": grammar.library_sha256,
                "ordering": ordering,
                "maximum_states": maximum_states,
                "argument_beam": argument_beam,
                "maximum_frontier": maximum_frontier,
                "budget": budget,
                "program": result,
            }
        ),
    }

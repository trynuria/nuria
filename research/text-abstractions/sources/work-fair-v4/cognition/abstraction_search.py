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
from cognition.text_composition import POSITIONS, SLICE_ENDS, SLICE_STARTS
from cognition.text_skills import (
    CASES,
    PATTERNS,
    SCALES,
    VERSION,
    evaluate,
    fields,
    integer,
    normalize_config,
    shape,
    text,
    validate_program,
)

SEARCH_VERSION = "verified-text-abstraction-search-v4-work-fair"
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


class _AssemblySliceEnd(Exception):
    pass


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
    coverage = np.minimum(1, x[:, 47] / np.maximum(x[:, 50], 1 / 128))
    related = 4 * x[:, 0] + 2 * x[:, 2] + x[:, 4] + x[:, 6] + 3 * x[:, 14] + x[:, 16]
    return related * coverage * x[:, 49] - x[:, 45]


def _native_operations(argument, values, targets):
    """Lazily interleave the original bounded unary parameter families."""
    separators = sorted({c for value in values for c in value if not c.isalnum()})[:32]
    separators += [s for s in " ,-_/.@:;=|\\\t()[]" if s not in separators]
    separators = separators[:32]
    lengths = sorted({len(y) for y in targets if 1 <= len(y) <= 8})
    preferred = [(0, n) for n in lengths]
    pairs = itertools.chain(
        preferred,
        (
            (a, b)
            for a in SLICE_STARTS
            for b in SLICE_ENDS
            if (a, b) not in preferred and (a != 0 or b is not None)
        ),
    )
    variants = (
        ({"op": op, "arg": argument} for op in sorted(CASES)),
        (
            {"op": "token", "arg": argument, "pattern": p, "index": i}
            for i in POSITIONS
            for p in sorted(PATTERNS)
        ),
        (
            {"op": "split", "arg": argument, "separator": s, "index": i}
            for i in POSITIONS
            for s in separators
        ),
        ({"op": "slice", "arg": argument, "start": a, "end": b} for a, b in pairs),
        (
            {"op": "replace", "arg": argument, "old": c, "new": ""}
            for c in sorted({c for value in values for c in value})[:64]
        ),
        (
            {"op": "number", "arg": argument, "scale": scale, "places": places}
            for scale in SCALES
            for places in (None, 0, 1, 2, 3, 4, 5, 6)
        ),
    )
    # The established shallow solver also tries cheap case transforms before
    # large token/slice parameter families. Keep that useful default here.
    yield from variants[0]
    for batch in itertools.zip_longest(*variants[1:]):
        for node in batch:
            if node is not None:
                yield node


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
    use_abstractions=True,
    use_native=True,
    scheduling="work_fair",
    assembly_slice=750,
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
    integer(assembly_slice, 1, 100000)
    if scheduling not in ("legacy", "work_fair"):
        raise ValueError("Unknown search scheduling rule")
    if type(use_abstractions) is not bool or type(use_native) is not bool:
        raise ValueError("Operation-family switches must be explicit booleans")
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
    invocation_counts = {"native": 0, "macro": 0}
    frontier_limited = False
    preparation_debit = 0
    cursors, turns, expanded = {}, {}, set()
    state_work = {}
    assembly_work = 0
    assembly_yields = 0

    def queue_priority(index):
        record = records[index]
        if scheduling == "legacy":
            return -record["priority"] + 0.02 * turns.get(index, 0)
        if ordering == "uniform":
            confidence = 1 / (1 + record["size"])
        elif ordering == "frequency":
            confidence = math.exp(min(0, record["priority"]))
        else:
            confidence = min(1.0, max(0.0, record["priority"] / 12))
        return -(0.1 + confidence) / math.sqrt(1 + state_work.get(index, 0) / 8)

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
        heapq.heappush(
            pending, (queue_priority(len(records) - 1), sequence, len(records) - 1)
        )
        sequence += 1
        if collect:
            trace.append(
                {
                    "node": node,
                    "values": list(observed),
                    "features": features,
                    "priority": record["priority"],
                }
            )

    def concatenate():
        """Assemble observed pieces; literal characters come only from targets."""
        nonlocal frontier_limited, assembly_work, assembly_yields
        start_work = meter.values["total"]

        def assembly_charge(key, amount=1):
            if (
                scheduling == "work_fair"
                and meter.values["total"] - start_work + amount > assembly_slice
            ):
                raise _AssemblySliceEnd
            meter.charge(key, amount)

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
            assembly_charge("primitive_attempts")
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
                    assembly_charge("primitive_attempts")
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
                assembly_charge("candidates")
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

    def run_assembly():
        nonlocal assembly_work, assembly_yields
        before = meter.values["total"]
        try:
            concatenate()
        except _AssemblySliceEnd:
            assembly_yields += 1
        finally:
            assembly_work += meter.values["total"] - before

    active_state, active_start = None, 0
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
        if result is None:
            run_assembly()
        cursors, turns, expanded = {}, {}, set()

        def invocations(current):
            beam = sorted(
                range(len(records)), key=lambda i: (-records[i]["priority"], i)
            )[:argument_beam]

            def macro_actions():
                if not use_abstractions:
                    return
                for macro_index, macro in enumerate(macros):
                    for position in range(macro.arity):
                        choices = [
                            [current] if i == position else beam
                            for i in range(macro.arity)
                        ]
                        for indices in itertools.product(*choices):
                            yield "macro", macro_index, indices, 0

            def native_actions():
                if not use_native:
                    return
                argument, values = records[current]["node"], records[current]["values"]
                meter.data(values)
                meter.data(targets)
                separators = {"", ".", "-", ":", "/", " "}
                for target in targets:
                    for alphanumeric, group in itertools.groupby(target, str.isalnum):
                        piece = "".join(group)
                        if not alphanumeric and len(piece.encode()) <= 16:
                            separators.add(piece)
                separators = sorted(separators, key=lambda value: (len(value), value))[
                    :16
                ]

                def unary():
                    for node in _native_operations(argument, values, targets):
                        yield "native", node, (current,), 1 + records[current]["size"]

                def joins():
                    for other in beam:
                        for indices in ((current, other), (other, current)):
                            for separator in separators:
                                parts = [records[indices[0]]["node"]]
                                if separator:
                                    parts.append({"op": "constant", "value": separator})
                                parts.append(records[indices[1]]["node"])
                                size = (
                                    1
                                    + sum(records[i]["size"] for i in indices)
                                    + int(bool(separator))
                                )
                                yield (
                                    "native",
                                    {"op": "concat", "parts": parts},
                                    indices,
                                    size,
                                )

                for pair in itertools.zip_longest(unary(), joins()):
                    for action in pair:
                        if action is not None:
                            yield action

            for pair in itertools.zip_longest(native_actions(), macro_actions()):
                for action in pair:
                    if action is not None:
                        yield action

        while pending and result is None:
            queued, _, current = heapq.heappop(pending)
            expected = queue_priority(current)
            if scheduling == "work_fair" and expected > queued:
                sequence += 1
                heapq.heappush(pending, (expected, sequence, current))
                continue
            if current not in cursors:
                cursors[current], turns[current] = invocations(current), 0
            try:
                kind, payload, indices, native_size = next(cursors[current])
            except StopIteration:
                continue
            expanded.add(current)
            active_state, active_start = current, meter.values["total"]
            turns[current] += 1
            sequence += 1
            # One invocation yields the frontier. A large multi-hole expansion
            # cannot spend the full allowance before another state is considered.
            heapq.heappush(
                pending,
                (
                    queue_priority(current),
                    sequence,
                    current,
                ),
            )
            invocation_counts[kind] += 1
            meter.charge("primitive_attempts")
            args = [records[i]["node"] for i in indices]
            argument_sizes = [records[i]["size"] for i in indices]
            ancestry = tuple(a for i in indices for a in records[i]["abstractions"])
            if kind == "native":
                meter.charge(
                    "primitive_attempts", sum(argument_sizes) + 2 * native_size
                )
                node = payload
            else:
                macro_index = payload
                macro = macros[macro_index]
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
                    + (2 * expanded_size if expanded_size <= config["max_ops"] else 0),
                )
                meter.data(args)
                try:
                    report = macro.apply(args)
                    if (
                        report["construction_work"]
                        != proof_nodes + sum(argument_sizes) + 2 * expanded_size
                    ):
                        raise RuntimeError("Prepared construction accounting changed")
                    node = report["program"]["expr"]
                except ValueError:
                    meter.values["errors"] += 1
                    state_work[current] = (
                        state_work.get(current, 0)
                        + meter.values["total"]
                        - active_start
                    )
                    active_state = None
                    continue
                ancestry += (macro.abstraction_sha256,)
            before = len(records)
            attempt(node, ancestry)
            if (
                result is None
                and len(records) > before
                and all(
                    piece in target
                    for piece, target in zip(
                        records[-1]["values"], targets, strict=True
                    )
                )
                and any(records[-1]["values"])
            ):
                run_assembly()
            state_work[current] = (
                state_work.get(current, 0) + meter.values["total"] - active_start
            )
            active_state = None
    except WorkLimit:
        if active_state is not None:
            state_work[active_state] = (
                state_work.get(active_state, 0) + meter.values["total"] - active_start
            )
        status = "work_limit"
    return {
        "program": result,
        "status": status,
        "work": copy.deepcopy(meter.values),
        "states": len(records),
        "expanded_states": len(expanded),
        "expansion_turns": sum(turns.values()),
        "invocation_counts": invocation_counts,
        "use_native": use_native,
        "use_abstractions": use_abstractions,
        "attempted": len(attempted),
        "frontier_limited": frontier_limited,
        "scheduling": scheduling,
        "state_work": {str(key): value for key, value in state_work.items()},
        "assembly_work": assembly_work,
        "assembly_yields": assembly_yields,
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
                "use_native": use_native,
                "use_abstractions": use_abstractions,
                "maximum_states": maximum_states,
                "argument_beam": argument_beam,
                "maximum_frontier": maximum_frontier,
                "scheduling": scheduling,
                "assembly_slice": assembly_slice,
                "budget": budget,
                "program": result,
            }
        ),
    }

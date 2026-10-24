"""Finite composition search in the existing safe text language.

This versioned learner leaves the original qualification implementation intact.
Its search family contains shallow projections, a second case/whitespace or
slice operation, and concatenations of those atoms. It does not search every
valid AST. The two modes share the same ordered family: ``deduplicated`` avoids
expanding training-equivalent atoms; ``enumerative`` retains them. Neither mode
receives validation rows or a task identifier. Training equivalence is not a
generalization guarantee; final acceptance still belongs to the buyer.
"""

from __future__ import annotations

import copy
import heapq
import json
from decimal import InvalidOperation
from itertools import zip_longest

from cognition.skills import WorkLimit, _features, _Meter, fingerprint
from cognition.text_skills import (
    CASES,
    PATTERNS,
    SCALES,
    VERSION,
    TextSkillLibrary,
    evaluate,
    fields,
    integer,
    normalize_config,
    shape,
    text,
    validate_program,
)

SEARCH_VERSION = "bounded-text-composition-v1"
SEARCH_MODES = ("deduplicated", "enumerative")
POSITIONS = (0, -1, 1, -2, 2, -3, 3, -4)
SLICE_STARTS = (0, 1, 2, 3, 4, 5, 6, 7, 8, -1, -2, -3, -4, -5, -6, -7, -8)
SLICE_ENDS = (None, -1, -2, -3, -4, -5, -6, -7, -8, 1, 2, 3, 4, 5, 6, 7, 8)


def _slices(arg, values=(), outputs=()):
    pairs = [
        (start, end)
        for start in SLICE_STARTS
        for end in SLICE_ENDS
        if start != 0 or end is not None
    ]
    # Disclosed output lengths prioritize possible boundaries, never remove
    # candidates. This remains a language search, not a task recipe lookup.
    if values and outputs:
        pairs.sort(
            key=lambda pair: (
                not all(
                    len(value[slice(*pair)]) == len(output)
                    for value, output in zip(values, outputs, strict=True)
                )
            )
        )
    for start, end in pairs:
        yield {"op": "slice", "arg": arg, "start": start, "end": end}


def _families(arg, values):
    """Parameters depend only on the currently disclosed input strings."""
    yield [{"op": case, "arg": arg} for case in sorted(CASES)]
    yield [
        {"op": "token", "arg": arg, "pattern": pattern, "index": position}
        for position in POSITIONS
        for pattern in sorted(PATTERNS)
    ]
    separators = set(" ,-_/.@:;=|\\\t()[]")
    separators.update(char for value in values for char in value if not char.isalnum())
    ordered_separators = sorted(separators)[:32]
    ordered_separators.sort(
        key=lambda separator: not any(separator in value for value in values)
    )
    yield [
        {"op": "split", "arg": arg, "separator": separator, "index": position}
        for position in POSITIONS
        for separator in ordered_separators
    ]
    yield list(_slices(arg))
    yield [
        {"op": "replace", "arg": arg, "old": old, "new": ""}
        for old in sorted({char for value in values for char in value})[:64]
    ]
    yield [
        {"op": "number", "arg": arg, "scale": scale, "places": places}
        for scale in SCALES
        for places in (None, 0, 1, 2, 3, 4, 5, 6)
    ]


def infer_composed(inputs, outputs, config, meter, *, mode="deduplicated"):
    """Propose an AST from disclosed examples, or exhaust the finite meter.

    Semantic pruning is sound only for fitting these examples: equal complete
    vectors remain equal through the same deterministic outer operation. It
    preserves the earliest smallest-node representative. The final atom filter
    requires each piece to occur in its target; intermediate atoms are never
    filtered this way, since a later case operation can change their strings.
    """
    if mode not in SEARCH_MODES:
        raise ValueError("Unsupported compositional search mode")
    config = normalize_config(config)
    if (
        type(inputs) is not list
        or type(outputs) is not list
        or len(inputs) != len(outputs)
    ):
        raise ValueError("Composition search needs paired example lists")
    integer(len(inputs), 1, config["max_rows"])
    rows = [fields(value, config) for value in inputs]
    outputs = [text(value, config["max_input_bytes"]) for value in outputs]
    for value in inputs + outputs:
        meter.data(value)
    targets = tuple(outputs)
    atoms, atom_signatures, expansion_signatures = [], {}, {}

    def attempt(node, argument_values=None):
        if meter.values["candidates"] >= config["max_candidates"]:
            raise WorkLimit("Composition candidate limit reached")
        meter.charge("candidates")
        meter.data(json.dumps(node, sort_keys=True, separators=(",", ":")))
        try:
            size = shape(node, config)
            if argument_values is None:
                observed = tuple(evaluate(node, row, meter, config) for row in rows)
            else:
                # The inner vector was already executed and charged. Reuse it
                # through the same interpreter rather than reparse/reexecute
                # that AST for every outer operation. Both modes use this cache.
                cached = {**node, "arg": {"op": "field", "index": 0}}
                observed = tuple(
                    evaluate(cached, [value], meter, config)
                    for value in argument_values
                )
        except (ValueError, IndexError, KeyError, InvalidOperation) as error:
            if isinstance(error, WorkLimit):
                raise
            meter.values["errors"] += 1
            return None
        meter.data(json.dumps(observed, separators=(",", ":")))
        return observed, size

    def remember(node, observed, size):
        # Concatenation cannot remove characters. Filtering its finished pieces
        # by exact substring containment is safe even across word boundaries.
        if not any(observed) or not all(
            piece in target for piece, target in zip(observed, targets, strict=True)
        ):
            return
        prior = atom_signatures.get(observed)
        if mode == "deduplicated" and prior is not None and prior <= size:
            meter.values["semantic_duplicates"] += 1
            return
        atom_signatures[observed] = size
        atoms.append((node, observed, size))

    def concat():
        initial = (0,) * len(targets)
        queue, sequence, visited = [(0, 0, initial, ())], 0, {}
        while queue:
            cost, _, positions, parts = heapq.heappop(queue)
            if positions == tuple(map(len, targets)) and parts:
                node = (
                    parts[0]
                    if len(parts) == 1
                    else {"op": "concat", "parts": list(parts)}
                )
                checked = attempt(node)
                if checked is not None and checked[0] == targets:
                    return node
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
            literals = [
                (
                    {"op": "constant", "value": prefix[:length]},
                    (prefix[:length],) * len(targets),
                    1,
                )
                for length in range(min(len(prefix), 128), 0, -1)
            ]
            for node, observed, size in atoms + literals:
                if meter.values["candidates"] >= config["max_candidates"]:
                    raise WorkLimit("Composition candidate limit reached")
                meter.charge("candidates")
                meter.data(json.dumps(observed, separators=(",", ":")))
                if cost + size + 1 > config["max_ops"] or not all(
                    target.startswith(piece, position)
                    for target, piece, position in zip(
                        targets, observed, positions, strict=True
                    )
                ):
                    continue
                future = tuple(
                    position + len(piece)
                    for position, piece in zip(positions, observed, strict=True)
                )
                key = (future, len(parts) + 1)
                if key in visited and visited[key] <= cost + size:
                    continue
                visited[key] = cost + size
                sequence += 1
                heapq.heappush(queue, (cost + size, sequence, future, (*parts, node)))
        return None

    if len(set(outputs)) == 1 and len(outputs[0].encode()) <= 256:
        constant = {"op": "constant", "value": outputs[0]}
        checked = attempt(constant)
        if checked is not None and checked[0] == targets:
            return constant

    # All fields and cheap shallow case operations run before any composition.
    # Subsequent parameter batches are interleaved across fields; a verbose
    # early field cannot consume the whole budget before a later field is seen.
    seeds = [{"op": "field", "index": index} for index in range(min(map(len, rows)))]
    for seed in seeds:
        checked = attempt(seed)
        if checked is not None:
            observed, size = checked
            if observed == targets:
                return seed
            remember(seed, observed, size)
    families = [
        list(_families(seed, [row[seed["index"]] for row in rows])) for seed in seeds
    ]
    expandable = []

    def shallow(batch):
        fresh = []
        for node in batch:
            if node is None:
                continue
            checked = attempt(node)
            if checked is None:
                continue
            observed, size = checked
            if observed == targets:
                return node, fresh
            remember(node, observed, size)
            prior = expansion_signatures.get(observed)
            if mode == "deduplicated" and prior is not None and prior <= size:
                meter.values["semantic_duplicates"] += 1
                continue
            expansion_signatures[observed] = size
            fresh.append((node, observed))
        expandable.extend(fresh)
        return None, fresh

    def cases(records):
        for case in sorted(CASES):
            for node, observed in records:
                outer = {"op": case, "arg": node}
                checked = attempt(outer, observed)
                if checked is None:
                    continue
                values, size = checked
                if values == targets:
                    return outer
                remember(outer, values, size)
        return None

    def finish_stage(records):
        proposal = cases(records)
        return proposal if proposal is not None else concat()

    case_batch = [
        node
        for batch in zip_longest(*(family[0] for family in families))
        for node in batch
        if node is not None
    ]
    proposal, fresh = shallow(case_batch)
    if proposal is not None:
        return proposal
    proposal = finish_stage(fresh)
    if proposal is not None:
        return proposal

    # Both extraction families use the same position order. Visit the common
    # first/last positions in each family before their less common positions.
    for position_offset in range(len(POSITIONS)):
        for family_index in (1, 2):
            per_field = []
            for family in families:
                group_size = len(family[family_index]) // len(POSITIONS)
                per_field.append(
                    family[family_index][
                        position_offset * group_size : (position_offset + 1)
                        * group_size
                    ]
                )
            batch = [
                node
                for grouped in zip_longest(*per_field)
                for node in grouped
                if node is not None
            ]
            proposal, fresh = shallow(batch)
            if proposal is not None:
                return proposal
            proposal = finish_stage(fresh)
            if proposal is not None:
                return proposal

    # Length-compatible shallow slices first, then all remaining boundaries.
    slices = [
        list(_slices(seed, [row[seed["index"]] for row in rows], outputs))
        for seed in seeds
    ]
    for grouped in zip_longest(*slices):
        proposal, fresh = shallow(grouped)
        if proposal is not None:
            return proposal
        proposal = cases(fresh)
        if proposal is not None:
            return proposal
    proposal = concat()
    if proposal is not None:
        return proposal
    for family_index in (4, 5):
        for grouped in zip_longest(*(family[family_index] for family in families)):
            proposal, fresh = shallow(grouped)
            if proposal is not None:
                return proposal
            proposal = cases(fresh)
            if proposal is not None:
                return proposal
        proposal = concat()
        if proposal is not None:
            return proposal

    # Expensive second-layer slices are last; all their case alternatives have
    # already been tried. This changes order, not the two modes' finite family.
    for node, observed in expandable:
        for outer in _slices(node, observed, outputs):
            checked = attempt(outer, observed)
            if checked is None:
                continue
            values, size = checked
            if values == targets:
                return outer
            remember(outer, values, size)
    proposal = concat()
    if proposal is not None:
        return proposal
    return None


class CompositionalTextLibrary(TextSkillLibrary):
    """Existing buyer-admission contract with an explicitly selected new search."""

    def __init__(self, *args, search_mode="deduplicated", **kwargs):
        if search_mode not in SEARCH_MODES:
            raise ValueError("Unsupported compositional search mode")
        self.search_mode = search_mode
        super().__init__(*args, **kwargs)

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
        rows = [fields(value, self.config) for value in inputs]
        outputs = [text(value, self.config["max_input_bytes"]) for value in outputs]
        meter, node, status = _Meter(budget), None, "not_found"
        try:
            if mode in ("retained_factored", "active"):
                for _, record in sorted(
                    self.state["programs"].items(),
                    key=lambda pair: (-pair[1]["uses"], pair[0]),
                ):
                    if meter.values["candidates"] >= self.config["max_candidates"]:
                        raise WorkLimit("Text search candidate limit reached")
                    meter.charge("candidates")
                    try:
                        observed = [
                            evaluate(record["program"]["expr"], row, meter, self.config)
                            for row in rows
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
                node = infer_composed(
                    inputs, outputs, self.config, meter, mode=self.search_mode
                )
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
        proposal_id, program_sha = (
            None,
            fingerprint(program) if program is not None else None,
        )
        if program is not None:
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
            "proposal_id": proposal_id,
            "program": program,
            "program_sha256": program_sha,
            "source_id": self.source_id,
            "config_sha256": self.config_sha256,
            "solved_training": program is not None,
            "exhausted": status == "work_limit",
            "mode": mode,
            "work": copy.deepcopy(meter.values),
            "search_version": SEARCH_VERSION,
            "search_mode": self.search_mode,
        }

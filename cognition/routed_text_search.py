"""Metered search-order tools in the existing finite safe text grammar.

The candidate engine and buyer-admission contract are derived from
``cognition.text_composition`` (bounded-text-composition-v1). The frozen module
remains unchanged. Profiles alter actual candidate visitation under the same
work meter, AST validator and interpreter; they do not add operation families,
see final targets or establish that a neural router chooses well.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import json
from decimal import InvalidOperation
from itertools import zip_longest
from pathlib import Path

from cognition import skills, text_composition, text_skills
from cognition.skills import WorkLimit, _features, _Meter, fingerprint
from cognition.text_composition import (
    POSITIONS,
    SEARCH_MODES,
    SEARCH_VERSION,
    _families,
    _slices,
)
from cognition.text_composition import (
    infer_composed as _original_infer,
)
from cognition.text_skills import (
    CASES,
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

ROUTING_VERSION = "routed-text-search-v1"
PROFILE_STAGES = {
    "default": ("case", "extract", "split", "slice", "replace", "number"),
    "extract_first": ("extract", "case", "split", "slice", "replace", "number"),
    "slice_first": ("slice", "case", "extract", "split", "replace", "number"),
    "split_first": ("split", "case", "extract", "slice", "replace", "number"),
    "replace_first": ("replace", "case", "extract", "split", "slice", "number"),
    "number_first": ("number", "case", "extract", "split", "slice", "replace"),
    "composition_first": ("case", "extract", "split", "slice", "replace", "number"),
    "reverse_fields": ("case", "extract", "split", "slice", "replace", "number"),
}
PROFILES = tuple(PROFILE_STAGES)


def implementation_hash():
    return fingerprint(
        {
            Path(module.__file__).name: hashlib.sha256(
                Path(module.__file__).read_bytes()
            ).hexdigest()
            for module in (skills, text_composition, text_skills)
        }
        | {Path(__file__).name: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    )


def infer_routed(
    inputs, outputs, config, meter, *, profile="default", mode="deduplicated"
):
    """Propose an AST from disclosed examples, or exhaust the finite meter.

    Semantic pruning is sound only for fitting these examples: equal complete
    vectors remain equal through the same deterministic outer operation. It
    preserves the earliest smallest-node representative. The final atom filter
    requires each piece to occur in its target; intermediate atoms are never
    filtered this way, since a later case operation can change their strings.
    """
    if profile not in PROFILES:
        raise ValueError("Unsupported text routing profile")
    if profile == "default":
        return _original_infer(inputs, outputs, config, meter, mode=mode)
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
    if profile == "reverse_fields":
        seeds.reverse()
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
        proposal = outer_slices(records) if profile == "composition_first" else None
        if proposal is not None:
            return proposal
        proposal = cases(records)
        return proposal if proposal is not None else concat()

    expanded = set()

    def outer_slices(records):
        for node, observed in records:
            key = fingerprint(node)
            if key in expanded:
                continue
            expanded.add(key)
            for outer in _slices(node, observed, outputs):
                checked = attempt(outer, observed)
                if checked is None:
                    continue
                values, size = checked
                if values == targets:
                    return outer
                remember(outer, values, size)
        return None

    def case_stage():
        batch = [
            node
            for grouped in zip_longest(*(family[0] for family in families))
            for node in grouped
            if node is not None
        ]
        proposal, fresh = shallow(batch)
        return proposal if proposal is not None else finish_stage(fresh)

    def extraction_stage(family_index):
        for position_offset in range(len(POSITIONS)):
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
        return None

    def slice_stage():
        groups = [
            list(_slices(seed, [row[seed["index"]] for row in rows], outputs))
            for seed in seeds
        ]
        for grouped in zip_longest(*groups):
            proposal, fresh = shallow(grouped)
            if proposal is not None:
                return proposal
            if profile == "composition_first":
                proposal = outer_slices(fresh)
                if proposal is not None:
                    return proposal
            proposal = cases(fresh)
            if proposal is not None:
                return proposal
        return concat()

    def other_stage(family_index):
        for grouped in zip_longest(*(family[family_index] for family in families)):
            proposal, fresh = shallow(grouped)
            if proposal is not None:
                return proposal
            if profile == "composition_first":
                proposal = outer_slices(fresh)
                if proposal is not None:
                    return proposal
            proposal = cases(fresh)
            if proposal is not None:
                return proposal
        return concat()

    stages = {
        "case": case_stage,
        "extract": lambda: extraction_stage(1),
        "split": lambda: extraction_stage(2),
        "slice": slice_stage,
        "replace": lambda: other_stage(4),
        "number": lambda: other_stage(5),
    }
    for name in PROFILE_STAGES[profile]:
        proposal = stages[name]()
        if proposal is not None:
            return proposal
    proposal = outer_slices(expandable)
    return proposal if proposal is not None else concat()


class RoutedTextLibrary(TextSkillLibrary):
    """Source-bound verified knowledge shared across explicit routing tools.

    Changing profile selects another reviewed visitation order, while retained
    programs keep their original buyer evidence. A state from a different source
    or implementation is refused; legacy histories require explicit migration.
    """

    def __init__(
        self,
        source_id,
        config=None,
        state=None,
        *,
        profile="default",
        search_mode="deduplicated",
    ):
        if profile not in PROFILES or search_mode not in SEARCH_MODES:
            raise ValueError("Unsupported text routing profile or search mode")
        self.profile, self.search_mode = profile, search_mode
        self.implementation_sha256 = implementation_hash()
        if state is not None:
            if (
                not isinstance(state, dict)
                or set(state)
                != {
                    "schema",
                    "source_id",
                    "implementation_sha256",
                    "profiles",
                    "search_mode",
                    "library",
                }
                or state["schema"] != ROUTING_VERSION
                or state["source_id"] != source_id
                or state["implementation_sha256"] != self.implementation_sha256
                or state["profiles"] != list(PROFILES)
                or state["search_mode"] != search_mode
            ):
                raise ValueError("Routed text checkpoint authority differs")
            state = state["library"]
        if config is None:
            config = text_skills.DEFAULT_TEXT_CONFIG
        super().__init__(source_id, config, state=state)

    def dump(self):
        return {
            "schema": ROUTING_VERSION,
            "source_id": self.source_id,
            "implementation_sha256": self.implementation_sha256,
            "profiles": list(PROFILES),
            "search_mode": self.search_mode,
            "library": super().dump(),
        }

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
        library_before_sha256 = fingerprint(self.dump())
        training_sha256 = fingerprint([inputs, outputs])
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
                node = infer_routed(
                    inputs,
                    outputs,
                    self.config,
                    meter,
                    profile=self.profile,
                    mode=self.search_mode,
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
        result = {
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

        result.update(
            {
                "routing_version": ROUTING_VERSION,
                "routing_profile": self.profile,
                "routing_implementation_sha256": self.implementation_sha256,
                "routing_library_before_sha256": library_before_sha256,
                "routing_training_sha256": training_sha256,
            }
        )
        result["routing_execution_sha256"] = fingerprint(result)
        return result

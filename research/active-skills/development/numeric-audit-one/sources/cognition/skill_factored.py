"""Metered normalization and inverse-constraint expression synthesis.

This conventional domain-specific solver searches a disclosed family: one
registered affix/whitespace operation, decimal parsing and optional scaling per
field, optionally joined by one binary arithmetic operation. It receives only
visible examples, never a task recipe or hidden validation row. The production
search was selected after it outperformed generic enumeration in development.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal, InvalidOperation, localcontext

from cognition.skills import WorkLimit


def solve_factored(inputs, outputs, config, meter, retained=()):
    """Return a proposed AST using the same interpreter and finite work meter.

    Retained entries are previously buyer-verified complete programs. They are
    rechecked on current examples before the identical stateless fallback runs.
    Inverse arithmetic guides search but is not an extra program primitive.
    Every attempted program, executed node, signature and constraint is charged.
    """
    from cognition.skill_expressions import (
        VERSION,
        _evaluate,
        _formatted,
        _number,
        validate_program,
    )

    meter.values.setdefault("constraint_operations", 0)
    pool = {}
    found = None
    exhausted = False

    def rendered(node):
        return {"version": VERSION, "expr": {"op": "format", "arg": node}}

    def signature(values):
        meter.data(json.dumps(values, separators=(",", ":")))

    def candidate():
        if meter.values["candidates"] >= config["max_candidates"]:
            raise WorkLimit("Factored candidate allowance is exhausted")
        meter.charge("candidates")

    def attempt(program):
        candidate()
        try:
            program = validate_program(program, config)
        except ValueError:
            meter.values["errors"] += 1
            return None
        values = []
        for text in inputs:
            try:
                values.append(_evaluate(program["expr"], text, meter, config))
            except ValueError as error:
                if isinstance(error, WorkLimit):
                    raise
                return None
        signature(values)
        return values

    try:
        for program in retained:
            if attempt(program) == outputs:
                found = program
                break
        affixes = [None] + [
            dict(op)
            for op in config["operations"]
            if op["op"] in ("strip", "prefix", "suffix")
        ]
        scales = [None] + [
            dict(op) for op in config["operations"] if op["op"] == "scale"
        ]
        fields = [dict(op) for op in config["operations"] if op["op"] == "field"]
        for field in fields:
            if found is not None:
                break
            for affix in affixes:
                text_node = field
                if affix is not None:
                    text_node = {**affix, "arg": text_node}
                numeric_node = {"op": "decimal", "arg": text_node}
                for scale in scales:
                    node = (
                        copy.deepcopy(numeric_node)
                        if scale is None
                        else {**scale, "arg": copy.deepcopy(numeric_node)}
                    )
                    values = attempt(rendered(node))
                    if values is None:
                        continue
                    if values == outputs:
                        found = rendered(node)
                        break
                    key = tuple(values)
                    if key in pool:
                        meter.values["semantic_duplicates"] += 1
                    else:
                        pool[key] = node
                if found is not None:
                    break
        try:
            target = [_number(Decimal(value)) for value in outputs]
        except (ValueError, InvalidOperation):
            target = None
        allowed_binary = [
            op["op"]
            for op in config["operations"]
            if op["op"] in ("add", "subtract", "multiply")
        ]
        for left_values, left_node in list(pool.items()):
            if found is not None or target is None:
                break
            for name in allowed_binary:
                required, wildcard = [], False
                for left_text, target_value in zip(left_values, target, strict=True):
                    left = Decimal(left_text)
                    meter.charge("primitive_attempts")
                    meter.charge(
                        "byte_blocks",
                        sum(
                            max(1, (len(str(value).encode()) + 31) // 32)
                            for value in (left, target_value)
                        ),
                    )
                    meter.values["constraint_operations"] += 1
                    with localcontext() as arithmetic:
                        arithmetic.prec = 64
                        if name == "add":
                            value = target_value - left
                        elif name == "subtract":
                            value = left - target_value
                        elif left != 0:
                            value = target_value / left
                        elif target_value == 0:
                            value, wildcard = None, True
                        else:
                            required = None
                            break
                    required.append(None if value is None else _formatted(value))
                if required is None:
                    continue
                signature(required)
                if wildcard:
                    candidates = []
                    for values, node in pool.items():
                        candidate()
                        signature(values)
                        if all(
                            a is None or a == b
                            for a, b in zip(required, values, strict=True)
                        ):
                            candidates.append(node)
                else:
                    candidates = (
                        [pool[tuple(required)]] if tuple(required) in pool else []
                    )
                for right_node in candidates:
                    program = rendered(
                        {"op": name, "left": left_node, "right": right_node}
                    )
                    if attempt(program) == outputs:
                        found = program
                        break
                if found is not None:
                    break
    except WorkLimit:
        exhausted = True
    return {
        "program": copy.deepcopy(found),
        "exhausted": exhausted,
        "semantic_states": len(pool),
    }

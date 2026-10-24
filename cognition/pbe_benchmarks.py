"""Bounded, data-only SyGuS examples and specification-derived buyer checks.

This reader never invokes a solver or executes benchmark source. It implements
the small SMT string/integer/boolean subset used by the pinned Duet development
specifications. A source ``define-fun`` is an optional oracle, not training data:
its signature and every original constraint must agree before it may label
additional inputs. Those checks are explicitly specification-derived examples,
not additional original observations or real-world records.

Only the diagnostic input/output fields of a generated job belong to the
learner. Source metadata, score labels, source oracles, and unsupported reasons
are buyer-side material. Unsupported and unscorable records are returned rather
than silently removed from a course denominator.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

VERSION = "bounded-sygus-buyer-v1"
MAX_SOURCE_BYTES = 131_072
MAX_TOKENS = 32_768
MAX_DEPTH = 48
MAX_STEPS = 8_192
MAX_STRING = 4_096
MAX_INTEGER = 2**53 - 1
MAX_EXAMPLES = 2_048
MAX_FIELDS = 8
SORTS = ("String", "Int", "Bool")


class BenchmarkError(ValueError):
    """A malformed, unsupported, inconsistent, or over-limit specification."""


class _Literal(str):
    """An SMT string literal, kept distinct from an identifier."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _checked(value):
    if type(value) is str and len(value) <= MAX_STRING:
        return value
    if type(value) is bool:
        return value
    if type(value) is int and abs(value) <= MAX_INTEGER:
        return value
    raise BenchmarkError("Oracle value exceeds supported type or size bounds")


def _typed(value, sort):
    _checked(value)
    expected = {"String": str, "Int": int, "Bool": bool}.get(sort)
    if expected is None or type(value) is not expected:
        raise BenchmarkError("Value does not match declared SMT sort")
    return value


def _tokenize(source):
    if type(source) is not str or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise BenchmarkError("Specification source size bound exceeded")
    result, index = [], 0
    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if char == ";":
            end = source.find("\n", index)
            index = len(source) if end < 0 else end + 1
            continue
        if char in "()":
            result.append(char)
            index += 1
        elif char == '"':
            index += 1
            chars = []
            while index < len(source):
                if source[index] == '"':
                    if index + 1 < len(source) and source[index + 1] == '"':
                        chars.append('"')
                        index += 2
                        continue
                    index += 1
                    break
                chars.append(source[index])
                index += 1
                if len(chars) > MAX_STRING:
                    raise BenchmarkError("SMT string literal size bound exceeded")
            else:
                raise BenchmarkError("Unterminated SMT string literal")
            value = "".join(chars)
            if re.search(r"\\u(?:\{|[0-9a-fA-F]{4})", value):
                raise BenchmarkError("SMT Unicode escape notation is unsupported")
            result.append(_Literal(value))
        else:
            start = index
            while index < len(source) and not source[index].isspace():
                if source[index] in "();":
                    break
                index += 1
            if index == start:
                raise BenchmarkError("Unsupported SMT token")
            result.append(source[start:index])
        if len(result) > MAX_TOKENS:
            raise BenchmarkError("SMT token bound exceeded")
    return result


def parse_sexpressions(source):
    """Parse bounded SMT data; quoted symbols and executable commands are absent."""
    stream, index = _tokenize(source), 0

    def one(depth):
        nonlocal index
        if depth > MAX_DEPTH or index >= len(stream):
            raise BenchmarkError("SMT nesting or expression bound exceeded")
        node = stream[index]
        index += 1
        if type(node) is str and node == "(":
            items = []
            while index < len(stream):
                if type(stream[index]) is str and stream[index] == ")":
                    index += 1
                    return items
                items.append(one(depth + 1))
            raise BenchmarkError("Unclosed SMT expression")
        if type(node) is str and node == ")":
            raise BenchmarkError("Unmatched SMT closing parenthesis")
        return node

    forms = []
    while index < len(stream):
        forms.append(one(0))
    return forms


def evaluate_expression(expression, bindings=None):
    """Evaluate a finite SMT subset with strict types and bounded intermediates."""
    bindings = {} if bindings is None else dict(bindings)
    for value in bindings.values():
        _checked(value)
    steps = 0

    def run(node, env, depth=0):
        nonlocal steps
        steps += 1
        if steps > MAX_STEPS or depth > MAX_DEPTH:
            raise BenchmarkError("Oracle evaluation work bound exceeded")
        if isinstance(node, _Literal):
            return str(node)
        if type(node) is str:
            if node in env:
                return env[node]
            if node in ("true", "false"):
                return node == "true"
            if len(node) <= 17 and re.fullmatch("-?[0-9]+", node):
                return _checked(int(node))
            raise BenchmarkError("Unknown oracle symbol")
        if type(node) is not list or not node or type(node[0]) is not str:
            raise BenchmarkError("Malformed oracle expression")
        op, args = node[0], node[1:]

        def arity(low, high=None):
            if not low <= len(args) <= (low if high is None else high):
                raise BenchmarkError("Oracle operator arity differs")

        def values(sorts):
            arity(len(sorts))
            return [
                _typed(run(arg, env, depth + 1), sort)
                for arg, sort in zip(args, sorts, strict=True)
            ]

        if op == "ite":
            arity(3)
            condition = _typed(run(args[0], env, depth + 1), "Bool")
            return run(args[1] if condition else args[2], env, depth + 1)
        if op == "let":
            arity(2)
            if type(args[0]) is not list or not args[0]:
                raise BenchmarkError("Malformed oracle let bindings")
            scoped = {}
            for pair in args[0]:
                if (
                    type(pair) is not list
                    or len(pair) != 2
                    or type(pair[0]) is not str
                    or pair[0] in scoped
                ):
                    raise BenchmarkError("Malformed or duplicate oracle binding")
                scoped[pair[0]] = run(pair[1], env, depth + 1)
            return run(args[1], env | scoped, depth + 1)
        if op == "str.++":
            arity(2, MAX_FIELDS * 4)
            items = [_typed(run(a, env, depth + 1), "String") for a in args]
            if sum(map(len, items)) > MAX_STRING:
                raise BenchmarkError("Oracle string expansion bound exceeded")
            return "".join(items)
        if op == "str.len":
            return len(values(("String",))[0])
        if op == "str.substr":
            value, start, length = values(("String", "Int", "Int"))
            return "" if start < 0 or length < 0 else value[start : start + length]
        if op == "str.at":
            value, start = values(("String", "Int"))
            return value[start] if 0 <= start < len(value) else ""
        if op == "str.indexof":
            value, needle, start = values(("String", "String", "Int"))
            return value.find(needle, start) if 0 <= start <= len(value) else -1
        if op == "str.replace":
            value, old, new = values(("String", "String", "String"))
            # Bound before allocating, including the empty-needle prefix case.
            length = len(value) + (len(new) - len(old) if old in value else 0)
            if length > MAX_STRING:
                raise BenchmarkError("Oracle replacement size bound exceeded")
            return value.replace(old, new, 1)
        if op in ("str.contains", "str.prefixof", "str.suffixof"):
            left, right = values(("String", "String"))
            if op == "str.contains":
                return right in left
            return (
                right.startswith(left) if op == "str.prefixof" else right.endswith(left)
            )
        if op in ("str.to.int", "str.to_int"):
            value = values(("String",))[0]
            if not re.fullmatch("[0-9]+", value):
                return -1
            if len(value.lstrip("0")) > 16:
                raise BenchmarkError("Oracle integer conversion bound exceeded")
            return _checked(int(value))
        if op in ("int.to.str", "str.from_int"):
            value = values(("Int",))[0]
            return str(value) if value >= 0 else ""
        if op in ("+", "-", "*"):
            arity(1 if op == "-" else 2, 2)
            items = [_typed(run(a, env, depth + 1), "Int") for a in args]
            if op == "+":
                value = items[0] + items[1]
            elif op == "*":
                value = items[0] * items[1]
            else:
                value = -items[0] if len(items) == 1 else items[0] - items[1]
            return _checked(value)
        if op == "=":
            arity(2, 32)
            items = [run(a, env, depth + 1) for a in args]
            if len({type(v) for v in items}) != 1:
                raise BenchmarkError("Equality operands have different sorts")
            return all(v == items[0] for v in items[1:])
        if op in ("<", "<=", ">", ">="):
            left, right = values(("Int", "Int"))
            return {
                "<": left < right,
                "<=": left <= right,
                ">": left > right,
                ">=": left >= right,
            }[op]
        if op in ("and", "or"):
            arity(2, 32)
            items = [_typed(run(a, env, depth + 1), "Bool") for a in args]
            return all(items) if op == "and" else any(items)
        if op == "not":
            return not values(("Bool",))[0]
        raise BenchmarkError("Unsupported oracle operator")

    return _checked(run(expression, bindings))


def _signature(params):
    if type(params) is not list or not 1 <= len(params) <= MAX_FIELDS:
        raise BenchmarkError("Unsupported function field count")
    result = []
    for pair in params:
        if (
            type(pair) is not list
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
            or pair[1] not in SORTS
            or pair[0] in {p[0] for p in result}
        ):
            raise BenchmarkError("Invalid function parameter signature")
        result.append((pair[0], pair[1]))
    return tuple(result)


@dataclass(frozen=True)
class PBEProblem:
    """Buyer-side parsed source, including every original constraint in order."""

    name: str
    parameters: tuple
    output_sort: str
    examples: tuple
    source_sha256: str
    oracle_parameters: tuple | None = None
    oracle_expression: object | None = None
    oracle_issue: str | None = None

    def oracle(self, inputs):
        if self.oracle_expression is None or self.oracle_issue:
            raise BenchmarkError("No verified source oracle")
        if len(inputs) != len(self.parameters):
            raise BenchmarkError("Oracle input arity differs")
        checked = [
            _typed(value, sort)
            for value, (_, sort) in zip(inputs, self.parameters, strict=True)
        ]
        value = evaluate_expression(
            self.oracle_expression,
            {p[0]: v for p, v in zip(self.oracle_parameters, checked, strict=True)},
        )
        return _typed(value, self.output_sort)


def parse_problem(source):
    """Read one SyGuS target and equality constraints without executing commands."""
    if type(source) is bytes:
        try:
            source = source.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise BenchmarkError("Specification is not UTF-8") from error
    forms = parse_sexpressions(source)
    if any(type(f) is not list or not f or type(f[0]) is not str for f in forms):
        raise BenchmarkError("Top-level source forms must be command lists")
    allowed = {
        "set-logic",
        "synth-fun",
        "constraint",
        "check-synth",
        "define-fun",
        "set-option",
        "set-info",
    }
    if any(f[0] not in allowed for f in forms):
        raise BenchmarkError("Unsupported source command")
    targets = [f for f in forms if f[0] == "synth-fun"]
    if len(targets) != 1 or len(targets[0]) not in (4, 5):
        raise BenchmarkError("Specification must declare one synthesis target")
    target = targets[0]
    if (
        type(target[1]) is not str
        or type(target[3]) is not str
        or target[3] not in SORTS
    ):
        raise BenchmarkError("Unsupported target signature")
    name, params, output_sort = target[1], _signature(target[2]), target[3]
    rows, observed = [], {}
    for form in forms:
        if form[0] != "constraint":
            continue
        if (
            len(form) != 2
            or type(form[1]) is not list
            or len(form[1]) != 3
            or form[1][0] != "="
        ):
            raise BenchmarkError(
                "Only direct target equality constraints are supported"
            )
        left, right = form[1][1:]
        if not (type(left) is list and left and left[0] == name):
            left, right = right, left
        if not (
            type(left) is list
            and left
            and left[0] == name
            and len(left) == len(params) + 1
        ):
            raise BenchmarkError("Constraint target or input arity differs")
        inputs = tuple(
            _typed(evaluate_expression(value), sort)
            for value, (_, sort) in zip(left[1:], params, strict=True)
        )
        output = _typed(evaluate_expression(right), output_sort)
        key = _json(inputs)
        if key in observed and observed[key] != output:
            raise BenchmarkError("Original constraints assign conflicting labels")
        observed[key] = output
        rows.append((inputs, output))
        if len(rows) > MAX_EXAMPLES:
            raise BenchmarkError("Original example count bound exceeded")
    if not rows:
        raise BenchmarkError("Specification has no original examples")
    definitions = [f for f in forms if f[0] == "define-fun"]
    named = [f for f in definitions if len(f) > 1 and f[1] in (name, "f_1")]
    oracle_params, oracle_expr, issue = None, None, None
    if named:
        try:
            if len(named) != 1 or len(named[0]) != 5:
                raise BenchmarkError(
                    "Source oracle definition is ambiguous or malformed"
                )
            oracle_params = _signature(named[0][2])
            if (
                tuple(p[1] for p in oracle_params) != tuple(p[1] for p in params)
                or named[0][3] != output_sort
            ):
                raise BenchmarkError("Source oracle signature differs")
            oracle_expr = named[0][4]
            for inputs, expected in rows:
                value = evaluate_expression(
                    oracle_expr,
                    {p[0]: v for p, v in zip(oracle_params, inputs, strict=True)},
                )
                if type(value) is not type(expected) or value != expected:
                    raise BenchmarkError(
                        "Source oracle contradicts original constraints"
                    )
        except BenchmarkError as error:
            issue = str(error)
    return PBEProblem(
        name,
        params,
        output_sort,
        tuple(rows),
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
        oracle_params,
        oracle_expr,
        issue,
    )


def _mutate(value, shift):
    """A fixed alphabet bijection retains separators and changes literal content."""
    result = []
    for char in value:
        if "a" <= char <= "z":
            char = chr((ord(char) - 97 + shift) % 26 + 97)
        elif "A" <= char <= "Z":
            char = chr((ord(char) - 65 + shift) % 26 + 65)
        elif "0" <= char <= "9":
            char = chr((ord(char) - 48 + shift) % 10 + 48)
        result.append(char)
    return "".join(result)


def make_buyer_job(problem, *, identity, family, source, derived_score_rows=24):
    """Construct a diagnostic/score partition with an explicit source inventory.

    The first four distinct source inputs are diagnostic. Every other original
    constraint remains a scored row, including repeated non-diagnostic rows.
    Repeated diagnostic constraints are recorded in the source inventory and
    never masquerade as unseen checks. Verified source oracles label up to the
    requested count of deterministic content mutations, in fixed order, without
    consulting a candidate tool, model, loss, or policy outcome.
    """
    if type(derived_score_rows) is not int or not 0 <= derived_score_rows <= 128:
        raise BenchmarkError("Derived score row count bound exceeded")
    diagnostics, seen = [], set()
    for inputs, output in problem.examples:
        encoded = _json(inputs)
        if encoded not in seen and len(diagnostics) < 4:
            diagnostics.append((encoded, output))
            seen.add(encoded)
    scores = [(_json(i), o) for i, o in problem.examples if _json(i) not in seen]
    original_score_rows = len(scores)
    unsupported = None
    if problem.output_sort != "String" or any(
        p[1] != "String" for p in problem.parameters
    ):
        unsupported = (
            "Current finite text learner supports only String fields and String outputs"
        )
    generated, oracle_failures = [], 0
    existing = {_json(i) for i, _ in problem.examples}
    if (
        derived_score_rows
        and not unsupported
        and problem.oracle_expression is not None
        and not problem.oracle_issue
    ):
        for shift in range(1, 27):
            for inputs, _ in problem.examples:
                mutated = tuple(_mutate(value, shift) for value in inputs)
                encoded = _json(mutated)
                if encoded in existing:
                    continue
                existing.add(encoded)
                try:
                    output = problem.oracle(mutated)
                except BenchmarkError:
                    oracle_failures += 1
                    continue
                generated.append((encoded, output))
                if len(generated) >= derived_score_rows:
                    break
            if len(generated) >= derived_score_rows:
                break
    if not unsupported and not scores and not generated:
        unsupported = (
            "No disjoint original score example and no usable verified oracle extension"
        )
    inventory = [
        {
            "input": _json(i),
            "output": o,
            "role": "diagnostic" if _json(i) in seen else "score",
        }
        for i, o in problem.examples
    ]
    if unsupported:
        # Keep all source records, but do not pass typed-incompatible labels into
        # an otherwise string-only tool. The evaluator retains this failed item.
        diagnostics, scores, generated = [], [], []
    scores += generated
    return {
        "id": identity,
        "diagnostic_inputs": [r[0] for r in diagnostics],
        "diagnostic_outputs": [r[1] for r in diagnostics],
        "score_inputs": [r[0] for r in scores],
        "score_outputs": [r[1] for r in scores],
        "unsupported": unsupported,
        "family": family,
        "source": dict(source) | {"sha256": problem.source_sha256},
        "buyer_metadata": {
            "version": VERSION,
            "input_sorts": [p[1] for p in problem.parameters],
            "output_sort": problem.output_sort,
            "original_constraints": inventory,
            "original_constraint_count": len(problem.examples),
            "distinct_original_inputs": len({_json(i) for i, _ in problem.examples}),
            "original_scored_rows": original_score_rows,
            "original_diagnostic_constraint_rows": len(problem.examples)
            - original_score_rows,
            "verified_source_oracle": problem.oracle_expression is not None
            and not problem.oracle_issue,
            "oracle_issue": problem.oracle_issue,
            "derived_score_kind": "specification-derived buyer examples; not original real-world data",
            "derived_score_recipe": f"First {derived_score_rows} novel whole-row alphabet/digit shifts, shift 1..26 then source order; separators preserved",
            "derived_score_rows": len(generated),
            "derived_oracle_evaluation_failures": oracle_failures,
        },
    }


def make_source_job(raw, *, identity, family, source, derived_score_rows=24):
    """Retain malformed/unsupported source files as explicit failed course items.

    The caller supplies bytes from its separately pinned, hash-verified source
    inventory. This function deliberately performs no filesystem or network I/O.
    """
    if type(raw) not in (str, bytes):
        raise BenchmarkError("Specification must be UTF-8 text or bytes")
    if type(derived_score_rows) is not int or not 0 <= derived_score_rows <= 128:
        raise BenchmarkError("Derived score row count bound exceeded")
    payload = raw.encode("utf-8") if type(raw) is str else raw
    try:
        return make_buyer_job(
            parse_problem(raw),
            identity=identity,
            family=family,
            source=source,
            derived_score_rows=derived_score_rows,
        )
    except BenchmarkError as error:
        return {
            "id": identity,
            "diagnostic_inputs": [],
            "diagnostic_outputs": [],
            "score_inputs": [],
            "score_outputs": [],
            "unsupported": str(error),
            "family": family,
            "source": dict(source) | {"sha256": hashlib.sha256(payload).hexdigest()},
            "buyer_metadata": {
                "version": VERSION,
                "original_constraint_count": None,
                "constraint_inventory": "Unknown: source cannot be safely interpreted",
                "derived_score_rows": 0,
            },
        }

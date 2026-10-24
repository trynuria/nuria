"""Execute a source-bound program-acquisition experiment with selected outcomes.

Real Bike Sharing numeric observations supply targets. String wrappers, unit
changes and their compositions are GENERATED tasks, not original dataset labels.
The typed-program solver sees example input/output pairs, never task generators,
held-out rows, expected programs, future results or unselected execution costs.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import random
import time
import zipfile
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from statistics import mean, stdev

from cognition.skill_expressions import DEFAULT_EXPRESSION_CONFIG as DEFAULT_CONFIG
from cognition.skills import SkillLibrary

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "research/intervention-memory/data/bike-sharing-original.zip"
ARCHIVE_SHA = "b70182d0d0508e9abbb79306ce5c0cec34869000f8220175ac83d11dbe845401"
CSV_SHA = "e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f"
SCHEMA = "nuria.skill-expression-acquisition.v1"
FIELDS = ("casual", "registered", "temp", "hum")
ENCODINGS = ("raw", "prefix100", "suffix1000", "percent100", "n1000")
TRAIN_RECIPES = tuple(
    {
        "operation": "field",
        "indices": [index],
        "encodings": [encoding if slot == index else "raw" for slot in range(4)],
        "claim": "single-source-field normalization",
    }
    for encoding in ENCODINGS
    for index in range(4)
) + (
    {
        "operation": "add",
        "indices": [0, 1],
        "encodings": ["raw"] * 4,
        "claim": "observed-cnt identity",
    },
    {
        "operation": "subtract",
        "indices": [1, 0],
        "encodings": ["raw"] * 4,
        "claim": "source-derived arithmetic",
    },
    {
        "operation": "add",
        "indices": [2, 3],
        "encodings": ["raw"] * 4,
        "claim": "source-derived arithmetic",
    },
    {
        "operation": "multiply",
        "indices": [2, 3],
        "encodings": ["raw"] * 4,
        "claim": "source-derived arithmetic",
    },
)
TRANSFER_RECIPES = tuple(
    {
        "operation": operation,
        "indices": indices,
        "encodings": [
            enc1 if slot == indices[0] else enc2 if slot == indices[1] else "raw"
            for slot in range(4)
        ],
        "claim": "observed-cnt identity"
        if operation == "add" and indices == [0, 1]
        else "source-derived arithmetic",
    }
    for operation, indices, enc1, enc2 in (
        ("add", [0, 1], "prefix100", "suffix1000"),
        ("add", [0, 1], "suffix1000", "percent100"),
        ("add", [0, 1], "percent100", "prefix100"),
        ("add", [0, 1], "n1000", "suffix1000"),
        ("add", [0, 1], "prefix100", "n1000"),
        ("add", [0, 1], "percent100", "n1000"),
        ("add", [2, 3], "prefix100", "suffix1000"),
        ("add", [2, 3], "suffix1000", "percent100"),
        ("add", [2, 3], "n1000", "prefix100"),
        ("multiply", [2, 3], "prefix100", "suffix1000"),
        ("multiply", [2, 3], "percent100", "n1000"),
        ("multiply", [2, 3], "n1000", "prefix100"),
        ("subtract", [1, 0], "prefix100", "suffix1000"),
        ("subtract", [1, 0], "suffix1000", "percent100"),
        ("subtract", [1, 0], "percent100", "n1000"),
        ("multiply", [0, 2], "suffix1000", "prefix100"),
    )
)
BRANCHES = (
    "fresh",
    "factored_fresh",
    "retrieval",
    "frequency",
    "contextual",
    "frozen",
    "no_acquisition",
    "shuffled_context",
)
MODES = {
    "fresh": "fresh",
    "factored_fresh": "factored",
    "retrieval": "retrieval",
    "frequency": "macro",
    "contextual": "contextual",
    "frozen": "contextual",
    "no_acquisition": "contextual",
    "shuffled_context": "contextual",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def numeric(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Nonfinite number")
    if number == 0:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def load_rows():
    raw = ARCHIVE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA:
        raise ValueError("Dataset archive identity changed")
    csv_raw = zipfile.ZipFile(io.BytesIO(raw)).read("hour.csv")
    if hashlib.sha256(csv_raw).hexdigest() != CSV_SHA:
        raise ValueError("Dataset CSV identity changed")
    rows = list(csv.DictReader(io.StringIO(csv_raw.decode("utf-8"))))
    if len(rows) != 17379:
        raise ValueError("Unexpected source row count")
    return rows


def encode_field(text, encoding):
    if encoding == "raw":
        return text
    if encoding == "prefix100":
        return "value=" + numeric(Decimal(text) * 100)
    if encoding == "suffix1000":
        return numeric(Decimal(text) * 1000) + " units"
    if encoding == "percent100":
        return numeric(Decimal(text) * 100) + "%"
    if encoding == "n1000":
        return "n=" + numeric(Decimal(text) * 1000)
    raise ValueError("Unknown generated field encoding")


def target_for(row, recipe):
    values = [Decimal(row[field]) for field in FIELDS]
    indices = recipe["indices"]
    operation = recipe["operation"]
    if operation == "field":
        result = values[indices[0]]
    elif operation == "add":
        result = values[indices[0]] + values[indices[1]]
    elif operation == "subtract":
        result = values[indices[0]] - values[indices[1]]
    elif operation == "multiply":
        result = values[indices[0]] * values[indices[1]]
    else:
        raise ValueError("Unknown generated target operation")
    if recipe["claim"] == "observed-cnt identity" and result != Decimal(row["cnt"]):
        raise ValueError("Real source cnt does not equal casual + registered")
    return numeric(result)


def reference_program(recipe):
    """Evaluator-only construction: never supplied to search or its library."""

    def leaf(index):
        value = {"op": "field", "index": index}
        encoding = recipe["encodings"][index]
        if encoding in ("prefix100", "n1000"):
            value = {
                "op": "prefix",
                "value": "value=" if encoding == "prefix100" else "n=",
                "arg": value,
            }
        elif encoding in ("suffix1000", "percent100"):
            value = {
                "op": "suffix",
                "value": " units" if encoding == "suffix1000" else "%",
                "arg": value,
            }
        value = {"op": "decimal", "arg": value}
        if encoding != "raw":
            value = {
                "op": "scale",
                "numerator": 1,
                "denominator": 100 if encoding in ("prefix100", "percent100") else 1000,
                "arg": value,
            }
        return value

    indices = recipe["indices"]
    expression = (
        leaf(indices[0])
        if recipe["operation"] == "field"
        else {
            "op": recipe["operation"],
            "left": leaf(indices[0]),
            "right": leaf(indices[1]),
        }
    )
    return {
        "version": "safe-expression-v1",
        "expr": {"op": "format", "arg": expression},
    }


def features(inputs):
    decoded = [json.loads(value) for value in inputs]
    values = [
        min(
            1.0,
            sum(len(value.encode()) for value in inputs) / max(1, len(inputs)) / 256,
        )
    ]
    for slot in range(4):
        strings = [row[slot] for row in decoded]
        values.extend(
            [
                sum(value.startswith("value=") for value in strings) / len(strings),
                sum(value.startswith("n=") for value in strings) / len(strings),
                sum(value.endswith(" units") for value in strings) / len(strings),
                sum(value.endswith("%") for value in strings) / len(strings),
            ]
        )
    return values


def make_tasks(rows, seed, phase, count):
    year_rows = [
        row for row in rows if row["yr"] == ("0" if phase == "acquisition" else "1")
    ]
    if seed in (9101, 9102, 9103):
        pool = [
            row
            for index, row in enumerate(year_rows[:1728])
            if index % 3 == seed - 9101
        ]
    elif 9201 <= seed <= 9212:
        pool = [
            row
            for index, row in enumerate(year_rows[1728:])
            if index % 12 == seed - 9201
        ]
    else:
        raise ValueError("Seed is outside the registered disjoint row partitions")
    recipes = TRAIN_RECIPES if phase == "acquisition" else TRANSFER_RECIPES
    rng = random.Random(f"{SCHEMA}:{phase}:{seed}")
    tasks = []
    for step in range(count):
        recipe = recipes[step % len(recipes)]
        selected = rng.sample(pool, 24)
        unique = {}
        for row in selected:
            unique.setdefault(target_for(row, recipe), row)
        if len(unique) < 4:
            raise ValueError("Registered task lacks four distinct visible targets")
        first = list(unique.values())[:4]
        selected = first + [row for row in selected if row not in first]
        ids = {row["instant"] for row in selected}
        pool = [row for row in pool if row["instant"] not in ids]
        inputs = [
            json.dumps(
                [
                    encode_field(numeric(row[field]), encoding)
                    for field, encoding in zip(FIELDS, recipe["encodings"], strict=True)
                ],
                separators=(",", ":"),
            )
            for row in selected
        ]
        task = {
            "phase": phase,
            "step": step,
            "recipe": copy.deepcopy(recipe),
            "row_ids": [int(row["instant"]) for row in selected],
            "inputs": inputs,
            "outputs": [target_for(row, recipe) for row in selected],
        }
        task["id"] = digest(task)
        tasks.append(task)
    return tasks


class ToolFailure(ValueError):
    def __init__(self, message, units):
        super().__init__(message)
        self.units = units


def execute(program, text, shared_meter=None):
    """Independent exact AST execution; no learner execution helper imports."""
    if (
        type(program) is not dict
        or set(program) != {"version", "expr"}
        or program["version"] != "safe-expression-v1"
    ):
        raise ToolFailure("Invalid expression envelope", 0)
    units = 0
    count = 0
    allowed = {canonical(op) for op in DEFAULT_CONFIG["operations"]}

    def visit(node, depth):
        nonlocal units, count
        count += 1
        if count > 15 or depth > 8 or type(node) is not dict:
            raise ToolFailure("AST bounds exceeded", units)
        name = node.get("op")
        if name == "field":
            operation = dict(node)
            if canonical(operation) not in allowed:
                raise ToolFailure("Unregistered field", units)
            operands = [text]
        elif name in ("add", "subtract", "multiply"):
            if (
                set(node) != {"op", "left", "right"}
                or canonical({"op": name}) not in allowed
            ):
                raise ToolFailure("Malformed binary node", units)
            operands = [visit(node["left"], depth + 1), visit(node["right"], depth + 1)]
        else:
            if "arg" not in node:
                raise ToolFailure("Unary argument missing", units)
            operation = {key: value for key, value in node.items() if key != "arg"}
            if canonical(operation) not in allowed:
                raise ToolFailure("Unregistered unary node", units)
            operands = [visit(node["arg"], depth + 1)]
        charge = 1 + sum(
            max(1, (len(str(value).encode()) + 31) // 32) for value in operands
        )
        if shared_meter is not None:
            shared_meter.charge("primitive_attempts")
            shared_meter.charge("byte_blocks", charge - 1)
        units += charge
        try:
            if name == "field":
                values = json.loads(text)
                if (
                    type(values) is not list
                    or len(values) != 4
                    or any(type(v) is not str for v in values)
                ):
                    raise ValueError("Input must be four text fields")
                result = values[node["index"]]
            elif name in ("add", "subtract", "multiply"):
                if any(type(v) is not Decimal for v in operands):
                    raise ValueError("Binary operands must be numbers")
                a, b = operands
                with localcontext() as context:
                    context.prec = 64
                    result = (
                        a + b
                        if name == "add"
                        else a - b
                        if name == "subtract"
                        else a * b
                    )
            else:
                value = operands[0]
                expected_type = Decimal if name in ("scale", "format") else str
                if type(value) is not expected_type:
                    raise ValueError("Primitive input type mismatch")
                if name == "strip":
                    result = value.strip()
                elif name == "prefix":
                    if not value.startswith(node["value"]):
                        raise ValueError("Prefix absent")
                    result = value[len(node["value"]) :]
                elif name == "suffix":
                    if not value.endswith(node["value"]):
                        raise ValueError("Suffix absent")
                    result = value[: -len(node["value"])]
                elif name == "decimal":
                    result = Decimal(value)
                elif name == "scale":
                    with localcontext() as context:
                        context.prec = 64
                        result = value * node["numerator"] / node["denominator"]
                elif name == "format":
                    result = numeric(value)
                else:
                    raise ValueError("Unsupported primitive")
            if type(result) is Decimal:
                if (
                    not result.is_finite()
                    or abs(result) > Decimal("1e30")
                    or abs(result.as_tuple().exponent) > 64
                ):
                    raise ValueError("Numeric bounds exceeded")
            elif type(result) is not str or len(result.encode()) > 512:
                raise ValueError("Text bounds exceeded")
            return result
        except (ValueError, TypeError, KeyError, IndexError, InvalidOperation) as error:
            raise ToolFailure("Expression primitive failed", units) from error

    result = visit(program["expr"], 1)
    if type(result) is not str:
        raise ToolFailure("Expression final type is not text", units)
    return result, units


def check_program(program, inputs, outputs):
    actual, charged, first = [], 0, None
    for index, (text, expected) in enumerate(zip(inputs, outputs, strict=True)):
        try:
            value, work = execute(program, text)
            charged += work
            actual.append(value)
        except ToolFailure as error:
            charged += error.units
            actual.append(None)
        if actual[-1] != expected and first is None:
            first = index
    return {
        "passed": first is None,
        "correct": sum(a == b for a, b in zip(actual, outputs, strict=True)),
        "rows": len(outputs),
        "outputs": actual,
        "tool_units": charged,
        "first_counterexample_index": first,
        "actual_sha256": digest(actual),
    }


class SearchExhausted(ValueError):
    pass


class SearchMeter:
    def __init__(self, budget):
        self.budget = budget
        self.values = {
            key: 0
            for key in (
                "total",
                "primitive_attempts",
                "candidates",
                "byte_blocks",
                "errors",
                "semantic_duplicates",
                "constraint_operations",
            )
        }

    def charge(self, key, n=1):
        if self.values["total"] + n > self.budget:
            raise SearchExhausted("Factored search allowance exhausted")
        self.values[key] += n
        self.values["total"] += n

    def signature(self, values):
        self.charge(
            "byte_blocks",
            max(
                1, (len(json.dumps(values, separators=(",", ":")).encode()) + 31) // 32
            ),
        )


def factored_solve(library, inputs, outputs, budget, features):
    """Strong stateless baseline: unary normalization + inverse binary joins.

    This baseline knows the restricted one-binary-expression hypothesis family,
    not any task recipe, source field, target operator or withheld input. Every
    tested normalization, signature, constraint arithmetic and final candidate
    execution is charged. It uses the same legal output AST registry.
    """
    meter = SearchMeter(budget)
    pool = {}
    found = None
    exhausted = False

    def rendered(node):
        return {"version": "safe-expression-v1", "expr": {"op": "format", "arg": node}}

    def attempt(program):
        meter.charge("candidates")
        values = []
        for text in inputs:
            try:
                value, _ = execute(program, text, shared_meter=meter)
            except ToolFailure:
                meter.values["errors"] += 1
                return None
            values.append(value)
        meter.signature(values)
        return values

    try:
        affixes = [None] + [
            dict(op)
            for op in DEFAULT_CONFIG["operations"]
            if op["op"] in ("strip", "prefix", "suffix")
        ]
        scales = [None] + [
            dict(op) for op in DEFAULT_CONFIG["operations"] if op["op"] == "scale"
        ]
        for index in range(4):
            for affix in affixes:
                text_node = {"op": "field", "index": index}
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
            if found is not None:
                break
        target = [Decimal(value) for value in outputs]
        for left_values, left_node in list(pool.items()):
            if found is not None:
                break
            for name in ("add", "subtract", "multiply"):
                required = []
                wildcard = False
                for left_text, target_value in zip(left_values, target, strict=True):
                    left = Decimal(left_text)
                    meter.charge("primitive_attempts")
                    meter.charge(
                        "byte_blocks",
                        sum(
                            max(1, (len(str(v).encode()) + 31) // 32)
                            for v in (left, target_value)
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
                            value = None
                            wildcard = True
                        else:
                            required = None
                            break
                    required.append(None if value is None else numeric(value))
                if required is None:
                    continue
                meter.signature(required)
                candidates = (
                    [
                        node
                        for values, node in pool.items()
                        if all(
                            a is None or a == b
                            for a, b in zip(required, values, strict=True)
                        )
                    ]
                    if wildcard
                    else [pool[tuple(required)]]
                    if tuple(required) in pool
                    else []
                )
                for right_node in candidates:
                    program = rendered(
                        {"op": name, "left": left_node, "right": right_node}
                    )
                    values = attempt(program)
                    if values == outputs:
                        found = program
                        break
                if found is not None:
                    break
    except SearchExhausted:
        exhausted = True
    proposal_id = (
        digest(
            {
                "algorithm": "factored-fresh-v1",
                "source_id": library.source_id,
                "inputs": inputs,
                "outputs": outputs,
                "program": found,
            }
        )
        if found
        else None
    )
    return {
        "proposal_id": proposal_id,
        "program": found,
        "program_sha256": digest(found) if found else None,
        "solved_training": found is not None,
        "mode": "factored",
        "exhausted": exhausted,
        "work": meter.values,
        "semantic_states": len(pool),
        "source_id": library.source_id,
        "config_sha256": digest(library.config),
    }


def implementation_hashes():
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            Path(__file__),
            ROOT / "cognition/skills.py",
            ROOT / "cognition/skill_expressions.py",
            ROOT / "scripts/verify_skill_expression_results.py",
        )
    }


def protocol():
    return {
        "schema": SCHEMA,
        "status": "development-not-preregistered",
        "selected_method": "frequency",
        "summary_version": 2,
        "dataset": {
            "archive_sha256": ARCHIVE_SHA,
            "csv_sha256": CSV_SHA,
            "rows": 17379,
            "source": "UCI Bike Sharing DOI:10.24432/C5W894",
            "license": "CC BY4.0",
        },
        "data_scope": "Real observed casual/registered/temp/hum inputs; cnt=casual+registered is checked against the actualCSVcnt field. Other arithmetic targets and all field/unit encodings are generated and separately labeled.",
        "development_seeds": [9101, 9102, 9103],
        "reserved_seeds": list(range(9201, 9213)),
        "acquisition_tasks": 24,
        "transfer_tasks": 16,
        "visible_examples": 4,
        "cegis_validation_rows": 8,
        "scored_rows": 12,
        "max_cegis_rounds": 2,
        "solve_budget": 100000,
        "config": copy.deepcopy(DEFAULT_CONFIG),
        "acquisition_recipes": copy.deepcopy(TRAIN_RECIPES),
        "unseen_transfer_recipes": copy.deepcopy(TRANSFER_RECIPES),
        "split": "Acquisition uses2011, transfer2012. First1728rows/year are split into3 disjointdevelopmentbuckets; remainingrows split modulo12 into disjointreservedseedbuckets. Rows never repeat within a seed. Transfer wrapper structures are unseen. Fourvisibleexamples have distincttargetvalues for identification.",
        "branches": list(BRANCHES),
        "modes": MODES,
        "source_hashes": implementation_hashes(),
        "factored_baseline": "Same example information and AST primitives; unary numeric normalization per field followed by inverse constraints for one add/subtract/multiply. It uses no library. All controller arithmetic, signatures and final AST execution are charged. This is a restricted domain-specific strong baseline, not an oracle.",
        "primary": "Fraction of transfer tasks with all 12 independent scored outputs exactly correct within shared per-task solver budget; all acquisition and verification work reported.",
        "gates": {
            "minimum_transfer_success_gain": 0.05,
            "confidence": "paired seed-level 95% t interval, df=11",
            "practical": "The retention method selected using development data before freeze beats fresh stateless search by >=5 percentage points on transfer with paired lower95%>0; total charged work ratio<=1.1. CPU/per-correct is reported separately; an efficiency claim additionally requires CPU ratio<=1.1 without success regression.",
            "reliability": "No malformed admitted program or missing task/attempt may pass verification.",
            "learning": "The selected retention method beats no-acquisition on frozen unseen-AST transfer; early/late and halfway-frozen acquisition are separate mechanism diagnostics.",
            "neural": "No neural features or neural superiority claim in this study.",
            "runtime": "Before final scoring, every declared task must be executable by an evaluator-only reference AST under the same registry and node bound; reference ASTs never reach search.",
        },
        "selection_limit": "Production retention method fixed from3 development seeds before reserved execution. Retrieval/frequency/contextual results all remain visible, and contextual-vs-frequency is a separate specificity test. No reserved-outcome retuning.",
        "retention": "Every acquisition and reserved task, failed attempt, CEGIS counterexample, program, output and cost is retained. No failed seed is replaced.",
    }


def export_manifest(destination, seed=9101):
    rows = load_rows()
    entries = make_tasks(rows, seed, "acquisition", 24) + make_tasks(
        rows, seed, "transfer", 16
    )
    manifest = {
        "schema": SCHEMA + ".tasks",
        "seed": seed,
        "source": {
            "archive_sha256": ARCHIVE_SHA,
            "csv_sha256": CSV_SHA,
            "doi": "10.24432/C5W894",
            "description": "Finite development expression corpus. Real multicolumn observations and checked count identity; other arithmetic targets and encodings generated. First4 rows visible, next8 buyer validation, final12 scored. Recipe/source-row IDs are evaluator metadata.",
        },
        "config": copy.deepcopy(DEFAULT_CONFIG),
        "tasks": entries,
    }
    destination.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def run_task(library, task, branch, config):
    start, cpu = time.monotonic(), time.process_time()
    visible_inputs = list(task["inputs"][:4])
    visible_outputs = list(task["outputs"][:4])
    transfer = task["phase"] == "transfer"
    learn = (
        not transfer
        and branch not in ("fresh", "factored_fresh", "no_acquisition")
        and not (
            branch == "frozen" and task["step"] >= config["acquisition_tasks"] // 2
        )
    )
    observed_features = features(visible_inputs)
    if branch == "shuffled_context":
        random.Random(f"context-control:{task['phase']}:{task['step']}").shuffle(
            observed_features
        )
    attempts = []
    remaining = config["solve_budget"]
    final_program = None
    for round_index in range(config["max_cegis_rounds"]):
        if remaining < 1:
            break
        result = (
            factored_solve(
                library, visible_inputs, visible_outputs, remaining, observed_features
            )
            if branch == "factored_fresh"
            else library.solve(
                visible_inputs,
                visible_outputs,
                mode=MODES[branch],
                budget=remaining,
                features=observed_features,
            )
        )
        remaining -= result["work"]["total"]
        attempt = {
            "round": round_index,
            "visible_inputs": list(visible_inputs),
            "visible_outputs": list(visible_outputs),
            "result": result,
            "admission_allowed": learn,
            "validation": None,
            "receipt": None,
        }
        attempts.append(attempt)
        final_program = result["program"]
        if final_program is None:
            break
        checked = check_program(
            final_program, task["inputs"][4:12], task["outputs"][4:12]
        )
        attempt["validation"] = checked
        receipt = {
            "source_id": library.source_id,
            "proposal_id": result["proposal_id"],
            "program_sha256": result["program_sha256"],
            "evaluation_id": digest(
                {"task": task["id"], "round": round_index, "phase": task["phase"]}
            ),
            "accepted": checked["passed"],
            "correct_rows": checked["correct"],
            "total_rows": checked["rows"],
            "evidence_sha256": digest(
                {
                    "inputs": task["inputs"][4:12],
                    "outputs": task["outputs"][4:12],
                    "observed": checked["outputs"],
                }
            ),
            "work": checked["tool_units"],
        }
        if branch not in ("fresh", "factored_fresh"):
            library.admit_verified(result["proposal_id"], receipt, retain=learn)
        attempt["receipt"] = receipt
        if checked["passed"] or transfer:
            break
        index = checked["first_counterexample_index"]
        if index is None:
            break
        # CEGIS revelation is AFTER committed execution, acquisition only.
        visible_inputs.append(task["inputs"][4 + index])
        visible_outputs.append(task["outputs"][4 + index])
    scored = (
        check_program(final_program, task["inputs"][12:], task["outputs"][12:])
        if final_program is not None
        else {
            "passed": False,
            "correct": 0,
            "rows": 12,
            "outputs": [None] * 12,
            "tool_units": 0,
            "first_counterexample_index": 0,
            "actual_sha256": digest([None] * 12),
        }
    )
    solver_work = sum(attempt["result"]["work"]["total"] for attempt in attempts)
    buyer_work = sum(
        (attempt["validation"] or {}).get("tool_units", 0) for attempt in attempts
    )
    state = library.dump()
    state_hash = digest(state)
    return {
        "task": task,
        "branch": branch,
        "attempts": attempts,
        "final_program": final_program,
        "score": scored,
        "solver_work": solver_work,
        "buyer_work": buyer_work,
        "scoring_work": scored["tool_units"],
        "total_work": solver_work + buyer_work + scored["tool_units"],
        "cpu_seconds": time.process_time() - cpu,
        "wall_seconds": time.monotonic() - start,
        "library_programs": len(state["programs"]),
        "library_macros": len(state["macros"]),
        "library_state_sha256": state_hash,
    }


def interval(values):
    n = len(values)
    average = mean(values)
    if n < 2:
        return {
            "mean": average,
            "lower95": None,
            "upper95": None,
            "independent_streams": n,
        }
    # Reserved n=12 has df11; development n=2 is descriptive with df1.
    critical = {2: 12.706204736432095, 3: 4.302652729911275, 12: 2.200985160091639}[n]
    half = critical * stdev(values) / n**0.5
    return {
        "mean": average,
        "lower95": average - half,
        "upper95": average + half,
        "independent_streams": n,
        "method": "paired independent-stream Student t interval",
    }


def summarize(trials, selected_method, summary_version=2):
    per_branch = {}
    for branch in BRANCHES:
        selected = [trial for trial in trials if trial["branch"] == branch]
        per_branch[branch] = {
            "transfer_success": mean(t["transfer_success"] for t in selected),
            "acquisition_success": mean(t["acquisition_success"] for t in selected),
            "early_acquisition_success": mean(
                t["early_acquisition_success"] for t in selected
            ),
            "late_acquisition_success": mean(
                t["late_acquisition_success"] for t in selected
            ),
            "mean_total_work": mean(t["total_work"] for t in selected),
            "mean_cpu_seconds": mean(t["cpu_seconds"] for t in selected),
        }
    if summary_version >= 2:
        for branch in BRANCHES:
            correct = per_branch[branch]["transfer_success"] * 16
            per_branch[branch]["total_cpu_per_correct_transfer_task"] = (
                per_branch[branch]["mean_cpu_seconds"] / correct if correct else None
            )
            per_branch[branch]["total_work_per_correct_transfer_task"] = (
                per_branch[branch]["mean_total_work"] / correct if correct else None
            )

    def pairs(left_branch, comparator):
        left = {t["seed"]: t for t in trials if t["branch"] == left_branch}
        right = {t["seed"]: t for t in trials if t["branch"] == comparator}
        return interval(
            [
                left[seed]["transfer_success"] - right[seed]["transfer_success"]
                for seed in sorted(left)
            ]
        )

    simple = max(
        ("fresh", "factored_fresh"),
        key=lambda method: (
            per_branch[method]["transfer_success"],
            -per_branch[method]["mean_total_work"],
        ),
    )
    competitive = pairs(selected_method, simple)
    learning = pairs(selected_method, "no_acquisition")
    specificity = pairs("contextual", "frequency")
    work_ratio = per_branch[selected_method]["mean_total_work"] / max(
        1, per_branch[simple]["mean_total_work"]
    )
    cpu_ratio = per_branch[selected_method]["mean_cpu_seconds"] / max(
        1e-12, per_branch[simple]["mean_cpu_seconds"]
    )
    return {
        "branches": per_branch,
        "selected_retention_method": selected_method,
        "strongest_stateless_method": simple,
        "selected_minus_strongest_stateless": competitive,
        "selected_minus_no_acquisition": learning,
        "contextual_minus_frequency": specificity,
        "selected_total_work_ratio_vs_stateless": work_ratio,
        "selected_cpu_ratio_vs_stateless": cpu_ratio,
        "gates": {
            "practical_benefit": competitive["mean"] >= 0.05
            and competitive["lower95"] is not None
            and competitive["lower95"] > 0
            and work_ratio <= 1.1,
            "acquisition_transfer": learning["mean"] >= 0.05
            and learning["lower95"] is not None
            and learning["lower95"] > 0,
            "cpu_nonregression": cpu_ratio <= 1.1 and competitive["mean"] >= 0,
            "contextual_specificity": specificity["mean"] >= 0.05
            and specificity["lower95"] is not None
            and specificity["lower95"] > 0,
            "neural_specificity": "not_tested",
        },
        "scope": "Executable arithmetic program acquisition from real multicolumn observations with generated encodings and heldout AST structures. No neural superiority, external hiring or broad intelligence claim.",
    }


def evaluate(config, seeds, destination):
    import gzip

    rows = load_rows()
    destination.mkdir(parents=True, exist_ok=False)
    traces = gzip.open(
        destination / "traces.jsonl.gz", "wt", encoding="utf-8", newline="\n"
    )
    trials, tasks_hash = [], hashlib.sha256()
    try:
        for seed in seeds:
            tasks = make_tasks(
                rows, seed, "acquisition", config["acquisition_tasks"]
            ) + make_tasks(rows, seed, "transfer", config["transfer_tasks"])
            for task in tasks:
                tasks_hash.update(canonical(task).encode() + b"\n")
            for branch in BRANCHES:
                library = SkillLibrary(
                    f"skills:{seed}:{branch}", config=config["config"]
                )
                records = []
                for task in tasks:
                    if branch in ("fresh", "factored_fresh"):
                        library = SkillLibrary(
                            f"skills:{seed}:{branch}", config=config["config"]
                        )
                    record = run_task(library, task, branch, config)
                    record["seed"] = seed
                    traces.write(canonical(record) + "\n")
                    records.append(record)
                acq = [r for r in records if r["task"]["phase"] == "acquisition"]
                transfer = [r for r in records if r["task"]["phase"] == "transfer"]
                trials.append(
                    {
                        "seed": seed,
                        "branch": branch,
                        "acquisition_tasks": len(acq),
                        "transfer_tasks": len(transfer),
                        "acquisition_success": mean(r["score"]["passed"] for r in acq),
                        "early_acquisition_success": mean(
                            r["score"]["passed"] for r in acq[: len(acq) // 2]
                        ),
                        "late_acquisition_success": mean(
                            r["score"]["passed"] for r in acq[len(acq) // 2 :]
                        ),
                        "transfer_success": mean(
                            r["score"]["passed"] for r in transfer
                        ),
                        "total_work": sum(r["total_work"] for r in records),
                        "cpu_seconds": sum(r["cpu_seconds"] for r in records),
                        "final_library": library.dump(),
                    }
                )
    finally:
        traces.close()
    snapshots = destination / "sources"
    for filename in implementation_hashes():
        path = snapshots / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / filename).read_bytes())
    result = {
        "schema": SCHEMA + ".results",
        "protocol_sha256": digest(config),
        "seeds": seeds,
        "tasks_sha256": tasks_hash.hexdigest(),
        "trial_count": len(trials),
        "trace_sha256": hashlib.sha256(
            (destination / "traces.jsonl.gz").read_bytes()
        ).hexdigest(),
        "trials": trials,
        "summary": summarize(
            trials, config["selected_method"], config.get("summary_version", 1)
        ),
        "source_hashes": implementation_hashes(),
    }
    (destination / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    (destination / "protocol.json").write_text(json.dumps(config, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("development", "reserved", "freeze", "manifest"),
        required=True,
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--expected-protocol-sha256")
    parser.add_argument(
        "--selected-method",
        choices=("retrieval", "frequency", "contextual"),
        default="frequency",
    )
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("Preserve prior output; use a new destination")
    if args.phase == "manifest":
        export_manifest(args.out)
        print(json.dumps({"manifest": str(args.out)}))
        return
    if args.phase == "freeze":
        frozen = protocol()
        frozen["status"] = "frozen-before-reserved-execution"
        frozen["selected_method"] = args.selected_method
        args.out.write_text(json.dumps(frozen, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "protocol": str(args.out),
                    "sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
                }
            )
        )
        return
    if args.phase == "reserved":
        if args.protocol is None or args.expected_protocol_sha256 is None:
            raise ValueError(
                "Reserved execution requires an exact precommitted protocol hash"
            )
        raw = args.protocol.read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.expected_protocol_sha256:
            raise ValueError("Reserved protocol file identity differs")
        config = json.loads(raw)
        if config["status"] != "frozen-before-reserved-execution":
            raise ValueError("Reserved execution requires a frozen protocol")
        if config["source_hashes"] != implementation_hashes():
            raise ValueError("Evaluated source changed after protocol freeze")
        seeds = config["reserved_seeds"]
    else:
        config = protocol()
        config["selected_method"] = args.selected_method
        seeds = config["development_seeds"]
    value = evaluate(config, seeds, args.out)
    print(
        json.dumps(
            {"result": str(args.out / "result.json"), "summary": value["summary"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

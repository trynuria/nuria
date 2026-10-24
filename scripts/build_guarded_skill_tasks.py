"""Build finite synthetic record-repair streams for guarded-routing studies.

These are authored stress tasks, not customer requests or an independent
external benchmark. Each task has six possible diagnostic inputs and twenty-four
untouched scoring inputs. Some diagnostic prefixes deliberately make different
field projections indistinguishable. Later rows separate those projections.

Targets come from ordinary Python string operations in ``target`` rather than
the learner or its AST interpreter. The equivalent AST is private buyer metadata
for a separate grammar/target consistency check; it must never enter a learner
brief. A publication protocol must freeze this generator and evaluation sources
before deriving or generating reserved seeds. This module does not select or
generate a reserved seed automatically.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import re
from pathlib import Path

SCHEMA = "nuria.guarded-skill-tasks.v1"
CONFIG = {
    "version": "safe-text-v1",
    "max_ops": 24,
    "max_candidates": 30000,
    "max_rows": 32,
    "max_input_bytes": 2048,
    "max_programs": 128,
    "max_macros": 0,
    "max_fields": 8,
    "max_parts": 4,
}
DIAGNOSTIC_ROWS = 6
SCORING_ROWS = 24
FAMILIES = (
    "preserve-field",
    "trim-field",
    "uppercase-field",
    "lowercase-field",
    "uppercase-token",
    "lowercase-token",
    "trim-delimited-field",
    "short-token",
    "uppercase-prefix",
    "joined-fields",
    "joined-normalized-tokens",
    "joined-code-and-segment",
)
WORDS = (
    "Acacia",
    "birch",
    "Cobalt",
    "DELTA",
    "ember",
    "Fjord",
    "Garnet",
    "HARBOR",
    "Indigo",
    "juniper",
    "Kestrel",
    "LARCH",
    "Maple",
    "nectar",
    "Onyx",
    "PINE",
    "Quartz",
    "reed",
    "Sable",
    "TIDAL",
)


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode()).hexdigest()


def bounded_integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(label + " is outside its finite integer bound")
    return value


def field(index):
    return {"op": "field", "index": index}


def unary(operation, argument, **parameters):
    return {"op": operation, "arg": argument, **parameters}


def token(index, position):
    return unary("token", field(index), pattern="alpha", index=position)


def program(recipe):
    """Render a data-only AST; execution is not used to obtain target labels."""
    family = recipe["family"]
    left, right = recipe["fields"]
    position, separator = recipe["token_index"], recipe["separator"]
    if family == "preserve-field":
        expr = field(left)
    elif family in ("trim-field", "uppercase-field", "lowercase-field"):
        operation = {
            "trim-field": "strip",
            "uppercase-field": "upper",
            "lowercase-field": "lower",
        }[family]
        expr = unary(operation, field(left))
    elif family in ("uppercase-token", "lowercase-token"):
        expr = unary(
            "upper" if family == "uppercase-token" else "lower",
            token(left, position),
        )
    elif family == "trim-delimited-field":
        expr = unary(
            "strip",
            unary("split", field(left), separator=separator, index=position),
        )
    elif family == "short-token":
        expr = unary("slice", token(left, position), start=0, end=3)
    elif family == "uppercase-prefix":
        expr = unary("upper", unary("slice", field(left), start=2, end=5))
    elif family == "joined-fields":
        expr = {
            "op": "concat",
            "parts": [
                unary("strip", field(left)),
                {"op": "constant", "value": " | "},
                unary("strip", field(right)),
            ],
        }
    elif family == "joined-normalized-tokens":
        expr = {
            "op": "concat",
            "parts": [
                unary("upper", token(left, 0)),
                {"op": "constant", "value": "."},
                unary("lower", token(right, -1)),
            ],
        }
    elif family == "joined-code-and-segment":
        expr = {
            "op": "concat",
            "parts": [
                unary("slice", token(left, 0), start=0, end=3),
                {"op": "constant", "value": ":"},
                unary(
                    "upper",
                    unary("split", field(right), separator=separator, index=-1),
                ),
            ],
        }
    else:
        raise ValueError("Unknown generator recipe family")
    return {"version": CONFIG["version"], "expr": expr}


def target(recipe, values):
    """Independent target oracle, written without learner/interpreter imports."""
    if (
        type(values) is not list
        or len(values) != 6
        or any(type(value) is not str for value in values)
    ):
        raise ValueError("Generator oracle needs six string fields")
    family = recipe["family"]
    left_index, right_index = recipe["fields"]
    left, right = values[left_index], values[right_index]
    position, separator = recipe["token_index"], recipe["separator"]
    if family == "preserve-field":
        return left
    if family == "trim-field":
        return left.strip()
    if family == "uppercase-field":
        return left.upper()
    if family == "lowercase-field":
        return left.lower()
    if family == "uppercase-token":
        return re.findall(r"[A-Za-z]+", left)[position].upper()
    if family == "lowercase-token":
        return re.findall(r"[A-Za-z]+", left)[position].lower()
    if family == "trim-delimited-field":
        return left.split(separator)[position].strip()
    if family == "short-token":
        return re.findall(r"[A-Za-z]+", left)[position][:3]
    if family == "uppercase-prefix":
        return left[2:5].upper()
    if family == "joined-fields":
        return left.strip() + " | " + right.strip()
    if family == "joined-normalized-tokens":
        return (
            re.findall(r"[A-Za-z]+", left)[0].upper()
            + "."
            + re.findall(r"[A-Za-z]+", right)[-1].lower()
        )
    if family == "joined-code-and-segment":
        return (
            re.findall(r"[A-Za-z]+", left)[0][:3]
            + ":"
            + right.split(separator)[-1].upper()
        )
    raise ValueError("Unknown generator recipe family")


def recipe_for(index, rng, existing):
    family = FAMILIES[index % len(FAMILIES)]
    # Equivalent programs are identified by their AST, not unused parameters.
    for _ in range(1000):
        recipe = {
            "family": family,
            "fields": rng.sample(range(6), 2),
            "token_index": rng.choice((0, -1)),
            "separator": rng.choice((":", "/", "-", ".")),
        }
        identity = digest(program(recipe))
        if identity not in existing:
            return {**recipe, "program_sha256": identity}
    # A family can exhaust its six shallow projections. Reusing that program
    # is an explicit recurrence, never a falsely advertised new program.
    return {**recipe, "program_sha256": identity}


def record(rng, row_number, ambiguous):
    values = []
    for column in range(6):
        first, second, third, last = rng.sample(WORDS, 4)
        values.append(
            f"  {first} {second}:{third}/record-{rng.randrange(100, 1000)}.csv {last} "
        )
    if ambiguous:
        # A declared stress prefix: different field selections have identical
        # answers. The final rows do not retain this equality constraint.
        values = [values[0]] * 6
    # The suffix makes every original input distinct even if RNG words repeat;
    # it is ordinary record data, not a format, recipe or skill identifier.
    values = [value + f"{row_number:03d}" for value in values]
    return values


def learner_request(job):
    """The complete information boundary visible before asking an oracle."""
    return {"diagnostic_inputs": list(job["diagnostic_inputs"])}


def generate(seed, count=96):
    """Return a private buyer corpus; callers must enforce reserved access."""
    bounded_integer(seed, 0, 2**256 - 1, "Seed")
    bounded_integer(count, 1, 128, "Task count")
    rng = random.Random(seed)
    recipes, identities, jobs = [], set(), []
    next_recipe = 0
    for index in range(count):
        planned_recurrence = index >= 8 and index % 4 == 3
        if planned_recurrence:
            recipe = copy.deepcopy(rng.choice(recipes[:-2] or recipes))
        else:
            recipe = recipe_for(next_recipe, rng, identities)
            next_recipe += 1
        identity = recipe["program_sha256"]
        actual_recurrence = identity in identities
        if not actual_recurrence:
            recipes.append(copy.deepcopy(recipe))
            identities.add(identity)
        stress = index % 2 == 0
        rows, outputs, row_ids = [], [], []
        for position in range(DIAGNOSTIC_ROWS + SCORING_ROWS):
            values = record(rng, position, stress and position < 2)
            encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
            rows.append(encoded)
            outputs.append(target(recipe, values))
            row_ids.append(digest({"seed": seed, "task": index, "row": position}))
        provenance = {
            "schema": SCHEMA,
            "seed": seed,
            "ordinal": index,
            "row_ids": row_ids,
            "inputs_sha256": digest(rows),
        }
        jobs.append(
            {
                "id": digest(provenance),
                "family": recipe["family"],
                "diagnostic_inputs": rows[:DIAGNOSTIC_ROWS],
                "diagnostic_outputs": outputs[:DIAGNOSTIC_ROWS],
                "score_inputs": rows[DIAGNOSTIC_ROWS:],
                "score_outputs": outputs[DIAGNOSTIC_ROWS:],
                "row_ids": {
                    "diagnostic": row_ids[:DIAGNOSTIC_ROWS],
                    "score": row_ids[DIAGNOSTIC_ROWS:],
                },
                "private_recipe": {
                    **recipe,
                    "program": program(recipe),
                    "recurrence": actual_recurrence,
                    "planned_recurrence": planned_recurrence,
                    "ambiguous_prefix_stress": stress,
                },
                "unsupported": None,
            }
        )
    return {
        "schema": SCHEMA,
        "config": copy.deepcopy(CONFIG),
        "source": {
            "seed": seed,
            "task_count": count,
            "scope": "Authored synthetic finite stress stream; not independently authored external tasks or customer traffic",
            "diagnostic_rows_per_task": DIAGNOSTIC_ROWS,
            "score_rows_per_task": SCORING_ROWS,
            "unique_program_count": len(identities),
            "oracle": "Python standard-library target function, independent of the learner and its interpreter",
            "recurrence_rule": "After eight tasks, every fourth task draws an earlier program; shallow template exhaustion is separately marked as recurrence",
            "stress_rule": "Even task ordinals have identical fields in the first two diagnostic rows; remaining diagnostic and final rows vary fields independently",
            "authority": "Recipe, family, source ordinal, targets and row identities remain buyer-only; learner_request exposes diagnostic inputs only",
        },
        "tasks": jobs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development-seed", type=int, choices=range(4), required=True)
    parser.add_argument("--count", type=int, default=96)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus = generate(args.development_seed, args.count)
    # Reserved seeds require the separate evaluator's published freeze gate.
    # This convenience CLI is deliberately limited to the declared DEV seeds.
    with args.output.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write((json.dumps(corpus, indent=2, allow_nan=False) + "\n").encode())
    print(json.dumps({"tasks": len(corpus["tasks"]), "sha256": digest(corpus)}))


if __name__ == "__main__":
    main()

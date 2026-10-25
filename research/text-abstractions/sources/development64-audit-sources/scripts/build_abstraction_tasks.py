"""Author finite structural-transfer development tasks for safe text programs.

This is an authored engineering pilot, not customer traffic, an external
benchmark or a reserved qualification. The fixed ordered pool is selected only
by whole-program structural exclusion, never by controller scores or a learned
macro library. Python target branches supply labels independently of the
learner's AST interpreter. Recipes, structural identities and scoring rows are
buyer-only metadata; ``learner_request`` exposes diagnostic inputs alone.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re

SCHEMA = "nuria.abstraction-development-tasks.v1"
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
DIAGNOSTIC_ROWS = 4
SCORING_ROWS = 24
CLUSTERS = 8
STRUCTURAL_POOL = (
    "upper-inner-token",
    "joined-token-and-prefix",
    "trim-upper-segment",
    "joined-segment-and-token",
    "lower-last-segment-token",
    "slice-joined-tokens",
    "upper-sliced-last-token",
    "joined-short-and-trimmed-segments",
    "lower-final-token-suffix",
    "joined-trimmed-segment-and-code",
    "last-token-of-lower-middle-segment",
    "lower-joined-prefix-and-token",
    "slice-upper-last-segment",
    "joined-sliced-token-and-segment-suffix",
    "middle-segment-of-trimmed-lower",
    "upper-joined-segment-and-inner-token",
    "compact-joined-segments",
    "joined-two-views-of-one-field",
    "title-sliced-middle-segment",
    "joined-inner-tokens-with-final-case",
)
WORDS = (
    "Acacia",
    "BIRCH",
    "Cobalt",
    "delta",
    "Ember",
    "FJORD",
    "Garnet",
    "harbor",
    "Indigo",
    "JUNIPER",
    "Kestrel",
    "larch",
    "Maple",
    "NECTAR",
    "Onyx",
    "pine",
    "Quartz",
    "REED",
    "Sable",
    "tidal",
    "Umber",
    "VIOLET",
    "Willow",
    "xenon",
    "Yarrow",
    "ZEPHYR",
    "Copper",
    "dahlia",
)
OP_FIELDS = {
    "field": {"index"},
    "constant": {"value"},
    **{
        op: {"arg"}
        for op in ("strip", "lower", "upper", "title", "capitalize", "compact")
    },
    "slice": {"arg", "start", "end"},
    "token": {"arg", "pattern", "index"},
    "split": {"arg", "separator", "index"},
    "replace": {"arg", "old", "new"},
    "number": {"arg", "scale", "places"},
    "concat": {"parts"},
    "if_equal": {"field", "value", "then", "else"},
}


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode()).hexdigest()


def bounded(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(label + " is outside its finite integer bound")
    return value


def field(index):
    return {"op": "field", "index": index}


def unary(op, arg, **parameters):
    return {"op": op, "arg": arg, **parameters}


def skeleton(program_value):
    """Alpha-normalize field references and typed literals, preserving aliases.

    Operation names, parameter keys and tree order remain. Scalar parameters,
    including registered token-pattern values, become typed placeholders.
    Field references have their own namespace; two occurrences of one field
    remain distinguishable from two different fields. This is structural
    normalization, not a proof of semantic novelty or program equivalence.
    """
    if (
        type(program_value) is not dict
        or set(program_value) != {"version", "expr"}
        or program_value["version"] != CONFIG["version"]
    ):
        raise ValueError("Structural identity needs a safe-text program")
    fields, literals = {}, {}
    remaining = [CONFIG["max_ops"]]

    def reference(value):
        bounded(value, 0, CONFIG["max_fields"] - 1, "Field reference")
        if value not in fields:
            fields[value] = len(fields)
        return {"field": fields[value]}

    def literal(value):
        if type(value) not in (str, int, type(None)):
            raise ValueError("Structural literals need explicit safe scalar types")
        if type(value) is str and len(value.encode()) > 256:
            raise ValueError("Structural literal exceeds its text bound")
        if type(value) is int:
            bounded(value, -512, 512, "Structural integer")
        kind = "null" if value is None else type(value).__name__
        key = (kind, value)
        if key not in literals:
            literals[key] = len(literals)
        return {"literal": kind, "alias": literals[key]}

    def walk(node, depth=0):
        remaining[0] -= 1
        if (
            remaining[0] < 0
            or depth > 8
            or type(node) is not dict
            or type(node.get("op")) is not str
            or node["op"] not in OP_FIELDS
            or set(node) != {"op", *OP_FIELDS[node["op"]]}
        ):
            raise ValueError("Structural AST exceeds its syntax or finite bound")
        result = {"op": node["op"]}
        for key in sorted(OP_FIELDS[node["op"]]):
            value = node[key]
            if (node["op"] == "field" and key == "index") or (
                node["op"] == "if_equal" and key == "field"
            ):
                result[key] = reference(value)
            elif key in ("arg", "then", "else"):
                result[key] = walk(value, depth + 1)
            elif key == "parts":
                if (
                    type(value) is not list
                    or not 2 <= len(value) <= CONFIG["max_parts"]
                ):
                    raise ValueError("Structural concatenation is not bounded")
                result[key] = [walk(part, depth + 1) for part in value]
            else:
                result[key] = literal(value)
        return result

    return {"version": CONFIG["version"], "expr": walk(program_value["expr"])}


def skeleton_hash(program_value):
    return digest(skeleton(program_value))


def program(recipe):
    """Render the equivalent buyer AST without using it to produce labels."""
    family = recipe["family"]
    left, right = recipe["fields"]
    separator, joiner = recipe["separator"], recipe["joiner"]

    def tok(arg, position=0):
        return unary("token", arg, pattern="alpha", index=position)

    def seg(arg, position=0):
        return unary("split", arg, separator=separator, index=position)

    def cut(arg, start, end):
        return unary("slice", arg, start=start, end=end)

    def join(a, b):
        return {"op": "concat", "parts": [a, {"op": "constant", "value": joiner}, b]}

    a, b = field(left), field(right)
    if family == STRUCTURAL_POOL[0]:
        expr = unary("upper", cut(tok(a), 1, 4))
    elif family == STRUCTURAL_POOL[1]:
        expr = join(unary("upper", tok(a)), unary("lower", cut(tok(b, -1), 0, 3)))
    elif family == STRUCTURAL_POOL[2]:
        expr = unary("strip", seg(unary("upper", a)))
    elif family == STRUCTURAL_POOL[3]:
        expr = join(cut(unary("lower", seg(a)), 0, 5), unary("upper", tok(b, -1)))
    elif family == STRUCTURAL_POOL[4]:
        expr = unary("lower", tok(seg(a, -1)))
    elif family == STRUCTURAL_POOL[5]:
        expr = cut(join(unary("upper", tok(a)), unary("lower", tok(b, -1))), 1, 9)
    elif family == STRUCTURAL_POOL[6]:
        expr = unary("upper", tok(cut(a, 2, -2), -1))
    elif family == STRUCTURAL_POOL[7]:
        expr = join(
            unary("lower", cut(seg(a, 1), 1, 6)),
            unary("strip", unary("upper", seg(b, -1))),
        )
    elif family == STRUCTURAL_POOL[8]:
        expr = unary("lower", cut(tok(a, -1), -4, None))
    elif family == STRUCTURAL_POOL[9]:
        expr = join(
            unary("strip", unary("lower", seg(a))), unary("upper", cut(tok(b), 0, 2))
        )
    elif family == STRUCTURAL_POOL[10]:
        expr = tok(unary("lower", seg(a, 1)), -1)
    elif family == STRUCTURAL_POOL[11]:
        expr = unary("lower", join(cut(tok(a), 0, 3), unary("upper", tok(b, -1))))
    elif family == STRUCTURAL_POOL[12]:
        expr = cut(unary("upper", seg(a, -1)), 1, 7)
    elif family == STRUCTURAL_POOL[13]:
        expr = join(
            tok(unary("lower", cut(a, 2, -2)), -1),
            cut(unary("upper", seg(b)), -5, None),
        )
    elif family == STRUCTURAL_POOL[14]:
        expr = seg(unary("strip", unary("lower", a)), 1)
    elif family == STRUCTURAL_POOL[15]:
        expr = unary("upper", join(unary("strip", seg(a)), cut(tok(b), 1, 4)))
    elif family == STRUCTURAL_POOL[16]:
        expr = unary(
            "compact", join(unary("lower", seg(a)), unary("upper", seg(b, -1)))
        )
    elif family == STRUCTURAL_POOL[17]:
        expr = join(unary("upper", tok(a)), unary("lower", cut(tok(a, -1), 0, 3)))
    elif family == STRUCTURAL_POOL[18]:
        expr = unary("title", cut(seg(a, 1), 1, 8))
    elif family == STRUCTURAL_POOL[19]:
        expr = unary(
            "upper", join(cut(tok(a), 1, 4), unary("lower", cut(tok(b, -1), 1, 4)))
        )
    else:
        raise ValueError("Unknown authored structural family")
    return {"version": CONFIG["version"], "expr": expr}


def target(recipe, values):
    """Independent ordinary-Python buyer semantics, with no AST execution."""
    if (
        type(values) is not list
        or len(values) != 6
        or any(type(v) is not str for v in values)
    ):
        raise ValueError("Target oracle requires six string fields")
    family = recipe["family"]
    a, b = (values[index] for index in recipe["fields"])
    separator, joiner = recipe["separator"], recipe["joiner"]

    def tokens(value):
        return re.findall(r"[A-Za-z]+", value)

    if family == STRUCTURAL_POOL[0]:
        return tokens(a)[0][1:4].upper()
    if family == STRUCTURAL_POOL[1]:
        return tokens(a)[0].upper() + joiner + tokens(b)[-1][:3].lower()
    if family == STRUCTURAL_POOL[2]:
        return a.upper().split(separator)[0].strip()
    if family == STRUCTURAL_POOL[3]:
        return a.split(separator)[0].lower()[:5] + joiner + tokens(b)[-1].upper()
    if family == STRUCTURAL_POOL[4]:
        return tokens(a.split(separator)[-1])[0].lower()
    if family == STRUCTURAL_POOL[5]:
        return (tokens(a)[0].upper() + joiner + tokens(b)[-1].lower())[1:9]
    if family == STRUCTURAL_POOL[6]:
        return tokens(a[2:-2])[-1].upper()
    if family == STRUCTURAL_POOL[7]:
        return (
            a.split(separator)[1][1:6].lower()
            + joiner
            + b.split(separator)[-1].upper().strip()
        )
    if family == STRUCTURAL_POOL[8]:
        return tokens(a)[-1][-4:].lower()
    if family == STRUCTURAL_POOL[9]:
        return a.split(separator)[0].lower().strip() + joiner + tokens(b)[0][:2].upper()
    if family == STRUCTURAL_POOL[10]:
        return tokens(a.split(separator)[1].lower())[-1]
    if family == STRUCTURAL_POOL[11]:
        return (tokens(a)[0][:3] + joiner + tokens(b)[-1].upper()).lower()
    if family == STRUCTURAL_POOL[12]:
        return a.split(separator)[-1].upper()[1:7]
    if family == STRUCTURAL_POOL[13]:
        return tokens(a[2:-2].lower())[-1] + joiner + b.split(separator)[0].upper()[-5:]
    if family == STRUCTURAL_POOL[14]:
        return a.lower().strip().split(separator)[1]
    if family == STRUCTURAL_POOL[15]:
        return (a.split(separator)[0].strip() + joiner + tokens(b)[0][1:4]).upper()
    if family == STRUCTURAL_POOL[16]:
        return "".join(
            (
                a.split(separator)[0].lower() + joiner + b.split(separator)[-1].upper()
            ).split()
        )
    if family == STRUCTURAL_POOL[17]:
        return tokens(a)[0].upper() + joiner + tokens(a)[-1][:3].lower()
    if family == STRUCTURAL_POOL[18]:
        return a.split(separator)[1][1:8].title()
    if family == STRUCTURAL_POOL[19]:
        return (tokens(a)[0][1:4] + joiner + tokens(b)[-1][1:4].lower()).upper()
    raise ValueError("Unknown authored target family")


def _rng(seed, family, instance, phase):
    return random.Random(int(digest([seed, family, instance, phase]), 16))


def _recipe(seed, family, instance):
    rng = _rng(seed, family, instance, "parameters")
    return {
        "family": family,
        "fields": rng.sample(range(6), 2),
        "separator": rng.choice((":", "/", "|", "-")),
        "joiner": rng.choice(("@", "~", "#", "=")),
    }


def _rows(seed, recipe, instance, phase, count):
    rng = _rng(seed, recipe["family"], instance, phase)
    rows, outputs = [], []
    while len(rows) < count:
        values = []
        for _ in range(6):
            words = rng.sample(WORDS, 5)
            words = [word.swapcase() if rng.randrange(2) else word for word in words]
            a, b, c, d, e = words
            sep = recipe["separator"]
            values.append(
                f"  {a} {b}{sep} {c} {d}{sep} {e} {rng.randrange(100000, 1000000)}  "
            )
        encoded = serial(values)
        if encoded not in rows:
            rows.append(encoded)
            outputs.append(target(recipe, values))
    return rows, outputs


def learner_request(task):
    return {"diagnostic_inputs": list(task["diagnostic_inputs"])}


def generate(seed, per_cluster=8, excluded_skeletons=()):
    """Build eight fixed-pool structural clusters, defaulting to 64 DEV tasks.

    Exclusions are canonical skeleton objects or their lowercase SHA256 hashes.
    The first eight eligible families in ``STRUCTURAL_POOL`` are selected. No
    program outcomes, controller scores or mined-library contents are read.
    ``per_cluster`` changes only the number of data instances, not the pool.
    """
    bounded(seed, 0, 2**256 - 1, "Seed")
    bounded(per_cluster, 1, 32, "Instances per structural cluster")
    if (
        not isinstance(excluded_skeletons, (list, tuple, set, frozenset))
        or len(excluded_skeletons) > 65536
    ):
        raise ValueError("Structural exclusions must be a bounded collection")
    excluded = set()
    for item in excluded_skeletons:
        identity = digest(item) if type(item) is dict else item
        if type(identity) is not str or not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("Structural exclusions need skeleton objects or hashes")
        excluded.add(identity)
    selected = []
    for family in STRUCTURAL_POOL:
        recipe = {"family": family, "fields": [0, 1], "separator": ":", "joiner": "~"}
        structure = skeleton(program(recipe))
        if digest(structure) not in excluded:
            selected.append(
                {
                    "family": family,
                    "skeleton": structure,
                    "skeleton_sha256": digest(structure),
                }
            )
        if len(selected) == CLUSTERS:
            break
    if len(selected) != CLUSTERS:
        raise ValueError("Fewer than eight predeclared structural families remain")
    tasks = []
    for cluster in selected:
        family = cluster["family"]
        for instance in range(per_cluster):
            recipe = _recipe(seed, family, instance)
            ast = program(recipe)
            if skeleton(ast) != cluster["skeleton"]:
                raise ValueError("Parameter variation changed structural aliases")
            inputs, outputs = _rows(
                seed, recipe, instance, "diagnostic", DIAGNOSTIC_ROWS
            )
            final_inputs, final_outputs = _rows(
                seed, recipe, instance, "buyer", SCORING_ROWS
            )
            if set(inputs) & set(final_inputs):
                raise ValueError("Diagnostic and buyer inputs unexpectedly overlap")
            identity = digest([SCHEMA, seed, family, instance])
            tasks.append(
                {
                    "id": identity,
                    "family": family,
                    "cluster": cluster["skeleton_sha256"],
                    "diagnostic_inputs": inputs,
                    "diagnostic_outputs": outputs,
                    "score_inputs": final_inputs,
                    "score_outputs": final_outputs,
                    "private_recipe": {
                        **recipe,
                        "program": ast,
                        "program_sha256": digest(ast),
                        "skeleton": copy.deepcopy(cluster["skeleton"]),
                        "skeleton_sha256": cluster["skeleton_sha256"],
                        "instance": instance,
                    },
                    "unsupported": None,
                }
            )
    return {
        "schema": SCHEMA,
        "config": copy.deepcopy(CONFIG),
        "seed": seed,
        "clusters": selected,
        "tasks": tasks,
        "source": {
            "scope": "Authored synthetic structural development pilot; not external tasks, customer traffic or a reserved qualification",
            "selection": "First eight fixed ordered pool families whose alpha-normalized whole-program skeleton is not excluded",
            "pool": list(STRUCTURAL_POOL),
            "excluded_skeletons": sorted(excluded),
            "per_cluster": per_cluster,
            "task_count": len(tasks),
            "diagnostic_rows_per_task": DIAGNOSTIC_ROWS,
            "score_rows_per_task": SCORING_ROWS,
            "oracle": "Independent Python target branches; no learner or AST-interpreter imports",
            "authority": "Recipes, structure, outputs and score rows are buyer-only; learner_request exposes diagnostic inputs alone",
            "independence": "Diagnostic and buyer rows use distinct deterministic RNG substreams; instances within a structural cluster are dependent",
            "novelty_limit": "Structural exclusion does not prove semantic novelty or external generalization",
        },
    }

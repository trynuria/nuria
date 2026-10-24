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

from cognition.skills import DEFAULT_CONFIG, SkillLibrary

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "research/intervention-memory/data/bike-sharing-original.zip"
ARCHIVE_SHA = "b70182d0d0508e9abbb79306ce5c0cec34869000f8220175ac83d11dbe845401"
CSV_SHA = "e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f"
SCHEMA = "nuria.skill-acquisition.v1"
FIELDS = ("temp", "hum", "windspeed", "cnt", "casual", "registered")
TRAIN_STRUCTURES = (
    (),
    ("space",),
    ("prefix",),
    ("suffix",),
    ("token",),
    ("json",),
    ("percent",),
    ("milli",),
    ("prefix", "space"),
    ("suffix", "json"),
    ("token", "space"),
    ("prefix", "token"),
)
TRANSFER_STRUCTURES = (
    ("prefix", "token", "json"),
    ("suffix", "json", "space"),
    ("percent", "prefix", "token"),
    ("milli", "suffix", "json"),
    ("prefix", "json", "token"),
    ("suffix", "token", "json"),
    ("percent", "json", "space"),
    ("milli", "prefix", "token", "json"),
)
BRANCHES = (
    "fresh",
    "retrieval",
    "frequency",
    "contextual",
    "frozen",
    "no_acquisition",
    "shuffled_context",
)
MODES = {
    "fresh": "fresh",
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
    return format(number.normalize(), "f")


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


def corrupt(text, structure):
    for wrapper in structure:
        if wrapper == "space":
            text = " \t" + text + " \n"
        elif wrapper == "prefix":
            text = "value=" + text
        elif wrapper == "suffix":
            text += " units"
        elif wrapper == "token":
            text = "observation|" + text + "|ok"
        elif wrapper == "json":
            text = json.dumps(text)
        elif wrapper == "percent":
            text = numeric(Decimal(text) * 100) + "%"
        elif wrapper == "milli":
            text = numeric(Decimal(text) * 1000)
        else:
            raise ValueError("Unknown generated wrapper")
    return text


def features(inputs):
    # Only observable example inputs; no source family, structure or best tool ID.
    n = max(1, len(inputs))
    return [
        min(1.0, sum(len(s.encode()) for s in inputs) / n / 128),
        sum(s != s.strip() for s in inputs) / n,
        sum("value=" in s for s in inputs) / n,
        sum(" units" in s for s in inputs) / n,
        sum("|" in s for s in inputs) / n,
        sum(s.lstrip().startswith('"') for s in inputs) / n,
        sum("%" in s for s in inputs) / n,
    ]


def make_tasks(rows, seed, phase, count):
    year_rows = [
        row for row in rows if row["yr"] == ("0" if phase == "acquisition" else "1")
    ]
    if seed in (8101, 8102, 8103):
        pool = [
            row
            for index, row in enumerate(year_rows[:1728])
            if index % 3 == seed - 8101
        ]
    elif 8201 <= seed <= 8212:
        pool = [
            row
            for index, row in enumerate(year_rows[1728:])
            if index % 12 == seed - 8201
        ]
    else:
        raise ValueError("Seed is outside the registered disjoint row partitions")
    structures = TRAIN_STRUCTURES if phase == "acquisition" else TRANSFER_STRUCTURES
    rng = random.Random(f"{SCHEMA}:{phase}:{seed}")
    tasks = []
    for step in range(count):
        structure = structures[step % len(structures)]
        field = FIELDS[rng.randrange(len(FIELDS))]
        selected = rng.sample(pool, 24)
        unique = {}
        for row in selected:
            unique.setdefault(numeric(row[field]), row)
        if len(unique) < 4:
            # This is input-identifiability conditioning, not tool-outcome selection.
            field = "cnt"
            unique = {}
            for row in selected:
                unique.setdefault(numeric(row[field]), row)
        if len(unique) < 4:
            raise ValueError(
                "A registered task has fewer than four distinct example targets"
            )
        first = list(unique.values())[:4]
        selected = first + [row for row in selected if row not in first]
        chosen_ids = {row["instant"] for row in selected}
        pool = [row for row in pool if row["instant"] not in chosen_ids]
        outputs = [numeric(row[field]) for row in selected]
        inputs = [corrupt(value, structure) for value in outputs]
        # IDs commit full generated task identity, but the solver receives no ID.
        task = {
            "phase": phase,
            "step": step,
            "structure": list(structure),
            "field": field,
            "row_ids": [int(row["instant"]) for row in selected],
            "inputs": inputs,
            "outputs": outputs,
        }
        task["id"] = digest(task)
        tasks.append(task)
    return tasks


def primitive_cost(value):
    return 1 + max(1, (len(str(value).encode()) + 31) // 32)


class ToolFailure(ValueError):
    def __init__(self, message, units):
        super().__init__(message)
        self.units = units


def execute(program, text):
    """Independent bounded tool interpreter; no solver helper imports."""
    if (
        not isinstance(program, dict)
        or set(program) != {"version", "ops"}
        or program["version"] != "safe-transform-v1"
    ):
        raise ToolFailure("Invalid program envelope", 0)
    if len(program["ops"]) > 8:
        raise ToolFailure("Program exceeds operation bound", 0)
    allowed = {canonical(op) for op in DEFAULT_CONFIG["operations"]}
    if any(canonical(op) not in allowed for op in program["ops"]):
        raise ToolFailure("Unregistered primitive", 0)
    value, units = text, 0
    for operation in program["ops"]:
        units += primitive_cost(value)
        try:
            name = operation["op"]
            required = Decimal if name in ("scale", "format") else str
            if type(value) is not required:
                raise ValueError("Wrong primitive input type")
            if name == "strip":
                value = value.strip()
            elif name == "prefix":
                prefix = operation["value"]
                if not value.startswith(prefix):
                    raise ValueError("Missing prefix")
                value = value[len(prefix) :]
            elif name == "suffix":
                suffix = operation["value"]
                if not value.endswith(suffix):
                    raise ValueError("Missing suffix")
                value = value[: -len(suffix)]
            elif name == "split":
                value = value.split(operation["separator"])[operation["index"]]
            elif name == "json":
                value = json.loads(value)
                if type(value) is not str:
                    raise ValueError("JSON result is not text")
            elif name == "decimal":
                value = Decimal(value)
            elif name == "scale":
                with localcontext() as context:
                    context.prec = 64
                    value = value * operation["numerator"] / operation["denominator"]
            elif name == "format":
                value = format(value, "f")
                if "." in value:
                    value = value.rstrip("0").rstrip(".")
                if value in ("-0", ""):
                    value = "0"
            else:
                raise ValueError("Unknown primitive")
            if type(value) is Decimal:
                if (
                    not value.is_finite()
                    or abs(value) > Decimal("1e30")
                    or abs(value.as_tuple().exponent) > 64
                ):
                    raise ValueError("Number exceeds bound")
            elif type(value) is not str or len(value.encode()) > 256:
                raise ValueError("Intermediate text exceeds bound")
        except (ValueError, TypeError, KeyError, IndexError, InvalidOperation) as error:
            raise ToolFailure("Primitive failed", units) from error
    if type(value) is not str:
        raise ToolFailure("Program does not return text", units)
    return value, units


def check_program(program, inputs, outputs):
    actual, charged, first = [], 0, None
    for index, (text, expected) in enumerate(zip(inputs, outputs, strict=True)):
        try:
            value, cost = execute(program, text)
            charged += cost
            actual.append(value)
            passed = value == expected
        except ToolFailure as error:
            charged += error.units
            actual.append(None)
            passed = False
        if not passed and first is None:
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


def implementation_hashes():
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            Path(__file__),
            ROOT / "cognition/skills.py",
            ROOT / "scripts/verify_skill_results.py",
        )
    }


def protocol():
    return {
        "schema": SCHEMA,
        "status": "frozen-before-reserved-execution",
        "dataset": {
            "archive_sha256": ARCHIVE_SHA,
            "csv_sha256": CSV_SHA,
            "rows": 17379,
            "source": "UCI Bike Sharing DOI:10.24432/C5W894",
            "license": "CC BY4.0",
        },
        "data_scope": "Real observed numeric values; all wrapper/unit corruptions and task structures are generated.",
        "development_seeds": [8101, 8102, 8103],
        "reserved_seeds": list(range(8201, 8213)),
        "acquisition_tasks": 24,
        "transfer_tasks": 16,
        "visible_examples": 4,
        "cegis_validation_rows": 8,
        "scored_rows": 12,
        "max_cegis_rounds": 2,
        "solve_budget": 2400,
        "config": copy.deepcopy(DEFAULT_CONFIG),
        "acquisition_structures": [list(s) for s in TRAIN_STRUCTURES],
        "unseen_transfer_structures": [list(s) for s in TRANSFER_STRUCTURES],
        "split": "Acquisition uses2011, transfer2012. First1728rows/year are split into3 disjointdevelopmentbuckets; remainingrows split modulo12 into disjointreservedseedbuckets. Rows never repeat within a seed. Transfer wrapper structures are unseen. Fourvisibleexamples have distincttargetvalues for identification.",
        "branches": list(BRANCHES),
        "modes": MODES,
        "source_hashes": implementation_hashes(),
        "primary": "Fraction of transfer tasks with all 12 independent scored outputs exactly correct within shared per-task solver budget; all acquisition and verification work reported.",
        "gates": {
            "minimum_transfer_success_gain": 0.05,
            "confidence": "paired seed-level 95% t interval, df=11",
            "practical": "Contextual branch beats strongest registered simpler branch by >=5 percentage points with paired lower95%>0; total work must not exceed simpler branch by >10%.",
            "reliability": "No malformed admitted program or missing task/attempt may pass verification.",
            "learning": "Contextual transfer beats no-acquisition; online acquisition late gain vs early and frozen reported separately, not substituted for transfer.",
            "neural": "No neural features or neural superiority claim in this study.",
        },
        "selection_limit": "Strongest simpler branch selected by reserved performance for a conservative comparator; no method retuning from reserved results.",
        "retention": "Every acquisition and reserved task, failed attempt, CEGIS counterexample, program, output and cost is retained. No failed seed is replaced.",
    }


def export_manifest(destination, seed=8101):
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
            "description": "Finite development corpus. Real numeric observations; generated format corruptions. First4 rows visible, next8 buyer validation, final12 scored. Structure/field/source-row IDs are evaluator metadata.",
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
        and branch not in ("fresh", "no_acquisition")
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
        result = library.solve(
            visible_inputs,
            visible_outputs,
            mode=MODES[branch],
            budget=remaining,
            features=observed_features,
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
        if branch != "fresh":
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
        "library_state_sha256": digest(state),
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


def summarize(trials):
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
    simple = max(
        ("fresh", "retrieval", "frequency"),
        key=lambda branch: (
            per_branch[branch]["transfer_success"],
            -per_branch[branch]["mean_total_work"],
        ),
    )

    def pairs(comparator):
        left = {t["seed"]: t for t in trials if t["branch"] == "contextual"}
        right = {t["seed"]: t for t in trials if t["branch"] == comparator}
        return interval(
            [
                left[seed]["transfer_success"] - right[seed]["transfer_success"]
                for seed in sorted(left)
            ]
        )

    competitive = pairs(simple)
    learning = pairs("no_acquisition")
    work_ratio = per_branch["contextual"]["mean_total_work"] / max(
        1, per_branch[simple]["mean_total_work"]
    )
    return {
        "branches": per_branch,
        "strongest_simpler_branch": simple,
        "contextual_minus_strongest_simple": competitive,
        "contextual_minus_no_acquisition": learning,
        "contextual_total_work_ratio_vs_simple": work_ratio,
        "gates": {
            "practical_benefit": competitive["mean"] >= 0.05
            and competitive["lower95"] is not None
            and competitive["lower95"] > 0
            and work_ratio <= 1.1,
            "acquisition_transfer": learning["mean"] >= 0.05
            and learning["lower95"] is not None
            and learning["lower95"] > 0,
            "neural_specificity": "not_tested",
        },
        "scope": "Task/algorithm learning and real executable transformations on generated format corruptions of real observations. No neural superiority, external hiring or broad intelligence claim.",
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
                    if branch == "fresh":
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
        "summary": summarize(trials),
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
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("Preserve prior output; use a new destination")
    if args.phase == "manifest":
        export_manifest(args.out)
        print(json.dumps({"manifest": str(args.out)}))
        return
    if args.phase == "freeze":
        args.out.write_text(json.dumps(protocol(), indent=2) + "\n")
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
        if config["source_hashes"] != implementation_hashes():
            raise ValueError("Evaluated source changed after protocol freeze")
        seeds = config["reserved_seeds"]
    else:
        config = protocol()
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

"""Independently execute bounded data-only programs and check private study traces.

No learner or evaluator is imported. Private traces may contain source benchmark
examples and must not be copied wholesale into public research artifacts. CPU
measurements are recorded process evidence, not independently witnessed runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "word": r"\w+",
    "digits": r"[0-9]+",
    "alpha": r"[A-Za-z]+",
    "alnum": r"[A-Za-z0-9]+",
    "upper": r"[A-Z]+",
    "lower": r"[a-z]+",
    "nonspace": r"\S+",
}


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode()).hexdigest()


def require(condition, label):
    if not condition:
        raise ValueError(label)


def units(value):
    return max(1, (len(str(value).encode()) + 31) // 32)


def text_run(program, source, config):
    require(
        type(program) is dict
        and set(program) == {"version", "expr"}
        and program["version"] == "safe-text-v1",
        "Text program envelope changed",
    )
    count = 0

    def validate(node, depth):
        nonlocal count
        count += 1
        require(
            count <= config["max_ops"]
            and depth <= 8
            and type(node) is dict
            and type(node.get("op")) is str,
            "Text syntax exceeds bounds",
        )
        name, keys, children = node["op"], {"op"}, []
        if name == "field":
            keys.add("index")
            require(
                type(node.get("index")) is int
                and 0 <= node["index"] < config["max_fields"],
                "Invalid text field",
            )
        elif name == "constant":
            keys.add("value")
            require(
                type(node.get("value")) is str and len(node["value"].encode()) <= 256,
                "Invalid text constant",
            )
        elif name == "concat":
            keys.add("parts")
            require(
                type(node.get("parts")) is list
                and 2 <= len(node["parts"]) <= config["max_parts"],
                "Invalid text concatenation",
            )
            children = node["parts"]
        elif name == "if_equal":
            keys |= {"field", "value", "then", "else"}
            require(
                type(node.get("field")) is int
                and 0 <= node["field"] < config["max_fields"],
                "Invalid conditional field",
            )
            require(
                type(node.get("value")) is str and len(node["value"].encode()) <= 128,
                "Invalid conditional constant",
            )
            children = [node.get("then"), node.get("else")]
        else:
            keys.add("arg")
            children = [node.get("arg")]
            if name == "slice":
                keys |= {"start", "end"}
                require(
                    type(node.get("start")) is int and -512 <= node["start"] <= 512,
                    "Invalid slice",
                )
                require(
                    node.get("end") is None
                    or type(node["end"]) is int
                    and -512 <= node["end"] <= 512,
                    "Invalid slice end",
                )
            elif name == "token":
                keys |= {"pattern", "index"}
                require(
                    node.get("pattern") in PATTERNS
                    and type(node.get("index")) is int
                    and -8 <= node["index"] <= 7,
                    "Invalid registered token",
                )
            elif name == "split":
                keys |= {"separator", "index"}
                require(
                    type(node.get("separator")) is str
                    and 1 <= len(node["separator"].encode()) <= 8
                    and type(node.get("index")) is int
                    and -8 <= node["index"] <= 7,
                    "Invalid split",
                )
            elif name == "replace":
                keys |= {"old", "new"}
                require(
                    type(node.get("old")) is str
                    and 1 <= len(node["old"].encode()) <= 16
                    and type(node.get("new")) is str
                    and len(node["new"].encode()) <= 16,
                    "Invalid replacement",
                )
            elif name == "number":
                keys |= {"scale", "places"}
                require(
                    node.get("scale") in ("1", "100", "0.01", "1000", "0.001"),
                    "Invalid numeric scale",
                )
                require(
                    node.get("places") is None
                    or type(node["places"]) is int
                    and 0 <= node["places"] <= 6,
                    "Invalid decimal places",
                )
            else:
                require(
                    name
                    in ("strip", "lower", "upper", "title", "capitalize", "compact"),
                    "Unregistered text operation",
                )
        require(set(node) == keys, "Text operation fields changed")
        for child in children:
            validate(child, depth + 1)

    validate(program["expr"], 0)
    require(
        type(source) is str and len(source.encode()) <= config["max_input_bytes"],
        "Text source exceeds bounds",
    )
    work = units(source)

    def execute(node, values):
        nonlocal work
        work += 1 + units(node["op"])
        name = node["op"]
        if name == "field":
            answer = values[node["index"]]
        elif name == "constant":
            answer = node["value"]
        elif name == "concat":
            answer = "".join(execute(child, values) for child in node["parts"])
        elif name == "if_equal":
            work += units(values[node["field"]])
            answer = execute(
                node["then"]
                if values[node["field"]] == node["value"]
                else node["else"],
                values,
            )
        else:
            value = execute(node["arg"], values)
            work += units(value)
            if name in ("strip", "lower", "upper", "title", "capitalize"):
                answer = {
                    "strip": str.strip,
                    "lower": str.lower,
                    "upper": str.upper,
                    "title": str.title,
                    "capitalize": str.capitalize,
                }[name](value)
            elif name == "compact":
                answer = "".join(value.split())
            elif name == "slice":
                answer = value[node["start"] : node["end"]]
            elif name == "token":
                answer = re.findall(PATTERNS[node["pattern"]], value)[node["index"]]
            elif name == "split":
                answer = value.split(node["separator"])[node["index"]]
            elif name == "replace":
                answer = value.replace(node["old"], node["new"])
            else:
                require(len(value) <= 80, "Numeric source exceeds bounds")
                number = Decimal(value.replace(",", ""))
                require(
                    number.is_finite()
                    and number.copy_abs() <= Decimal("1e18")
                    and -24 <= number.as_tuple().exponent <= 24
                    and len(number.as_tuple().digits) <= 32,
                    "Numeric text exceeds bounds",
                )
                with localcontext() as arithmetic:
                    arithmetic.prec = 64
                    number *= Decimal(node["scale"])
                    if node["places"] is not None:
                        number = number.quantize(Decimal(1).scaleb(-node["places"]))
                    answer = format(number, "f")
                    if node["places"] is None and "." in answer:
                        answer = answer.rstrip("0").rstrip(".")
        work += units(answer)
        require(
            type(answer) is str and len(answer.encode()) <= config["max_input_bytes"],
            "Text result exceeds bounds",
        )
        return answer

    try:
        values = json.loads(source)
        require(
            type(values) is list
            and 1 <= len(values) <= config["max_fields"]
            and all(
                type(value) is str and len(value.encode()) <= config["max_input_bytes"]
                for value in values
            ),
            "Invalid source fields",
        )
        return execute(program["expr"], values), work
    except (ValueError, IndexError, KeyError, InvalidOperation):
        return None, work


def outcome(program, inputs, expected, config):
    if config["version"] == "safe-expression-v1":
        from scripts.verify_skill_expression_results import run
    else:
        run = text_run
    outputs, execution_work = [], 0
    for source in inputs:
        if program is None:
            output, work = None, 0
        else:
            output, work = run(program, source, config)
        outputs.append(output)
        execution_work += work
    comparison_work = sum(
        1
        + (
            (len(actual.encode()) if actual is not None else 0)
            + len(target.encode())
            + 31
        )
        // 32
        for actual, target in zip(outputs, expected, strict=True)
    )
    correct = sum(
        actual == target for actual, target in zip(outputs, expected, strict=True)
    )
    return {
        "outputs": outputs,
        "correct": correct,
        "rows": len(expected),
        "passed": program is not None and correct == len(expected),
        "execution_work": execution_work,
        "comparison_work": comparison_work,
        "total_work": execution_work + comparison_work,
    }


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "run.json").read_bytes())
    for name, expected in manifest["source_hashes"].items():
        require(
            hashlib.sha256((directory / "sources" / name).read_bytes()).hexdigest()
            == expected,
            "Evaluated source snapshot changed",
        )
    require(
        json.loads((directory / "execution-status.json").read_bytes())["complete"],
        "Run did not complete",
    )
    jobs = {job["id"]: job for job in manifest["jobs"]}
    summaries, seen, learned = defaultdict(lambda: defaultdict(float)), set(), {}
    for method, initial in manifest["initial_libraries"].items():
        learned[method] = set(initial["programs"])
    with (directory / "traces.jsonl").open() as stream:
        for line in stream:
            record = json.loads(line)
            method, job = record["method"], jobs[record["job_id"]]
            require((method, job["id"]) not in seen, "Duplicate job result")
            seen.add((method, job["id"]))
            require(
                set(record["programs_before"]) == learned[method],
                "Retained history changed before job",
            )
            require(
                record["retain"]
                == (
                    method != "frozen" or job["phase"] in ("acquisition", "development")
                ),
                "Frozen control changed retention policy",
            )
            for index in record["probe_indices"]:
                require(
                    type(index) is int and 0 <= index < len(job["diagnostic_inputs"]),
                    "Probe selected a scoring row",
                )
            expected_probes = [
                job["diagnostic_inputs"][index] for index in record["probe_indices"]
            ]
            expected_labels = [
                job["diagnostic_outputs"][index] for index in record["probe_indices"]
            ]
            require(
                record["probe_inputs"] == expected_probes
                and record["probe_outputs"] == expected_labels,
                "Probe evidence differs from source",
            )
            require(
                len(record["probe_indices"]) <= manifest["limits"]["maximum_probes"]
                and len(set(expected_probes)) == len(expected_probes),
                "Probe allowance or unique-input evidence changed",
            )
            if record["synthesis_training_sha256"] is not None:
                if method.startswith("fixed"):
                    order = list(range(len(record["probe_indices"])))
                else:
                    order = sorted(
                        range(len(record["probe_indices"])),
                        key=lambda index: record["probe_indices"][index],
                    )
                visible_inputs = [expected_probes[index] for index in order]
                visible_outputs = [expected_labels[index] for index in order]
                examples = (
                    [visible_inputs, visible_outputs]
                    if manifest["config"]["version"] == "safe-text-v1"
                    else {"inputs": visible_inputs, "outputs": visible_outputs}
                )
                require(
                    record["synthesis_training_sha256"] == digest(examples),
                    "Synthesis consumed examples outside acquired probes",
                )
            expected_probe_work = sum(
                1 + (len(value.encode()) + len(label.encode()) + 31) // 32
                for value, label in zip(expected_probes, expected_labels, strict=True)
            )
            require(
                record["controller_work"]["probe_work"] == expected_probe_work,
                "Probe disclosure work omitted",
            )
            checked = outcome(
                record["program"],
                job["score_inputs"],
                job["score_outputs"],
                manifest["config"],
            )
            require(
                checked == record["checked"],
                "Independent scored outputs or work disagree",
            )
            if record["selection"] == "retained":
                require(
                    digest(record["program"]) in learned[method],
                    "Selected skill was never verified",
                )
            if record["admission"] is not None:
                receipt = record["admission"]
                ceiling = (
                    2048
                    if manifest["config"]["version"] == "safe-text-v1"
                    else manifest["config"]["max_rows"]
                )
                require(
                    type(receipt["total_rows"]) is int
                    and 1 <= receipt["total_rows"] <= ceiling,
                    "Buyer receipt row ceiling changed",
                )
                require(
                    receipt["accepted"] == checked["passed"]
                    and receipt["program_sha256"] == digest(record["program"])
                    and receipt["work"] == checked["total_work"]
                    and receipt["source_id"] == manifest["source_id"],
                    "Buyer admission differs from checked outcome",
                )
                if (
                    checked["passed"]
                    and record["retain"]
                    and len(learned[method]) < manifest["config"]["max_programs"]
                ):
                    learned[method].add(digest(record["program"]))
            require(
                set(record["programs_after"]) == learned[method],
                "Library learned without a verified admitted result",
            )
            require(
                record["total_work"]
                == record["controller_work"]["total"]
                + checked["total_work"]
                + record["administration_work"],
                "Total work components disagree",
            )
            require(
                math.isfinite(record["cpu_seconds"]) and record["cpu_seconds"] >= 0,
                "Invalid process CPU",
            )
            summary = summaries[method]
            summary["jobs"] += 1
            summary["exact_successes"] += checked["passed"]
            summary["probes"] += len(record["probe_indices"])
            summary["total_work"] += record["total_work"]
            summary["cpu_seconds"] += record["cpu_seconds"]
    require(len(seen) == len(jobs) * len(manifest["methods"]), "Missing job results")
    result = json.loads((directory / "result.json").read_bytes())
    for method, summary in summaries.items():
        for key, value in summary.items():
            require(
                math.isclose(
                    value, result["methods"][method][key], rel_tol=1e-12, abs_tol=1e-12
                ),
                "Published method totals changed",
            )
        final = json.loads((directory / f"library-{method}.json").read_bytes())
        require(
            set(final["programs"]) == learned[method],
            "Final retained checkpoint differs",
        )
    return {
        "passed": True,
        "records": len(seen),
        "jobs": len(jobs),
        "methods": len(manifest["methods"]),
        "scope": "Independent data-only execution and selected-label/admission arithmetic; search and physical CPU are not independently witnessed",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2))


if __name__ == "__main__":
    main()

"""Versioned task contracts and deterministic buyer-side delivery checks."""

import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from commerce.ledger import canonical

IDENT = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
KINDS = ("agent", "human", "compute")
ACTIONS = ("explore", "predict", "experiment", "compare")


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def text(value, maximum):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("Task text is missing or oversized")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("Task text contains control characters")
    if re.search(
        r"(?i)(wallet-auth:|BEGIN .*PRIVATE KEY|api[_-]?key=|secret=|token=)", value
    ):
        raise ValueError("Private authorization does not belong in a task")
    return value


def validate_task(task):
    required = {
        "id",
        "title",
        "purpose",
        "brief",
        "kind",
        "provider",
        "action",
        "maximum_micro_usd",
        "delivery_seconds",
        "acceptance",
        "evaluation_nonce",
    }
    if not required <= set(task) or set(task) - required not in (set(), {"outcome"}):
        raise ValueError("Task contract fields are invalid")
    for key in ("id", "provider"):
        if not isinstance(task[key], str) or not IDENT.fullmatch(task[key]):
            raise ValueError("Task identity is invalid")
    if not isinstance(task["evaluation_nonce"], str) or not HASH.fullmatch(
        task["evaluation_nonce"]
    ):
        raise ValueError("A private random evaluation nonce is required")
    for key, maximum in (("title", 200), ("purpose", 500), ("brief", 5000)):
        text(task[key], maximum)
    if task["kind"] not in KINDS or task["action"] not in ACTIONS:
        raise ValueError("Task capability is unsupported")
    if (
        type(task["maximum_micro_usd"]) is not int
        or task["maximum_micro_usd"] < 0
        or task["maximum_micro_usd"] == 0
        and (
            task["kind"] != "compute"
            or task.get("acceptance", {}).get("type")
            not in ("transform_program", "transform_execution")
        )
    ):
        raise ValueError("Task needs a finite total cost ceiling")
    if (
        type(task["delivery_seconds"]) is not int
        or not 60 <= task["delivery_seconds"] <= 604800
    ):
        raise ValueError("Task delivery horizon is invalid")
    rule = task["acceptance"]
    if not isinstance(rule, dict):
        raise ValueError("Task acceptance is missing")
    if rule.get("type") == "predictions":
        if set(rule) != {
            "type",
            "dataset_sha256",
            "targets",
            "baseline",
            "minimum_gain",
        }:
            raise ValueError("Prediction evaluation fields are invalid")
        targets, baseline = rule["targets"], rule["baseline"]
        if (
            not isinstance(targets, list)
            or not isinstance(baseline, list)
            or not 8 <= len(targets) <= 10000
            or len(targets) != len(baseline)
        ):
            raise ValueError("Held-out evaluation size is invalid")
        if not isinstance(rule["dataset_sha256"], str) or not HASH.fullmatch(
            rule["dataset_sha256"]
        ):
            raise ValueError("Dataset commitment is invalid")
        if any(type(v) is not int or v not in (0, 1) for v in targets) or any(
            not finite(p) or not 0 <= p <= 1 for p in baseline
        ):
            raise ValueError("Held-out evaluation values are invalid")
        if not finite(rule["minimum_gain"]) or not 0 <= rule["minimum_gain"] <= 1:
            raise ValueError("Acceptance gain is invalid")
    elif rule.get("type") == "transform_program":
        from cognition.skills import normalize_config

        if set(rule) != {
            "type",
            "config",
            "training_inputs",
            "training_outputs",
            "validation_inputs",
            "validation_outputs",
            "heldout_inputs",
            "heldout_outputs",
            "maximum_work",
        }:
            raise ValueError("Transform-program evaluation fields are invalid")
        config = normalize_config(rule["config"])
        for name, minimum, maximum in (
            ("training_inputs", 2, config["max_rows"]),
            ("training_outputs", 2, config["max_rows"]),
            ("validation_inputs", 1, config["max_rows"]),
            ("validation_outputs", 1, config["max_rows"]),
            ("heldout_inputs", 8, config["max_rows"]),
            ("heldout_outputs", 8, config["max_rows"]),
        ):
            values = rule[name]
            if (
                type(values) is not list
                or not minimum <= len(values) <= maximum
                or any(
                    type(value) is not str
                    or len(value.encode()) > config["max_input_bytes"]
                    for value in values
                )
            ):
                raise ValueError("Transform-program rows exceed their typed bounds")
        if (
            len(rule["training_inputs"]) != len(rule["training_outputs"])
            or len(rule["validation_inputs"]) != len(rule["validation_outputs"])
            or len(rule["heldout_inputs"]) != len(rule["heldout_outputs"])
            or type(rule["maximum_work"]) is not int
            or not 1 <= rule["maximum_work"] <= 10_000_000
            or set(rule["training_inputs"]) & set(rule["heldout_inputs"])
            or set(rule["training_inputs"]) & set(rule["validation_inputs"])
            or set(rule["validation_inputs"]) & set(rule["heldout_inputs"])
            or rule["maximum_work"] <= 3 * transform_verifier_ceiling(config)
        ):
            raise ValueError(
                "Transform-program training and evaluation split is invalid"
            )
    elif rule.get("type") == "transform_execution":
        from cognition.skills import SOURCE, normalize_config

        if set(rule) != {
            "type",
            "config",
            "source_id",
            "skill_id",
            "inputs",
            "expected_outputs",
            "maximum_work",
        }:
            raise ValueError("Retained-skill execution fields are invalid")
        config = normalize_config(rule["config"])
        if (
            task["kind"] != "compute"
            or task["maximum_micro_usd"] != 0
            or type(rule["source_id"]) is not str
            or not SOURCE.fullmatch(rule["source_id"])
            or type(rule["skill_id"]) is not str
            or not HASH.fullmatch(rule["skill_id"])
            or type(rule["inputs"]) is not list
            or type(rule["expected_outputs"]) is not list
            or not 1 <= len(rule["inputs"]) <= config["max_rows"]
            or len(rule["inputs"]) != len(rule["expected_outputs"])
            or any(
                type(value) is not str
                or len(value.encode()) > config["max_input_bytes"]
                for value in rule["inputs"] + rule["expected_outputs"]
            )
            or type(rule["maximum_work"]) is not int
            or not execution_verifier_ceiling(config)
            < rule["maximum_work"]
            <= 10_000_000
        ):
            raise ValueError(
                "Retained-skill execution source, rows or work allowance is invalid"
            )
    elif rule.get("type") == "document":
        if set(rule) != {"type", "required_sections", "minimum_characters"}:
            raise ValueError("Document evaluation fields are invalid")
        if (
            not isinstance(rule["required_sections"], list)
            or not 1 <= len(rule["required_sections"]) <= 16
        ):
            raise ValueError("Document sections are missing")
        for section in rule["required_sections"]:
            text(section, 80)
        if (
            type(rule["minimum_characters"]) is not int
            or not 1 <= rule["minimum_characters"] <= 100000
        ):
            raise ValueError("Document length is invalid")
    else:
        raise ValueError("A reviewed delivery verifier is required")
    if "outcome" in task:
        outcome = task["outcome"]
        if (
            set(outcome)
            != {
                "type",
                "endpoint",
                "expected_sha256",
                "samples",
                "interval_seconds",
                "maximum_latency_ms",
            }
            or outcome["type"] != "http_availability"
        ):
            raise ValueError("Outcome measurement contract is unsupported")
        url = urlsplit(outcome["endpoint"])
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.port not in (None, 443)
            or not HASH.fullmatch(outcome["expected_sha256"])
        ):
            raise ValueError("Outcome probe needs a pinned public resource")
        if (
            type(outcome["samples"]) is not int
            or not 3 <= outcome["samples"] <= 60
            or type(outcome["interval_seconds"]) is not int
            or not 60 <= outcome["interval_seconds"] <= 3600
            or not finite(outcome["maximum_latency_ms"])
            or not 1 <= outcome["maximum_latency_ms"] <= 12000
        ):
            raise ValueError("Outcome observation window is invalid")
    return task


def contractor_brief(task, job_id):
    """Public contract only: held-out targets and baseline stay with the buyer."""
    rule = task["acceptance"]
    acceptance = {
        k: v
        for k, v in rule.items()
        if k
        not in (
            "targets",
            "baseline",
            "heldout_inputs",
            "heldout_outputs",
            "validation_inputs",
            "validation_outputs",
            "expected_outputs",
        )
    }
    return {
        "schema": "nuria.task.v1",
        "job_id": job_id,
        "contract_sha256": digest(task),
        "title": task["title"],
        "purpose": task["purpose"],
        "brief": task["brief"],
        "kind": task["kind"],
        "delivery_seconds": task["delivery_seconds"],
        "acceptance": acceptance,
        "delivery_schema": "nuria.task-result.v1",
        "outcome": task.get("outcome"),
    }


def verify_delivery(task, job_id, raw):
    if not isinstance(raw, bytes) or len(raw) > 262144:
        raise ValueError("Task delivery is missing or oversized")
    value = json.loads(raw)
    if (
        set(value) != {"schema", "job_id", "contract_sha256", "result"}
        or value["schema"] != "nuria.task-result.v1"
        or value["job_id"] != job_id
        or value["contract_sha256"] != digest(task)
    ):
        raise ValueError("Delivery is not bound to this task")
    rule, result = task["acceptance"], value["result"]
    if rule["type"] == "transform_execution":
        if (
            type(result) is not dict
            or set(result)
            != {
                "status",
                "outputs",
                "skill_id",
                "program_sha256",
                "source_id",
                "inputs_sha256",
                "work",
            }
            or result["source_id"] != rule["source_id"]
            or result["skill_id"] != rule["skill_id"]
            or result["inputs_sha256"] != digest(rule["inputs"])
            or result["status"]
            not in ("served", "skill_missing", "failed", "work_limit")
            or type(result["outputs"]) is not list
            or len(result["outputs"]) != len(rule["inputs"])
            or any(
                output is not None
                and (
                    type(output) is not str
                    or len(output.encode()) > rule["config"]["max_input_bytes"]
                )
                for output in result["outputs"]
            )
            or result["status"] == "served"
            and result["program_sha256"] != rule["skill_id"]
            or result["program_sha256"] is not None
            and (
                type(result["program_sha256"]) is not str
                or not HASH.fullmatch(result["program_sha256"])
            )
        ):
            raise ValueError(
                "Retained-skill execution artifact is malformed or unbound"
            )
        observed_work = transform_work(result["work"])
        verifier_work = sum(
            1
            + (
                (len(actual.encode()) if actual is not None else 0)
                + len(expected.encode())
                + 31
            )
            // 32
            for actual, expected in zip(
                result["outputs"], rule["expected_outputs"], strict=True
            )
        )
        if (
            observed_work
            > rule["maximum_work"] - execution_verifier_ceiling(rule["config"])
            or observed_work + verifier_work > rule["maximum_work"]
        ):
            raise ValueError(
                "Retained-skill execution exceeded its committed compute allowance"
            )
        correct = sum(
            actual == expected
            for actual, expected in zip(
                result["outputs"], rule["expected_outputs"], strict=True
            )
        )
        rows = len(rule["inputs"])
        baseline = (
            sum(
                value == expected
                for value, expected in zip(
                    rule["inputs"], rule["expected_outputs"], strict=True
                )
            )
            / rows
        )
        candidate = correct / rows
        passed = result["status"] == "served" and correct == rows
        utility = candidate - baseline if result["status"] == "served" else -1
        return {
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "passed": passed,
            "utility": max(
                -1,
                utility - 0.01 * (observed_work + verifier_work) / rule["maximum_work"],
            ),
            "metric": "verified_retained_skill_execution",
            "baseline": baseline,
            "candidate": candidate,
            "exact_rows": correct,
            "evaluation_rows": rows,
            "skill_id": rule["skill_id"],
            "program_sha256": result["program_sha256"],
            "source_id": rule["source_id"],
            "inputs_sha256": result["inputs_sha256"],
            "execution_status": result["status"],
            "execution_work": observed_work,
            "solver_work": 0,
            "verifier_work": verifier_work,
            "total_work": observed_work + verifier_work,
            "maximum_work": rule["maximum_work"],
            "evaluation_sha256": digest(
                {
                    "inputs": rule["inputs"],
                    "outputs": rule["expected_outputs"],
                    "nonce": task["evaluation_nonce"],
                }
            ),
            "scope": "Buyer checked actual outputs from a previously verified retained program on an unlabelled request. No synthesis, target access, financial payment or fallback repair is authorized.",
        }
    if rule["type"] == "transform_program":
        from cognition.skills import execute_checked, validate_program

        if (
            type(result) is not dict
            or set(result) != {"program", "proposal_id", "work"}
            or result["program"] is not None
            and (
                type(result["proposal_id"]) is not str
                or not HASH.fullmatch(result["proposal_id"])
            )
            or result["program"] is None
            and result["proposal_id"] is not None
            or type(result["work"]) is not dict
        ):
            raise ValueError("Transform-program artifact is malformed")
        program = result["program"]
        solver_work = transform_work(result["work"])
        if solver_work > rule["maximum_work"] - transform_verifier_ceiling(
            rule["config"]
        ):
            raise ValueError(
                "Transform-program solver exceeded its committed work ceiling"
            )
        predicted, verifier_work = [], 0
        if program is not None:
            validate_program(program, rule["config"])
            for row in rule["heldout_inputs"]:
                observed = execute_checked(program, row, rule["config"])
                predicted.append(observed["output"])
                verifier_work += transform_work(observed["work"])
        if solver_work + verifier_work > rule["maximum_work"]:
            raise ValueError(
                "Transform-program verification exceeded its committed work ceiling"
            )
        correct = sum(
            actual == expected
            for actual, expected in zip(
                predicted, rule["heldout_outputs"], strict=False
            )
        )
        count = len(rule["heldout_inputs"])
        baseline = (
            sum(
                original == expected
                for original, expected in zip(
                    rule["heldout_inputs"], rule["heldout_outputs"], strict=True
                )
            )
            / count
        )
        accuracy = correct / count
        return {
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "passed": program is not None and correct == count,
            "utility": max(
                -1,
                accuracy
                - baseline
                - 0.01 * (solver_work + verifier_work) / rule["maximum_work"],
            ),
            "metric": "held_out_exact_transform_gain",
            "baseline": baseline,
            "candidate": accuracy,
            "exact_rows": correct,
            "evaluation_rows": count,
            "program_sha256": digest(program) if program is not None else None,
            "proposal_id": result["proposal_id"],
            "verifier_work": verifier_work,
            "solver_work": solver_work,
            "total_work": solver_work + verifier_work,
            "maximum_work": rule["maximum_work"],
            "evaluation_sha256": digest(
                {
                    "inputs": rule["heldout_inputs"],
                    "outputs": rule["heldout_outputs"],
                    "nonce": task["evaluation_nonce"],
                }
            ),
            "scope": "Buyer executed the bounded declarative program on withheld rows. Utility is exact accuracy gain over unchanged input minus 0.01 times the used fraction of committed compute. Work units are not money or neural superiority.",
        }
    if rule["type"] == "predictions":
        if (
            not isinstance(result, list)
            or len(result) != len(rule["targets"])
            or any(not finite(p) or not 0 <= p <= 1 for p in result)
        ):
            raise ValueError("Prediction artifact is malformed")
        targets = rule["targets"]
        loss = sum((p - y) ** 2 for p, y in zip(result, targets, strict=True)) / len(
            targets
        )
        baseline = sum(
            (p - y) ** 2 for p, y in zip(rule["baseline"], targets, strict=True)
        ) / len(targets)
        gain = baseline - loss
        return {
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "passed": gain >= rule["minimum_gain"],
            "utility": max(-1, min(1, gain)),
            "metric": "held_out_brier_gain",
            "baseline": baseline,
            "candidate": loss,
            "scope": "Buyer-owned held-out result; no executable delivery is run",
        }
    if (
        not isinstance(result, dict)
        or set(result) != {"text"}
        or not isinstance(result["text"], str)
    ):
        raise ValueError("Document artifact is malformed")
    content = result["text"]
    passed = len(content) >= rule["minimum_characters"] and all(
        section.casefold() in content.casefold()
        for section in rule["required_sections"]
    )
    return {
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "passed": passed,
        "utility": None,
        "metric": "document_structure",
        "scope": "Format and delivery only; usefulness requires a separate measured outcome. No automatic positive learning credit.",
    }


def transform_verifier_ceiling(config):
    """Worst-case fixed-registry buyer work, reserved before synthesis."""
    blocks = (config["max_input_bytes"] + 31) // 32
    if config["version"] == "safe-expression-v1":
        blocks = 2 * ((max(config["max_input_bytes"], 97) + 31) // 32)
    return config["max_rows"] * config["max_ops"] * (1 + blocks)


def transform_work(report):
    if (
        type(report) is not dict
        or type(report.get("total")) is not int
        or not 0 <= report["total"] <= 10_000_000
    ):
        raise ValueError("Transform-program work report is malformed")
    return report["total"]


def execution_verifier_ceiling(config):
    return config["max_rows"] * (1 + 2 * ((config["max_input_bytes"] + 31) // 32))

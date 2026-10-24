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
            not in ("transform_program", "transform_execution", "transform_active")
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
    elif rule.get("type") == "transform_active":
        from cognition.skill_language import normalize_config

        expected_fields = {
            "type",
            "config",
            "diagnostic_inputs",
            "diagnostic_outputs",
            "heldout_inputs",
            "heldout_outputs",
            "maximum_work",
            "maximum_probes",
            "synthesis_work",
            "minimum_evidence",
        }
        composed = rule.get("policy") == "composed_fixed4"
        if composed:
            expected_fields |= {"policy", "source_id"}
        if set(rule) != expected_fields:
            raise ValueError("Active-transform evaluation fields are invalid")
        config = normalize_config(rule["config"])
        if (
            task["kind"] != "compute"
            or task["maximum_micro_usd"] != 0
            or task.get("outcome")
            or config["version"] != "safe-expression-v1"
            and not composed
        ):
            raise ValueError("Active transforms require qualified included compute")
        if composed:
            from commerce.composed_skill_runtime import validate_composed_authority

            validate_composed_authority(rule, task["provider"], rule["source_id"])
        for name, minimum in (
            ("diagnostic_inputs", 2),
            ("diagnostic_outputs", 2),
            ("heldout_inputs", 8),
            ("heldout_outputs", 8),
        ):
            values = rule[name]
            if (
                type(values) is not list
                or not minimum <= len(values) <= config["max_rows"]
                or any(
                    type(value) is not str
                    or len(value.encode()) > config["max_input_bytes"]
                    for value in values
                )
            ):
                raise ValueError("Active-transform rows exceed their typed bounds")
        if (
            len(rule["diagnostic_inputs"]) != len(rule["diagnostic_outputs"])
            or len(rule["heldout_inputs"]) != len(rule["heldout_outputs"])
            or len(set(rule["diagnostic_inputs"])) != len(rule["diagnostic_inputs"])
            or set(rule["diagnostic_inputs"]) & set(rule["heldout_inputs"])
            or type(rule["maximum_probes"]) is not int
            or not 1 <= rule["maximum_probes"] <= len(rule["diagnostic_inputs"])
            or type(rule["minimum_evidence"]) is not int
            or not 1 <= rule["minimum_evidence"] <= rule["maximum_probes"]
            or type(rule["synthesis_work"]) is not int
            or not 1 <= rule["synthesis_work"] <= 1_000_000
            or type(rule["maximum_work"]) is not int
            or not active_verifier_ceiling(config) < rule["maximum_work"] <= 10_000_000
        ):
            raise ValueError("Active-transform split, probes or work is invalid")
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
            "diagnostic_outputs",
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
    if rule["type"] == "transform_active":
        return verify_active_delivery(task, job_id, raw, result)
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


def active_probe_work(value, output):
    """Charge the disclosed input/label bytes, including a probe operation."""
    return 1 + (len(value.encode()) + len(output.encode()) + 31) // 32


def active_probe_receipt(task, job_id, action):
    """Buyer interface: reveal exactly one explicitly requested diagnostic label.

    This function never reads the final evaluation partition. Its result is
    committed by the execution adapter before the routing session consumes it.
    """
    rule = task["acceptance"]
    index = action.get("input_index")
    if (
        rule["type"] != "transform_active"
        or action.get("kind") != "probe"
        or type(action.get("id")) is not str
        or not HASH.fullmatch(action["id"])
        or type(index) is not int
        or not 0 <= index < len(rule["diagnostic_inputs"])
        or action.get("input") != rule["diagnostic_inputs"][index]
        or action.get("input_sha256") != digest(rule["diagnostic_inputs"][index])
        or action.get("maximum_output_bytes") != rule["config"]["max_input_bytes"]
        or action.get("maximum_work")
        != 1
        + (
            len(rule["diagnostic_inputs"][index].encode())
            + rule["config"]["max_input_bytes"]
            + 31
        )
        // 32
        or action["id"] != digest({k: v for k, v in action.items() if k != "id"})
    ):
        raise ValueError("Probe is not bound to one declared diagnostic input")
    value, output = rule["diagnostic_inputs"][index], rule["diagnostic_outputs"][index]
    work = active_probe_work(value, output)
    identity = digest(
        {
            "job_id": job_id,
            "contract_sha256": digest(task),
            "action_id": action["id"],
            "input_index": index,
            "input_sha256": digest(value),
            "output": output,
            "work": work,
            "nonce": task["evaluation_nonce"],
        }
    )
    return {
        "input_index": index,
        "input_sha256": digest(value),
        "output": output,
        "receipt_id": identity,
        "work": work,
    }


def verify_active_delivery(task, job_id, raw, result):
    """Check the committed selection on untouched rows, without repair feedback."""
    from cognition.skill_language import execute_checked, validate_program
    from cognition.skills import SOURCE

    rule = task["acceptance"]
    if rule["config"].get("version") == "safe-text-v1":
        from commerce.composed_skill_runtime import validate_composed_authority

        validate_composed_authority(
            rule,
            task["provider"],
            result.get("source_id") if type(result) is dict else None,
        )
    fields = {
        "status",
        "program",
        "program_sha256",
        "proposal_id",
        "selection",
        "reason",
        "work",
        "probes_used",
        "probe_receipt_sha256s",
        "source_id",
        "session_id",
        "session_sha256",
        "probe_receipts",
        "accounting_uncertain",
        "uncertain_reservation",
    }
    if (
        type(result) is not dict
        or set(result) != fields
        or result["status"] not in ("proposed", "abstained")
        or type(result["reason"]) is not str
        or len(result["reason"]) > 500
        or type(result["source_id"]) is not str
        or not SOURCE.fullmatch(result["source_id"])
        or type(result["session_sha256"]) is not str
        or not HASH.fullmatch(result["session_sha256"])
        or type(result["session_id"]) is not str
        or not HASH.fullmatch(result["session_id"])
        or result["accounting_uncertain"] is not False
        or type(result["uncertain_reservation"]) is not int
        or result["uncertain_reservation"] != 0
        or type(result["probe_receipts"]) is not list
        or type(result["probes_used"]) is not int
        or not 0 <= result["probes_used"] <= rule["maximum_probes"]
        or result["probes_used"] != len(result["probe_receipts"])
        or type(result["probe_receipt_sha256s"]) is not list
    ):
        raise ValueError("Active-transform selection artifact is malformed")
    probe_work, indices, receipts = 0, set(), []
    for record in result["probe_receipts"]:
        if (
            type(record) is not dict
            or set(record) != {"action", "receipt"}
            or type(record["action"]) is not dict
            or type(record["receipt"]) is not dict
        ):
            raise ValueError("Active-transform probe evidence is malformed")
        expected = active_probe_receipt(task, job_id, record["action"])
        expected["source_id"] = result["source_id"]
        if (
            record["receipt"] != expected
            or expected["input_index"] in indices
            or record["action"].get("session_id") != result["session_id"]
        ):
            raise ValueError("Active-transform probe evidence changed or repeated")
        indices.add(expected["input_index"])
        receipts.append(digest(expected))
        probe_work += expected["work"]
    if sorted(receipts) != sorted(result["probe_receipt_sha256s"]):
        raise ValueError("Active-transform probe commitments disagree")
    program = result["program"]
    proposed = result["status"] == "proposed"
    if (
        proposed
        and (
            program is None
            or result["probes_used"] < rule["minimum_evidence"]
            or result["selection"] not in ("retained", "synthesized")
            or result["program_sha256"] != digest(program)
            or result["proposal_id"] is not None
            and (
                type(result["proposal_id"]) is not str
                or not HASH.fullmatch(result["proposal_id"])
            )
            or result["selection"] == "synthesized"
            and (
                type(result["proposal_id"]) is not str
                or not HASH.fullmatch(result["proposal_id"])
            )
        )
        or not proposed
        and any(
            result[key] is not None
            for key in ("program", "program_sha256", "proposal_id", "selection")
        )
    ):
        raise ValueError("Active-transform terminal choice is inconsistent")
    routing_work = transform_work(result["work"])
    components = ("portfolio_work", "routing_work", "probe_work", "synthesis_work")
    if (
        routing_work < probe_work
        or any(
            type(result["work"].get(key)) is not int
            or not 0 <= result["work"][key] <= 10_000_000
            for key in components
        )
        or sum(result["work"][key] for key in components) != routing_work
        or result["work"]["probe_work"] != probe_work
        or result["work"]["synthesis_work"] > rule["synthesis_work"]
        or result["work"].get("probe_requests") != result["probes_used"]
        or routing_work > rule["maximum_work"] - active_verifier_ceiling(rule["config"])
    ):
        raise ValueError("Active-transform routing exceeded its reserved allowance")
    predicted, verifier_work = [], 0
    if proposed:
        validate_program(program, rule["config"])
        for row in rule["heldout_inputs"]:
            checked = execute_checked(program, row, rule["config"])
            predicted.append(checked["output"])
            verifier_work += transform_work(checked["work"])
    verifier_work += sum(
        active_probe_work(value, expected)
        for value, expected in zip(
            rule["heldout_inputs"], rule["heldout_outputs"], strict=True
        )
    )
    verifier_work += sum(
        active_probe_work(value or "", expected)
        for value, expected in zip(predicted, rule["heldout_outputs"], strict=False)
    )
    if routing_work + verifier_work > rule["maximum_work"]:
        raise ValueError("Active-transform final execution exceeded its allowance")
    count = len(rule["heldout_inputs"])
    correct = sum(
        actual == expected
        for actual, expected in zip(predicted, rule["heldout_outputs"], strict=False)
    )
    baseline = (
        sum(
            value == expected
            for value, expected in zip(
                rule["heldout_inputs"], rule["heldout_outputs"], strict=True
            )
        )
        / count
    )
    accuracy = correct / count
    return {
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "passed": proposed and correct == count,
        "utility": max(
            -1,
            accuracy
            - baseline
            - 0.01 * (routing_work + verifier_work) / rule["maximum_work"],
        ),
        "metric": "active_selection_held_out_exact_gain",
        "baseline": baseline,
        "candidate": accuracy,
        "exact_rows": correct,
        "evaluation_rows": count,
        "program_sha256": result["program_sha256"],
        "proposal_id": result["proposal_id"],
        "selection": result["selection"],
        "execution_status": result["status"],
        "source_id": result["source_id"],
        "session_sha256": result["session_sha256"],
        "probes_used": result["probes_used"],
        "probe_work": probe_work,
        "probe_receipt_sha256s": receipts,
        "solver_work": routing_work,
        "verifier_work": verifier_work,
        "total_work": routing_work + verifier_work,
        "maximum_work": rule["maximum_work"],
        "evaluation_sha256": digest(
            {
                "inputs": rule["heldout_inputs"],
                "outputs": rule["heldout_outputs"],
                "nonce": task["evaluation_nonce"],
            }
        ),
        "scope": "The router selected a program using charged diagnostic labels only. The buyer then executed that fixed selection on withheld rows; final targets provide no routing or repair fallback.",
    }


def active_verifier_ceiling(config):
    """Reserve interpreter work and both final exact comparison passes."""
    from cognition.skill_language import execution_work_ceiling

    return execution_work_ceiling(
        config, config["max_rows"]
    ) + 2 * execution_verifier_ceiling(config)

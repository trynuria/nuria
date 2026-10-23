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
    if type(task["maximum_micro_usd"]) is not int or task["maximum_micro_usd"] <= 0:
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
    acceptance = {k: v for k, v in rule.items() if k not in ("targets", "baseline")}
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

"""Finite, domain-labelled evidence for a committed text-skill proposal.

The caller authenticates the buyer and chooses relevant challenge cases. Coverage
labels are caller assertions, not inferred guarantees about an input domain.
This module neither trains a controller nor admits a skill into a live library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import InvalidOperation

from cognition.skills import WorkLimit, _Meter, fingerprint
from cognition.text_skills import (
    evaluate,
    fields,
    integer,
    normalize_config,
    text,
    validate_program,
)

SCHEMA = "nuria.text-skill-qualification.v1"
_FACTORY = object()


def _serial(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    )


def _label(value):
    if type(value) is not str or not 1 <= len(value.encode()) <= 128:
        raise ValueError("Qualification labels must be bounded nonempty strings")
    return value


@dataclass(frozen=True, slots=True, init=False)
class PreparedQualification:
    """Immutable local plan; serialized hashes do not authenticate a buyer."""

    payload: str
    plan_sha256: str
    _factory: object

    def __init__(self):
        raise TypeError("Use prepare_qualification to validate a finite plan")


def prepare_qualification(
    program,
    diagnostic_inputs,
    config,
    *,
    source_id,
    domain,
    cases,
    minimum_coverage,
):
    """Bind a proposal to separate, caller-labelled challenge rows and scope.

    Source and domain identify the evidence scope; neither is an authentication
    mechanism. The caller must commit the program before obtaining these labels.
    Plans may be made after a study for diagnostic purposes, but must not then be
    described as an untouched qualification of that study.
    """
    config = normalize_config(config)
    program = validate_program(program, config)
    _label(source_id)
    _label(domain)
    if type(diagnostic_inputs) is not list or not 1 <= len(diagnostic_inputs) <= 64:
        raise ValueError("Qualification needs bounded original diagnostic inputs")
    # Canonical field arrays prevent whitespace or JSON escaping from hiding a
    # duplicate or a previously disclosed input.
    disclosed = [_serial(fields(value, config)) for value in diagnostic_inputs]
    if len(set(disclosed)) != len(disclosed):
        raise ValueError("Diagnostic inputs must be semantically distinct")
    if type(minimum_coverage) is not dict or not 1 <= len(minimum_coverage) <= 16:
        raise ValueError("Qualification needs explicit bounded coverage requirements")
    required = {
        _label(label): integer(count, 1, 64)
        for label, count in minimum_coverage.items()
    }
    if type(cases) is not list or not 2 <= len(cases) <= 64:
        raise ValueError("Qualification needs between two and 64 distinct cases")
    seen = set(disclosed)
    clean = []
    coverage = dict.fromkeys(required, 0)
    for case in cases:
        if type(case) is not dict or set(case) != {"input", "expected", "coverage"}:
            raise ValueError("Malformed qualification case")
        value = _serial(fields(case["input"], config))
        if value in seen:
            raise ValueError("Challenge inputs repeat or overlap disclosed inputs")
        seen.add(value)
        text(value, config["max_input_bytes"])
        expected = text(case["expected"], config["max_input_bytes"])
        labels = case["coverage"]
        if (
            type(labels) is not list
            or not 1 <= len(labels) <= 16
            or any(type(label) is not str or label not in required for label in labels)
            or len(set(labels)) != len(labels)
        ):
            raise ValueError("Challenge coverage must name distinct required labels")
        for label in labels:
            coverage[label] += 1
        clean.append({"input": value, "expected": expected, "coverage": sorted(labels)})
    if any(coverage[label] < count for label, count in required.items()):
        raise ValueError("Qualification plan lacks its required case coverage")
    payload = {
        "schema": SCHEMA,
        "source_id": source_id,
        "domain": domain,
        "config": config,
        "program": program,
        "program_sha256": fingerprint(program),
        "diagnostic_inputs_sha256": fingerprint(disclosed),
        "cases": clean,
        "minimum_coverage": required,
    }
    result = object.__new__(PreparedQualification)
    object.__setattr__(result, "payload", _serial(payload))
    object.__setattr__(result, "plan_sha256", fingerprint(payload))
    object.__setattr__(result, "_factory", _FACTORY)
    return result


def qualify_text_skill(prepared, *, budget=10000):
    """Execute one committed finite plan under a single interpreter allowance.

    Every planned case must run and match. Failed cases remain in the record;
    later successes cannot erase them. Exhaustion never produces qualification.
    Hashing and plan validation are bounded host work outside the virtual meter;
    the plan's serialized bytes, interpreter operations and comparisons are
    charged below. The receipt is evidence, not a signed admission credential.
    """
    if (
        not isinstance(prepared, PreparedQualification)
        or prepared._factory is not _FACTORY
    ):
        raise ValueError("Qualification requires a prepared local plan")
    payload = json.loads(prepared.payload)
    if fingerprint(payload) != prepared.plan_sha256:
        raise ValueError("Qualification plan identity changed")
    config, program = payload["config"], payload["program"]
    meter = _Meter(budget)
    rows, status = [], "qualified_for_cases"
    coverage = dict.fromkeys(payload["minimum_coverage"], 0)
    try:
        meter.data(prepared.payload)
        for index, case in enumerate(payload["cases"]):
            output, error = None, None
            start = meter.values["total"]
            try:
                meter.data(case["input"])
                output = evaluate(
                    program["expr"], fields(case["input"], config), meter, config
                )
                meter.data(case["expected"])
                meter.charge("primitive_attempts")
            except WorkLimit:
                raise
            except (ValueError, IndexError, InvalidOperation, KeyError):
                meter.values["errors"] += 1
                error = "invalid_input"
            passed = error is None and output == case["expected"]
            rows.append(
                {
                    "case_index": index,
                    "input_sha256": fingerprint(case["input"]),
                    "expected_sha256": fingerprint(case["expected"]),
                    "output": output,
                    "error": error,
                    "passed": passed,
                    "work": meter.values["total"] - start,
                }
            )
            if passed:
                for label in case["coverage"]:
                    coverage[label] += 1
            else:
                status = "rejected"
    except WorkLimit:
        status = "work_limit"
    qualified = (
        status == "qualified_for_cases"
        and len(rows) == len(payload["cases"])
        and all(coverage[k] >= v for k, v in payload["minimum_coverage"].items())
    )
    receipt = {
        "schema": SCHEMA,
        "source_id": payload["source_id"],
        "domain": payload["domain"],
        "plan_sha256": prepared.plan_sha256,
        "program_sha256": payload["program_sha256"],
        "config_sha256": fingerprint(config),
        "diagnostic_inputs_sha256": payload["diagnostic_inputs_sha256"],
        "qualified_for_cases": qualified,
        "status": status,
        "planned_cases": len(payload["cases"]),
        "completed_cases": len(rows),
        "passed_cases": sum(row["passed"] for row in rows),
        "passed_coverage": coverage,
        "minimum_coverage": payload["minimum_coverage"],
        "rows": rows,
        "work_limit": budget,
        "work": dict(meter.values),
        "scope": "Finite caller-labelled cases; no universal domain guarantee or admission authority",
    }
    receipt["receipt_sha256"] = fingerprint(receipt)
    return receipt

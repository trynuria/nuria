"""Bounded whole-program field-template reuse in the unchanged safe text DSL.

Preparation validates an ordered accepted-program history and canonicalizes its
field references. It does not authenticate that history: the caller must bind it
to earlier buyer receipts. Serving tries legal bindings on disclosed examples,
then may spend the remaining allowance in the unchanged composition search.
A disclosed fit is a proposal, never evidence of final buyer acceptance.

The work ledger counts declared construction, validation, serialization and
interpreter operations. Configuration/source commitments and immutable-cache
integrity hashing are inspection overhead, reported separately in bytes; these
units are not a complete CPU or memory accounting.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from decimal import InvalidOperation
from itertools import product
from pathlib import Path

from cognition import skills, text_composition, text_skills
from cognition.skills import WorkLimit, _Meter, fingerprint
from cognition.text_composition import infer_composed
from cognition.text_skills import (
    VERSION as LANGUAGE_VERSION,
)
from cognition.text_skills import (
    evaluate,
    fields,
    integer,
    normalize_config,
    shape,
    text,
    validate_program,
)

VERSION = "whole-program-field-templates-v1"
MAX_HISTORY = 4096
MAX_TEMPLATES = 4096
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_PREPARATION_WORK = 7_500_000
_FACTORY = object()


def _serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _config(config):
    clean = normalize_config(config)
    if type(clean["version"]) is not str or any(
        type(value) is not int for key, value in clean.items() if key != "version"
    ):
        raise ValueError("Template language bounds require exact integers")
    return clean


def implementation_hash():
    """Bind preparation to this implementation and the unchanged interpreter."""
    paths = [
        Path(module.__file__) for module in (skills, text_skills, text_composition)
    ]
    paths.append(Path(__file__))
    return fingerprint(
        {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    )


@dataclass(frozen=True, slots=True, init=False)
class PreparedTemplates:
    """Immutable in-memory cache; external restoration is deliberately absent."""

    _payload: str
    _identity: str
    _report: str
    _report_identity: str
    _factory: object

    @property
    def report(self):
        """Return a detached report so callers cannot mutate retained metadata."""
        return json.loads(self._report)

    @property
    def preparation_sha256(self):
        return self._identity


def _canonicalize(node, meter, slots):
    meter.charge("primitive_attempts")
    result = dict(node)
    key = (
        "index"
        if node["op"] == "field"
        else "field"
        if node["op"] == "if_equal"
        else None
    )
    if key is not None:
        original = node[key]
        if original not in slots:
            slots[original] = len(slots)
        result[key] = slots[original]
    for child in ("arg", "then", "else"):
        if child in node:
            result[child] = _canonicalize(node[child], meter, slots)
    if "parts" in node:
        result["parts"] = [
            _canonicalize(child, meter, slots) for child in node["parts"]
        ]
    return result


def _instantiate(node, binding, meter):
    meter.charge("primitive_attempts")
    result = dict(node)
    key = (
        "index"
        if node["op"] == "field"
        else "field"
        if node["op"] == "if_equal"
        else None
    )
    if key is not None:
        result[key] = binding[node[key]]
    for child in ("arg", "then", "else"):
        if child in node:
            result[child] = _instantiate(node[child], binding, meter)
    if "parts" in node:
        result["parts"] = [
            _instantiate(child, binding, meter) for child in node["parts"]
        ]
    return result


def _validate_paid(program, config, meter):
    # Validate first to bound every subsequent traversal/serialization even for
    # rejected input. The counted shape inspection and bytes describe the same
    # bounded static validation operation, not an arbitrary source execution.
    clean = validate_program(program, config)
    encoded = _serial(clean)
    meter.data(encoded)
    meter.charge("primitive_attempts", shape(clean["expr"], config))
    return clean, encoded


def prepare_templates(programs, config, *, max_templates=4096):
    """Prepare ordered whole-program templates from caller-verified history.

    Repeated references retain one slot. Different original fields receive
    distinct slots, but their legal serving bindings may collapse to one field.
    Duplicate templates retain the first source program. Capacity failure raises
    with its completed preparation work retained in ``error.report``; no partial
    cache is silently substituted for the requested history.
    """
    config = _config(config)
    integer(max_templates, 1, MAX_TEMPLATES)
    if type(programs) not in (list, tuple) or len(programs) > MAX_HISTORY:
        raise ValueError("Template history must be a bounded ordered program list")
    meter = _Meter(MAX_PREPARATION_WORK)
    templates, seen, originals = [], set(), []
    duplicate_count = retained_bytes = 0
    try:
        meter.data(_serial(config))
        for program in programs:
            meter.charge("candidates")
            clean, encoded = _validate_paid(program, config, meter)
            retained_bytes += len(encoded.encode())
            if retained_bytes > MAX_CACHE_BYTES:
                raise WorkLimit("Template history byte bound reached")
            originals.append(fingerprint(clean))
            slots = {}
            node = _canonicalize(clean["expr"], meter, slots)
            template_json = _serial(node)
            meter.data(template_json)
            if template_json in seen:
                duplicate_count += 1
                meter.values["semantic_duplicates"] += 1
                continue
            if len(templates) >= max_templates:
                raise WorkLimit("Distinct template capacity reached")
            seen.add(template_json)
            templates.append(
                {
                    "expr": node,
                    "slots": len(slots),
                    "template_sha256": fingerprint(node),
                    "source_program_sha256": fingerprint(clean),
                }
            )
        payload = {
            "schema": VERSION,
            "config": config,
            "config_sha256": fingerprint(config),
            "implementation_sha256": implementation_hash(),
            "source_programs_sha256": fingerprint(originals),
            "history_programs": len(programs),
            "max_templates": max_templates,
            "templates": templates,
        }
        encoded = _serial(payload)
        if len(encoded.encode()) > MAX_CACHE_BYTES:
            raise WorkLimit("Prepared template cache byte bound reached")
        meter.data(encoded)
    except (ValueError, IndexError, KeyError, InvalidOperation) as error:
        error.report = {
            "status": "failed",
            "error_type": type(error).__name__,
            "preparation_work": dict(meter.values),
            "history_programs_seen": len(originals),
            "templates": len(templates),
        }
        raise
    identity = _sha(encoded)
    report = {
        "schema": VERSION,
        "status": "prepared",
        "preparation_sha256": identity,
        "config_sha256": payload["config_sha256"],
        "implementation_sha256": payload["implementation_sha256"],
        "source_programs_sha256": payload["source_programs_sha256"],
        "history_programs": len(programs),
        "templates": len(templates),
        "template_duplicates": duplicate_count,
        "stored_bytes": len(encoded.encode()),
        "source_program_serialized_bytes": retained_bytes,
        "preparation_work": dict(meter.values),
        "binding_policy": "all-common-fields-with-alias-preservation-and-collapse",
    }
    cache = object.__new__(PreparedTemplates)
    for name, value in (
        ("_payload", encoded),
        ("_identity", identity),
        ("_report", _serial(report)),
        ("_report_identity", fingerprint(report)),
        ("_factory", _FACTORY),
    ):
        object.__setattr__(cache, name, value)
    return cache


def _prepared_payload(prepared, config):
    if type(prepared) is not PreparedTemplates:
        raise ValueError("A prepared immutable template cache is required")
    try:
        if (
            prepared._factory is not _FACTORY
            or type(prepared._payload) is not str
            or type(prepared._report) is not str
            or len(prepared._payload.encode()) > MAX_CACHE_BYTES
            or len(prepared._report.encode()) > 16_384
            or _sha(prepared._payload) != prepared._identity
            or _sha(prepared._report) != prepared._report_identity
        ):
            raise ValueError("Template cache identity is inconsistent")
        payload, report = json.loads(prepared._payload), prepared.report
        if (
            payload["schema"] != VERSION
            or payload["config"] != config
            or payload["config_sha256"] != fingerprint(config)
            or payload["implementation_sha256"] != implementation_hash()
            or report["preparation_sha256"] != prepared._identity
            or report["stored_bytes"] != len(prepared._payload.encode())
            or report["templates"] != len(payload["templates"])
            or not 0 <= len(payload["templates"]) <= payload["max_templates"]
            or type(payload["max_templates"]) is not int
            or not 1 <= payload["max_templates"] <= MAX_TEMPLATES
        ):
            raise ValueError("Template cache source or configuration changed")
        return payload
    except (AttributeError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Template cache is malformed") from error


def solve_templates(inputs, outputs, config, prepared, *, budget=50000, fallback=True):
    """Commit the first disclosed fit under one finite serving allowance.

    Only the common disclosed field count bounds bindings. No final buyer rows,
    targets, task identifiers or history generator recipes enter this API.
    ``fallback`` invokes unchanged ``infer_composed`` with only the remainder.
    The returned program is detached from the immutable preparation.
    """
    config = _config(config)
    meter = _Meter(budget)
    if type(fallback) is not bool:
        raise ValueError("Template fallback must be an explicit boolean")
    if (
        type(inputs) is not list
        or type(outputs) is not list
        or len(inputs) != len(outputs)
    ):
        raise ValueError("Template serving needs paired disclosed example lists")
    integer(len(inputs), 1, config["max_rows"])
    rows = [fields(value, config) for value in inputs]
    targets = [text(value, config["max_input_bytes"]) for value in outputs]
    payload = _prepared_payload(prepared, config)
    common_fields = min(map(len, rows))
    attempted = checked = duplicates = binding_count = 0
    seen, seen_bytes = set(), 0
    selected = selection = None
    stop = "no_program"
    try:
        if payload["templates"]:
            for value in inputs + targets:
                meter.data(value)
        for template in payload["templates"]:
            for binding in product(range(common_fields), repeat=template["slots"]):
                if attempted >= config["max_candidates"]:
                    raise WorkLimit("Template candidate allowance reached")
                meter.charge("candidates")
                attempted += 1
                binding_count += 1
                expr = _instantiate(template["expr"], binding, meter)
                program, _ = _validate_paid(
                    {"version": LANGUAGE_VERSION, "expr": expr}, config, meter
                )
                encoded = _serial(expr)
                meter.data(encoded)
                if encoded in seen:
                    duplicates += 1
                    meter.values["semantic_duplicates"] += 1
                    continue
                candidate_bytes = len(encoded.encode())
                if seen_bytes + candidate_bytes > MAX_CACHE_BYTES:
                    raise WorkLimit("Bound-AST deduplication byte limit reached")
                seen_bytes += candidate_bytes
                seen.add(encoded)
                try:
                    fits = True
                    for row, target in zip(rows, targets, strict=True):
                        checked += 1
                        observed = evaluate(expr, row, meter, config)
                        meter.data(_serial(observed))
                        if observed != target:
                            fits = False
                            break
                except (ValueError, IndexError, KeyError, InvalidOperation) as error:
                    if isinstance(error, WorkLimit):
                        raise
                    meter.values["errors"] += 1
                    continue
                if fits:
                    selected = program
                    selection = {
                        "source": "field_templates",
                        "template_sha256": template["template_sha256"],
                        "source_program_sha256": template["source_program_sha256"],
                        "binding": list(binding),
                    }
                    break
            if selected is not None:
                break
    except WorkLimit:
        stop = "work_or_capacity_exhausted"
    template_work = dict(meter.values)
    fallback_work = None
    if selected is None and fallback and meter.values["total"] < budget:
        remainder = _Meter(budget - meter.values["total"])
        try:
            node = infer_composed(inputs, outputs, config, remainder)
            if node is not None:
                selected = validate_program(
                    {"version": LANGUAGE_VERSION, "expr": node}, config
                )
                selection = {"source": "composed_fallback"}
        except WorkLimit:
            stop = "work_or_capacity_exhausted"
        fallback_work = dict(remainder.values)
        for key in meter.values:
            meter.values[key] += fallback_work[key]
    result = {
        "schema": VERSION,
        "program": copy.deepcopy(selected),
        "status": "selected" if selected is not None else stop,
        "work": dict(meter.values),
        "template_work": template_work,
        "fallback_work": fallback_work,
        "selection_metadata": selection,
        "preparation_sha256": prepared.preparation_sha256,
        "config_sha256": fingerprint(config),
        "implementation_sha256": payload["implementation_sha256"],
        "disclosed_examples_sha256": fingerprint([inputs, targets]),
        "statistics": {
            "templates": len(payload["templates"]),
            "bindings_attempted": binding_count,
            "candidate_attempts": attempted,
            "complete_ast_duplicates": duplicates,
            "deduplication_bytes": seen_bytes,
            "disclosed_rows_checked": checked,
            "disclosed_row_count": len(inputs),
            "common_disclosed_field_count": common_fields,
            "integrity_payload_bytes": len(prepared._payload.encode()),
        },
        "buyer_accepted": None,
    }
    result["commitment"] = fingerprint(result)
    return result

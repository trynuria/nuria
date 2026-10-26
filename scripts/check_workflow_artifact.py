"""Check saved operational briefs against their enrolled source projection.

The checker imports no workflow catalog, planner or renderer. It derives its own
counts and priority order, then checks the actual Markdown bytes and embedded
facts. Reported delivery and settlement are not independent semantic acceptance
or payment evidence. A mismatch produces an identified rejection, not a retry.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

CHECKER_ID = "nuria.workflow-artifact-checker.v1"
SCOPE = "Recorded task evidence; no independent payment or semantic acceptance verification."
TITLES = {
    "provider_brief": "Provider brief",
    "payment_brief": "Payment brief",
    "audit_brief": "Audit brief",
}
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 262144


class ArtifactMismatch(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ArtifactMismatch(code)


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _text(value, maximum=256):
    require(
        type(value) is str and 0 < len(value.encode()) <= maximum and "\0" not in value,
        "source_text",
    )
    return value


def _rows(projection):
    require(
        type(projection) is dict and type(projection.get("action_tasks")) is list,
        "source_shape",
    )
    original = projection["action_tasks"]
    require(len(original) <= 10000, "source_row_limit")
    result, identities = [], set()
    for entry in original:
        require(type(entry) is dict, "source_row_shape")
        identity = _text(entry["id"])
        require(identity not in identities, "duplicate_task_id")
        identities.add(identity)
        provider = _text(entry["provider"])
        require(
            all(
                type(entry.get(key)) is dict
                for key in ("acceptance", "settlement", "outcome")
            ),
            "source_nested_shape",
        )
        delivery = entry["acceptance"]["passed"]
        utility = entry["outcome"]["utility"]
        basis = entry["settlement"]["basis"]
        amount = entry["settlement"]["total_micro_usd"]
        require(delivery is None or type(delivery) is bool, "delivery_type")
        require(
            utility is None or type(utility) in (int, float) and math.isfinite(utility),
            "utility_type",
        )
        require(
            basis is None
            or type(basis) is str
            and len(basis.encode()) <= 256
            and "\0" not in basis,
            "settlement_basis_type",
        )
        require(
            type(amount) is int and 0 <= amount <= 2**63 - 1, "settlement_amount_type"
        )
        result.append(
            {
                "id": identity,
                "provider": provider,
                "delivery": delivery,
                "utility": utility,
                "basis": basis,
                "amount": amount,
            }
        )
    return result


def _expected(projection, kind, audit_policy):
    rows = _rows(projection)
    common = {"row_count": len(rows), "semantic_unknown": len(rows)}
    if kind == "provider_brief":
        providers = []
        for name in sorted({row["provider"] for row in rows}):
            selected = [row for row in rows if row["provider"] == name]
            providers.append(
                {
                    "provider": name,
                    "rows": len(selected),
                    "delivered": sum(row["delivery"] is True for row in selected),
                    "delivery_failed": sum(
                        row["delivery"] is False for row in selected
                    ),
                    "delivery_unknown": sum(
                        row["delivery"] is None for row in selected
                    ),
                    "utility_positive": sum(
                        row["utility"] is not None and row["utility"] > 0
                        for row in selected
                    ),
                    "utility_nonpositive": sum(
                        row["utility"] is not None and row["utility"] <= 0
                        for row in selected
                    ),
                    "utility_unknown": sum(row["utility"] is None for row in selected),
                    "recorded_micro_usd": sum(row["amount"] for row in selected),
                    "included_capacity": sum(
                        row["basis"] == "included_capacity" for row in selected
                    ),
                }
            )
        return {**common, "providers": providers}
    if kind == "payment_brief":
        delivery = Counter(
            {True: "passed", False: "failed", None: "unknown"}[row["delivery"]]
            for row in rows
        )
        utility = Counter(
            "unknown"
            if row["utility"] is None
            else "positive"
            if row["utility"] > 0
            else "nonpositive"
            for row in rows
        )
        settlement = Counter(
            row["basis"]
            if row["basis"] in ("included_capacity", "reported_external")
            else "other"
            for row in rows
        )
        return {
            **common,
            "delivery": {key: delivery[key] for key in ("passed", "failed", "unknown")},
            "utility": {
                key: utility[key] for key in ("positive", "nonpositive", "unknown")
            },
            "settlement": {
                **{
                    key: settlement[key]
                    for key in ("included_capacity", "reported_external", "other")
                },
                "total_recorded_micro_usd": sum(row["amount"] for row in rows),
                "positive_recorded_amounts": sum(row["amount"] > 0 for row in rows),
            },
            "independently_verified_payments": 0,
        }
    policy = {"maximum_tasks": 4} if audit_policy is None else audit_policy
    require(
        type(policy) is dict and set(policy) == {"maximum_tasks"}, "audit_policy_shape"
    )
    maximum = policy["maximum_tasks"]
    require(type(maximum) is int and 0 <= maximum <= 10000, "audit_policy_bound")
    failures = sorted(row["id"] for row in rows if row["delivery"] is False)
    missing_outcomes = sorted(
        row["id"]
        for row in rows
        if row["delivery"] is not False and row["utility"] is None
    )
    eligible = failures + missing_outcomes
    return {
        **common,
        "policy": {"maximum_tasks": maximum},
        "eligible_ids": eligible,
        "selected_ids": eligible[:maximum],
        "deferred_ids": eligible[maximum:],
        "work_units": len(eligible[:maximum]),
        "execution": "planned_only",
        "usd_spent": 0,
    }


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_fact_key")
        result[key] = value
    return result


def verify_artifact(
    projection, artifact, kind, audit_policy=None, *, case_id=None, artifact_root=None
):
    """Return a stable checked verdict without disclosing paths or source rows.

    ``artifact`` is the host-returned record, not a model-supplied file path.
    ``source_sha256`` hashes the complete supplied projection, including its
    existing provenance fields; it is not that projection's internal source ID.
    No checker result can turn a local audit plan into an executed or paid job.
    """
    evidence = {
        "checker_id": CHECKER_ID,
        "expected_kind": kind if type(kind) is str else {"type": type(kind).__name__},
        "case_id": case_id
        if case_id is None or type(case_id) is str
        else {"type": type(case_id).__name__},
    }
    accepted = False
    try:
        require(kind in TITLES, "unknown_artifact_kind")
        require(
            case_id is None
            or type(case_id) is str
            and 0 < len(case_id.encode()) <= 256,
            "case_identity",
        )
        source_bytes = canonical(projection)
        require(len(source_bytes) <= MAX_SOURCE_BYTES, "source_byte_limit")
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        evidence["source_sha256"] = source_hash
        expected = _expected(projection, kind, audit_policy)
        evidence["expected_facts_sha256"] = fingerprint(expected)
        require(
            type(artifact) is dict
            and set(artifact) == {"kind", "path", "content_sha256", "source_sha256"},
            "artifact_record_shape",
        )
        require(
            artifact["kind"] == kind and artifact["source_sha256"] == source_hash,
            "artifact_source_binding",
        )
        require(
            type(artifact["content_sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", artifact["content_sha256"]) is not None,
            "artifact_content_identity",
        )
        require(
            type(artifact["path"]) is str
            and 0 < len(artifact["path"].encode()) <= 4096,
            "artifact_path_shape",
        )
        path = Path(artifact["path"])
        if artifact_root is not None:
            root = Path(artifact_root)
            require(not root.is_symlink() and root.is_dir(), "artifact_root_shape")
            root = root.resolve()
            path = path if path.is_absolute() else root / path
            require(path.resolve().is_relative_to(root), "artifact_root_escape")
            relative = path.relative_to(root)
            parent = root
            for component in relative.parts:
                parent /= component
                require(not parent.is_symlink(), "artifact_symlink_component")
        require(
            path.suffix == ".md" and not path.is_symlink() and path.is_file(),
            "artifact_not_regular_markdown",
        )
        require(path.stat().st_size <= MAX_ARTIFACT_BYTES, "artifact_byte_limit")
        with path.open("rb") as stream:
            content = stream.read(MAX_ARTIFACT_BYTES + 1)
        require(len(content) <= MAX_ARTIFACT_BYTES, "artifact_byte_limit")
        evidence["content_sha256"] = hashlib.sha256(content).hexdigest()
        require(
            evidence["content_sha256"] == artifact["content_sha256"],
            "artifact_content_hash",
        )
        text = content.decode("utf-8")
        prefix = (
            "# "
            + TITLES[kind]
            + "\n\n"
            + SCOPE
            + (
                "\n\nCoverage: " + projection["coverage"]
                if "coverage" in projection
                else ""
            )
            + "\n\nSource: `"
            + source_hash
            + "`\n\n```json\n"
        )
        require(
            text.startswith(prefix) and text.endswith("\n```\n"),
            "artifact_document_contract",
        )
        facts = json.loads(
            text[len(prefix) : -5],
            object_pairs_hook=_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ArtifactMismatch("nonfinite_fact_value")
            ),
        )
        require(type(facts) is dict, "artifact_fact_shape")
        evidence["observed_facts_sha256"] = fingerprint(facts)
        require(canonical(facts) == canonical(expected), "artifact_facts_mismatch")
        evidence["decision"] = "accepted"
        accepted = True
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError):
        # Source, file, identity and fact failures are identified rejections.
        # Exception strings can contain host paths, so they never enter evidence.
        evidence["decision"] = "rejected"
    return {
        "accepted": accepted,
        "evidence_sha256": fingerprint(evidence),
        "checker_id": CHECKER_ID,
    }

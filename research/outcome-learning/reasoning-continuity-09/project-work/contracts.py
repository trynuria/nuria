"""Project-work contracts: known-defect repair and actual mixed-price accounting.

Authored boundary cases are qualifications, not real user activity. Original
archives stay immutable; repairs produce a new returned module.
"""

import math


def audit_budget(data):
    rows = data["requests"]
    charged = sum(x["charged_micro_usd"] or 0 for x in rows)
    unresolved = sum(
        x["reserved_micro_usd"] for x in rows if x["charged_micro_usd"] is None
    )
    computed, mismatches = 0, []
    for row in rows:
        usage = row["usage"]
        if usage is None:
            continue
        price = row["pricing"]
        large = usage["prompt_tokens"] > price.get(
            "long_context_threshold", float("inf")
        )
        irate = price["input_micro_usd_per_million_tokens"] * (
            price.get("long_context_input_multiplier", 1) if large else 1
        )
        orate = price["output_micro_usd_per_million_tokens"] * (
            price.get("long_context_output_multiplier", 1) if large else 1
        )
        value = math.ceil(
            (usage["prompt_tokens"] * irate + usage["completion_tokens"] * orate)
            / 1_000_000
        )
        computed += value
        if row["charged_micro_usd"] is not None and row["charged_micro_usd"] != value:
            mismatches.append(row["request_sha256"])
    return {
        "schema": "nuria.cost-audit-report.v1",
        "scope": "Configured conservative ledger accounting; not a provider invoice.",
        "requests": len(rows),
        "known_usage": sum(x["usage"] is not None for x in rows),
        "unknown_usage": sum(x["usage"] is None for x in rows),
        "charged_micro_usd": charged,
        "unresolved_reserve_micro_usd": unresolved,
        "accounted_micro_usd": charged + unresolved,
        "remaining_micro_usd": data["cap_micro_usd"] - charged - unresolved,
        "blocked": any(x["charged_micro_usd"] is None for x in rows),
        "recomputed_known_usage_micro_usd": computed,
        "cost_mismatch_requests": sorted(mismatches),
        "models": sorted({x["pricing"]["model"] for x in rows}),
    }


def repair_contracts(source):
    old = 'sum(row.get("accepted") is None for row in outcomes)'
    new = 'sum(type(row.get("accepted")) is not bool for row in outcomes)'
    if source.count(old) != 1:
        raise ValueError("Expected the archived unique unknown-label expression")
    return source.replace(old, new)

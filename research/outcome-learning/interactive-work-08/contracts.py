"""Independent, explicit delivery expectations for real project data.

Recorded organoid activity is a data source, not live tissue or evidence about
Nuria's consciousness. Episodes here mean consecutive nonempty population bins.
These host expectations are never supplied to the model or its tool workspace.
"""

import hashlib
import math
from collections import Counter


def neural_report(data):
    rows, units = data["counts"], data["unit_ids"]
    population = [sum(row) for row in rows]
    totals = [sum(row[i] for row in rows) for i in range(len(units))]
    episodes = []
    active = []
    for index, total in enumerate(population + [0]):
        if total:
            active.append(index)
        elif active:
            begin, end = active[0], active[-1] + 1
            episodes.append(
                {
                    "start_bin": begin,
                    "stop_bin": end,
                    "spikes": sum(population[begin:end]),
                    "active_units": sum(
                        any(rows[j][i] for j in range(begin, end))
                        for i in range(len(units))
                    ),
                }
            )
            active = []
    return {
        "schema": "nuria.recording-report.v1",
        "bins": len(rows),
        "units": len(units),
        "bin_microseconds": round(data["bin_seconds"] * 1_000_000),
        "total_spikes": sum(population),
        "population_by_bin": population,
        "unit_totals": [
            {"unit_id": unit, "spikes": total} for unit, total in zip(units, totals)
        ],
        "active_units": [unit for unit, total in zip(units, totals) if total > 0],
        "episodes": episodes,
        "recording_id": data["nwb_identifier"],
        "interpretation": "Recorded organoid activity; nonempty-bin episodes, not consciousness or live tissue.",
    }


def inference_report(data):
    calls, outcomes = data["calls"], data["outcomes"]
    uses = [row.get("usage") or {} for row in calls]
    rates = data["rates"]
    cost = sum(
        math.ceil(
            (
                use.get("prompt_tokens", 0)
                * rates["input_micro_usd_per_million_tokens"]
                + use.get("completion_tokens", 0)
                * rates["output_micro_usd_per_million_tokens"]
            )
            / 1_000_000
        )
        for use in uses
    )
    hashes = Counter(
        row.get("response_sha256") for row in calls if row.get("response_sha256")
    )
    return {
        "schema": "nuria.inference-report.v1",
        "calls": len(calls),
        "completed_calls": sum(row["state"] == "completed" for row in calls),
        "failed_calls": sum(row["state"] == "failed" for row in calls),
        "usage_unknown_calls": sum(row.get("usage") is None for row in calls),
        "prompt_tokens": sum(use.get("prompt_tokens", 0) for use in uses),
        "completion_tokens": sum(use.get("completion_tokens", 0) for use in uses),
        "configured_micro_usd": cost,
        "cost_scope": "Configured uncached token rates for known usage; not a provider invoice or settlement of missing usage.",
        "repeated_response_hashes": sorted(
            key for key, count in hashes.items() if count > 1
        ),
        "outcomes": len(outcomes),
        "accepted": sum(row.get("accepted") is True for row in outcomes),
        "rejected": sum(row.get("accepted") is False for row in outcomes),
        "unknown": sum(row.get("accepted") is None for row in outcomes),
        "tasks": sorted(row["task_id"] for row in outcomes),
    }


def bytes_hash(raw):
    return hashlib.sha256(raw).hexdigest()

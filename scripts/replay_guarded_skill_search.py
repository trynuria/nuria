"""Reproduce every search from already published guarded-skill evidence.

This is replication of a completed result, not another qualification. It reads
the published corpora without generating tasks, verifies the original public
protocol and frozen source hashes, then executes every original method in the
original order. All record fields except the two measured CPU fields must match
exactly. No mismatching output is substituted back into the replayed history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scripts.verify_active_skill_results import digest, serial
from scripts.verify_guarded_skill_results import read

ROOT = Path(__file__).resolve().parents[1]
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
EXCLUDED_FIELDS = frozenset(("cpu_seconds", "core_cpu_seconds"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def deterministic(record):
    return {key: value for key, value in record.items() if key not in EXCLUDED_FIELDS}


def differences(expected, actual, path="", maximum=12):
    """Report field paths and hashes, never embed an artifact's raw content."""
    if serial(expected) == serial(actual):
        return []
    if type(expected) is dict and type(actual) is dict and set(expected) == set(actual):
        result = []
        for key in sorted(expected):
            result.extend(
                differences(
                    expected[key], actual[key], path + "/" + key, maximum - len(result)
                )
            )
            if len(result) >= maximum:
                break
        return result
    if type(expected) is list and type(actual) is list and len(expected) == len(actual):
        result = []
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            result.extend(
                differences(left, right, path + "/" + str(index), maximum - len(result))
            )
            if len(result) >= maximum:
                break
        return result
    return [
        {
            "field": path or "/",
            "expected_sha256": digest(expected),
            "actual_sha256": digest(actual),
        }
    ]


def frozen_sources(protocol, *, source_root=ROOT):
    hashes = protocol["source_hashes"]
    require(
        type(hashes) is dict and len(hashes) == 15,
        "The original fifteen-source freeze changed",
    )
    for name, expected in hashes.items():
        relative = Path(name)
        require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and type(expected) is str
            and HASH.fullmatch(expected)
            and hashlib.sha256((Path(source_root) / relative).read_bytes()).hexdigest()
            == expected,
            "Current evaluated sources differ from the original public freeze",
        )
    return hashes


def verify_manifest(
    started, summary, corpora, traces, protocol, protocol_raw, public_protocol
):
    require(
        public_protocol == protocol_raw,
        "Public protocol differs from the original frozen bytes",
    )
    require(
        type(started.get("freeze_commit")) is str
        and COMMIT.fullmatch(started["freeze_commit"])
        and started["protocol_sha256"] == hashlib.sha256(protocol_raw).hexdigest()
        and started["phase"] == summary["phase"] == "reserved"
        and started["source_hashes"]
        == summary["source_hashes"]
        == protocol["source_hashes"]
        and summary["corpora_sha256"] == digest(corpora)
        and summary["traces_sha256"] == digest(traces),
        "Original protocol or result commitments changed",
    )
    terms = protocol["terms"]
    require(
        terms == {key: started[key] for key in ("seeds", "jobs", "methods", "limits")}
        and len(terms["seeds"]) == len(set(terms["seeds"])) == 12
        and len(terms["methods"]) == len(set(terms["methods"])) == 9
        and terms["jobs"] == 48
        and len(corpora) == 12
        and len(traces) == 12 * 48 * 9,
        "Complete original paired coverage changed",
    )
    expected_order = []
    for stream, (seed, corpus) in enumerate(zip(terms["seeds"], corpora, strict=True)):
        require(
            corpus["source"]["seed"] == seed
            and corpus["config"] == protocol["configuration"]
            and len(corpus["tasks"]) == 48,
            "Published corpus source or configuration changed",
        )
        for index, job in enumerate(corpus["tasks"]):
            require(
                len(job["diagnostic_inputs"]) == 6 and len(job["score_inputs"]) == 24,
                "Original diagnostic/final partition changed",
            )
            rotation = (index + stream) % len(terms["methods"])
            order = terms["methods"][rotation:] + terms["methods"][:rotation]
            expected_order.extend((seed, index, method, job["id"]) for method in order)
    observed_order = [
        (row["seed"], row["ordinal"], row["method"], row["job_id"]) for row in traces
    ]
    require(
        len(set(observed_order)) == len(observed_order)
        and observed_order == expected_order,
        "Original method order, coverage or task identity changed",
    )


def replay_records(started, corpora, traces, *, runner=None, progress=None):
    """Execute the original method histories without changing any expected row."""
    if runner is None:
        from scripts.evaluate_guarded_skills import run_job

        runner = run_job
    offset, matched, mismatches = 0, Counter(), []
    completed, execution_error = 0, None
    seeds, methods, jobs = started["seeds"], started["methods"], started["jobs"]
    for stream, (seed, corpus) in enumerate(zip(seeds, corpora, strict=True)):
        states = {method: None for method in methods}
        namespace = digest([started["phase"], seeds, seed, jobs])
        for index, job in enumerate(corpus["tasks"]):
            rotation = (index + stream) % len(methods)
            order = methods[rotation:] + methods[:rotation]
            for method in order:
                try:
                    actual, final = runner(
                        states[method],
                        job,
                        method,
                        corpus["config"],
                        "nuria:guarded-study-v1",
                        started["limits"],
                        namespace,
                    )
                except Exception as error:
                    execution_error = {
                        "seed": seed,
                        "ordinal": index,
                        "method": method,
                        "error_type": type(error).__name__,
                    }
                    break
                states[method] = final
                actual.update(seed=seed, ordinal=index)
                expected = traces[offset]
                offset += 1
                completed += 1
                left, right = deterministic(expected), deterministic(actual)
                if serial(left) == serial(right):
                    matched[method] += 1
                else:
                    mismatches.append(
                        {
                            "seed": seed,
                            "ordinal": index,
                            "method": method,
                            "expected_record_sha256": digest(left),
                            "actual_record_sha256": digest(right),
                            "first_differences": differences(left, right),
                        }
                    )
            if execution_error:
                break
        if progress:
            progress(
                {
                    "streams_completed": stream + 1,
                    "records_completed": completed,
                    "mismatching_records": len(mismatches),
                }
            )
        if execution_error:
            break
    return {
        "complete": completed == len(traces) and execution_error is None,
        "records_completed": completed,
        "deterministic_matches": sum(matched.values()),
        "matches_per_method": {method: matched[method] for method in methods},
        "mismatching_records": len(mismatches),
        "mismatches": mismatches,
        "execution_error": execution_error,
    }


def fetch_public_protocol(commit):
    require(COMMIT.fullmatch(commit or ""), "Original public freeze commit is invalid")
    request = urllib.request.Request(
        "https://raw.githubusercontent.com/trynuria/nuria/"
        + commit
        + "/research/guarded-skills/protocol.json",
        headers={"User-Agent": "Nuria-public-result-replication"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read(100001)
    require(len(raw) <= 100000, "Public protocol exceeds its retrieval bound")
    return raw


def write_new(path, value):
    with path.open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(
            json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--protocol", type=Path, default=ROOT / "research/guarded-skills/protocol.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(
        not args.output.resolve().is_relative_to(ROOT),
        "Replication output must use a new private directory",
    )
    protocol_raw = args.protocol.read_bytes()
    require(len(protocol_raw) <= 100000, "Local protocol exceeds its input bound")
    protocol = json.loads(protocol_raw)
    frozen_sources(protocol)
    started, summary, corpora, traces = (
        read(args.directory, name)
        for name in ("started.json", "summary.json", "corpora.json", "traces.json")
    )
    public = fetch_public_protocol(started["freeze_commit"])
    verify_manifest(started, summary, corpora, traces, protocol, protocol_raw, public)
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    began = datetime.now(timezone.utc).isoformat()
    identity = {
        "schema": "nuria.guarded-skill-search-replication.v1",
        "scope": "Reproduction of already published result, not another qualification, new seed study or method retuning",
        "freeze_commit": started["freeze_commit"],
        "protocol_sha256": started["protocol_sha256"],
        "public_protocol_verified": True,
        "source_hashes": protocol["source_hashes"],
        "original_traces_sha256": summary["traces_sha256"],
        "original_corpora_sha256": summary["corpora_sha256"],
        "replication_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "expected_records": len(traces),
        "started_utc": began,
        "excluded_fields": sorted(EXCLUDED_FIELDS),
    }
    write_new(args.output / "started.json", identity)
    clock = time.monotonic()
    result = replay_records(
        started,
        corpora,
        traces,
        progress=lambda value: print(serial(value), flush=True),
    )
    frozen_sources(protocol)
    report = {
        **identity,
        **result,
        "elapsed_seconds": time.monotonic() - clock,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "exact_replication": result["complete"] and result["mismatching_records"] == 0,
        "limits": "Every original search, diagnostic exchange, output, receipt, library transition and declared deterministic cost was re-executed and compared. Measured CPU fields are excluded. Same implementation/runtime reproduction does not establish external generalization, neural advantage or independently attest physical instrumentation.",
    }
    write_new(args.output / "replication.json", report)
    print(
        serial(
            {
                key: report[key]
                for key in (
                    "exact_replication",
                    "records_completed",
                    "deterministic_matches",
                    "mismatching_records",
                )
            }
        )
    )
    if not report["exact_replication"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

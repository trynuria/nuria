"""Verify retained development bytes and independently replay delivered programs.

This does not rerun search, training, timing or external receipt authentication.
Its independent text interpreter reconstructs buyer acceptance from every
published authored semantic-search delivery, including failed proposals.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from scripts.build_guarded_skill_tasks import CONFIG, generate
from scripts.check_publication import RULES
from scripts.verify_active_skill_results import outcome

# The 2,000-job membership trace expands to 118,440,934 bytes. Keep a finite
# admission bound that covers that complete trace; never read an unbounded zip.
MAX_ARTIFACT = 128 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def unpack(path):
    require(path.stat().st_size <= MAX_ARTIFACT, "Published artifact exceeds its bound")
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as stream:
            data = stream.read(MAX_ARTIFACT + 1)
    else:
        data = path.read_bytes()
    require(len(data) <= MAX_ARTIFACT, "Expanded artifact exceeds its bound")
    return data


def safe_path(root, name):
    require(isinstance(name, str), "Archive path must be text")
    relative = Path(name)
    require(
        not relative.is_absolute() and ".." not in relative.parts,
        "Archive path escapes its root",
    )
    path = root / relative
    require(
        path.is_file() and not path.is_symlink(), "Archive item is absent or symlinked"
    )
    require(
        path.resolve().is_relative_to(root.resolve()),
        "Archive item escapes through a parent link",
    )
    return path


def check_archives(root):
    artifacts, original = {}, {}
    manifests = sorted(root.glob("archive-manifest-*.json"))
    require(len(manifests) >= 3, "A required development archive is missing")
    require(
        [path.name for path in manifests]
        == [
            f"archive-manifest-{number:02}.json"
            for number in range(1, len(manifests) + 1)
        ],
        "Development archive sequence is incomplete",
    )
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_bytes())
        require(
            manifest["schema"] == "nuria.neural-routing-development-archive.v1",
            "Archive schema changed",
        )
        for row in manifest["artifacts"]:
            name = row["path"]
            require(name not in artifacts, "Archive path appears twice")
            path = safe_path(root, name)
            raw = path.read_bytes()
            require(
                hashlib.sha256(raw).hexdigest() == row["published_sha256"],
                "Published archive bytes changed",
            )
            content = unpack(path)
            normalized_hash = row.get("normalized_sha256", row["published_sha256"])
            require(
                hashlib.sha256(content).hexdigest() == normalized_hash,
                "Expanded archive identity changed",
            )
            text = content.decode()
            require(
                not any(re.search(pattern, text) for _, pattern in RULES),
                "Private publication material appears in archive",
            )
            artifacts[name] = normalized_hash
            original[name] = row["original_sha256"]
    return artifacts, original


def _data(root, name):
    direct = root / name
    path = direct if direct.exists() else root / (name + ".gz")
    return json.loads(unpack(path))


def _rows(root, name):
    direct = root / name
    path = direct if direct.exists() else root / (name + ".gz")
    return [json.loads(line) for line in unpack(path).splitlines() if line]


def verify(root):
    root = Path(root)
    artifacts, originals = check_archives(root)
    summary = json.loads((root / "semantic-summary.json").read_bytes())
    require(
        summary.get("schema") == "nuria.semantic-guidance-development.v1"
        and [a.get("attempt") for a in summary.get("attempts", [])] == [1, 2, 3, 4]
        and all(type(a["attempt"]) is int for a in summary["attempts"]),
        "Semantic development variants are absent, duplicated or reordered",
    )
    jobs = {
        job["id"]: job for seed in (0, 1, 2, 3) for job in generate(seed, 96)["tasks"]
    }
    require(len(jobs) == 384, "Authored course identity or denominator changed")
    reconstructed = []
    decisions = 0
    for attempt in summary["attempts"]:
        number = attempt["attempt"]
        require(number in (1, 2, 3, 4), "Unexpected development variant")
        directory = f"semantic-priority-dev-{number:02}"
        totals = defaultdict(lambda: defaultdict(float))
        observed = set()
        for fold in (0, 1):
            run = _data(root, f"{directory}/summary-{fold}.json")
            dataset = _data(root, f"{directory}/training-{fold}.json")
            records = dataset["records"]
            require(
                not set(dataset["training_job_groups"])
                & set(dataset["test_job_groups"]),
                "Program groups overlap across a development fold",
            )
            for method, fitted in run["fits"].items():
                evidence = fitted["evidence"]
                require(
                    evidence["training_sha256"] == digest(records),
                    "A model trained from different shared records",
                )
                require(
                    evidence["records_count"] == len(records)
                    and evidence["positive_count"] == sum(r["label"] for r in records),
                    "Model training counts differ",
                )
                require(
                    evidence["configuration"]["method"] == method,
                    "Model mechanism identity differs",
                )
                totals[method]["fit_cpu_seconds"] += fitted["cpu_seconds"]
            rows = _rows(root, f"{directory}/evaluation-{fold}.jsonl")
            require(len(rows) == run["jobs"], "A fold delivery is missing")
            local = defaultdict(lambda: defaultdict(float))
            for row in rows:
                key = row["job_id"]
                require(
                    key in jobs and key not in observed,
                    "A task is absent or duplicated",
                )
                observed.add(key)
                job = jobs[key]
                expected_fold = (
                    int(
                        hashlib.sha256(
                            job["private_recipe"]["program_sha256"].encode()
                        ).hexdigest()[:8],
                        16,
                    )
                    % 2
                )
                require(
                    expected_fold == fold, "A task moved to a different program group"
                )
                require(
                    set(row["methods"]) == set(run["methods"]),
                    "A task omits a comparator",
                )
                for method, result in row["methods"].items():
                    checked = outcome(
                        result["program"],
                        job["score_inputs"],
                        job["score_outputs"],
                        CONFIG,
                    )
                    require(
                        type(result["accepted"]) is bool
                        and result["accepted"] == checked["passed"]
                        and result["correct_rows"] == checked["correct"]
                        and result["rows"] == checked["rows"]
                        and result["buyer_work"] == checked["total_work"],
                        "A delivered program disagrees with independent buyer replay",
                    )
                    values = {
                        "accepted": int(checked["passed"]),
                        "wrong": int(
                            result["program"] is not None and not checked["passed"]
                        ),
                        "search_work": result["work"]["total"],
                        "audit_work": result["audit_work"],
                        "buyer_work": checked["total_work"],
                        **{
                            key: result[key]
                            for key in (
                                "cpu_seconds",
                                "signature_cpu_seconds",
                                "scorer_cpu_seconds",
                                "signature_count",
                                "buyer_cpu_seconds",
                            )
                        },
                    }
                    require(
                        values["search_work"] <= 50000,
                        "An authored search exceeded its interpreter allowance",
                    )
                    require(
                        all(
                            type(value) in (int, float)
                            and math.isfinite(value)
                            and value >= 0
                            for value in values.values()
                        ),
                        "A recorded cost is malformed",
                    )
                    for name, value in values.items():
                        totals[method][name] += value
                        local[method][name] += value
                    decisions += 1
            for method, published in run["methods"].items():
                for name, value in published.items():
                    require(
                        math.isclose(
                            local[method][name], value, rel_tol=1e-10, abs_tol=1e-9
                        ),
                        "A fold summary differs from its traces",
                    )
        require(set(jobs) == observed, "Development coverage is incomplete")
        require(
            set(attempt["methods"]) == set(totals),
            "An aggregate summary omits a comparator",
        )
        for method, published in attempt["methods"].items():
            totals[method]["search_fit_buyer_cpu_seconds"] = sum(
                totals[method][k]
                for k in ("cpu_seconds", "fit_cpu_seconds", "buyer_cpu_seconds")
            )
            for key, value in published.items():
                require(
                    math.isclose(
                        totals[method][key], value, rel_tol=1e-10, abs_tol=1e-9
                    ),
                    "Aggregate semantic summary differs from its traces",
                )
        reconstructed.append(
            {
                "attempt": number,
                "jobs": len(observed),
                "accepted": {m: int(v["accepted"]) for m, v in totals.items()},
            }
        )
    require(len(reconstructed) == 4, "A failed development variant was omitted")
    return {
        "schema": "nuria.neural-routing-development-verification.v1",
        "verified": True,
        "archive_files": len(artifacts),
        "independently_replayed_deliveries": decisions,
        "attempts": reconstructed,
        "summary_sha256": hashlib.sha256(
            (root / "semantic-summary.json").read_bytes()
        ).hexdigest(),
        "verification_sources_sha256": {
            name: hashlib.sha256(
                (Path(__file__).resolve().parents[1] / name).read_bytes()
            ).hexdigest()
            for name in (
                "scripts/check_neural_routing_results.py",
                "scripts/build_guarded_skill_tasks.py",
                "scripts/verify_active_skill_results.py",
            )
        },
        "scope": "Published and expanded byte identities, privacy screening, program-group separation, shared training-set identities, independent final output/acceptance/work replay and aggregate arithmetic. Search visitation, membership teaching, gradient training, CPU timing, external receipts and statistical superiority are not independently reconstructed.",
        "superiority_established": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("development", type=Path)
    parser.add_argument("new_receipt", type=Path)
    args = parser.parse_args()
    result = verify(args.development)
    with args.new_receipt.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in ("scope", "attempts")
            }
        )
    )


if __name__ == "__main__":
    main()

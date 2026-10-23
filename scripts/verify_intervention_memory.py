"""Independently reconstruct native-work, delayed-credit and score evidence.

No evaluator, policy or SQLite lab code is imported. This checks retained
measurements and their use; it does not independently rerun SQLite or Brian2.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import math
import re
import subprocess
from pathlib import Path, PurePosixPath
from statistics import mean, stdev

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
METHODS = (
    "neural_origin",
    "raw_origin",
    "neural_receipt_time",
    "raw_receipt_time",
    "random_features",
    "frozen",
    "frequency",
    "no_index",
    "static_training",
    "all_indexes_reference",
)
CONTRASTS = (
    ("neural_origin", "no_index"),
    ("neural_origin", "frequency"),
    ("neural_origin", "raw_origin"),
    ("neural_origin", "neural_receipt_time"),
    ("raw_origin", "raw_receipt_time"),
    ("neural_origin", "static_training"),
    ("neural_origin", "random_features"),
    ("neural_origin", "all_indexes_reference"),
)
RANGES = (((0, 23), (0, 6)), ((1, 12), (0, 1)), ((1, 4), (1, 4)))
HASH = re.compile(r"[a-f0-9]{64}\Z")
REVISION = re.compile(r"[a-f0-9]{40}\Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, low=0):
    require(type(value) is int and low <= value <= 2**53 - 1, "Invalid work/count")
    return value


def equal(actual, expected, label="Published value"):
    if isinstance(expected, dict):
        require(
            isinstance(actual, dict) and actual.keys() == expected.keys(),
            label + " fields differ",
        )
        for key in expected:
            equal(actual[key], expected[key], label + "/" + key)
    elif isinstance(expected, list):
        require(
            isinstance(actual, list) and len(actual) == len(expected),
            label + " length differs",
        )
        for a, b in zip(actual, expected, strict=True):
            equal(a, b, label)
    elif type(expected) is float:
        require(
            type(actual) in (int, float) and math.isfinite(actual),
            label + " is not finite",
        )
        require(
            math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-10),
            label + " differs",
        )
    else:
        require(
            actual == expected and type(actual) is type(expected), label + " differs"
        )


def verify_kernel(kernel, protocol):
    identity, table = kernel["identity"], kernel["table"]
    require(
        identity["dataset_sha256"] == protocol["data"]["csv_sha256"],
        "Dataset identity differs",
    )
    require(identity["rows"] == protocol["data"]["rows"], "Dataset row count differs")
    require(
        isinstance(identity["sqlite_version"], str)
        and isinstance(identity["sqlite_compile_options"], list),
        "Native identity missing",
    )
    keys = {
        canonical({"family": f, "a": a, "b": b})
        for f, bounds in enumerate(RANGES)
        for a, b in itertools.product(*(range(lo, hi + 1) for lo, hi in bounds))
    }
    require(
        set(table) == keys, "Native kernel incomplete or contains unregistered queries"
    )
    builds, sizes = {}, {}
    for query_key, observations in table.items():
        query = json.loads(query_key)
        require(
            type(observations) is list and len(observations) == 5, "Kernel arm missing"
        )
        answers = []
        for arm, observation in enumerate(observations):
            require(
                observation["arm"] == (arm if arm < 4 else "all-indexes-reference"),
                "Native arm differs",
            )
            require(observation["queries"] == [query], "Kernel query differs")
            require(len(observation["results"]) == 1, "Kernel result shape differs")
            for key, value in identity.items():
                equal(observation[key], value, "Native identity/" + key)
            require(
                observation["results_sha256"] == digest(observation["results"]),
                "Native result hash differs",
            )
            build = integer(observation["index_build_steps"])
            work = integer(observation["query_steps"], 1)
            require(
                observation["total_steps"] == build + work,
                "Native work components differ",
            )
            size = integer(observation["index_bytes"])
            require(
                observation["index_count"] == (3 if arm == 4 else int(arm != 0)),
                "Native index capacity differs",
            )
            require(
                observation["automatic_indexes"] is False,
                "Native planner settings differ",
            )
            if arm == 0:
                require(build == 0 and size == 0, "No-index arm has construction cost")
            require(
                builds.setdefault(arm, build) == build
                and sizes.setdefault(arm, size) == size,
                "Index cost/size changes across kernel",
            )
            answers.append(observation["results"])
        require(
            all(answer == answers[0] for answer in answers),
            "Index intervention changes answers",
        )
    return len(table)


def selected_work(kernel, arm, queries):
    require(
        type(arm) is int
        and 0 <= arm <= 4
        and type(queries) is list
        and 1 <= len(queries) <= 32,
        "Invalid selected request",
    )
    observations = []
    for query in queries:
        require(
            type(query) is dict and set(query) == {"family", "a", "b"},
            "Invalid query fields",
        )
        key = canonical(query)
        require(key in kernel["table"], "Unregistered selected query")
        observations.append(kernel["table"][key][arm])
    build = observations[0]["index_build_steps"]
    require(
        all(row["index_build_steps"] == build for row in observations),
        "Selected construction cost differs",
    )
    query_steps = sum(row["query_steps"] for row in observations)
    return {
        "arm": arm,
        "results_sha256": digest([row["results"][0] for row in observations]),
        "index_build_steps": build,
        "query_steps": query_steps,
        "total_steps": build + query_steps,
        "index_bytes": observations[0]["index_bytes"],
    }


def neural_features(counts):
    require(
        type(counts) is list and len(counts) == 1024, "Neural count evidence missing"
    )
    for count in counts:
        integer(count)
    groups = []
    for start in range(0, 1024, 16):
        groups.append(min(4.0, sum(counts[start : start + 16]) / 16) / 4)
    return [sum(groups[start : start + 4]) / 4 for start in range(0, 64, 4)]


def registered_workload(task, seed, episodes):
    """Regenerate only the public fixed environment, independently of policies."""
    rng = np.random.default_rng(seed)
    phase = int(rng.integers(0, 3))
    previous, blocks = [0.0] * 3, []
    for step in range(episodes):
        if task == "short":
            probabilities, size = rng.dirichlet(np.ones(3)), 2
        else:
            if task == "changing":
                dominant, concentration = (phase + step // 4) % 3, 0.9
            elif task == "recurrent":
                dominant, concentration = (phase + (step // 8) % 2) % 3, 0.8
            elif task == "stationary":
                dominant, concentration = phase, 0.9
            else:
                raise ValueError("Unregistered environment")
            probabilities = np.full(3, (1 - concentration) / 2)
            probabilities[dominant], size = concentration, 32
        queries = []
        for family in rng.choice(3, size=size, p=probabilities):
            first, second = RANGES[int(family)]
            queries.append(
                {
                    "family": int(family),
                    "a": int(rng.integers(first[0], first[1] + 1)),
                    "b": int(rng.integers(second[0], second[1] + 1)),
                }
            )
        histogram = [
            sum(query["family"] == family for query in queries) / size
            for family in range(3)
        ]
        blocks.append(
            {
                "queries": queries,
                "context": histogram + previous + [size / 32],
                "delay": int(rng.integers(3, 10)),
            }
        )
        previous = histogram
    return blocks


class IndependentPolicy:
    """Audit the published ridge equations using only arrived selected costs."""

    def __init__(self, method, protocol):
        self.method, self.protocol = method, protocol
        require(
            protocol.get("feedback_order", "due_step,intent_id")
            in ("due_step,intent_id", "due_step,origin_step"),
            "Unregistered feedback order",
        )
        self.width = 8 if method.startswith("raw") else 24
        self.clock = 0
        self.models = [
            [np.eye(self.width), np.zeros(self.width), 0, 0] for _ in range(4)
        ]
        self.pending = []
        self.covered = set()

    def effective(self, arm):
        matrix, values, _, updated = self.models[arm]
        factor = self.protocol["discount"] ** (self.clock - updated)
        identity = np.eye(self.width)
        return identity + factor * (matrix - identity), factor * values

    def arrive(self, step, current_context):
        ready = sorted(
            (item for item in self.pending if item[0] <= step),
            key=lambda item: (item[0], item[1]),
        )
        self.pending = [item for item in self.pending if item[0] > step]
        for _, _, arm, original, cost in ready:
            if self.method == "frozen":
                continue
            context = current_context if "receipt_time" in self.method else original
            vector = np.asarray([1.0, *context])
            matrix, values = self.effective(arm)
            discount = self.protocol["discount"]
            identity = np.eye(self.width)
            utility = min(
                1.0, max(-1.0, 1 - cost / self.protocol["reward_scale_vm_steps"])
            )
            count = self.models[arm][2]
            self.clock += 1
            self.models[arm] = [
                identity + discount * (matrix - identity) + np.outer(vector, vector),
                discount * values + utility * vector,
                count + 1,
                self.clock,
            ]

    def check_decision(self, row, context):
        intent = row["decision"]
        require(isinstance(intent, dict), "Neural/raw policy decision missing")
        require(
            intent["id"]
            == digest({key: value for key, value in intent.items() if key != "id"}),
            "Decision commitment differs",
        )
        equal(
            intent["context"],
            [float(value) for value in context],
            "Originating context",
        )
        require(
            intent["source_id"] == f"sqlite:{row['task']}:{row['seed']}"
            and intent["step"] == row["step"]
            and intent["method"] == "intervention-memory-v1",
            "Decision origin differs",
        )
        require(
            intent["completed_before"] == self.clock == row["completed_before"],
            "Future or missing feedback enters decision",
        )
        require(
            intent["covered_arms_before"] == sorted(self.covered),
            "Coverage history differs",
        )
        vector = np.asarray([1.0, *context])
        scores = []
        for arm in range(4):
            matrix, values = self.effective(arm)
            expected = float(vector @ np.linalg.solve(matrix, values))
            uncertainty = math.sqrt(
                max(0.0, float(vector @ np.linalg.solve(matrix, vector)))
            )
            scores.append(
                {
                    "arm": arm,
                    "expected_utility": expected,
                    "uncertainty": uncertainty,
                    "score": expected + self.protocol["alpha"] * uncertainty,
                }
            )
        equal(intent["scores"], scores, "Selected-feedback UCB scores")
        uncovered = sorted(set(range(4)) - self.covered)
        arm = (
            uncovered[0]
            if uncovered
            else max(range(4), key=lambda a: (scores[a]["score"], -a))
        )
        require(
            intent["selection"] == ("initial_coverage" if uncovered else "ridge_ucb"),
            "Initial coverage marker differs",
        )
        require(
            row["arm"] == intent["arm"] == arm and row["intent_sha256"] == intent["id"],
            "Selection differs from committed scores",
        )
        self.covered.add(arm)
        self.pending.append(
            (
                row["step"] + row["delay"],
                row["step"]
                if self.protocol.get("feedback_order") == "due_step,origin_step"
                else intent["id"],
                arm,
                context,
                row["total_steps"],
            )
        )


def reconstructed_summary(trials, protocol, phase):
    summary = {}
    seeds = protocol[phase + "_seeds"]
    table = {(row["task"], row["method"], row["seed"]): row for row in trials}
    critical = protocol["critical_95"] if phase == "validation" else 4.302652729911275
    for task in protocol["tasks"]:
        entry = {
            "means": {
                method: float(
                    mean(table[task, method, seed]["total_vm_steps"] for seed in seeds)
                )
                for method in METHODS
            },
            "contrasts": {},
        }
        for target, baseline in CONTRASTS:
            gains = [
                (
                    table[task, baseline, seed]["total_vm_steps"]
                    - table[task, target, seed]["total_vm_steps"]
                )
                / table[task, baseline, seed]["total_vm_steps"]
                for seed in seeds
            ]
            center = mean(gains)
            error = stdev(gains) / math.sqrt(len(gains)) if len(gains) > 1 else 0.0
            entry["contrasts"][target + "__" + baseline] = {
                "relative_reduction": center,
                "lower_95": center - critical * error,
                "upper_95": center + critical * error,
                "seed_wins": sum(value > 0 for value in gains),
                "seeds": len(gains),
            }
        summary[task] = entry
    return summary


def committed_source(name, source_ref=None):
    """Read one safe relative source path, optionally at an immutable revision."""
    require(
        isinstance(name, str)
        and name
        and "\\" not in name
        and "\x00" not in name
        and not PurePosixPath(name).is_absolute()
        and str(PurePosixPath(name)) == name
        and all(part not in (".", "..") for part in PurePosixPath(name).parts),
        "Invalid source commitment path",
    )
    if source_ref is not None:
        require(
            isinstance(source_ref, str) and REVISION.fullmatch(source_ref),
            "Source revision must be a full lowercase commit hash",
        )
        try:
            return subprocess.run(
                ["git", "show", f"{source_ref}:{name}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout
        except subprocess.CalledProcessError as error:
            raise ValueError("Historical source unavailable: " + name) from error
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT), "Invalid source commitment path")
    return path.read_bytes()


def verify(directory: Path, protocol_path: Path | None = None, source_ref=None):
    if source_ref is not None:
        require(
            isinstance(source_ref, str) and REVISION.fullmatch(source_ref),
            "Source revision must be a full lowercase commit hash",
        )
    protocol_path = protocol_path or ROOT / "research/intervention-memory/protocol.json"
    protocol = json.loads(protocol_path.read_text())
    result = json.loads((directory / "result.json").read_text())
    phase = result["phase"]
    require(phase in ("development", "validation"), "Unregistered phase")
    require(
        result["protocol_sha256"] == digest(protocol), "Protocol commitment differs"
    )
    require(
        result["dataset_sha256"] == protocol["data"]["csv_sha256"],
        "Published dataset differs",
    )
    require(
        result["methods"] == list(METHODS), "A registered method is missing/reordered"
    )
    require(
        result["financial_transactions"] == 0 and result["all_answers_equal"] is True,
        "Experiment scope differs",
    )
    if phase == "validation":
        require(
            len(protocol["validation_seeds"]) == 24
            and protocol["episodes"] == 48
            and protocol["warmup"] == 12
            and set(protocol["tasks"])
            == {"changing", "recurrent", "stationary", "short"},
            "Validation registration differs",
        )
        require(result.get("source_hashes"), "Validation source commitments missing")
        implementation = json.loads(
            (protocol_path.parent / "implementation.json").read_text()
        )
        require(
            implementation["protocol_sha256"] == digest(protocol)
            and implementation["source_hashes"] == result["source_hashes"],
            "Frozen implementation differs",
        )
        require(
            isinstance(implementation["frozen_commit"], str)
            and re.fullmatch(r"[a-f0-9]{40}", implementation["frozen_commit"]),
            "Frozen revision identity missing",
        )
    for name, expected in result.get("source_hashes", {}).items():
        require(HASH.fullmatch(expected), "Invalid source commitment")
        require(
            hashlib.sha256(committed_source(name, source_ref)).hexdigest() == expected,
            "Evaluated source changed: " + name,
        )
    required_artifacts = {"kernel.json", "traces.jsonl.gz", "batch-audits.json"}
    require(
        set(result["artifacts"]) == required_artifacts,
        "Evidence artifact commitments missing",
    )
    for name in required_artifacts:
        require(
            hashlib.sha256((directory / name).read_bytes()).hexdigest()
            == result["artifacts"][name],
            "Evidence artifact changed: " + name,
        )
    kernel = json.loads((directory / "kernel.json").read_text())
    kernel_entries = verify_kernel(kernel, protocol)
    equal(result["native_identity"], kernel["identity"], "Published native identity")
    seeds, episodes = protocol[phase + "_seeds"], protocol["episodes"]
    expected_groups = {
        (task, seed, method)
        for task in protocol["tasks"]
        for seed in seeds
        for method in METHODS
    }
    trial_table = {
        (row["task"], row["seed"], row["method"]): row for row in result["trials"]
    }
    require(
        set(trial_table) == expected_groups
        and len(trial_table) == len(result["trials"]),
        "Trial coverage is incomplete/duplicated",
    )
    groups, shared, schedules = {}, {}, {}
    projection = np.random.default_rng(912).normal(size=(16, 7))
    lines, decompressed = 0, 0
    with gzip.open(directory / "traces.jsonl.gz", "rt") as stream:
        for line in stream:
            decompressed += len(line)
            require(
                len(line) <= 100_000 and decompressed <= 500_000_000,
                "Trace evidence exceeds its bound",
            )
            row = json.loads(line)
            key = (row["task"], row["seed"], row["method"])
            require(key in expected_groups, "Unregistered trace branch")
            group = groups.setdefault(
                key,
                {
                    "rows": [],
                    "policy": IndependentPolicy(row["method"], protocol),
                    "static": [0] * 4,
                },
            )
            previous = group["rows"][-1] if group["rows"] else None
            require(
                row["step"] == len(group["rows"]) < episodes,
                "Trace is missing, duplicated or reordered",
            )
            schedule_key = (row["task"], row["seed"])
            if schedule_key not in schedules:
                schedules[schedule_key] = registered_workload(*schedule_key, episodes)
            for field, expected in schedules[schedule_key][row["step"]].items():
                equal(row[field], expected, "Registered environment/" + field)
            require(
                type(row["delay"]) is int and 3 <= row["delay"] <= 9,
                "Unregistered feedback delay",
            )
            queries = row["queries"]
            require(
                len(queries) == (2 if row["task"] == "short" else 32),
                "Registered workload size differs",
            )
            histogram = [
                sum(query["family"] == family for query in queries) / len(queries)
                for family in range(3)
            ]
            expected_context = (
                histogram
                + (previous["context"][:3] if previous else [0.0] * 3)
                + [len(queries) / 32]
            )
            equal(row["context"], expected_context, "Visible workload context")
            features = neural_features(row["spike_counts"])
            equal(row["neural_features"], features, "Spike-derived features")
            require(
                HASH.fullmatch(row["neural_state_sha256"]),
                "Neural state commitment missing",
            )
            evidence = {
                field: row[field]
                for field in (
                    "context",
                    "queries",
                    "delay",
                    "neural_features",
                    "spike_counts",
                    "neural_state_sha256",
                )
            }
            shared_key = (row["task"], row["seed"], row["step"])
            require(
                shared.setdefault(shared_key, digest(evidence)) == digest(evidence),
                "Branches receive different workload/neural evidence",
            )
            expected_work = selected_work(kernel, row["arm"], queries)
            method = row["method"]
            if method == "static_training" and row["step"] < protocol["warmup"]:
                all_costs = [
                    selected_work(kernel, arm, queries)["total_steps"]
                    for arm in range(4)
                ]
                group["static"] = [
                    a + b for a, b in zip(group["static"], all_costs, strict=True)
                ]
                expected_work["calibration_steps"] = (
                    sum(all_costs) - expected_work["total_steps"]
                )
                expected_work["total_steps"] = sum(all_costs)
            else:
                require(
                    "calibration_steps" not in row, "Unregistered calibration charge"
                )
            for field, value in expected_work.items():
                equal(row[field], value, "Selected measurement/" + field)
            if method in METHODS[:6]:
                context = (
                    row["context"]
                    if method.startswith("raw")
                    else row["context"] + features
                )
                if method == "random_features":
                    context = (
                        row["context"]
                        + np.tanh(projection @ np.asarray(row["context"])).tolist()
                    )
                group["policy"].arrive(row["step"], context)
                group["policy"].check_decision(row, context)
            else:
                require(
                    row["decision"] is None
                    and row["intent_sha256"] is None
                    and row["completed_before"] == 0,
                    "Heuristic receives invented learning",
                )
                if method == "frequency":
                    arm = (
                        0
                        if len(queries) < 4
                        else max(range(3), key=lambda a: (histogram[a], -a)) + 1
                    )
                elif method == "static_training":
                    arm = (
                        row["step"] % 4
                        if row["step"] < protocol["warmup"]
                        else min(range(4), key=lambda a: (group["static"][a], a))
                    )
                else:
                    arm = 4 if method == "all_indexes_reference" else 0
                require(row["arm"] == arm, "Heuristic/static selection differs")
            group["rows"].append(
                {field: row[field] for field in ("context", "total_steps", "arm")}
            )
            lines += 1
    require(
        set(groups) == expected_groups
        and all(len(group["rows"]) == episodes for group in groups.values()),
        "Full trace coverage missing",
    )
    for key, group in groups.items():
        trial = trial_table[key]
        total = sum(row["total_steps"] for row in group["rows"])
        scored = sum(row["total_steps"] for row in group["rows"][protocol["warmup"] :])
        require(
            trial["total_vm_steps"] == total and trial["scored_vm_steps"] == scored,
            "Trial cost summary differs",
        )
        learned = key[2] in METHODS[:6] and key[2] != "frozen"
        require(
            trial["completed"] == (episodes if learned else 0),
            "Settled outcome count differs",
        )
        require(
            trial["retained_capabilities"]
            == (len({row["arm"] for row in group["rows"]}) if learned else 0),
            "Retained capability count differs",
        )
        require(
            trial["recovered"] == (key[2] in ("neural_origin", "raw_origin")),
            "Recovery branch marker differs",
        )
    recomputed = reconstructed_summary(result["trials"], protocol, phase)
    equal(result["summary"], recomputed, "Published summary")
    gates = {
        "correctness": True,
        "benefit": all(
            recomputed[task]["contrasts"]["neural_origin__no_index"]["lower_95"] > 0
            for task in ("changing", "recurrent")
        ),
        "credit": all(
            recomputed[task]["contrasts"]["neural_origin__neural_receipt_time"][
                "lower_95"
            ]
            > 0
            for task in ("changing", "recurrent")
        ),
        "neural_specificity": all(
            recomputed[task]["contrasts"]["neural_origin__" + comparator]["lower_95"]
            > 0
            for task in protocol["tasks"]
            for comparator in ("raw_origin", "random_features")
        ),
        "stable": recomputed["stationary"]["contrasts"][
            "neural_origin__static_training"
        ]["lower_95"]
        > -0.15,
    }
    if "gates" in result:
        equal(result["gates"], gates, "Registered gates")
    elif phase == "validation":
        raise ValueError("Validation gates missing")
    audits = json.loads((directory / "batch-audits.json").read_text())
    audit_keys = set()
    for audit in audits:
        key = (audit["task"], audit["seed"], audit["arm"])
        require(
            key not in audit_keys
            and key[0] in protocol["tasks"]
            and key[1] in seeds
            and key[2] in range(5),
            "Native batch audit coverage differs",
        )
        audit_keys.add(key)
        expected = selected_work(kernel, audit["arm"], audit["queries"])
        require(
            audit["queries"] == schedules[key[:2]][(key[1] * 7) % episodes]["queries"],
            "Native batch does not audit its registered workload",
        )
        equal(audit["replay"], expected, "Batch replay")
        require(audit["matched"] is True, "Native batch mismatch")
        actual = audit["observed"]
        for field in (
            "index_build_steps",
            "query_steps",
            "total_steps",
            "index_bytes",
            "results_sha256",
        ):
            equal(actual[field], expected[field], "Fresh native batch/" + field)
        require(
            actual["queries"] == audit["queries"]
            and actual["results_sha256"] == digest(actual["results"]),
            "Fresh native batch answer commitment differs",
        )
        for field, value in kernel["identity"].items():
            equal(actual[field], value, "Fresh native identity/" + field)
    require(
        len(audit_keys)
        == len(protocol["tasks"]) * len(seeds) * 5
        == result["native_batch_audits"],
        "Native batch audits missing",
    )
    verification = {
        "schema": "nuria.intervention-verification.v1",
        "verified": True,
        "phase": phase,
        "protocol_sha256": digest(protocol),
        "trace_rows": lines,
        "trials": len(groups),
        "kernel_queries": kernel_entries,
        "native_batch_audits": len(audits),
        "gates": gates,
        "checks": [
            "complete registered coverage",
            "native kernel cost and exact answer composition",
            "fresh recorded batch agreement",
            "origin-bound selected feedback and independent ridge scores",
            "spike count feature reconstruction",
            "charged static calibration",
            "independent paired intervals",
        ],
        "scope": "Independent retained-evidence reconstruction; not an independent SQLite/Brian2 rerun, external witness, walltime or neural superiority claim.",
    }
    if source_ref is not None:
        verification["source_ref"] = source_ref
    return verification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--source-ref")
    args = parser.parse_args()
    result = verify(args.directory, args.protocol, args.source_ref)
    if args.output:
        with args.output.open("x") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

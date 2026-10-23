"""Measure selected-only delayed learning on verified native SQLite work.

The database contains public observations; workload schedules are generated.
Policies replay a complete measured cost kernel, hidden behind a selected-arm
interface. Native batch audits separately verify exact kernel composition.
"""

import argparse
import gzip
import hashlib
import itertools
import json
import math
import time
from pathlib import Path

import numpy as np

from cognition.intervention_encoding import InterventionBrain
from cognition.intervention_memory import InterventionMemory
from cognition.sqlite_lab import CSV_SHA256, FAMILIES, RANGES, SQLiteLab

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "cognition/brain.py",
    "cognition/task_brain.py",
    "cognition/intervention_encoding.py",
    "cognition/intervention_memory.py",
    "cognition/sqlite_lab.py",
    "scripts/evaluate_intervention_memory.py",
)
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


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def schedules(task, seed, episodes):
    rng = np.random.default_rng(seed)
    phase = int(rng.integers(0, 3))
    previous = [0.0] * 3
    result = []
    for step in range(episodes):
        if task == "changing":
            dominant = (phase + step // 4) % 3
            p = np.full(3, 0.05)
            p[dominant] = 0.9
            size = 32
        elif task == "recurrent":
            dominant = (phase + (step // 8) % 2) % 3
            p = np.full(3, 0.1)
            p[dominant] = 0.8
            size = 32
        elif task == "stationary":
            p = np.full(3, 0.05)
            p[phase] = 0.9
            size = 32
        elif task == "short":
            p = rng.dirichlet(np.ones(3))
            size = 2
        else:
            raise ValueError("Unregistered workload family")
        queries = []
        for family in rng.choice(3, size=size, p=p):
            columns = FAMILIES[int(family)]
            queries.append(
                {
                    "family": int(family),
                    **{
                        key: int(rng.integers(RANGES[column][0], RANGES[column][1] + 1))
                        for key, column in zip(("a", "b"), columns, strict=True)
                    },
                }
            )
        histogram = [
            sum(q["family"] == family for q in queries) / size for family in range(3)
        ]
        result.append(
            {
                "step": step,
                "queries": queries,
                "context": histogram + previous + [size / 32],
                "delay": int(rng.integers(3, 10)),
            }
        )
        previous = histogram
    return result


def make_kernel(lab):
    table = {}
    identity = None
    for family, columns in enumerate(FAMILIES):
        for a, b in itertools.product(
            *(range(RANGES[c][0], RANGES[c][1] + 1) for c in columns)
        ):
            query = {"family": family, "a": a, "b": b}
            observations = [lab.run(arm, [query]) for arm in range(4)]
            observations.append(lab.run_all_indexes_reference([query]))
            if len({row["results_sha256"] for row in observations}) != 1:
                raise RuntimeError("An index changed a query answer")
            table[canonical(query)] = observations
            identity = {
                key: observations[0][key]
                for key in (
                    "sqlite_version",
                    "sqlite_source_id",
                    "sqlite_compile_options",
                    "dataset_sha256",
                    "rows",
                )
            }
    return {
        "identity": identity,
        "table": table,
        "scope": "Native statement measurements; dataset is real, workload schedules are generated. Unselected entries are audit data, never policy feedback.",
    }


def measured(kernel, arm, queries):
    rows = [kernel["table"][canonical(q)][arm] for q in queries]
    build = rows[0]["index_build_steps"]
    if any(row["index_build_steps"] != build for row in rows):
        raise RuntimeError("Index construction cost changed across the kernel")
    results = [row["results"][0] for row in rows]
    return {
        "arm": arm,
        "results_sha256": digest(results),
        "index_build_steps": build,
        "query_steps": sum(row["query_steps"] for row in rows),
        "total_steps": build + sum(row["query_steps"] for row in rows),
        "index_bytes": rows[0]["index_bytes"],
    }


class ReceiptTimeControl(InterventionMemory):
    """Intentional estimator defect: use delivery-time, not origin context."""

    override = None

    def _context(self, context):
        return super()._context(self.override if self.override is not None else context)


def neural_contexts(schedule, directory):
    brain = InterventionBrain(directory)
    output = []
    started = time.process_time()
    for block in schedule:
        frame = brain.advance({"context": block["context"]}, duration_ms=20)
        output.append(
            {
                "features": np.asarray(frame["features"])
                .reshape(16, 4)
                .mean(axis=1)
                .tolist(),
                "neural_state_sha256": brain.state_hash(),
                "spikes_sha256": frame["spikes_sha256"],
                "counts": frame["counts"],
            }
        )
    return output, time.process_time() - started


def run_policy(method, task, seed, schedule, neural, kernel, protocol):
    source = f"sqlite:{task}:{seed}"
    dimension = 7 if method.startswith("raw") else 23
    cls = ReceiptTimeControl if "receipt_time" in method else InterventionMemory
    learner = cls(
        source, dimension, discount=protocol["discount"], alpha=protocol["alpha"]
    )
    projection = np.random.default_rng(912).normal(size=(16, 7))
    pending, trace, sequence = [], [], 0
    static_costs = [0] * 4
    chosen_static = 0
    policy_cpu = time.process_time()
    for block, state in zip(schedule, neural, strict=True):
        step = block["step"]
        raw = block["context"]
        x = raw if dimension == 7 else raw + state["features"]
        if method == "random_features":
            x = raw + np.tanh(projection @ np.asarray(raw)).tolist()
        ready = [item for item in pending if item[0] <= step]
        pending = [item for item in pending if item[0] > step]
        for _, _, receipt in sorted(ready, key=lambda item: (item[0], item[1])):
            sequence += 1
            receipt["sequence"] = sequence
            learner.override = x if isinstance(learner, ReceiptTimeControl) else None
            if method != "frozen":
                learner.learn(receipt)
            learner.override = None
        if method in (
            "frequency",
            "no_index",
            "static_training",
            "all_indexes_reference",
        ):
            if method == "frequency":
                arm = 0 if len(block["queries"]) < 4 else int(np.argmax(raw[:3])) + 1
            elif method == "static_training":
                if step < protocol["warmup"]:
                    arm = step % 4
                else:
                    if step == protocol["warmup"]:
                        chosen_static = int(np.argmin(static_costs))
                    arm = chosen_static
            else:
                arm = 4 if method == "all_indexes_reference" else 0
            intent = None
        else:
            intent = learner.choose(x, step)
            arm = intent["arm"]
        observed = measured(kernel, arm, block["queries"])
        if method == "static_training" and step < protocol["warmup"]:
            # Training-only calibration; all-arm profiling is explicitly charged.
            calibration = [
                measured(kernel, a, block["queries"])["total_steps"] for a in range(4)
            ]
            static_costs = [
                a + b for a, b in zip(static_costs, calibration, strict=True)
            ]
            observed = {
                **observed,
                "calibration_steps": sum(calibration) - observed["total_steps"],
                "total_steps": sum(calibration),
            }
        if intent:
            receipt = {
                "intent_id": intent["id"],
                "source_id": source,
                "arm": arm,
                "cost": observed["total_steps"],
                "reference_cost": protocol["reward_scale_vm_steps"],
                "artifact_sha256": digest(
                    {
                        "arm": arm,
                        "index_bytes": observed["index_bytes"],
                        "dataset_sha256": CSV_SHA256,
                    }
                ),
                "result_sha256": observed["results_sha256"],
                "verified": True,
                "sequence": 0,
            }
            pending.append((step + block["delay"], step, receipt))
        trace.append(
            {
                "method": method,
                "task": task,
                "seed": seed,
                "step": step,
                "context": raw,
                "queries": block["queries"],
                "delay": block["delay"],
                "arm": arm,
                "intent_sha256": intent["id"] if intent else None,
                "decision": intent,
                "neural_features": state["features"],
                "spike_counts": state["counts"],
                "neural_state_sha256": state["neural_state_sha256"],
                "completed_before": learner.completed,
                **observed,
            }
        )
        if step == len(schedule) // 2 and method in ("neural_origin", "raw_origin"):
            checkpoint = learner.state()
            learner = cls(
                source,
                dimension,
                state=json.loads(canonical(checkpoint)),
                discount=protocol["discount"],
                alpha=protocol["alpha"],
            )
            if digest(learner.state()) != digest(checkpoint):
                raise RuntimeError("Intervention recovery changed state")
    for _, _, receipt in sorted(pending, key=lambda item: (item[0], item[1])):
        sequence += 1
        receipt["sequence"] = sequence
        learner.override = x if isinstance(learner, ReceiptTimeControl) else None
        if method != "frozen":
            learner.learn(receipt)
        learner.override = None
    return trace, {
        "task": task,
        "seed": seed,
        "method": method,
        "total_vm_steps": sum(row["total_steps"] for row in trace),
        "scored_vm_steps": sum(
            row["total_steps"] for row in trace[protocol["warmup"] :]
        ),
        "completed": learner.completed,
        "retained_capabilities": len(learner.capabilities),
        "policy_cpu_seconds": time.process_time() - policy_cpu,
        "recovered": method in ("neural_origin", "raw_origin"),
        "state_sha256": digest(learner.state()),
    }


def summarize(rows, protocol):
    comparisons = {}
    for task in protocol["tasks"]:
        subset = [r for r in rows if r["task"] == task]
        by_method = {
            m: {r["seed"]: r for r in subset if r["method"] == m} for m in METHODS
        }
        comparisons[task] = {
            "means": {
                m: float(np.mean([r["total_vm_steps"] for r in by_method[m].values()]))
                for m in METHODS
            },
            "contrasts": {},
        }
        for target, baseline in (
            ("neural_origin", "no_index"),
            ("neural_origin", "frequency"),
            ("neural_origin", "raw_origin"),
            ("neural_origin", "neural_receipt_time"),
            ("raw_origin", "raw_receipt_time"),
            ("neural_origin", "static_training"),
            ("neural_origin", "random_features"),
            ("neural_origin", "all_indexes_reference"),
        ):
            gains = [
                (
                    by_method[baseline][s]["total_vm_steps"]
                    - by_method[target][s]["total_vm_steps"]
                )
                / by_method[baseline][s]["total_vm_steps"]
                for s in by_method[target]
            ]
            mean = float(np.mean(gains))
            se = (
                float(np.std(gains, ddof=1) / math.sqrt(len(gains)))
                if len(gains) > 1
                else 0.0
            )
            critical = (
                protocol["critical_95"]
                if len(gains) == len(protocol["validation_seeds"])
                else 4.302652729911275
            )
            comparisons[task]["contrasts"][target + "__" + baseline] = {
                "relative_reduction": mean,
                "lower_95": mean - critical * se,
                "upper_95": mean + critical * se,
                "seed_wins": sum(g > 0 for g in gains),
                "seeds": len(gains),
            }
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--phase", choices=("development", "validation"), required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new evidence directory")
    protocol = json.loads(
        (ROOT / "research/intervention-memory/protocol.json").read_text()
    )
    source_hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in SOURCE_FILES
    }
    if args.phase == "validation":
        frozen = json.loads(
            (ROOT / "research/intervention-memory/implementation.json").read_text()
        )
        if frozen["source_hashes"] != source_hashes or frozen[
            "protocol_sha256"
        ] != digest(protocol):
            raise ValueError("The frozen validation implementation changed")
    csv = args.csv.read_bytes()
    if hashlib.sha256(csv).hexdigest() != CSV_SHA256:
        raise ValueError("Dataset bytes differ from the fixed public dataset")
    args.output.mkdir(parents=True)
    started = time.monotonic()
    with SQLiteLab(csv) as lab:
        kernel = make_kernel(lab)
        if (
            args.phase == "validation"
            and kernel["identity"] != protocol["native_identity"]
        ):
            raise ValueError("The registered native SQLite build differs")
        save(args.output / "kernel.json", kernel)
        print(
            canonical(
                {"phase": "native_kernel_measured", "entries": len(kernel["table"])}
            ),
            flush=True,
        )
        trials, audits = [], []
        seeds = protocol[args.phase + "_seeds"]
        with gzip.open(args.output / "traces.jsonl.gz", "wt") as stream:
            for task in protocol["tasks"]:
                for seed in seeds:
                    schedule = schedules(task, seed, protocol["episodes"])
                    neural, cpu = neural_contexts(
                        schedule, args.output / "private-brain" / f"{task}-{seed}"
                    )
                    # Batch rerun uses actual C statements, not kernel summation.
                    query = schedule[(seed * 7) % len(schedule)]["queries"]
                    for arm in range(5):
                        actual = (
                            lab.run(arm, query)
                            if arm < 4
                            else lab.run_all_indexes_reference(query)
                        )
                        replay = measured(kernel, arm, query)
                        if any(
                            actual[key] != replay[key]
                            for key in ("total_steps", "results_sha256", "index_bytes")
                        ):
                            raise RuntimeError(
                                "Native batch execution differs from the measured kernel"
                            )
                        audits.append(
                            {
                                "task": task,
                                "seed": seed,
                                "arm": arm,
                                "queries": query,
                                "observed": actual,
                                "replay": replay,
                                "matched": True,
                            }
                        )
                    for method in METHODS:
                        trace, result = run_policy(
                            method, task, seed, schedule, neural, kernel, protocol
                        )
                        for row in trace:
                            stream.write(canonical(row) + "\n")
                        result["neural_feature_cpu_seconds_shared"] = cpu
                        trials.append(result)
                    print(
                        canonical({"phase": args.phase, "task": task, "seed": seed}),
                        flush=True,
                    )
        summary = summarize(trials, protocol)
        gates = {
            "correctness": True,
            "benefit": all(
                summary[t]["contrasts"]["neural_origin__no_index"]["lower_95"] > 0
                for t in ("changing", "recurrent")
            ),
            "credit": all(
                summary[t]["contrasts"]["neural_origin__neural_receipt_time"][
                    "lower_95"
                ]
                > 0
                for t in ("changing", "recurrent")
            ),
            "neural_specificity": all(
                summary[t]["contrasts"]["neural_origin__" + b]["lower_95"] > 0
                for t in protocol["tasks"]
                for b in ("raw_origin", "random_features")
            ),
            "stable": summary["stationary"]["contrasts"][
                "neural_origin__static_training"
            ]["lower_95"]
            > -0.15,
        }
        save(args.output / "batch-audits.json", audits)
        outcome = {
            "schema": "nuria.intervention-evaluation.v1",
            "phase": args.phase,
            "protocol_sha256": digest(protocol),
            "source_hashes": source_hashes,
            "gates": gates,
            "dataset_sha256": CSV_SHA256,
            "native_identity": kernel["identity"],
            "scope": "Real public data and native statement work; generated workloads; offline selected-feedback policy replay with separate fresh native batch audits. VM counts exclude inference, parsing, cloning and correctness checking. No production database, token input, payment or TVB gain is represented.",
            "methods": METHODS,
            "trials": trials,
            "summary": summary,
            "native_batch_audits": len(audits),
            "all_answers_equal": True,
            "financial_transactions": 0,
            "elapsed_seconds": time.monotonic() - started,
            "artifacts": {
                "kernel.json": hashlib.sha256(
                    (args.output / "kernel.json").read_bytes()
                ).hexdigest(),
                "traces.jsonl.gz": hashlib.sha256(
                    (args.output / "traces.jsonl.gz").read_bytes()
                ).hexdigest(),
                "batch-audits.json": hashlib.sha256(
                    (args.output / "batch-audits.json").read_bytes()
                ).hexdigest(),
            },
        }
        save(args.output / "result.json", outcome)
        print(
            canonical(
                {
                    "phase": "complete",
                    "output": str(args.output),
                    "seconds": outcome["elapsed_seconds"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()

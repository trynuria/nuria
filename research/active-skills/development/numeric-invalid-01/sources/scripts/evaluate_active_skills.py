"""Measure input-only routing and selective labels on bounded private workloads.

External specification bodies and complete traces stay in private staging.
Development is not preregistered evidence. Reserved execution requires a frozen
protocol identifying every evaluated source and its exact specification split.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import time
from pathlib import Path

from cognition.active_skills import ActiveSkillSession, probe_cost
from cognition.skill_language import library_factory
from cognition.text_skills import DEFAULT_TEXT_CONFIG
from scripts.verify_active_skill_results import digest, outcome, serial

ROOT = Path(__file__).resolve().parents[1]
SOURCE_NAMES = (
    "cognition/active_skills.py",
    "cognition/skill_language.py",
    "cognition/text_skills.py",
    "cognition/skills.py",
    "cognition/skill_expressions.py",
    "cognition/skill_factored.py",
    "scripts/evaluate_active_skills.py",
    "scripts/verify_active_skill_results.py",
    "scripts/verify_skill_expression_results.py",
)


def source_hashes():
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in SOURCE_NAMES
    }


def write(path, value):
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    with Path(path).open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)


class RandomProbeSession(ActiveSkillSession):
    def _probe_index(self, candidates, unlabelled):
        if not self._plan(1 + len(unlabelled)):
            return None
        seed = digest([self.source_id, self.inputs, sorted(self._state["labels"])])
        return random.Random(seed).choice(unlabelled)


def numeric_jobs(path):
    raw = Path(path).read_bytes()
    if len(raw) > 4_000_000:
        raise ValueError("Numeric corpus exceeds bound")
    corpus = json.loads(raw)
    jobs = []
    for index, entry in enumerate(corpus["tasks"]):
        if len(entry["inputs"]) != 24 or len(entry["outputs"]) != 24:
            raise ValueError("Development numeric corpus needs original24-row files")
        jobs.append(
            {
                "id": digest(
                    {"corpus": hashlib.sha256(raw).hexdigest(), "index": index}
                ),
                "phase": entry["phase"],
                "family": "generated-numeric",
                "diagnostic_inputs": list(entry["inputs"][:4]),
                "diagnostic_outputs": list(entry["outputs"][:4]),
                "score_inputs": list(entry["inputs"][12:]),
                "score_outputs": list(entry["outputs"][12:]),
                "unused_buyer_validation_rows": 8,
                "unsupported": None,
            }
        )
    return (
        corpus["config"],
        jobs,
        {
            "corpus_sha256": hashlib.sha256(raw).hexdigest(),
            "scope": "Development/reanalysis of existing real observations with generated formatting; not fresh reserved observations",
        },
    )


def external_jobs(inventory_path, source_directory, phase):
    inventory_raw = Path(inventory_path).read_bytes()
    inventory = json.loads(inventory_raw)
    jobs, access = [], []
    for entry in inventory["tasks"]:
        if not entry["structurally_eligible"] or entry["split"] != phase:
            continue
        candidates = [
            Path(source_directory) / entry["specification"]["path"],
            Path(source_directory) / "development-specs" / (entry["task_id"] + ".json"),
        ]
        paths = [path for path in candidates if path.is_file()]
        if len(paths) != 1:
            raise ValueError(
                "Exactly one privately staged pinned specification is required: "
                + entry["task_id"]
            )
        with paths[0].open("rb") as stream:
            raw = stream.read(100001)
        specification = entry["specification"]
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if (
            len(raw) > 100000
            or len(raw) != specification["bytes"]
            or blob != specification["git_blob_sha1"]
        ):
            raise ValueError("Pinned external specification bytes changed")
        examples = json.loads(raw)["Examples"]
        unsupported = None
        if not 3 <= len(examples) <= 2048:
            unsupported = "insufficient_or_excess_original_examples"
        if any(
            type(row.get("Input")) is not list
            or not 1 <= len(row["Input"]) <= 8
            or any(type(value) is not str for value in row["Input"])
            or type(row.get("Output")) is not str
            for row in examples
        ):
            unsupported = "unsupported_source_types"
        if unsupported:
            inputs, outputs = [], []
        else:
            inputs = [
                json.dumps(row["Input"], separators=(",", ":")) for row in examples
            ]
            outputs = [row["Output"] for row in examples]
            if any(
                len(value.encode()) > DEFAULT_TEXT_CONFIG["max_input_bytes"]
                for value in inputs + outputs
            ) or any(len(value.encode()) > 256 for value in outputs[:2]):
                unsupported = "declared_text_or_training_target_bound"
        jobs.append(
            {
                "id": entry["task_id"],
                "family": entry["family"],
                "phase": phase,
                "diagnostic_inputs": inputs[:2],
                "diagnostic_outputs": outputs[:2],
                "score_inputs": inputs[2:],
                "score_outputs": outputs[2:],
                "original_examples": len(examples),
                "specification_sha256": hashlib.sha256(raw).hexdigest(),
                "unsupported": unsupported,
            }
        )
        access.append(
            {
                "task_id": entry["task_id"],
                "specification_sha256": hashlib.sha256(raw).hexdigest(),
                "phase": phase,
            }
        )
    return (
        copy.deepcopy(DEFAULT_TEXT_CONFIG),
        jobs,
        {
            "inventory_sha256": hashlib.sha256(inventory_raw).hexdigest(),
            "repository_commit": inventory["source"]["commit"],
            "source_access": access,
            "universe": inventory["summary"],
            "scope": "Externally authored synthetic task qualification; original examples only, no customer-traffic claim",
        },
    )


def external_terms(inventory_path, phase, limits, initial):
    raw = Path(inventory_path).read_bytes()
    inventory = json.loads(raw)
    return {
        "domain": "external",
        "phase": phase,
        "inventory_sha256": hashlib.sha256(raw).hexdigest(),
        "config_sha256": digest(DEFAULT_TEXT_CONFIG),
        "initial_library_sha256": digest(initial),
        "methods": ["fixed2", "active", "random", "frozen"],
        "limits": limits,
        "task_ids": [
            entry["task_id"]
            for entry in inventory["tasks"]
            if entry["structurally_eligible"] and entry["split"] == phase
        ],
        "split": "First2 original diagnostic rows; every remaining original row scores. No resampling or added examples.",
        "admission": "Aggregate exact buyer receipt after committed output can affect later files; no scoring label enters search.",
        "source_bounds": {
            "maximum_specification_bytes": 100000,
            "maximum_original_examples": 2048,
        },
    }


def run_job(initial, job, method, config, source_id, limits, retain):
    started, cpu = time.monotonic(), time.process_time()
    library = library_factory(source_id, config, state=initial)
    before = library.dump()
    actions, indices = [], []
    work = {
        "total": 0,
        "portfolio_work": 0,
        "routing_work": 0,
        "probe_work": 0,
        "synthesis_work": 0,
    }
    selected = {
        "program": None,
        "program_sha256": None,
        "proposal_id": None,
        "selection": None,
    }
    session_dump = None
    reason = job["unsupported"]
    if not reason and method.startswith("fixed"):
        count = int(method[5:])
        seen = set()
        for index, value in enumerate(job["diagnostic_inputs"][:count]):
            if value not in seen:
                indices.append(index)
                seen.add(value)
        work["probe_work"] = sum(
            probe_cost(
                job["diagnostic_inputs"][index], job["diagnostic_outputs"][index]
            )
            for index in indices
        )
        budget = min(
            limits["synthesis_work"], limits["maximum_work"] - work["probe_work"]
        )
        if budget > 0:
            proposal = library.solve(
                [job["diagnostic_inputs"][index] for index in indices],
                [job["diagnostic_outputs"][index] for index in indices],
                mode="retained_factored",
                budget=budget,
            )
            selected = {
                key: proposal[key]
                for key in ("program", "program_sha256", "proposal_id")
            }
            selected["selection"] = (
                "retained"
                if proposal["program_sha256"] in before["programs"]
                else "synthesized"
            )
            work["synthesis_work"] = proposal["work"]["total"]
            reason = (
                "fixed_examples_solved"
                if proposal["program"] is not None
                else "no_program_found"
            )
        else:
            reason = "work_limit"
        work["total"] = sum(work[key] for key in ("probe_work", "synthesis_work"))
    elif not reason:
        session_type = RandomProbeSession if method == "random" else ActiveSkillSession
        session = session_type(
            source_id, config, job["diagnostic_inputs"], library_state=before, **limits
        )
        for _ in range(70):
            action = session.next_action()
            actions.append(action)
            if action["kind"] == "probe":
                index = action["input_index"]
                indices.append(index)
                receipt = {
                    "source_id": source_id,
                    "input_index": index,
                    "input_sha256": digest(job["diagnostic_inputs"][index]),
                    "output": job["diagnostic_outputs"][index],
                    "receipt_id": digest(
                        {"job": job["id"], "method": method, "action": action["id"]}
                    ),
                    "work": probe_cost(
                        job["diagnostic_inputs"][index],
                        job["diagnostic_outputs"][index],
                    ),
                }
                session.observe_probe(action["id"], receipt)
            elif action["kind"] == "synthesize":
                session.perform_synthesis(action["id"])
            else:
                break
        else:
            raise ValueError("Bounded active action protocol did not terminate")
        result = session.result()
        if result["accounting_uncertain"]:
            raise ValueError("Physical tool work became ambiguous")
        selected = {
            key: result[key]
            for key in ("program", "program_sha256", "proposal_id", "selection")
        }
        reason, work, library = result["reason"], result["work"], session.library
        session_dump = session.dump()
    committed = digest(
        {"job": job["id"], "program": selected["program"], "probe_indices": indices}
    )
    pending = library.dump()["pending"].get(selected["proposal_id"])
    training_sha256 = pending["training_sha256"] if pending is not None else None
    checked = outcome(
        selected["program"], job["score_inputs"], job["score_outputs"], config
    )
    admission = None
    if selected["proposal_id"] is not None:
        receipt = {
            "source_id": source_id,
            "proposal_id": selected["proposal_id"],
            "program_sha256": selected["program_sha256"],
            "evaluation_id": digest({"committed": committed, "method": method}),
            "accepted": checked["passed"],
            "correct_rows": checked["correct"],
            "total_rows": checked["rows"],
            "evidence_sha256": digest(
                {
                    "inputs": job["score_inputs"],
                    "expected": job["score_outputs"],
                    "observed": checked["outputs"],
                }
            ),
            "work": checked["total_work"],
        }
        admission = library.admit_verified(
            selected["proposal_id"], receipt, retain=retain
        )
    final = library.dump()
    administration_work = (
        1
        + (
            len(serial(before).encode())
            + len(serial(final).encode())
            + (len(serial(session_dump).encode()) if session_dump is not None else 0)
            + 31
        )
        // 32
    )
    before_hash, after_hash = digest(before), digest(final)
    record = {
        "method": method,
        "job_id": job["id"],
        **selected,
        "reason": reason,
        "retain": retain,
        "committed_proposal_sha256": committed,
        "synthesis_training_sha256": training_sha256,
        "probe_indices": indices,
        "probe_inputs": [job["diagnostic_inputs"][index] for index in indices],
        "probe_outputs": [job["diagnostic_outputs"][index] for index in indices],
        "actions": actions,
        "controller_work": work,
        "checked": checked,
        "admission": admission,
        "library_before_sha256": before_hash,
        "library_after_sha256": after_hash,
        "programs_before": sorted(before["programs"]),
        "programs_after": sorted(final["programs"]),
        "session_sha256": digest(session_dump) if session_dump is not None else None,
        "administration_work": administration_work,
        "total_work": work["total"] + checked["total_work"] + administration_work,
        "cpu_seconds": time.process_time() - cpu,
        "wall_seconds": time.monotonic() - started,
    }
    return record, final


def summarize(records, jobs, methods):
    by_id = {job["id"]: job for job in jobs}
    summary = {}
    for method in methods:
        selected = [row for row in records if row["method"] == method]
        count = len(selected)
        summary[method] = {
            "jobs": count,
            "exact_successes": sum(row["checked"]["passed"] for row in selected),
            "proposed": sum(row["program"] is not None for row in selected),
            "abstained_or_failed_search": sum(
                row["program"] is None for row in selected
            ),
            "unsupported": sum(
                by_id[row["job_id"]]["unsupported"] is not None for row in selected
            ),
            "probes": sum(len(row["probe_indices"]) for row in selected),
            "total_work": sum(row["total_work"] for row in selected),
            "cpu_seconds": sum(row["cpu_seconds"] for row in selected),
            "score_rows": sum(row["checked"]["rows"] for row in selected),
            "correct_rows": sum(row["checked"]["correct"] for row in selected),
            "scoring_rows_overlapping_diagnostics": sum(
                value in set(by_id[row["job_id"]]["diagnostic_inputs"])
                for row in selected
                for value in by_id[row["job_id"]]["score_inputs"]
            ),
        }
        summary[method]["exact_success_rate"] = (
            summary[method]["exact_successes"] / count if count else None
        )
        summary[method]["proposal_coverage"] = (
            summary[method]["proposed"] / count if count else None
        )
    return summary


def run(args):
    output = args.out.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError(
            "Complete benchmark traces require private staging outside the repository"
        )
    before_hashes = source_hashes()
    requested_limits = {
        "maximum_work": args.maximum_work,
        "synthesis_work": args.synthesis_work,
        "maximum_probes": 4 if args.domain == "numeric" else 2,
        "minimum_evidence": 1,
    }
    if args.phase == "reserved":
        if (
            args.domain != "external"
            or args.protocol is None
            or args.protocol_sha256 is None
        ):
            raise ValueError("Reserved execution requires an external frozen protocol")
        raw = args.protocol.read_bytes()
        protocol = json.loads(raw)
        if (
            hashlib.sha256(raw).hexdigest() != args.protocol_sha256
            or protocol.get("status") != "frozen_before_reserved_access"
            or protocol.get("source_hashes") != before_hashes
        ):
            raise ValueError("Reserved protocol identity or source freeze is absent")
        config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        source_id = "active-study-external-" + digest(config)[:16]
        initial = library_factory(source_id, config).dump()
        if args.initial_library:
            initial = json.loads(args.initial_library.read_bytes())
            library_factory(source_id, config, state=initial)
        if protocol.get("execution_terms") != external_terms(
            args.inventory, "reserved", requested_limits, initial
        ):
            raise ValueError(
                "Reserved limits, initial state, inventory or task split changed"
            )
    if args.domain == "numeric":
        config, jobs, provenance = numeric_jobs(args.corpus)
        methods = ["fixed4", "fixed2", "active", "random", "frozen"]
    else:
        config, jobs, provenance = external_jobs(
            args.inventory, args.source_directory, args.phase
        )
        methods = ["fixed2", "active", "random", "frozen"]
    if not jobs or len(jobs) > 256:
        raise ValueError("Study needs a finite declared workload")
    source_id = "active-study-" + args.domain + "-" + digest(config)[:16]
    limits = requested_limits
    output.mkdir(mode=0o700)
    for name in SOURCE_NAMES:
        path = output / "sources" / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write((ROOT / name).read_bytes())
    initial = library_factory(source_id, config).dump()
    if args.initial_library:
        initial = json.loads(args.initial_library.read_bytes())
        library_factory(source_id, config, state=initial)
    libraries = {method: copy.deepcopy(initial) for method in methods}
    manifest = {
        "schema": "nuria.active-skill-evaluation.v1",
        "phase": args.phase,
        "domain": args.domain,
        "source_id": source_id,
        "source_hashes": before_hashes,
        "config": config,
        "limits": limits,
        "methods": methods,
        "jobs": jobs,
        "provenance": provenance,
        "initial_libraries": copy.deepcopy(libraries),
        "execution_terms": external_terms(args.inventory, args.phase, limits, initial)
        if args.domain == "external"
        else None,
        "scope": "Input-only conventional routing, selective buyer labels and prequential verified program reuse. No neural, consciousness or algorithmic novelty claim.",
        "timing_scope": "Per-file library initialization, controller, independent buyer interpreter, admission and state/hash preparation; source parsing and evidence serialization are outside timers. Raw CPU values are measured process evidence, not independent attestation.",
    }
    write(output / "run.json", manifest)
    records = []
    try:
        with (output / "traces.jsonl").open("x") as stream:
            os.fchmod(stream.fileno(), 0o600)
            for index, job in enumerate(jobs):
                order = (
                    methods[index % len(methods) :] + methods[: index % len(methods)]
                )
                for method in order:
                    retain = method != "frozen" or job["phase"] in (
                        "acquisition",
                        "development",
                    )
                    record, libraries[method] = run_job(
                        libraries[method],
                        job,
                        method,
                        config,
                        source_id,
                        limits,
                        retain,
                    )
                    records.append(record)
                    stream.write(serial(record) + "\n")
                    stream.flush()
        if before_hashes != source_hashes():
            raise ValueError("Evaluated source changed during execution")
        result = {
            "status": "development_reanalysis"
            if args.phase == "development"
            else "reserved",
            "domain": args.domain,
            "methods": summarize(records, jobs, methods),
            "scope": manifest["scope"],
            "provenance": provenance,
            "confidence_intervals": "None: task/family dependence and one declared development stream do not justify treating rows as independent trials.",
        }
        for method, state in libraries.items():
            write(output / f"library-{method}.json", state)
        if args.domain == "external" and args.phase == "development":
            write(
                output / "reserved-terms-template.json",
                {
                    "status": "draft_not_frozen",
                    "source_hashes": before_hashes,
                    "execution_terms": external_terms(
                        args.inventory, "reserved", limits, libraries["fixed2"]
                    ),
                },
            )
        write(output / "result.json", result)
        write(
            output / "execution-status.json",
            {
                "complete": True,
                "records": len(records),
                "source_hashes_unchanged": True,
            },
        )
        return result
    except BaseException as error:
        write(
            output / "execution-status.json",
            {
                "complete": False,
                "completed_records": len(records),
                "error_type": type(error).__name__,
                "source_hashes_unchanged": before_hashes == source_hashes(),
            },
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", choices=("numeric", "external"), required=True)
    parser.add_argument(
        "--phase", choices=("development", "reserved"), default="development"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "research/skill-acquisition/retention-tasks.json",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=ROOT / "research/active-skills/source-inventory.json",
    )
    parser.add_argument("--source-directory", type=Path)
    parser.add_argument("--initial-library", type=Path)
    parser.add_argument("--maximum-work", type=int, default=100000)
    parser.add_argument("--synthesis-work", type=int, default=10000)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--protocol-sha256")
    args = parser.parse_args()
    if not 1 <= args.synthesis_work <= args.maximum_work <= 10_000_000:
        parser.error("Work limits must be finite and shared across methods")
    if args.domain == "external" and args.source_directory is None:
        parser.error("External evaluation needs privately staged pinned source bytes")
    result = run(args)
    print(
        json.dumps(
            {
                "status": result["status"],
                "domain": result["domain"],
                "methods": result["methods"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

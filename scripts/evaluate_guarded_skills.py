"""Paired, frozen-source comparisons of guarded programming by example.

The learner receives only diagnostic inputs and explicitly requested answers.
Generated recipes and all final targets remain in the evaluator. Complete
traces are private; public reports use counts and commitments. Generated tasks
are engineering tests, not independent customer or external benchmark evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = (
    "old_fixed2",
    "old_fixed4",
    "composed_fixed2",
    "composed_fixed3",
    "composed_fixed4",
    "enumerative_fixed3",
    "enumerative_fixed4",
    "guarded",
    "frozen_guarded",
)
SOURCES = (
    "cognition/guarded_skills.py",
    "cognition/text_composition.py",
    "cognition/text_skills.py",
    "cognition/skills.py",
    "cognition/skill_language.py",
    "cognition/active_skills.py",
    "cognition/skill_expressions.py",
    "cognition/skill_factored.py",
    "cognition/composed_skills.py",
    "scripts/build_guarded_skill_tasks.py",
    "scripts/evaluate_guarded_skills.py",
    "scripts/verify_guarded_skill_results.py",
    "scripts/verify_active_skill_results.py",
    "scripts/summarize_guarded_skill_comparison.py",
    "tests/test_guarded_skill_comparison.py",
)


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode()).hexdigest()


def units(value):
    return 1 + (len(serial(value).encode()) + 31) // 32


def source_hashes():
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES
    }


def write_new(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def factory(method):
    from cognition.text_composition import CompositionalTextLibrary
    from cognition.text_skills import TextSkillLibrary

    if method.startswith("old_"):
        return TextSkillLibrary
    if method.startswith("enumerative_"):
        return lambda source, config, state=None: CompositionalTextLibrary(
            source, config, state, search_mode="enumerative"
        )
    return CompositionalTextLibrary


def receipt_for(proposal, checked, source_id, evaluation_id):
    return {
        "source_id": source_id,
        "proposal_id": proposal["proposal_id"],
        "program_sha256": proposal["program_sha256"],
        "evaluation_id": evaluation_id,
        "accepted": checked["passed"],
        "correct_rows": checked["correct"],
        "total_rows": checked["rows"],
        "evidence_sha256": digest(checked),
        "work": checked["total_work"],
    }


def run_job(initial, job, method, config, source_id, limits, namespace):
    from cognition.active_skills import probe_cost
    from cognition.guarded_skills import GuardedSkillSession
    from scripts.verify_active_skill_results import outcome

    started = time.process_time()
    build = factory(method)
    library = build(source_id, config, state=initial)
    before = library.dump()
    indices, actions = [], []
    session_dump = None
    work = {"probe_work": 0, "synthesis_work": 0, "routing_work": 0, "total": 0}
    core_start = time.process_time()
    if "fixed" in method:
        count = int(method.rsplit("fixed", 1)[1])
        seen = set()
        for index, value in enumerate(job["diagnostic_inputs"][:count]):
            if value not in seen:
                seen.add(value)
                indices.append(index)
        work["probe_work"] = sum(
            probe_cost(job["diagnostic_inputs"][i], job["diagnostic_outputs"][i])
            for i in indices
        )
        selected = library.solve(
            [job["diagnostic_inputs"][i] for i in indices],
            [job["diagnostic_outputs"][i] for i in indices],
            mode="retained_factored",
            budget=min(
                limits["synthesis_work"],
                limits["maximum_work"] - work["probe_work"],
            ),
        )
        work["synthesis_work"] = selected["work"]["total"]
        work["total"] = work["probe_work"] + work["synthesis_work"]
        selected = {
            **{k: selected[k] for k in ("program", "program_sha256", "proposal_id")},
            "selection": "retained"
            if selected["program_sha256"] in before["programs"]
            else "synthesized",
            "reason": "fixed_examples",
        }
    else:
        session = GuardedSkillSession(
            source_id,
            config,
            job["diagnostic_inputs"],
            library_state=before,
            factory=build,
            solver_id="composed-v2",
            **limits,
        )
        for _ in range(96):
            action = session.next_action()
            actions.append(action)
            if action["kind"] == "probe":
                index = action["input_index"]
                indices.append(index)
                session.observe_probe(
                    action["id"],
                    {
                        "source_id": source_id,
                        "input_index": index,
                        "input_sha256": digest(job["diagnostic_inputs"][index]),
                        "output": job["diagnostic_outputs"][index],
                        "receipt_id": digest(
                            [namespace, job["id"], method, action["id"]]
                        ),
                        "work": probe_cost(
                            job["diagnostic_inputs"][index],
                            job["diagnostic_outputs"][index],
                        ),
                    },
                )
            elif action["kind"] == "synthesize":
                session.perform_synthesis(action["id"])
            else:
                break
        else:
            raise ValueError("Guarded episode exceeded its finite action bound")
        result = session.result()
        if result.get("accounting_uncertain"):
            raise ValueError("Actual tool execution has uncertain accounting")
        selected = {
            k: result[k]
            for k in ("program", "program_sha256", "proposal_id", "selection", "reason")
        }
        work, library = result["work"], session.library
        session_dump = session.dump()
    core_cpu = time.process_time() - core_start
    committed = digest(
        {"job": job["id"], "program": selected["program"], "probes": indices}
    )
    checked = outcome(
        selected["program"], job["score_inputs"], job["score_outputs"], config
    )
    admission = None
    if selected["proposal_id"] is not None:
        admission = library.admit_verified(
            selected["proposal_id"],
            receipt_for(
                selected,
                checked,
                source_id,
                digest([namespace, method, job["id"], committed]),
            ),
            retain=method != "frozen_guarded",
        )
    dispositions = []
    for identity, pending in list(library.dump()["pending"].items()):
        # Selection is terminal. These actual buyer checks resolve abandoned
        # candidates without exposing targets to another search or retaining
        # an undelivered program. Correctness is recorded truthfully either way.
        checked_pending = outcome(
            pending["program"], job["score_inputs"], job["score_outputs"], config
        )
        candidate = {**pending, "proposal_id": identity}
        receipt = receipt_for(
            candidate,
            checked_pending,
            source_id,
            digest([namespace, method, job["id"], identity, "abstention-disposition"]),
        )
        library.admit_verified(identity, receipt, retain=False)
        dispositions.append(
            {
                "proposal_id": identity,
                "program_sha256": pending["program_sha256"],
                "program": pending["program"],
                "checked": checked_pending,
                "receipt": receipt,
            }
        )
    final = library.dump()
    if final["pending"]:
        raise ValueError("Episode leaves an unresolved provisional program")
    administration = units(before) + units(final) + units(session_dump)
    record = {
        "method": method,
        "job_id": job["id"],
        "family": job["family"],
        **selected,
        "probe_indices": indices,
        "actions": actions,
        "controller_work": work,
        "checked": checked,
        "admission": admission,
        "abstention_dispositions": dispositions,
        "programs_before": sorted(before["programs"]),
        "programs_after": sorted(final["programs"]),
        "library_before_sha256": digest(before),
        "library_after_sha256": digest(final),
        "library_before": before,
        "library_after": final,
        "session": session_dump,
        "committed_proposal_sha256": committed,
        "administration_work": administration,
        "total_work": work["total"]
        + checked["total_work"]
        + sum(item["checked"]["total_work"] for item in dispositions)
        + administration,
        "core_cpu_seconds": core_cpu,
    }
    # Include serialization, validation, final buyer checks and library admission
    # in the full-pipeline measurement for every branch. File I/O is separate.
    serial(record)
    record["cpu_seconds"] = time.process_time() - started
    return record, final


def summarize(records):
    grouped = defaultdict(list)
    for row in records:
        grouped[row["method"]].append(row)
    return {
        method: {
            "jobs": len(rows),
            "exact_successes": sum(r["checked"]["passed"] for r in rows),
            "proposed": sum(r["program"] is not None for r in rows),
            "wrong_proposals": sum(
                r["program"] is not None and not r["checked"]["passed"] for r in rows
            ),
            "abstentions": sum(r["program"] is None for r in rows),
            "labels": sum(len(r["probe_indices"]) for r in rows),
            "final_rows": sum(r["checked"]["rows"] for r in rows),
            "exact_final_rows": sum(r["checked"]["correct"] for r in rows),
            "total_work": sum(r["total_work"] for r in rows),
            "core_cpu_seconds": sum(r["core_cpu_seconds"] for r in rows),
            "cpu_seconds": sum(r["cpu_seconds"] for r in rows),
            "retained_selections": sum(
                r["selection"] == "retained" and r["checked"]["passed"] for r in rows
            ),
        }
        for method, rows in grouped.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("development", "reserved"), required=True)
    parser.add_argument("--seeds", default="0")
    parser.add_argument("--jobs", type=int, default=48)
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--maximum-work", type=int, default=200000)
    parser.add_argument("--synthesis-work", type=int, default=50000)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--freeze-commit")
    args = parser.parse_args()
    from scripts.build_guarded_skill_tasks import generate

    seeds = [int(s) for s in args.seeds.split(",")]
    methods = args.methods.split(",")
    if (
        len(seeds) != len(set(seeds))
        or not 1 <= len(seeds) <= 32
        or not 8 <= args.jobs <= 128
        or len(methods) != len(set(methods))
        or not set(methods) <= set(METHODS)
    ):
        raise ValueError("Finite paired study arguments are invalid")
    hashes = source_hashes()
    protocol_hash, freeze_verified_utc = None, None
    limits = {
        "maximum_work": args.maximum_work,
        "maximum_probes": 4,
        "synthesis_work": args.synthesis_work,
        "minimum_evidence": 2,
    }
    if args.phase == "development":
        if not set(seeds) <= {0, 1, 2, 3}:
            raise ValueError("Only declared development seeds may run before freeze")
    else:
        if args.protocol is None or args.freeze_commit is None:
            raise ValueError("Reserved execution requires a publicly frozen protocol")
        raw = args.protocol.read_bytes()
        protocol = json.loads(raw)
        expected = {
            "seeds": seeds,
            "jobs": args.jobs,
            "methods": methods,
            "limits": limits,
        }
        if protocol["terms"] != expected or protocol["source_hashes"] != hashes:
            raise ValueError("Execution differs from frozen terms or sources")
        with urllib.request.urlopen(
            "https://raw.githubusercontent.com/trynuria/nuria/"
            + args.freeze_commit
            + "/research/guarded-skills/protocol.json",
            timeout=20,
        ) as response:
            public = response.read(100001)
        if public != raw:
            raise ValueError("Public freeze does not match the private invocation")
        protocol_hash = hashlib.sha256(raw).hexdigest()
        freeze_verified_utc = datetime.now(timezone.utc).isoformat()
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    write_new(
        args.output / "started.json",
        {
            "phase": args.phase,
            "seeds": seeds,
            "jobs": args.jobs,
            "methods": methods,
            "limits": limits,
            "source_hashes": hashes,
            "status": "in_progress",
            "freeze_commit": args.freeze_commit,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_sha256": protocol_hash,
            "public_freeze_verified_utc": freeze_verified_utc,
        },
    )
    records, corpora, per_seed = [], [], []
    for ordinal, seed in enumerate(seeds):
        corpus = generate(seed, count=args.jobs)
        corpora.append(corpus)
        states = {method: None for method in methods}
        seed_rows = []
        for index, job in enumerate(corpus["tasks"]):
            rotation = (index + ordinal) % len(methods)
            order = methods[rotation:] + methods[:rotation]
            for method in order:
                record, states[method] = run_job(
                    states[method],
                    job,
                    method,
                    corpus["config"],
                    "nuria:guarded-study-v1",
                    limits,
                    digest([args.phase, seeds, seed, args.jobs]),
                )
                record["seed"] = seed
                record["ordinal"] = index
                records.append(record)
                seed_rows.append(record)
        per_seed.append({"seed": seed, "methods": summarize(seed_rows)})
    if source_hashes() != hashes:
        raise ValueError("Study sources changed during execution; result invalid")
    write_new(args.output / "corpora.json", corpora)
    write_new(args.output / "traces.json", records)
    summary = {
        "schema": "nuria.guarded-skill-study.v1",
        "phase": args.phase,
        "source_hashes": hashes,
        "methods": summarize(records),
        "per_seed": per_seed,
        "corpora_sha256": digest(corpora),
        "traces_sha256": digest(records),
        "scope": "Fresh generated engineering tasks, not external generalization, neural superiority or paid customer work. Full pipeline CPU and counted work include all failed work and buyer/state operations; neither is independently physically attested.",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_new(args.output / "summary.json", summary)
    print(serial(summary["methods"]))


if __name__ == "__main__":
    main()

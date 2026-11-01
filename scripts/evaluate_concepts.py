"""Reanalyse an existing PROSE export with entire task families held out.

Only the evaluator sees score rows. These previously inspected benchmark tasks
are development evidence, not a fresh external qualification or an AGI test.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import time
from collections import Counter
from pathlib import Path

from cognition.abstraction_search import prepare_grammar, solve_abstractions
from cognition.concept_learning import ConceptLibrary, digest, serial
from cognition.skills import fingerprint
from cognition.template_reuse import prepare_templates, solve_templates
from cognition.text_abstractions import extract_abstractions
from cognition.text_skills import DEFAULT_TEXT_CONFIG as CONFIG
from cognition.text_skills import execute_checked

METHODS = ("cold", "retained", "concepts", "templates", "native_abstractions")
ROOT = Path(__file__).resolve().parents[1]


def fold(family):
    return int(hashlib.sha256(family.encode()).hexdigest(), 16) % 3


def check(program, job):
    if program is None:
        return False, 0
    values = [execute_checked(program, value) for value in job["score_inputs"]]
    correct = sum(
        value["error"] is None and value["output"] == target
        for value, target in zip(values, job["score_outputs"], strict=True)
    )
    return bool(values) and correct == len(values), correct


def run(jobs):
    # The teacher is the unmodified composed solver, not held-out task recipes.
    empty = ConceptLibrary(
        {
            "version": "stitch-text-concepts-v1",
            "concepts": [],
            "programs": [],
            "training_sha256": digest([]),
        }
    )
    teacher, teacher_cpu = {}, time.process_time()
    for job in jobs:
        if not job["diagnostic_inputs"]:
            continue
        proposal = empty.solve(
            job["diagnostic_inputs"], job["diagnostic_outputs"], mode="cold"
        )
        exact, _ = check(proposal["program"], job)
        if exact:
            teacher[job["id"]] = proposal["program"]
    teacher_cpu = time.process_time() - teacher_cpu
    results, preparations = [], []
    for split in range(3):
        training = [
            j for j in jobs if fold(j["family"]) != split and j["id"] in teacher
        ]
        programs = [teacher[j["id"]] for j in training]
        started = time.process_time()
        learned = ConceptLibrary.learn(programs)
        learning_cpu = time.process_time() - started
        started = time.process_time()
        templates = prepare_templates(programs, CONFIG)
        template_cpu = time.process_time() - started
        records = [
            {
                "program": teacher[j["id"]],
                "program_sha256": fingerprint(teacher[j["id"]]),
                "origin_sha256": fingerprint(j["id"]),
                "evidence_sha256": fingerprint([j["score_inputs"], j["score_outputs"]]),
            }
            for j in training
        ]
        started = time.process_time()
        native = extract_abstractions(
            records, CONFIG, source_id="nuria:concept-cross-family-v1"
        )
        grammar = prepare_grammar(native, CONFIG)
        native_cpu = time.process_time() - started
        preparations.append(
            {
                "fold": split,
                "training_task_ids": [j["id"] for j in training],
                "training_families": sorted({j["family"] for j in training}),
                "learned": learned.state,
                "native_library": native,
                "template_report": templates.report,
                "cpu_seconds": {
                    "concepts": learning_cpu,
                    "templates": template_cpu,
                    "native_abstractions": native_cpu,
                },
            }
        )
        for mode in METHODS:
            for job in jobs:
                if fold(job["family"]) != split:
                    continue
                started = time.process_time()
                if not job["diagnostic_inputs"]:
                    proposal = {
                        "program": None,
                        "work": {"total": 0},
                        "source": "unsupported",
                    }
                elif mode == "templates":
                    proposal = solve_templates(
                        job["diagnostic_inputs"],
                        job["diagnostic_outputs"],
                        CONFIG,
                        templates,
                    )
                elif mode == "native_abstractions":
                    proposal = solve_abstractions(
                        job["diagnostic_inputs"],
                        job["diagnostic_outputs"],
                        CONFIG,
                        grammar,
                    )
                else:
                    proposal = learned.solve(
                        job["diagnostic_inputs"], job["diagnostic_outputs"], mode=mode
                    )
                serving_cpu = time.process_time() - started
                exact, correct = check(proposal["program"], job)
                results.append(
                    {
                        "id": job["id"],
                        "family": job["family"],
                        "fold": split,
                        "mode": mode,
                        "exact": exact,
                        "correct_rows": correct,
                        "score_rows": len(job["score_inputs"]),
                        "program": proposal["program"],
                        "work": proposal["work"],
                        "source": proposal.get("source", mode),
                        "cpu_seconds": serving_cpu,
                    }
                )
        print(f"Completed fold {split}", flush=True)
    summaries = {}
    cold = {r["id"]: r for r in results if r["mode"] == "cold"}
    for mode in METHODS:
        rows = [r for r in results if r["mode"] == mode]
        summaries[mode] = {
            "tasks": len(rows),
            "exact": sum(r["exact"] for r in rows),
            "wrong_proposals": sum(
                r["program"] is not None and not r["exact"] for r in rows
            ),
            "abstentions": sum(r["program"] is None for r in rows),
            "serving_work": sum(r["work"]["total"] for r in rows),
            "serving_cpu_seconds": sum(r["cpu_seconds"] for r in rows),
            "wins_vs_cold": [
                r["id"] for r in rows if r["exact"] and not cold[r["id"]]["exact"]
            ],
            "regressions_vs_cold": [
                r["id"] for r in rows if not r["exact"] and cold[r["id"]]["exact"]
            ],
        }
    return {
        "schema": "nuria.concept-transfer-development.v1",
        "jobs_sha256": digest(jobs),
        "python": platform.python_version(),
        "budget_per_task": 50000,
        "split": "sha256(family) modulo 3; no evaluation family in its training set",
        "family_sizes": dict(Counter(j["family"] for j in jobs)),
        "teacher": {
            "verified_programs": len(teacher),
            "acquisition_cpu_seconds": teacher_cpu,
        },
        "summaries": summaries,
        "preparations": preparations,
        "results": results,
        "limits": "Previously seen PROSE tasks; development comparison, not fresh qualification. Finite score rows can miss semantic errors. Work counters are not CPU instructions. No AGI or broad-generalization claim.",
    }, teacher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jobs", type=Path, help="Existing private PROSE job export")
    parser.add_argument("output", type=Path, help="New output directory")
    args = parser.parse_args()
    jobs = json.loads(args.jobs.read_text())
    args.output.mkdir()
    sources = [
        "cognition/concept_learning.py",
        "cognition/text_composition.py",
        "cognition/text_skills.py",
        "cognition/skills.py",
        "cognition/abstraction_search.py",
        "cognition/text_abstractions.py",
        "cognition/template_reuse.py",
        "scripts/evaluate_concepts.py",
        "requirements-concepts.txt",
    ]
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources}
    (args.output / "started.json").write_text(
        serial({"sources": hashes, "jobs_sha256": digest(jobs), "methods": METHODS})
        + "\n"
    )
    result, teacher = run(jobs)
    result["sources"] = hashes
    with gzip.open(args.output / "results.json.gz", "wt") as stream:
        json.dump(result, stream, sort_keys=True)
    summary = {
        key: value
        for key, value in result.items()
        if key not in ("results", "preparations")
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    # A usable model trained on all checked teacher programs is separate from
    # the held-out-fold models that produced the comparison above.
    library = ConceptLibrary.learn(list(teacher.values()))
    (args.output / "library.json").write_text(
        json.dumps(library.state, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result["summaries"], indent=2))


if __name__ == "__main__":
    main()

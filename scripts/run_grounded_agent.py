"""Run bounded analytical jobs; evaluator answers stay outside model messages."""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
from pathlib import Path

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace, grade_result
from cognition.grounded_agent import GroundedAnalyst
from cognition.procedural_memory import ProceduralMemory
from cognition.reasoner_client import LocalReasoner, digest


def read(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--course", required=True, type=Path)
    parser.add_argument("--oracle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:19487/v1/chat/completions"
    )
    parser.add_argument("--model-alias", default="default_model")
    parser.add_argument("--model-identity", required=True)
    parser.add_argument(
        "--memory-mode", choices=("none", "episode", "procedure"), default="none"
    )
    parser.add_argument("--memory", type=Path)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--max-tokens-per-turn", type=int, default=1536)
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument("--no-thinking", action="store_true")
    args = parser.parse_args()
    course, oracle = read(args.course), read(args.oracle)
    tasks = course["tasks"] if isinstance(course, dict) else course
    if (
        args.offset < 0
        or not 1 <= args.count <= 64
        or args.offset + args.count > len(tasks)
    ):
        raise ValueError("Requested course slice is invalid")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    workspace = AnalystWorkspace(args.registry)
    memory = ProceduralMemory(
        args.memory or args.output / "memory.sqlite",
        source_id=read(args.registry)["revision"],
        model_id=args.model_identity,
        tool_version=SCHEMA,
    )
    atexit.register(memory.close)
    reasoner = LocalReasoner(
        args.endpoint, args.model_alias, enable_thinking=not args.no_thinking
    )
    agent = GroundedAnalyst(
        reasoner,
        workspace,
        memory,
        memory_mode=args.memory_mode,
        max_turns=args.max_turns,
        max_output_tokens=args.max_output_tokens,
        max_tokens_per_turn=args.max_tokens_per_turn,
    )
    freeze = {
        "schema": "nuria.grounded-agent-course.v1",
        "configuration": agent.configuration,
        "thinking": reasoner.enable_thinking,
        "registry_sha256": workspace.registry_sha256,
        "course_file_sha256": hashlib.sha256(args.course.read_bytes()).hexdigest(),
        "oracle_file_sha256": hashlib.sha256(args.oracle.read_bytes()).hexdigest(),
        "selected_task_ids": [
            row["id"] for row in tasks[args.offset : args.offset + args.count]
        ],
        "seed": args.seed,
        "source_sha256": {
            name: hashlib.sha256(
                (Path(__file__).resolve().parents[1] / name).read_bytes()
            ).hexdigest()
            for name in (
                "cognition/reasoner_client.py",
                "cognition/grounded_agent.py",
                "cognition/working_evidence.py",
                "cognition/analyst_workspace.py",
                "cognition/procedural_memory.py",
                "scripts/run_grounded_agent.py",
            )
        },
        "scope": "Fixed course slice; original benchmark execution agreement, not SQL equivalence or model novelty",
    }
    with (args.output / "freeze.json").open("x") as stream:
        json.dump(freeze, stream, indent=2)
    reports = []
    with (args.output / "results.jsonl").open("x") as out:
        for task in tasks[args.offset : args.offset + args.count]:
            print(
                json.dumps(
                    {"event": "started", "id": task["id"], "db_id": task["db_id"]}
                ),
                flush=True,
            )
            result = agent.run(
                job_id=task["id"],
                question=task["question"],
                db_id=task["db_id"],
                seed=args.seed,
            )

            # Only this host-owned closure can read expected answers. It is never
            # a registered model tool or part of the model's prompt context.
            def checker(request):
                expected = oracle["tasks"][task["id"]]
                accepted = bool(
                    not request.get("abstained")
                    and result["selected_receipt"] is not None
                    and grade_result(
                        result["selected_receipt"],
                        expected["expected_rows"],
                        ordered=expected["ordered"],
                        expected_columns=expected["column_count"],
                    )
                )
                return {
                    "accepted": accepted,
                    "checker_id": "sqlite-execution-result-v1",
                    "evidence_sha256": digest(
                        {
                            "task": task["id"],
                            "proposal": request["proposal_sha256"],
                            "oracle": digest(expected),
                            "receipt": result["selected_receipt"],
                            "accepted": accepted,
                        }
                    ),
                }

            settlement = memory.settle(result["episode_id"], verify_terminal=checker)
            report = {
                "task_id": task["id"],
                "question": task["question"],
                "db_id": task["db_id"],
                "agent": result,
                "settlement": settlement,
            }
            out.write(json.dumps(report, allow_nan=False) + "\n")
            out.flush()
            os.fsync(out.fileno())
            reports.append(report)
            print(
                json.dumps(
                    {
                        "event": "completed",
                        "id": task["id"],
                        "status": result["status"],
                        "settlement": settlement,
                        "tool_calls": result["tool_calls"],
                        "tokens": result["model_usage"],
                        "wall_seconds": result["wall_seconds"],
                    }
                ),
                flush=True,
            )
    summary = {
        "jobs": len(reports),
        "accepted": sum(row["settlement"]["accepted"] for row in reports),
        "wrong_submissions": sum(
            row["agent"]["final_sql"] is not None and not row["settlement"]["accepted"]
            for row in reports
        ),
        "abstentions": sum(row["agent"]["final_sql"] is None for row in reports),
        "status_counts": {
            status: sum(row["agent"]["status"] == status for row in reports)
            for status in sorted({row["agent"]["status"] for row in reports})
        },
        "prompt_tokens": sum(
            row["agent"]["model_usage"]["prompt_tokens"] for row in reports
        ),
        "completion_tokens": sum(
            row["agent"]["model_usage"]["completion_tokens"] for row in reports
        ),
        "tool_calls": sum(row["agent"]["tool_calls"] for row in reports),
        "wall_seconds": sum(row["agent"]["wall_seconds"] for row in reports),
        "unknown_usage_jobs": sum(
            row["agent"]["model_usage"]["unknown_additional_usage"] for row in reports
        ),
    }
    with (args.output / "summary.json").open("x") as stream:
        json.dump(summary, stream, indent=2)
    memory.close()
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

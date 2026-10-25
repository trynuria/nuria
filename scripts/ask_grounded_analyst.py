"""Ask one registered-source question and retain its executed evidence privately.

This command starts no model service and performs no semantic self-grading.
An explicit model identity records the operator's runtime binding; it is not
remote weight attestation. A reserved job identity cannot be silently reused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from cognition.analyst_workspace import (
    MAX_DATABASE_BYTES,
    MAX_DOCUMENT_BYTES,
    SCHEMA,
    AnalystWorkspace,
)
from cognition.grounded_agent import GroundedAnalyst
from cognition.procedural_memory import ProceduralMemory
from cognition.reasoner_client import LocalReasoner, digest

VERSION = "nuria.registered-analysis-question.v1"


def _private_directory(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError("Analytical state directories cannot be symbolic links")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise ValueError("Analytical state requires a private mode-0700 directory")
    return path.resolve()


def _write_private(path, value):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _identity(state, workspace, model_identity):
    identity = {
        "schema": VERSION,
        "registry_sha256": workspace.registry_sha256,
        "source_id": workspace._manifest["revision"],
        "model_identity": model_identity,
        "tool_version": SCHEMA,
    }
    path = state / "identity.json"
    try:
        _write_private(path, identity)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError(
                "Analytical identity requires a private regular file"
            ) from None
        if json.loads(path.read_text()) != identity:
            raise ValueError(
                "Analytical state belongs to another source or model"
            ) from None


def _complete_own_query(result, memory, workspace, db_id, job_id):
    receipt = result.get("selected_receipt")
    if result.get("status") != "proposed" or receipt is None:
        return False
    state = memory.episode_state(result["episode_id"])
    source = workspace._manifest["databases"][db_id]
    workspace._registered_file(source["database"], MAX_DATABASE_BYTES)
    for document in source["documents"].values():
        workspace._registered_file(document, MAX_DOCUMENT_BYTES)
    if (
        receipt.get("status") != "ok"
        or receipt.get("complete") is not True
        or receipt.get("tool") != "query"
        or receipt.get("source") != result.get("source")
        or receipt["source"].get("registry_sha256") != workspace.registry_sha256
        or receipt["source"].get("db_sha256") != source["database"]["sha256"]
        or receipt["source"].get("db_id") != db_id
        or state["origin"]["job_id"] != job_id
        or state["proposal"] is None
        or state["proposal"]["proposal_sha256"] != result["proposal_sha256"]
        or state["proposal"]["final_sql"] != result.get("final_sql")
        or not any(
            action["ok"]
            and action["sql"] == result.get("final_sql")
            and action["result_sha256"] == receipt["result_sha256"]
            for action in state["actions"]
        )
    ):
        raise ValueError("The proposed answer lacks matching own execution evidence")
    return True


def ask(
    *,
    registry,
    state,
    model_identity,
    job_id,
    db_id,
    question,
    endpoint="http://127.0.0.1:19487/v1/chat/completions",
    model_alias="default_model",
    memory_mode="procedure",
    max_turns=12,
    max_output_tokens=6000,
    max_tokens_per_turn=1536,
    max_tool_calls=12,
    seed=20261025,
    enable_thinking=True,
    reasoner_factory=LocalReasoner,
):
    """Run one bounded attempt; retain unknown outcomes without inventing a grade.

    ``reasoner_factory`` is host-side test injection, never a command-line tool
    or a model-selected implementation. The default permits literal loopback
    HTTP only and opens no connection until the reserved attempt starts.
    """
    for value, maximum in ((job_id, 256), (question, 5000), (model_identity, 512)):
        if type(value) is not str or not 1 <= len(value.encode()) <= maximum:
            raise ValueError(
                "An explicit bounded job, question and model identity are required"
            )
    if type(seed) is not int or not 0 <= seed <= 2**32 - 25:
        raise ValueError("Analytical seed is outside its finite bound")
    for value, ceiling in (
        (max_turns, 12),
        (max_output_tokens, 6000),
        (max_tokens_per_turn, 1536),
        (max_tool_calls, 12),
    ):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise ValueError(
                "CLI inference limits can only lower the reviewed defaults"
            )
    workspace = AnalystWorkspace(registry)
    if type(db_id) is not str or db_id not in workspace._manifest["databases"]:
        raise ValueError("Database is not enrolled in the supplied registry")
    reasoner = reasoner_factory(endpoint, model_alias, enable_thinking=enable_thinking)
    directory = _private_directory(state)
    _identity(directory, workspace, model_identity)
    jobs = _private_directory(directory / "jobs")
    key = hashlib.sha256(job_id.encode()).hexdigest()
    attempt_path = jobs / (key + ".attempt.json")
    artifact_path = jobs / (key + ".artifact.json")
    output_path = jobs / (key + ".result.json")
    memory_path = directory / "memory.sqlite"
    if memory_path.is_symlink() or (memory_path.exists() and not memory_path.is_file()):
        raise ValueError("Analytical memory requires a private regular database file")
    memory = ProceduralMemory(
        memory_path,
        source_id=workspace._manifest["revision"],
        model_id=model_identity,
        tool_version=SCHEMA,
    )
    try:
        agent = GroundedAnalyst(
            reasoner,
            workspace,
            memory,
            memory_mode=memory_mode,
            max_turns=max_turns,
            max_output_tokens=max_output_tokens,
            max_tokens_per_turn=max_tokens_per_turn,
            max_tool_calls=max_tool_calls,
        )
        attempt = {
            "schema": VERSION,
            "job_id": job_id,
            "question": question,
            "db_id": db_id,
            "configuration": agent.configuration,
            "status": "reserved",
            "reserved_completion_tokens": max_output_tokens,
            "usage_unknown_until_result": True,
            "scope": "An interrupted attempt remains reserved. Use a new explicit "
            "job identity after inspecting and accounting for this attempt.",
        }
        try:
            _write_private(attempt_path, attempt)
        except FileExistsError:
            raise ValueError(
                "Job identity is already reserved; no implicit retry is allowed"
            ) from None
        try:
            result = agent.run(job_id=job_id, question=question, db_id=db_id, seed=seed)
            memory.verify()
            complete = _complete_own_query(result, memory, workspace, db_id, job_id)
            _write_private(artifact_path, result)
            receipt = result["selected_receipt"] if complete else None
            report = {
                "schema": VERSION,
                "job_id": job_id,
                "status": result["status"],
                "delivery_complete": complete,
                "semantic_accepted": None,
                "final_sql": result["final_sql"] if complete else None,
                "rows": receipt["rows"] if complete else None,
                "columns": receipt["columns"] if complete else None,
                "source": receipt["source"] if complete else result.get("source"),
                "execution_receipt_sha256": receipt["result_sha256"]
                if complete
                else None,
                "episode_id": result["episode_id"],
                "proposal_sha256": result["proposal_sha256"],
                "model_identity": model_identity,
                "model_usage": result["model_usage"],
                "reserved_completion_tokens": max_output_tokens,
                "unresolved_completion_ceiling": max_output_tokens
                - result["model_usage"]["completion_tokens"]
                if result["model_usage"]["unknown_additional_usage"]
                else 0,
                "tool_calls": result["tool_calls"],
                "artifact_sha256": hashlib.sha256(
                    artifact_path.read_bytes()
                ).hexdigest(),
                "artifact_path": str(artifact_path),
                "result_path": str(output_path),
                "scope": "SQL execution and source evidence only. Semantic correctness "
                "and usefulness remain unknown; no checker, settlement, "
                "payment or automatic learning credit was supplied.",
            }
            report["result_sha256"] = digest(report)
            _write_private(output_path, report)
            return report
        except BaseException as error:
            # Do not overwrite the reservation or repeat inference. This marker
            # cannot establish exact usage lost during a process interruption.
            interrupted = {
                "schema": VERSION,
                "job_id": job_id,
                "status": "interrupted_or_unresolved",
                "error_type": type(error).__name__,
                "usage_unknown": True,
                "reserved_completion_tokens": max_output_tokens,
                "semantic_accepted": None,
            }
            try:
                _write_private(jobs / (key + ".unresolved.json"), interrupted)
            except OSError:
                pass
            raise
    finally:
        memory.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--model-identity", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--db-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:19487/v1/chat/completions"
    )
    parser.add_argument("--model-alias", default="default_model")
    parser.add_argument(
        "--memory-mode", choices=("none", "episode", "procedure"), default="procedure"
    )
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--max-tokens-per-turn", type=int, default=1536)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument("--no-thinking", action="store_true")
    args = vars(parser.parse_args(argv))
    args["enable_thinking"] = not args.pop("no_thinking")
    try:
        report = ask(**args)
    except KeyboardInterrupt:
        print(
            "Interrupted; this job remains reserved in private state.", file=sys.stderr
        )
        return 130
    except (ValueError, OSError, RuntimeError) as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, allow_nan=False, indent=2))
    return 0 if report["delivery_complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

"""Run one registered-source question with a separately bounded semantic circuit.

The host reserves one encoding and at most 6,000 completion tokens before either
request. A completed SQL receipt is not a correctness grade. Independent terminal
checking and learning credit remain separate host operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace
from cognition.grounded_agent import GroundedAnalyst
from cognition.procedural_memory import ProceduralMemory, digest
from cognition.reasoner_client import LocalReasoner
from cognition.semantic_neural import STRATEGIES, SemanticNeuralBridge
from cognition.semantic_worker import OneShotSemanticEncoder
from scripts.ask_grounded_analyst import (
    _complete_own_query,
    _identity,
    _private_directory,
    _write_private,
)

VERSION = "nuria.semantic-analysis-question.v1"


def ask(
    *,
    registry,
    state,
    model_identity,
    job_id,
    db_id,
    question,
    cpu_python,
    model_directory,
    semantic_manifest,
    worker_sha256,
    endpoint="http://127.0.0.1:19487/v1/chat/completions",
    model_alias="default_model",
    max_output_tokens=6000,
    seed=20261025,
    reasoner_factory=LocalReasoner,
    encoder_factory=OneShotSemanticEncoder,
):
    """One reserved attempt with no automatic retry and no model grading tool.

    Factories are host-only unit-test injection; command-line callers cannot
    nominate implementation modules. The host must enforce its cumulative
    allowance before invoking this finite command.
    """
    for value, maximum in ((job_id, 256), (question, 5000), (model_identity, 512)):
        if type(value) is not str or not 0 < len(value.encode()) <= maximum:
            raise ValueError("Explicit bounded question, job and model are required")
    if type(seed) is not int or not 0 <= seed <= 2**32 - 25:
        raise ValueError("Analytical seed exceeds its bound")
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 6000:
        raise ValueError("Completion allowance can only lower the 6,000-token cap")
    if (
        type(worker_sha256) is not str
        or len(worker_sha256) != 64
        or set(worker_sha256) - set("0123456789abcdef")
    ):
        raise ValueError("An exact reviewed CPU worker SHA256 is required")
    workspace = AnalystWorkspace(registry)
    if type(db_id) is not str or db_id not in workspace._manifest["databases"]:
        raise ValueError("Question database is not enrolled")
    reasoner = reasoner_factory(endpoint, model_alias, enable_thinking=True)
    encoder = encoder_factory(
        cpu_python,
        model_directory,
        semantic_manifest,
        max_calls=1,
        timeout_seconds=30,
        worker_sha256=worker_sha256,
    )
    directory = _private_directory(state)
    _identity(directory, workspace, model_identity)
    jobs = _private_directory(directory / "jobs")
    key = hashlib.sha256(job_id.encode()).hexdigest()
    paths = {
        kind: jobs / (key + "." + kind + ".json")
        for kind in ("attempt", "decision", "artifact", "result", "unresolved")
    }
    memory_path = directory / "memory.sqlite"
    if memory_path.is_symlink() or (memory_path.exists() and not memory_path.is_file()):
        raise ValueError("Analytical memory requires a regular private file")
    memory = ProceduralMemory(
        memory_path,
        source_id=workspace._manifest["revision"],
        model_id=model_identity,
        tool_version=SCHEMA,
    )
    bridge = None
    try:
        bridge = SemanticNeuralBridge(
            _private_directory(directory / "semantic-lab"),
            source_id=workspace._manifest["revision"],
            model_id=model_identity,
            tool_version=SCHEMA,
            encoder=encoder,
        )
        context = {
            "schema": VERSION,
            "job_id": job_id,
            "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
            "db_id": db_id,
            "source_id": workspace._manifest["revision"],
            "registry_sha256": workspace.registry_sha256,
            "model_identity": model_identity,
            "encoder_binding_sha256": encoder.binding_sha256,
            "semantic_terms_sha256": bridge.semantic_terms_sha256,
            "limits": {
                "turns": 12,
                "completion_tokens": max_output_tokens,
                "tokens_per_turn": 1536,
                "tool_calls": 12,
                "cpu_encodes": 1,
            },
            "seed": seed,
        }
        attempt = {
            **context,
            "question": question,
            "context_sha256": digest(context),
            "status": "reserved",
            "usage_unknown_until_result": True,
            "reserved_completion_tokens": max_output_tokens,
            "reserved_cpu_encodes": 1,
        }
        try:
            _write_private(paths["attempt"], attempt)
        except FileExistsError:
            raise ValueError(
                "Job is already reserved; implicit retry refused"
            ) from None
        try:
            statistics = {tool: memory.outcome_stats(tool) for tool in STRATEGIES}
            decision = bridge.choose(job_id, digest(context), question, statistics)
            _write_private(paths["decision"], decision)
            neural_context = {
                key: decision[key]
                for key in ("first_tool", "utilities", "action_spikes", "window_ms")
            }
            agent = GroundedAnalyst(
                reasoner,
                workspace,
                memory,
                memory_mode="procedure",
                max_turns=12,
                max_output_tokens=max_output_tokens,
                max_tokens_per_turn=1536,
                max_tool_calls=12,
                initial_tool=decision["first_tool"],
                neural_context=neural_context,
            )
            result = agent.run(job_id=job_id, question=question, db_id=db_id, seed=seed)
            memory.verify()
            complete = _complete_own_query(result, memory, workspace, db_id, job_id)
            _write_private(paths["artifact"], result)
            query = result["selected_receipt"] if complete else None
            unknown = result["model_usage"]["unknown_additional_usage"]
            report = {
                "schema": VERSION,
                "job_id": job_id,
                "status": result["status"],
                "delivery_complete": complete,
                "semantic_accepted": None,
                "neural_credited": False,
                "first_tool": decision["first_tool"],
                "semantic_decision_sha256": decision["decision_sha256"],
                "semantic_observation": decision["semantic_observation"],
                "semantic_terms_sha256": bridge.semantic_terms_sha256,
                "final_sql": result["final_sql"] if complete else None,
                "rows": query["rows"] if complete else None,
                "columns": query["columns"] if complete else None,
                "source": query["source"] if complete else result.get("source"),
                "execution_receipt_sha256": query["result_sha256"]
                if complete
                else None,
                "episode_id": result["episode_id"],
                "proposal_sha256": result["proposal_sha256"],
                "model_identity": model_identity,
                "model_usage": result["model_usage"],
                "reserved_completion_tokens": max_output_tokens,
                "unresolved_completion_ceiling": max_output_tokens
                - result["model_usage"]["completion_tokens"]
                if unknown
                else 0,
                "reserved_cpu_encodes": 1,
                "completed_cpu_encodes": 1,
                "unresolved_cpu_encode_ceiling": 0,
                "artifact_sha256": hashlib.sha256(
                    paths["artifact"].read_bytes()
                ).hexdigest(),
                "decision_file_sha256": hashlib.sha256(
                    paths["decision"].read_bytes()
                ).hexdigest(),
                "tool_calls": result["tool_calls"],
                "paths": {
                    kind: str(path)
                    for kind, path in paths.items()
                    if kind != "unresolved"
                },
                "scope": "One actual semantic first-tool selection and source-bound SQL evidence; correctness and neural credit await a separate host terminal checker. No neural advantage is established.",
            }
            report["result_sha256"] = digest(report)
            _write_private(paths["result"], report)
            return report
        except BaseException as error:
            marker = {
                "schema": VERSION,
                "job_id": job_id,
                "status": "interrupted_or_unresolved",
                "error_type": type(error).__name__,
                "reserved_completion_tokens": max_output_tokens,
                "reserved_cpu_encodes": 1,
                "usage_unknown": True,
                "semantic_accepted": None,
                "neural_credited": False,
                "scope": "Reservation retained; reconcile actual receipts before another explicitly reviewed attempt.",
            }
            try:
                _write_private(paths["unresolved"], marker)
            except OSError:
                pass
            raise
    finally:
        if bridge is not None:
            bridge.close()
        memory.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "registry",
        "state",
        "cpu-python",
        "model-directory",
        "semantic-manifest",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ("model-identity", "job-id", "db-id", "question", "worker-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:19487/v1/chat/completions"
    )
    parser.add_argument("--model-alias", default="default_model")
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=20261025)
    try:
        report = ask(**vars(parser.parse_args(argv)))
    except KeyboardInterrupt:
        print(
            "Interrupted; both allowances remain reserved in private state.",
            file=sys.stderr,
        )
        return 130
    except (ValueError, OSError, RuntimeError) as error:
        print(
            json.dumps({"status": "error", "error_type": type(error).__name__}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["delivery_complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

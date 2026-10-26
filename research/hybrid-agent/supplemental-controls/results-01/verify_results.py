"""Replay sealed supplemental controls without inference or pickle loading.

Raw-run access requires explicit completion and absent guarded processes. A
portable package instead binds already closed records by exact archive hashes.
Local journals establish consistency, not external execution/time attestation.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import shutil
import sqlite3
import stat
import sys
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from urllib.parse import quote

CONDITIONS = ("schema_first", "stateless")
PROTOCOL_SHA = "130b963e08cf0c83db8cae5ee11b1bd3b0f9b0899fa5fac007ec50ed086c6869"
FREEZE_SHA = "a4cff862bda1b3f7136726106f468e5f8153af0372f5cbe437bc3c37965fe342"
HELPER_SHA = "1cb1a7d02659844485436dce090cdc5d235f886a83b1d88d4354acc0628131fa"
LIMITS = {
    "max_turns": 12,
    "max_output_tokens": 6000,
    "max_tool_calls": 12,
    "max_tokens_per_turn": 1536,
    "memory_bytes": 6000,
}
MAX_FILE = 32 * 1024**2
MAX_TOTAL = 256 * 1024**2


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    )


def read(path, maximum=MAX_FILE):
    path = Path(path)
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= maximum,
        "Missing, indirect or oversized file",
    )
    raw = path.read_bytes()
    require(len(raw) <= maximum, "File grew beyond its bound")
    return raw


def load(path):
    return json.loads(read(path))


def lines(path):
    return (
        [json.loads(x) for x in read(path).decode().splitlines()]
        if Path(path).exists()
        else []
    )


def alive(pid):
    require(type(pid) is int and pid > 1, "Invalid process guard")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ended_guard(run, pids, confirmed):
    # Check process state before opening any experimental record.
    require(confirmed is True and bool(pids), "Explicit process-end review required")
    require(
        all(not alive(pid) for pid in pids), "An inference process is still present"
    )
    require(
        (run / "summary.json").is_file() and (run / "selected-results.json").is_file(),
        "Final records are absent",
    )


def exact_int(value, low, high):
    require(type(value) is int and low <= value <= high, "Invalid exact count")
    return value


def account_fields(row, *, known, prompt, pending, unknown, stop=None):
    expected = {
        "new_known_completion_tokens": known,
        "new_known_prompt_tokens": prompt,
        "pending_completion_reservation": pending,
        "new_unknown_usage": unknown,
        "other_supplementary_reserved_completion_tokens": 0,
        "other_supplementary_known_completion_tokens": 3499,
        "control_conservative_charge": known + pending,
        "additional_conservative_charge": known + pending + 3499,
        "prior_conservative_charge": 186103,
        "cumulative_conservative_charge": 186103 + known + pending,
    }
    require(
        all(row.get(k) == v and type(row[k]) is type(v) for k, v in expected.items()),
        "Allowance arithmetic differs",
    )
    require(
        expected["additional_conservative_charge"] <= 60000
        and expected["cumulative_conservative_charge"] <= 260000,
        "Shared conservative allowance exceeded",
    )
    for key in ("elapsed_seconds", "remaining_seconds"):
        require(
            type(row.get(key)) in (int, float) and math.isfinite(row[key]),
            "Invalid recorded wall value",
        )
    require(
        row["elapsed_seconds"] >= 0
        and row["remaining_seconds"] <= 3600 - row["elapsed_seconds"] + 0.01,
        "Recorded relative wall allowance differs",
    )
    if stop is not None:
        require(row.get("stop_reason") == stop, "Unknown usage did not stop inference")


def check_allowance(journal, proposals, summary):
    require(len(journal) == 2 * len(proposals), "Reservation/closure count differs")
    known = prompt = pending = 0
    unknown, last_elapsed, stopped = False, 0.0, None
    for i, proposal in enumerate(proposals):
        reserved, closed = journal[2 * i : 2 * i + 2]
        require(
            not unknown and pending == 0 and stopped is None,
            "Inference continued after ambiguous usage or a sticky stop",
        )
        require(
            reserved.get("event") == "job_reserved"
            and [reserved.get(k) for k in ("condition", "task_id")]
            == [proposal[k] for k in ("condition", "task_id")]
            and reserved["remaining_seconds"] > 1
            and reserved["elapsed_seconds"] < 3600
            and reserved.get("stop_reason") is None,
            "Job began without its declared identity or live allowance",
        )
        account_fields(
            reserved, known=known, prompt=prompt, pending=6000, unknown=False
        )
        require(
            reserved["elapsed_seconds"] >= last_elapsed,
            "Recorded allowance clock reversed",
        )
        usage = proposal["observed_usage"]
        used = exact_int(usage["completion_tokens"], 0, 6000)
        prompt_used = exact_int(usage["prompt_tokens"], 0, 2**63 - 1)
        require(
            type(usage["unknown_additional_usage"]) is bool,
            "Unknown usage is not explicit",
        )
        unknown = usage["unknown_additional_usage"]
        known += used
        prompt += prompt_used
        pending = 6000 - used if unknown else 0
        require(closed.get("event") == "job_closed", "Missing ordered closure")
        account_fields(
            closed,
            known=known,
            prompt=prompt,
            pending=pending,
            unknown=unknown,
            stop="new_unreconciled_model_usage" if unknown else None,
        )
        stopped = closed.get("stop_reason")
        require(
            stopped
            in (
                None,
                "new_unreconciled_model_usage",
                "supplementary_host_error_requires_review",
            ),
            "Unexpected per-job stop",
        )
        if stopped == "supplementary_host_error_requires_review":
            require(
                proposal.get("status") == "host_error",
                "Host-error stop lacks an errored attempt",
            )
        require(
            closed["elapsed_seconds"] >= reserved["elapsed_seconds"],
            "Recorded closure preceded reservation",
        )
        last_elapsed = closed["elapsed_seconds"]
    final = summary["allowance"]
    account_fields(
        final,
        known=known,
        prompt=prompt,
        pending=pending,
        unknown=unknown,
        stop="new_unreconciled_model_usage" if unknown else None,
    )
    require(
        final["elapsed_seconds"] >= last_elapsed,
        "Final allowance preceded a job closure",
    )
    final_stop = final.get("stop_reason")
    require(stopped is None or final_stop == stopped, "A sticky stop was cleared")
    require(
        final_stop
        in (
            None,
            "new_unreconciled_model_usage",
            "supplementary_host_error_requires_review",
            "additional_completion_reservation_limit",
            "cumulative_completion_reservation_limit",
            "supplementary_wall_or_runtime_deadline",
        ),
        "Unknown terminal stop",
    )
    if len(proposals) < 24:
        require(final_stop is not None, "Incomplete coverage lacks a terminal stop")
    if final_stop == "additional_completion_reservation_limit":
        require(
            known + pending + 3499 + 6000 > 60000,
            "Additional-cap stop had full next-job headroom",
        )
    if final_stop == "cumulative_completion_reservation_limit":
        require(
            186103 + known + pending + 6000 > 260000,
            "Cumulative-cap stop had full next-job headroom",
        )
    if final_stop == "supplementary_wall_or_runtime_deadline":
        require(
            final["remaining_seconds"] <= 1, "Wall stop retained time for disclosure"
        )
    return {
        "known_completion_tokens": known,
        "known_prompt_tokens": prompt,
        "pending_completion_ceiling": pending,
        "new_unknown_usage": unknown,
        "historical_unknown_completion_ceiling": 12214,
        "semantic_completion_inside_additional_cap": 3499,
        "cumulative_conservative_charge": 186103 + known + pending,
    }


def check_events(events, summary, allowance):
    require(
        [e["event"] for e in events]
        == ["supplementary_started", "all_supplementary_inference_ended"],
        "Run did not close cleanly before its grading phase",
    )
    require(
        events[0]["original_study_mutated"] is False,
        "Original study mutation was reported",
    )
    start, end = events[0]["account"], events[1]["account"]
    account_fields(start, known=0, prompt=0, pending=0, unknown=False)
    require(start.get("stop_reason") is None, "Run started with a stopped allowance")
    account_fields(
        end,
        known=allowance["known_completion_tokens"],
        prompt=allowance["known_prompt_tokens"],
        pending=allowance["pending_completion_ceiling"],
        unknown=allowance["new_unknown_usage"],
    )
    require(
        end.get("stop_reason") == summary["allowance"].get("stop_reason")
        and start["elapsed_seconds"]
        <= end["elapsed_seconds"]
        <= summary["allowance"]["elapsed_seconds"],
        "Inference-end account snapshot differs from final accounting",
    )


def check_coverage(selected, proposals, outcomes, tasks):
    planned = [(c, t["id"]) for c in CONDITIONS for t in tasks]

    def key(row):
        return row["condition"], row["task_id"]

    require(
        len(tasks) == 12
        and len({t["id"] for t in tasks}) == 12
        and len(selected) == 24
        and list(map(key, selected)) == planned,
        "All twenty-four selected records must remain in planned order",
    )
    require(
        list(map(key, proposals)) == planned[: len(proposals)],
        "Attempted proposal order differs",
    )
    by_key = {key(r): r for r in selected}
    attempted = set(map(key, proposals))
    for row in proposals:
        require(
            row.get("evaluation") is None
            and {k: v for k, v in row.items() if k != "evaluation"}
            == {k: v for k, v in by_key[key(row)].items() if k != "evaluation"},
            "A committed proposal changed after inference",
        )
    for row in selected:
        if key(row) not in attempted:
            require(
                row["agent"] is None
                and row["evaluation"] is None
                and row["status"] == "not_executed"
                and row["observed_usage"]
                == {
                    "completion_tokens": 0,
                    "prompt_tokens": 0,
                    "unknown_additional_usage": False,
                },
                "An unattempted case contains activity",
            )
    complete = {}
    expected_outcomes = []
    for condition in CONDITIONS:
        batch = [r for r in selected if r["condition"] == condition]
        complete[condition] = all(r["agent"] is not None for r in batch)
        require(
            all((r["evaluation"] is not None) == complete[condition] for r in batch),
            "An incomplete condition was graded or a complete condition lacks grades",
        )
        if complete[condition]:
            expected_outcomes.extend(batch)
    require(outcomes == expected_outcomes, "Terminal outcome journal differs")
    return complete


def replay_request_hashes(agent, task, protocol, tool_schema):
    # Rebuild only the frozen host's public message construction. No request is sent.
    from cognition.grounded_agent import SYSTEM, _receipt_view
    from cognition.working_evidence import WorkingEvidence

    evidence = WorkingEvidence()
    history, actions, completion, checked = [], {}, 0, 0
    initial = {
        "job_id": task["id"],
        "question": task["question"],
        "db_id": task["db_id"],
        "tools": tool_schema,
        "historical_experience": "",
        "neural_context": None,
        "allowance": {"turns": 12, "completion_tokens": 6000},
    }
    for position, event in enumerate(agent["trace"]):
        kind, turn = event["kind"], event["turn"]
        if kind in ("host_initial_action", "action"):
            actions[turn] = event["action"]
        if kind in ("action", "invalid_action", "model_error"):
            messages = [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {**initial, "current_evidence": evidence.view()},
                        ensure_ascii=False,
                    ),
                },
                *history[-4:],
            ]
            body = {
                "model": protocol["model_alias"],
                "messages": messages,
                "max_tokens": min(6000 - completion, 1536),
                "temperature": 0,
                "seed": 20261025 + turn,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": True},
            }
            model = (
                event.get("model", {})
                if kind != "model_error"
                else event.get("diagnostic", {})
            )
            require(
                model.get("request_sha256") == digest(body),
                "Reasoning request hash does not reconstruct from retained host observations",
            )
            checked += 1
            usage = event.get("usage") if kind == "model_error" else model["usage"]
            if usage is not None:
                completion += usage["completion_tokens"]
            if kind == "action":
                action = event["action"]
                history.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(action, ensure_ascii=False),
                    }
                )
                if action["tool"] == "final" and not any(
                    row["kind"] == "tool_receipt"
                    and actions.get(row["turn"], {}).get("tool") == "query"
                    and row["receipt"].get("status") == "ok"
                    and row["receipt"].get("complete") is True
                    and actions.get(row["turn"], {}).get("arguments", {}).get("sql")
                    == action["arguments"]["sql"]
                    for row in agent["trace"][:position]
                ):
                    history.append(
                        {
                            "role": "user",
                            "content": "Execute this exact SQL with query first. Only a complete successful own query can be submitted.",
                        }
                    )
            elif kind == "invalid_action":
                history.extend(
                    [
                        {"role": "assistant", "content": "Invalid structured action"},
                        {
                            "role": "user",
                            "content": 'Return exactly one valid JSON object with "tool" and "arguments". No other text.',
                        },
                    ]
                )
            elif usage is not None and event.get("code") == "model_output_incomplete":
                history.append(
                    {
                        "role": "user",
                        "content": "The response allowance ended before a final action. Choose one concise JSON action using current_evidence; avoid repeating known metadata.",
                    }
                )
        elif kind == "tool_receipt":
            action, receipt = actions[turn], event["receipt"]
            evidence.observe(action, receipt)
            history.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {"observed_tool_receipt": _receipt_view(receipt)},
                        ensure_ascii=False,
                    ),
                }
            )
    return checked


def bounded_file(root, record):
    name = PurePosixPath(record["path"])
    require(
        not name.is_absolute() and all(p not in ("..", ".") for p in name.parts),
        "Registered path escapes its source",
    )
    file = root / str(name)
    require(
        file.resolve().is_relative_to(root.resolve())
        and sha(read(file)) == record["sha256"],
        "Registered file identity differs",
    )
    return file


def immutable_memory_records(directory, binding, replay):
    path = directory / "memory.sqlite"
    read(path)
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        require(
            connection.execute(
                "SELECT payload FROM memory_identity WHERE id=1"
            ).fetchone()
            == (replay.serial(binding),),
            "Memory binding differs",
        )
        rows = connection.execute("SELECT * FROM memory_events ORDER BY seq").fetchall()
    records = []
    episodes = {}
    parent = "0" * 64
    for count, (seq, previous, head, kind, episode, item, raw) in enumerate(rows, 1):
        value = json.loads(raw)
        require(
            seq == count
            and previous == parent
            and sha((parent + raw).encode()) == head
            and value["binding_sha256"] == replay.digest(binding)
            and value["schema"] == "nuria.procedural-memory.v1"
            and [value[k] for k in ("kind", "episode_id", "item_id")]
            == [kind, episode, item],
            "Memory journal chain differs",
        )
        record = {"seq": seq, "previous_hash": previous, "hash": head, **value}
        records.append(record)
        episodes.setdefault(episode, []).append(record)
        parent = head
    exported = load(directory / "memory-export.json")
    require(
        exported["binding"] == binding
        and exported["records"] == records
        and exported["head"] == parent
        and exported["events"] == len(records)
        and exported["binding_sha256"] == replay.digest(binding)
        and exported["truncated"] is False,
        "Memory export differs",
    )
    return episodes, records


def verify(
    run,
    protocol_path,
    freeze_path,
    inputs_root,
    closure_path,
    helper_path,
    publication_path,
    repository,
):
    require(
        sha(read(protocol_path)) == PROTOCOL_SHA
        and sha(read(freeze_path)) == FREEZE_SHA
        and sha(read(helper_path)) == HELPER_SHA,
        "Frozen source or protocol identity differs",
    )
    protocol, freeze, closure = (
        load(protocol_path),
        load(freeze_path),
        load(closure_path),
    )
    publication = load(publication_path)
    require(
        publication["exact_bytes_verified"] is True
        and publication["helper_sha256"] == HELPER_SHA
        and publication["protocol_sha256"] == PROTOCOL_SHA,
        "Prior public protocol identity differs",
    )
    closure_records = {
        "main_summary": "main-summary.json",
        "post_accounting": "post-accounting.json",
        "semantic_terminal": "semantic-terminal.json",
    }
    retained = {}
    for key, name in closure_records.items():
        path = closure_path.parent / name
        require(
            sha(read(path)) == protocol["starting_evidence"][key],
            "Prior closure record identity differs",
        )
        retained[key] = load(path)
    require(
        closure
        == {
            "schema": "nuria.supplementary-prior-closure.v1",
            "main": retained["main_summary"]["allowance"],
            "ordinary": retained["post_accounting"]["allowance"],
            "semantic": retained["semantic_terminal"]["allowance"],
        },
        "Prior closure projection differs from its pinned original records",
    )
    require(
        protocol["condition_order"] == list(CONDITIONS)
        and protocol["limits"] == LIMITS
        and protocol["memory_mode"] == "none"
        and protocol["neural_context"] is None
        and protocol["seed"] == 20261025
        and protocol["other_supplementary_known_completion_tokens"] == 3499
        and protocol["other_supplementary_reserved_completion_tokens"] == 0
        and protocol["starting_account"]["known_completion_tokens"] == 173889
        and protocol["starting_account"]["unknown_completion_ceiling"] == 12214
        and protocol["starting_account"]["prior_conservative_charge"] == 186103,
        "Reviewed control design or starting liabilities differ",
    )
    require(
        closure["main"]["known_completion_tokens"] == 165541
        and closure["main"]["prior_unknown_completion_ceiling"] == 12214
        and closure["ordinary"]["new_known_completion_tokens"] == 4849
        and closure["semantic"]["new_known_completion_tokens"] == 3499
        and closure["semantic"]["current_unknown_usage"] is False
        and closure["semantic"]["pending_completion_reservation"] == 0
        and 165541 + 4849 + 3499 + 12214 == 186103,
        "Prior closure accounting differs",
    )
    for name, expected in freeze["files"].items():
        path = (repository / name).resolve()
        require(
            path.is_relative_to(repository.resolve())
            and sha(read(path)) == expected
            and protocol["frozen_source_files"][name] == expected,
            "Frozen application source identity differs",
        )
    run_freeze = load(run / "run-freeze.json")
    require(
        run_freeze["protocol"] == protocol
        and run_freeze["protocol_sha256"] == PROTOCOL_SHA
        and run_freeze["source_freeze_sha256"] == FREEZE_SHA
        and run_freeze["publication_receipt_sha256"] == sha(read(publication_path)),
        "Run freeze is not bound to its published protocol",
    )
    summary, selected = load(run / "summary.json"), load(run / "selected-results.json")
    paths = {k: bounded_file(inputs_root, v) for k, v in protocol["artifacts"].items()}
    tasks = load(paths["reserved_course"])["tasks"]
    require(
        [t["id"] for t in tasks] == protocol["selected_task_ids"],
        "Selected course differs",
    )
    proposals, outcomes = lines(run / "proposals.jsonl"), lines(run / "outcomes.jsonl")
    complete = check_coverage(selected, proposals, outcomes, tasks)
    allowance = check_allowance(lines(run / "allowance.jsonl"), proposals, summary)
    events = lines(run / "events.jsonl")
    check_events(events, summary, allowance)
    require(
        summary["posthoc"] is True
        and summary["original_study_mutated"] is False
        and summary["planned_records"] == 24
        and summary["financial_transactions"] == 0
        and summary["source_verified_at_end"] is True
        and summary["terminal_error"] is None,
        "Finalization identity or source review differs",
    )
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(repository))
    from cognition.analyst_workspace import AnalystWorkspace
    from scripts import verify_grounded_comparison as replay

    registry = load(paths["registry"])
    databases = {
        k: bounded_file(paths["registry"].parent, v["database"])
        for k, v in registry["databases"].items()
    }
    for database in registry["databases"].values():
        for record in database["documents"].values():
            bounded_file(paths["registry"].parent, record)
    workspace = AnalystWorkspace(paths["registry"])
    system = next(
        ast.literal_eval(n.value)
        for n in ast.parse(
            read(repository / "cognition/grounded_agent.py").decode()
        ).body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SYSTEM" for t in n.targets)
    )
    binding = {
        "schema": "nuria.procedural-memory.v1",
        "source_id": registry["revision"],
        "model_id": protocol["model_identity"],
        "tool_version": "nuria.analyst-workspace.v1",
    }
    by_id = {t["id"]: t for t in tasks}
    # Oracle content is opened only now, after all completion guards and source checks.
    oracle = load(paths["oracle"])["tasks"]
    queries = requests = 0
    conditions, metrics = {}, {}
    for condition in CONDITIONS:
        episodes, records = immutable_memory_records(run / condition, binding, replay)
        require(
            not any(r["kind"] == "settlement" for r in records),
            "Supplemental outcome entered retrievable memory",
        )
        batch = [r for r in selected if r["condition"] == condition]
        agent_ids = {r["agent"]["episode_id"] for r in batch if r["agent"] is not None}
        attempted_ids = {r["task_id"] for r in proposals if r["condition"] == condition}
        require(
            agent_ids <= set(episodes)
            and {rows[0]["record"]["job_id"] for rows in episodes.values()}
            <= attempted_ids,
            "Memory episode denominator differs",
        )
        for row in batch:
            task, agent = by_id[row["task_id"]], row["agent"]
            require(row["db_id"] == task["db_id"], "Database identity differs")
            if agent is None:
                continue
            usage = replay.verify_agent(
                agent,
                task,
                condition,
                registry,
                replay.digest(registry, ascii=False),
                episodes[agent["episode_id"]],
                freeze["files"],
                system_sha=replay.digest(system),
                history=records,
            )
            require(
                agent["configuration"]["model_binding"] == binding
                and agent["configuration"]["reasoning_mode"] is True
                and row["status"] == agent["status"]
                and row["observed_usage"]
                == {
                    **usage,
                    "unknown_additional_usage": agent["model_usage"][
                        "unknown_additional_usage"
                    ],
                },
                "Agent identity or host usage differs",
            )
            requests += replay_request_hashes(
                agent, task, protocol, workspace.tool_schema
            )
            observed, columns = (None, None)
            if agent["final_sql"] is not None:
                observed, columns = replay.sql_result(
                    databases[task["db_id"]], agent["final_sql"]
                )
                require(
                    replay.rows_equal(observed, agent["selected_receipt"]["rows"], True)
                    and columns == len(agent["selected_receipt"]["columns"]),
                    "Final query does not reproduce its own receipt",
                )
                queries += 1
            if complete[condition]:
                expected = oracle[row["task_id"]]
                require(
                    expected["db_id"] == task["db_id"]
                    and expected["split"] == "reserved"
                    and expected["gradeable"] is True,
                    "Oracle task identity differs",
                )
                reference, count = replay.sql_result(
                    databases[task["db_id"]], expected["reference_sql"]
                )
                require(
                    count == expected["column_count"]
                    and replay.rows_equal(
                        reference, expected["expected_rows"], expected["ordered"]
                    ),
                    "Stored oracle result does not reproduce",
                )
                accepted = bool(
                    observed is not None
                    and columns == count
                    and replay.rows_equal(observed, reference, expected["ordered"])
                )
                require(
                    row["evaluation"]["accepted"] is accepted
                    and row["evaluation"]["proposal_sha256"] == agent["proposal_sha256"]
                    and row["evaluation"]["oracle_sha256"] == replay.digest(expected)
                    and row["evaluation"]["checker_id"]
                    == "posthoc-finite-sqlite-result-v1",
                    "Recorded acceptance differs from independent SQL replay",
                )
        conditions[condition] = {
            "selected": 12,
            "executed": sum(r["agent"] is not None for r in batch),
            "skipped_original_complete": False,
            "graded": sum(r["evaluation"] is not None for r in batch),
            "accepted": sum(
                r["evaluation"]["accepted"]
                for r in batch
                if r["evaluation"] is not None
            ),
            "abstentions": sum(
                r["agent"] is not None and r["agent"]["final_sql"] is None
                for r in batch
            ),
            "unknown_or_unexecuted": sum(r["evaluation"] is None for r in batch),
        }
        metrics[condition] = {
            "known_completion_tokens": sum(
                r["observed_usage"]["completion_tokens"] for r in batch
            ),
            "known_prompt_tokens": sum(
                r["observed_usage"]["prompt_tokens"] for r in batch
            ),
            "tool_calls": sum(
                r["agent"]["tool_calls"] for r in batch if r["agent"] is not None
            ),
            "wrong_submitted_answers": sum(
                r["evaluation"]["accepted"] is False
                and r["agent"]["final_sql"] is not None
                for r in batch
            )
            if complete[condition]
            else None,
        }
    require(
        summary["conditions"] == conditions
        and summary["complete"]
        == (all(complete.values()) and not allowance["new_unknown_usage"]),
        "Summary differs from independently reconstructed coverage",
    )
    return {
        "schema": "nuria.supplementary-controls-verification.v1",
        "passed": True,
        "posthoc": True,
        "protocol_sha256": PROTOCOL_SHA,
        "selected_records": 24,
        "attempted_records": len(proposals),
        "final_queries_replayed": queries,
        "reasoning_request_hashes_reconstructed": requests,
        "conditions": conditions,
        "metrics": metrics,
        "allowance": allowance,
        "complete": summary["complete"],
        "neural_credit_applied": False,
        "scope": "Finite SQLite replay, exact source and canonical host-request reconstruction, and local record consistency. Original study retains 64 of 120 executed records. Historical unknown ceiling is retained. Journals do not independently attest absolute UTC deadlines, backend cancellation, weights or cross-file ordering. Incomplete conditions remain ungraded; no neural-superiority claim.",
    }


def package_records(directory):
    manifest = load(directory / "manifest.json")
    require(
        manifest.get("schema") == "nuria.supplementary-controls-package.v1"
        and manifest.get("protocol_sha256") == PROTOCOL_SHA
        and manifest.get("closed_before_packaging") is True,
        "Package does not bind closed supplemental records",
    )
    archive = read(directory / "retained-controls.zip", 20 * 1024**2)
    require(sha(archive) == manifest["archive_sha256"], "Archive identity differs")
    import io

    result = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        infos = z.infolist()
        require(
            len(infos) <= 256 and len(infos) == len(manifest["files"]),
            "Archive member count differs",
        )
        expected = {row["path"]: row for row in manifest["files"]}
        require(len(expected) == len(infos), "Duplicate manifest members")
        total = 0
        for info in infos:
            path = PurePosixPath(info.filename)
            require(
                str(path) == info.filename
                and not path.is_absolute()
                and "\\" not in info.filename
                and ":" not in info.filename
                and all(p not in ("..", ".") for p in path.parts)
                and path.parts[0] in ("run", "inputs", "evidence")
                and not info.is_dir()
                and not (info.flag_bits & 1)
                and not stat.S_ISLNK(info.external_attr >> 16)
                and info.filename not in result
                and info.filename in expected,
                "Unsafe or duplicate archive member",
            )
            require(0 <= info.file_size <= MAX_FILE, "Archive member exceeds its bound")
            total += info.file_size
            require(total <= MAX_TOTAL, "Expanded package exceeds its bound")
            raw = z.read(info)
            require(
                len(raw) == info.file_size == expected[info.filename]["bytes"]
                and sha(raw) == expected[info.filename]["sha256"],
                "Original record identity differs",
            )
            result[info.filename] = raw
    return manifest, result


def materialize(records, workspace):
    require(not workspace.exists(), "A new private replay directory is required")
    require(
        shutil.disk_usage(workspace.parent).free - sum(map(len, records.values()))
        >= 20 * 1024**3,
        "Private replay would breach the 20 GiB reserve",
    )
    workspace.mkdir(mode=0o700)
    for name, raw in records.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
    return workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--workspace", type=Path)
    for key in (
        "run",
        "protocol",
        "freeze",
        "inputs-root",
        "closure",
        "helper",
        "publication",
        "repository",
        "output",
    ):
        parser.add_argument("--" + key, type=Path)
    parser.add_argument("--guard-pid", type=int, action="append")
    parser.add_argument("--confirmed-run-ended", action="store_true")
    args = parser.parse_args()
    require(
        args.repository is not None and args.output is not None,
        "Repository and new receipt paths are required",
    )
    if args.package is not None:
        require(
            args.workspace is not None
            and not args.guard_pid
            and all(
                getattr(args, k) is None
                for k in (
                    "run",
                    "protocol",
                    "freeze",
                    "inputs_root",
                    "closure",
                    "helper",
                    "publication",
                )
            ),
            "Sealed-package and raw-run modes must remain separate",
        )
        manifest, records = package_records(args.package)
        require(
            sha(read(args.package / "replay-result.json"))
            == manifest["replay_result_sha256"],
            "Published replay receipt identity differs",
        )
        workspace = materialize(records, args.workspace)
        args.run = workspace / "run"
        args.protocol = workspace / "evidence/protocol.json"
        args.freeze = workspace / "evidence/source-freeze.json"
        args.inputs_root = workspace
        args.closure = workspace / "evidence/prior-closure.json"
        args.helper = workspace / "evidence/evaluate-controls.py"
        args.publication = workspace / "evidence/publication.json"
    else:
        require(
            all(
                getattr(args, k) is not None
                for k in (
                    "run",
                    "protocol",
                    "freeze",
                    "inputs_root",
                    "closure",
                    "helper",
                    "publication",
                )
            ),
            "Explicit raw-run evidence paths are required",
        )
        ended_guard(args.run, args.guard_pid, args.confirmed_run_ended)
    require(
        shutil.disk_usage(args.output.parent).free >= 20 * 1024**3,
        "Private evidence reserve would be breached",
    )
    result = verify(
        args.run,
        args.protocol,
        args.freeze,
        args.inputs_root,
        args.closure,
        args.helper,
        args.publication,
        args.repository,
    )
    if args.package is not None:
        require(
            result == load(args.package / "replay-result.json"),
            "Independent replay differs from the retained receipt",
        )
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()

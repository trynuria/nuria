"""Independently replay a finished grounded-agent comparison without inference.

This verifier reads retained SQL in a bounded read-only SQLite connection. It
checks recorded consistency, not model-weight attestation or an external witness
of separate journals' wall-clock ordering. It never restores neural pickle data.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

VERSION = "nuria.grounded-comparison.v1"
ARMS = (
    "stateless",
    "episodes",
    "procedures",
    "schema_first",
    "hybrid_fixed",
    "hybrid_neural",
)
MODES = dict(
    zip(ARMS, ("none", "episode", "procedure", "none", "procedure", "procedure"))
)
LIMITS = {
    "max_turns": 12,
    "max_output_tokens": 6000,
    "max_tool_calls": 12,
    "max_tokens_per_turn": 1536,
    "memory_bytes": 6000,
}
SOURCE = (
    "cognition/reasoner_client.py",
    "cognition/grounded_agent.py",
    "cognition/analyst_workspace.py",
    "cognition/procedural_memory.py",
    "cognition/working_evidence.py",
    "cognition/hybrid_neural.py",
    "cognition/brain.py",
    "cognition/task_brain.py",
    "scripts/evaluate_grounded_agent.py",
)
GROUPS = (
    ("hybrid_fixed", "hybrid_neural"),
    ("stateless", "episodes", "procedures", "schema_first"),
)
HEX = re.compile(r"[0-9a-f]{64}\Z")
CAPSULE = re.compile(r"[0-9]{6}-[0-9a-f]{16}\Z")


def require(condition, label):
    if not condition:
        raise ValueError(label)


def serial(value, *, ascii=True):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ascii,
        allow_nan=False,
    )


def digest(value, *, ascii=True):
    return hashlib.sha256(serial(value, ascii=ascii).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, maximum=32 * 1024 * 1024):
    path = Path(path)
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= maximum,
        "Missing, indirect or oversized evidence file",
    )
    return json.loads(path.read_bytes())


def lines(path):
    path = Path(path)
    if not path.exists():
        return []
    require(
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size <= 64 * 1024 * 1024,
        "Evidence journal is indirect or oversized",
    )
    return [json.loads(line) for line in path.read_text().splitlines()]


def bound_file(root, record):
    path = (root / record["path"]).resolve()
    require(
        path.is_relative_to(root.resolve()) and path.is_file(),
        "Registered evidence escapes its source directory",
    )
    require(sha(path) == record["sha256"], "Registered evidence identity differs")
    return path


def hashed_record(value, key="result_sha256", *, ascii=True):
    require(
        type(value) is dict and HEX.fullmatch(value.get(key, "")) is not None,
        "Evidence record lacks a full identity",
    )
    require(
        value[key] == digest({k: v for k, v in value.items() if k != key}, ascii=ascii),
        "Evidence record checksum differs",
    )


def sql_result(path, sql):
    """Separate bounded reader; no tool runtime or model code is executed."""
    require(
        type(sql) is str and 0 < len(sql.encode()) <= 16384 and "\0" not in sql,
        "Replay SQL exceeds its bound",
    )
    allowed = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }

    def authorize(action, first, second, database, trigger):
        if action not in allowed:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and str(second).lower() in (
            "load_extension",
            "readfile",
            "writefile",
            "fts3_tokenizer",
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    ticks = 0

    def progress():
        nonlocal ticks
        ticks += 1
        return ticks > 1000

    uri = "file:" + quote(str(Path(path).resolve()), safe="/") + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
        connection.enable_load_extension(False)
        connection.set_authorizer(authorize)
        connection.set_progress_handler(progress, 1000)
        cursor = connection.execute(sql)
        require(
            cursor.description is not None and len(cursor.description) <= 128,
            "Replay did not return a bounded table",
        )
        rows = cursor.fetchmany(201)
        require(len(rows) <= 200, "Replay row allowance exceeded")
        data = [
            [{"blob_hex": cell.hex()} if type(cell) is bytes else cell for cell in row]
            for row in rows
        ]
        require(
            len(serial(data).encode()) <= 65536, "Replay result byte allowance exceeded"
        )
        return data, len(cursor.description)


def rows_equal(observed, expected, ordered):
    require(
        type(ordered) is bool and type(observed) is list and type(expected) is list,
        "Finite result comparison is malformed",
    )
    left, right = [serial(row) for row in observed], [serial(row) for row in expected]
    return left == right if ordered else Counter(left) == Counter(right)


def memory_records(directory, binding):
    path = directory / "memory.sqlite"
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        require(
            connection.execute(
                "SELECT payload FROM memory_identity WHERE id=1"
            ).fetchone()
            == (serial(binding),),
            "Memory source or model identity differs",
        )
        raw_rows = connection.execute(
            "SELECT * FROM memory_events ORDER BY seq"
        ).fetchall()
    parent, episodes, records = "0" * 64, defaultdict(list), []
    for count, (seq, previous, head, kind, episode, item, raw) in enumerate(
        raw_rows, 1
    ):
        value = json.loads(raw)
        require(
            seq == count
            and previous == parent
            and hashlib.sha256((parent + raw).encode()).hexdigest() == head
            and value.get("binding_sha256") == digest(binding)
            and value.get("schema") == "nuria.procedural-memory.v1"
            and [value.get(k) for k in ("kind", "episode_id", "item_id")]
            == [kind, episode, item],
            "Memory journal chain differs",
        )
        record = {"seq": seq, "previous_hash": previous, "hash": head, **value}
        records.append(record)
        episodes[episode].append(record)
        parent = head
    exported = load(directory / "memory-export.json")
    require(
        exported.get("binding") == binding
        and exported.get("records") == records
        and exported.get("head") == parent
        and exported.get("events") == len(records)
        and exported.get("binding_sha256") == digest(binding)
        and exported.get("truncated") is False,
        "Memory export differs from its retained database",
    )
    return dict(episodes), records


def retrieval_view(history, origin_record, question, mode, binding):
    """Rebuild only experience settled before this question's origin."""
    past = [row for row in history if row["seq"] < origin_record["seq"]]
    episodes = defaultdict(list)
    for row in past:
        episodes[row["episode_id"]].append(row)
    settlements = [row for row in reversed(past) if row["kind"] == "settlement"][:128]
    query = set(re.findall(r"\w+", question.casefold()))
    candidates = []
    if mode != "none":
        for settled in settlements:
            settlement = settled["record"]
            if mode == "procedure" and not settlement["accepted"]:
                continue
            episode = episodes[settled["episode_id"]]
            origin = next(r["record"] for r in episode if r["kind"] == "begin")
            proposal = next(r["record"] for r in episode if r["kind"] == "proposal")
            if mode == "procedure" and not proposal["procedure_text"]:
                continue
            item = {
                "episode_id": settled["episode_id"],
                "question": origin["question"],
                "context_sha256": origin["context_sha256"],
                "accepted": settlement["accepted"],
                "evidence_sha256": settlement["evidence_sha256"],
            }
            common = {
                key: proposal[key]
                for key in ("procedure_text", "rationale", "preconditions", "final_sql")
            }
            if mode == "episode":
                item.update(
                    actions=[r["record"] for r in episode if r["kind"] == "action"],
                    final_sql=proposal["final_sql"],
                    abstention_reason=proposal.get("abstention_reason"),
                )
            else:
                item.update(common)
            if len(serial(item).encode()) > LIMITS["memory_bytes"]:
                continue
            words = set(
                re.findall(r"\w+", (origin["question"] + serial(common)).casefold())
            )
            if query & words:
                candidates.append(
                    (len(query & words) / max(1, len(query | words)), item)
                )
        candidates.sort(key=lambda row: -row[0])
    selected, text = [], ""
    for _, item in candidates:
        candidate = serial([*selected, item])
        if len(selected) >= 3:
            break
        if len(candidate.encode()) <= LIMITS["memory_bytes"]:
            selected.append(item)
            text = candidate
    binding_sha = digest(binding)
    return {
        "mode": mode,
        "item_count": len(selected),
        "bytes": len(text.encode()),
        "tokens": len(text.encode()),
        "token_accounting": "utf8-byte-upper-bound",
        "binding_sha256": binding_sha,
        "memory_sha256": digest(
            {
                "binding_sha256": binding_sha,
                "mode": mode,
                "question_sha256": digest(question),
                "items": selected,
            }
        ),
    }


def verify_agent(
    agent,
    task,
    arm,
    registry,
    registry_sha,
    records,
    source_files,
    *,
    system_sha=None,
    decision=None,
    history=None,
):
    hashed_record(agent)
    require(
        agent["job_id"] == task["id"] and agent["accepted"] is None,
        "Agent identity or ungraded proposal state differs",
    )
    config = agent["configuration"]
    neural_context = (
        None
        if decision is None
        else {
            key: decision[key]
            for key in ("first_tool", "utilities", "action_spikes", "window_ms")
        }
    )
    initial = (
        decision["first_tool"]
        if decision
        else "read_knowledge"
        if arm == "schema_first"
        else None
    )
    require(
        all(config.get(k) == value for k, value in LIMITS.items())
        and config.get("memory_mode") == MODES[arm]
        and config.get("registry_sha256") == registry_sha,
        "Agent comparison limits or memory treatment differ",
    )
    require(
        config.get("initial_tool") == initial
        and config.get("initial_section") == "schema",
        "Agent inspection treatment differs",
    )
    if system_sha is not None:
        require(
            config.get("system_sha256") == system_sha,
            "Reasoner system identity differs",
        )
    database = registry["databases"][task["db_id"]]
    source = {
        "dataset_revision": registry["revision"],
        "db_id": task["db_id"],
        "db_sha256": database["database"]["sha256"],
        "registry_sha256": registry_sha,
    }
    require(agent["source"] in (None, source), "Agent source identity differs")
    require(
        records and records[0]["kind"] == "begin",
        "Agent has no originating memory record",
    )
    origin = records[0]["record"]
    require(
        origin["job_id"] == task["id"]
        and origin["question"] == task["question"]
        and digest(origin) == agent["episode_id"]
        and origin["context_sha256"] == agent["context_sha256"],
        "Agent origin or question differs",
    )
    require(
        agent["context_sha256"]
        == digest(
            {
                "question": task["question"],
                "db_id": task["db_id"],
                "policy": config,
                "neural_context": neural_context,
                "seed": 20261025,
            }
        ),
        "Agent context differs from its declared treatment",
    )
    require(
        agent["memory_retrieval"]
        == retrieval_view(
            records if history is None else history,
            records[0],
            task["question"],
            MODES[arm],
            config["model_binding"],
        ),
        "Memory retrieval differs from independently reconstructed prior outcomes",
    )
    memory_actions = [
        record["record"] for record in records if record["kind"] == "action"
    ]
    proposals = [record for record in records if record["kind"] == "proposal"]
    require(len(proposals) == 1, "Agent proposal is missing or duplicated")
    proposal = proposals[0]["record"]
    hashed_record(proposal, "proposal_sha256")
    require(
        proposal["proposal_sha256"] == agent["proposal_sha256"]
        and proposal["final_sql"] == agent["final_sql"]
        and proposal["trace_sha256"] == digest(memory_actions),
        "Final proposal differs from its own action history",
    )
    require(
        all(
            record["seq"] < proposals[0]["seq"]
            for record in records
            if record["kind"] == "action"
        ),
        "Tool activity appeared after final commitment",
    )
    actions, receipts, usage = {}, [], {"prompt_tokens": 0, "completion_tokens": 0}
    unknown, model_turns = False, set()
    for entry in agent["trace"]:
        turn, kind = entry["turn"], entry["kind"]
        if kind in ("host_initial_action", "action"):
            actions[turn] = entry["action"]
        if kind == "host_initial_action":
            require(
                turn == -1
                and entry["action"]["tool"] == initial
                and entry["neural_context"] == neural_context,
                "Host-enforced inspection strategy differs",
            )
        if kind in ("action", "invalid_action", "model_error"):
            require(
                type(turn) is int
                and 0 <= turn < LIMITS["max_turns"]
                and turn not in model_turns,
                "Model turn identity is duplicated or outside its bound",
            )
            model_turns.add(turn)
            observed = (
                entry.get("model", {}).get("usage")
                if kind != "model_error"
                else entry.get("usage")
            )
            if observed is not None:
                for key in usage:
                    require(
                        type(observed[key]) is int and observed[key] >= 0,
                        "Model usage is not an exact nonnegative count",
                    )
                    usage[key] += observed[key]
                require(
                    observed["completion_tokens"] <= LIMITS["max_tokens_per_turn"]
                    and observed["total_tokens"] == sum(observed[key] for key in usage),
                    "Per-call generation allowance or token total differs",
                )
            unknown = unknown or bool(entry.get("usage_unknown"))
        if kind != "tool_receipt":
            continue
        require(turn in actions, "Tool receipt has no committed action")
        action, receipt = actions[turn], entry["receipt"]
        hashed_record(receipt, ascii=False)
        require(
            receipt["tool"] == action["tool"] and receipt["source"] == source,
            "Tool receipt action or source differs",
        )
        require(
            receipt["request_sha256"]
            == digest(
                {
                    "db_id": task["db_id"],
                    "tool": action["tool"],
                    "arguments": action["arguments"],
                    "limits": config["tool_limits"],
                },
                ascii=False,
            ),
            "Tool request identity differs",
        )
        require(
            receipt["runtime"]["implementation_sha256"]
            == source_files["cognition/analyst_workspace.py"],
            "Tool runtime identity differs",
        )
        expected = {
            "action_id": f"turn-{turn:04d}",
            "tool_name": action["tool"],
            "sql": action["arguments"].get("sql")
            if action["tool"] == "query"
            else None,
            "ok": receipt.get("status") == "ok" and receipt.get("complete") is True,
            "cost_units": receipt.get("cost", {}).get("vm_progress_callbacks"),
            "result_sha256": receipt["result_sha256"],
        }
        require(
            len(receipts) < len(memory_actions)
            and all(
                memory_actions[len(receipts)].get(k) == v for k, v in expected.items()
            ),
            "Tool receipt differs from durable procedural memory",
        )
        receipts.append((action, receipt))
    require(
        len(receipts)
        == agent["tool_calls"]
        == len(memory_actions)
        <= LIMITS["max_tool_calls"],
        "Tool call denominator differs",
    )
    require(
        usage == {k: agent["model_usage"][k] for k in usage}
        and agent["model_usage"]["total_tokens"] == sum(usage.values())
        and agent["model_usage"]["unknown_additional_usage"] == unknown
        and usage["completion_tokens"] <= LIMITS["max_output_tokens"],
        "Agent token accounting differs from its retained calls",
    )
    if agent["final_sql"] is not None:
        require(
            agent["selected_receipt"] is not None
            and any(
                action["tool"] == "query"
                and action["arguments"].get("sql") == agent["final_sql"]
                and receipt == agent["selected_receipt"]
                and receipt["status"] == "ok"
                and receipt["complete"] is True
                for action, receipt in receipts
            ),
            "Final answer lacks complete own execution",
        )
    else:
        require(
            agent["selected_receipt"] is None,
            "An abstention contains a selected answer",
        )
    return usage


def summarize(rows):
    executed = [r for r in rows if r["agent"] is not None]
    settled = [r for r in rows if r["settlement"] is not None]
    attempted = sum(r["attempted"] for r in rows)
    return {
        "selected": len(rows),
        "descriptively_graded": sum("descriptive_evaluation" in r for r in rows),
        "descriptively_accepted": sum(
            r.get("descriptive_evaluation", {}).get("accepted", False) for r in rows
        ),
        "executed": len(executed),
        "attempted": attempted,
        "not_executed": len(rows) - attempted,
        "unfinished_attempts": attempted - len(executed),
        "settled": len(settled),
        "unsettled_executed": len(executed) - len(settled),
        "accepted": sum(r["settlement"]["accepted"] for r in settled),
        "wrong_submissions": sum(
            r["agent"]["final_sql"] is not None and not r["settlement"]["accepted"]
            for r in settled
        ),
        "abstentions": sum(r["agent"]["final_sql"] is None for r in executed),
        "prompt_tokens": sum(r["observed_usage"]["prompt_tokens"] for r in rows),
        "completion_tokens": sum(
            r["observed_usage"]["completion_tokens"] for r in rows
        ),
        "unknown_usage_jobs": sum(
            r["observed_usage"]["unknown_additional_usage"] for r in rows
        ),
        "tool_calls": sum(r["agent"].get("tool_calls", 0) for r in executed),
        "job_wall_seconds": sum(r["agent"].get("wall_seconds", 0) for r in executed),
        "status_counts": {
            s: sum(r["status"] == s for r in rows)
            for s in sorted({r["status"] for r in rows})
        },
    }


def verify_allowance(journal, proposals, protocol, summary):
    require(
        len(journal) == 2 * len(proposals),
        "Allowance journal lost a reservation or closure",
    )
    known, pending, unknown = protocol["prior_known_completion_tokens"], 0, False
    ceiling = protocol["prior_unknown_completion_ceiling"]
    elapsed = protocol["prior_wall_seconds"]
    for index, proposal in enumerate(proposals):
        reserve, close = journal[2 * index : 2 * index + 2]
        require(
            reserve["event"] == "job_reserved"
            and close["event"] == "job_closed"
            and all(reserve[k] == proposal[k] for k in ("arm", "phase", "task_id"))
            and not unknown
            and not pending
            and reserve["known_completion_tokens"] == known
            and reserve["pending_completion_reservation"] == 6000
            and reserve["conservative_completion_charge"] == known + ceiling + 6000
            and known + ceiling + 6000 <= protocol["global_completion_cap"],
            "Job inference reservation is missing, reordered or unbounded",
        )
        for event in (reserve, close):
            require(
                event["elapsed_global_seconds"] >= elapsed
                and event["prior_unknown_completion_ceiling"] == ceiling,
                "Global allowance time or historical liability regressed",
            )
            elapsed = event["elapsed_global_seconds"]
        used = proposal["observed_usage"]
        require(
            type(used["completion_tokens"]) is int
            and 0 <= used["completion_tokens"] <= 6000,
            "Recorded completion use exceeds its job reservation",
        )
        known += used["completion_tokens"]
        unknown = used["unknown_additional_usage"]
        pending = 6000 - used["completion_tokens"] if unknown else 0
        require(
            close["known_completion_tokens"] == known
            and close["pending_completion_reservation"] == pending
            and close["current_unknown_usage"] == unknown
            and close["conservative_completion_charge"] == known + ceiling + pending,
            "Closed inference allowance does not conserve consumption and liability",
        )
    state = summary["allowance"]
    require(
        state["known_completion_tokens"] == known
        and state["prior_unknown_completion_ceiling"] == ceiling
        and state["pending_completion_reservation"] == pending
        and state["current_unknown_usage"] == unknown
        and state["conservative_completion_charge"] == known + ceiling + pending,
        "Summary allowance differs from durable inference reservations",
    )


def verify_capsules(directory, expected_rows):
    """Check immutable lineage and stored origin credit without loading pickle."""
    identity, head = load(directory / "LAB.json"), load(directory / "HEAD.json")
    terms_sha = digest(identity["terms"])
    current, expected, sequence = (
        head["capsule"],
        head["manifest_sha256"],
        head["sequence"],
    )
    final_state, count = None, 0
    while current is not None:
        require(
            type(current) is str and CAPSULE.fullmatch(current) is not None,
            "Neural capsule path differs",
        )
        capsule = directory / "capsules" / current
        require(
            sha(capsule / "manifest.json") == expected,
            "Neural capsule manifest checksum differs",
        )
        manifest, metadata = (
            load(capsule / "manifest.json"),
            load(capsule / "metadata.json"),
        )
        require(
            manifest["sequence"] == sequence
            and manifest["terms_sha256"] == terms_sha
            and manifest["lab_id"] == identity["lab_id"],
            "Neural capsule lineage differs",
        )
        for name in ("metadata.json", "network.pkl"):
            require(
                sha(capsule / name) == manifest[name],
                "Neural capsule payload checksum differs",
            )
        state = metadata["state"]
        require(
            state["terms_sha256"] == terms_sha
            and state["lab_id"] == identity["lab_id"],
            "Neural state identity differs",
        )
        if final_state is None:
            final_state = state
        current, expected = (
            manifest["previous_capsule"],
            manifest["previous_manifest_sha256"],
        )
        require(
            (sequence == 0) == (current is None and expected is None),
            "Neural capsule genesis differs",
        )
        sequence -= 1
        count += 1
        require(count <= 513, "Neural lineage exceeds its bound")
    require(sequence == -1, "Neural lineage is incomplete")
    require(
        set(final_state["jobs"]) == {row["task_id"] for row in expected_rows},
        "Neural decision denominator differs",
    )
    for row in expected_rows:
        job = final_state["jobs"][row["task_id"]]
        decision = row["neural_decision"]
        hashed_record(decision, "decision_sha256")
        require(
            job["decision"] == decision
            and decision["origin_sha256"] == digest(job["origin"])
            and job["settlement"] == row.get("neural_credit"),
            "Neural origin, decision or delayed credit differs",
        )
        if row.get("neural_credit") is not None:
            credit, terminal = row["neural_credit"], row["settlement"]
            hashed_record(credit, "receipt_sha256")
            require(
                terminal is not None
                and credit["job_id"] == row["task_id"]
                and credit["accepted"] == terminal["accepted"]
                and credit["evidence_sha256"] == terminal["receipt_sha256"]
                and credit["decision_sha256"] == decision["decision_sha256"]
                and credit["origin_sha256"] == decision["origin_sha256"]
                and credit["neural_reward_signal"]
                == (1.0 if terminal["accepted"] else -1.0),
                "Neural reward was not bound to its own terminal outcome",
            )
    return {
        "capsules": count,
        "head_manifest_sha256": head["manifest_sha256"],
        "spiking_state_reexecuted": False,
        "pickle_loaded": False,
    }


def verify(run, protocol_path, freeze_path, expected_freeze, *, repository=None):
    run, protocol_path, freeze_path = map(Path, (run, protocol_path, freeze_path))
    # A running directory must never cause this post-run tool to expose labels.
    require(
        (run / "summary.json").is_file() and (run / "selected-results.json").is_file(),
        "Finished summary and selected results are required before artifact access",
    )
    require(sha(freeze_path) == expected_freeze, "Source-freeze identity differs")
    freeze, protocol = load(freeze_path), load(protocol_path)
    require(
        freeze["protocol_sha256"] == sha(protocol_path), "Protocol identity differs"
    )
    require(
        protocol["schema"] == VERSION
        and protocol["arms"] == list(ARMS)
        and protocol["execution_schedule"] == "primary-pair-then-secondary-blocks-v2"
        and protocol["comparison_groups"] == [list(group) for group in GROUPS]
        and protocol["limits"] == LIMITS
        and protocol["seed"] == 20261025
        and protocol["global_completion_cap"] == 200000
        and protocol["global_wall_seconds"] == 10800,
        "Prospective comparison design differs",
    )
    repository = Path(repository or Path(__file__).resolve().parents[1])
    require(
        set(SOURCE) <= set(freeze["files"]), "Frozen dependency identity is incomplete"
    )
    for name, expected in freeze["files"].items():
        path = (repository / name).resolve()
        require(
            path.is_relative_to(repository.resolve())
            and path.is_file()
            and sha(path) == expected,
            "Current frozen source identity differs",
        )
    require(
        load(run / "run-freeze.json")
        == {
            "protocol": protocol,
            "source_freeze": freeze,
            "source_freeze_sha256": expected_freeze,
        },
        "Run freeze differs from its independent binding",
    )
    files = {
        name: bound_file(protocol_path.parent, record)
        for name, record in protocol["artifacts"].items()
    }
    courses = {
        phase: load(files[phase + "_course"])["tasks"]
        for phase in ("teaching", "reserved")
    }
    require(
        [len(courses[k]) for k in courses] == [8, 12]
        and protocol["selected_task_ids"]
        == {k: [t["id"] for t in v] for k, v in courses.items()},
        "Selected course denominators or identities differ",
    )
    tasks = {t["id"]: t for course in courses.values() for t in course}
    require(len(tasks) == 20, "Course task identities overlap")
    registry, oracle = load(files["registry"]), load(files["oracle"])["tasks"]
    databases = {}
    for db_id, record in registry["databases"].items():
        databases[db_id] = bound_file(files["registry"].parent, record["database"])
        for document in record["documents"].values():
            bound_file(files["registry"].parent, document)
    selected, summary = load(run / "selected-results.json"), load(run / "summary.json")
    planned, blocks = [], []
    for group_index, group in enumerate(GROUPS):
        block_number = 0
        for phase, course in courses.items():
            for task in course:
                offset = block_number % len(group)
                order = group[offset:] + group[:offset]
                block_number += 1
                blocks.append((group_index, phase, task["id"], order))
                planned.extend((arm, phase, task["id"]) for arm in order)

    def key(row):
        return row["arm"], row["phase"], row["task_id"]

    require(
        len(selected) == 120 and [key(row) for row in selected] == planned,
        "All 120 selected method-task records must remain in planned order",
    )
    by_key = {key(row): row for row in selected}
    for row in selected:
        require(
            row["db_id"] == tasks[row["task_id"]]["db_id"]
            and type(row["attempted"]) is bool
            and row["comparison_group"]
            == next(i for i, g in enumerate(GROUPS) if row["arm"] in g),
            "Selected task identity differs",
        )
        if not row["attempted"]:
            require(
                row["agent"] is None
                and row["settlement"] is None
                and row["observed_usage"]
                == {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "unknown_additional_usage": False,
                },
                "An unattempted job contains activity",
            )
    proposals, outcomes = lines(run / "proposals.jsonl"), lines(run / "outcomes.jsonl")
    require(
        [key(row) for row in proposals]
        == [key(row) for row in selected if row["attempted"]],
        "Committed proposal denominator differs",
    )
    mutable = {
        "settlement",
        "neural_credit",
        "neural_credit_error",
        "settlement_error",
        "descriptive_evaluation",
    }
    for row in proposals:
        final = by_key[key(row)]
        require(
            {k: v for k, v in row.items() if k not in mutable}
            == {k: v for k, v in final.items() if k not in mutable}
            and row["settlement"] is None,
            "A committed proposal changed after grading",
        )
    expected_outcomes = []
    for group_index, group in enumerate(GROUPS):
        for _, phase, task_id, order in [
            block for block in blocks if block[0] == group_index
        ]:
            batch = [by_key[(arm, phase, task_id)] for arm in order]
            if phase == "teaching" and all(row["agent"] is not None for row in batch):
                expected_outcomes.extend(key(row) for row in batch)
        for arm in group:
            batch = [
                row
                for row in selected
                if row["arm"] == arm and row["phase"] == "reserved"
            ]
            if all(row["agent"] is not None for row in batch):
                expected_outcomes.extend(key(row) for row in batch)
    require(
        [key(row) for row in outcomes] == expected_outcomes,
        "Terminal outcome schedule or denominator differs",
    )
    for row in outcomes:
        require(row == by_key[key(row)], "Outcome journal differs from final results")
    block_events = [
        e for e in lines(run / "events.jsonl") if e["event"] == "matched_block"
    ]
    require(len(block_events) == 40, "Matched-block schedule evidence is incomplete")
    cumulative = protocol["prior_known_completion_tokens"]
    for event, (group_index, phase, task_id, order) in zip(block_events, blocks):
        require(
            (event["phase"], event["task_id"], event["arm_order"])
            == (phase, task_id, list(order))
            and event["known_completion_tokens"] == cumulative
            and event["required_completion_headroom"] == 6000 * len(order),
            "Balanced arm order or pre-block allowance differs",
        )
        batch = [by_key[(arm, phase, task_id)] for arm in order]
        if event["permitted"]:
            require(
                cumulative
                + protocol["prior_unknown_completion_ceiling"]
                + 6000 * len(order)
                <= 200000
                and event["elapsed_global_seconds"] < 10800,
                "A matched block started without its complete allowance",
            )
        else:
            require(
                not any(row["attempted"] for row in batch),
                "A refused block disclosed an inference request",
            )
        cumulative += sum(row["observed_usage"]["completion_tokens"] for row in batch)
    verify_allowance(lines(run / "allowance.jsonl"), proposals, protocol, summary)
    system_sha = None
    parsed = ast.parse((repository / "cognition/grounded_agent.py").read_text())
    for node in parsed.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SYSTEM" for t in node.targets
        ):
            system_sha = digest(ast.literal_eval(node.value))
    binding = {
        "schema": "nuria.procedural-memory.v1",
        "source_id": registry["revision"],
        "model_id": protocol["model_identity"],
        "tool_version": "nuria.analyst-workspace.v1",
    }
    memories, neural, replayed = {}, {}, 0
    for arm in ARMS:
        episodes, records = memory_records(run / arm, binding)
        memories[arm] = {
            "events": len(records),
            "head": records[-1]["hash"] if records else "0" * 64,
        }
        actual = [
            row for row in selected if row["arm"] == arm and row["agent"] is not None
        ]
        attempted_ids = {
            row["task_id"] for row in selected if row["arm"] == arm and row["attempted"]
        }
        memory_ids = [episode[0]["record"]["job_id"] for episode in episodes.values()]
        require(
            {row["agent"]["episode_id"] for row in actual} <= set(episodes)
            and set(memory_ids) <= attempted_ids
            and len(memory_ids) == len(set(memory_ids)),
            "Memory episode denominator differs",
        )
        reserved_proposals, reserved_settlements = [], []
        for row in actual:
            agent, task = row["agent"], tasks[row["task_id"]]
            episode = episodes[agent["episode_id"]]
            usage = verify_agent(
                agent,
                task,
                arm,
                registry,
                digest(registry, ascii=False),
                episode,
                freeze["files"],
                system_sha=system_sha,
                decision=row.get("neural_decision"),
                history=records,
            )
            require(
                agent["configuration"]["model_binding"] == binding
                and agent["configuration"]["reasoning_mode"]
                == protocol["enable_thinking"],
                "Agent model or reasoning mode differs",
            )
            require(
                row["observed_usage"]
                == {
                    **usage,
                    "unknown_additional_usage": agent["model_usage"][
                        "unknown_additional_usage"
                    ],
                },
                "Host and agent inference accounting differ",
            )
            if row["phase"] == "reserved":
                reserved_proposals.extend(
                    r["seq"] for r in episode if r["kind"] == "proposal"
                )
                reserved_settlements.extend(
                    r["seq"] for r in episode if r["kind"] == "settlement"
                )
            expected = oracle[row["task_id"]]
            require(
                type(expected["column_count"]) is int
                and 0 <= expected["column_count"] <= 128
                and expected["db_id"] == task["db_id"]
                and expected["split"] == row["phase"]
                and expected["gradeable"] is True,
                "Checker task identity differs",
            )
            reference, count = sql_result(
                databases[task["db_id"]], expected["reference_sql"]
            )
            require(
                count == expected["column_count"]
                and rows_equal(
                    reference, expected["expected_rows"], expected["ordered"]
                ),
                "Stored reference result does not reproduce",
            )
            accepted = False
            if agent["final_sql"] is not None:
                observed, count = sql_result(
                    databases[task["db_id"]], agent["final_sql"]
                )
                require(
                    rows_equal(observed, agent["selected_receipt"]["rows"], True)
                    and count == len(agent["selected_receipt"]["columns"]),
                    "Committed final query does not reproduce its own receipt",
                )
                accepted = count == expected["column_count"] and rows_equal(
                    observed, reference, expected["ordered"]
                )
                replayed += 1
            terminal = row["settlement"]
            durable = [r["record"] for r in episode if r["kind"] == "settlement"]
            require(
                durable == ([terminal] if terminal is not None else []),
                "Recorded terminal credit differs from memory",
            )
            if terminal is not None:
                hashed_record(terminal, "receipt_sha256")
                evidence = digest(
                    {
                        "task": task["id"],
                        "proposal": agent["proposal_sha256"],
                        "oracle": digest(expected),
                        "receipt": agent["selected_receipt"],
                        "accepted": accepted,
                    }
                )
                require(
                    type(terminal["accepted"]) is bool
                    and terminal["accepted"] == accepted
                    and terminal["episode_id"] == agent["episode_id"]
                    and terminal["origin_sha256"] == agent["episode_id"]
                    and terminal["checker_id"] == "strict-finite-sqlite-v1"
                    and terminal["terminal_reward"] == int(accepted)
                    and terminal["proposal_sha256"] == agent["proposal_sha256"]
                    and terminal["evidence_sha256"] == evidence,
                    "Terminal acceptance differs from independent SQL replay",
                )
            if "descriptive_evaluation" in row:
                group = [
                    by_key[(other, row["phase"], row["task_id"])]
                    for other in GROUPS[row["comparison_group"]]
                ]
                require(
                    all(r["agent"] is not None for r in group)
                    and terminal is None
                    and row["descriptive_evaluation"]["accepted"] == accepted
                    and row["descriptive_evaluation"]["oracle_sha256"]
                    == digest(expected),
                    "Post-stop descriptive grade is unmatched or inconsistent",
                )
        if reserved_settlements:
            require(
                len(reserved_proposals) == 12
                and max(reserved_proposals) < min(reserved_settlements),
                "Reserved outcomes entered memory before all twelve proposals",
            )
        neural_rows = [
            row
            for row in selected
            if row["arm"] == arm and row.get("neural_decision") is not None
        ]
        if arm in ("hybrid_fixed", "hybrid_neural") and neural_rows:
            directory = run / arm / "brain"
            identity = load(directory / "LAB.json")
            terms = identity["terms"]
            require(
                terms["source_id"] == registry["revision"]
                and terms["model_id"] == protocol["model_identity"]
                and terms["tool_version"] == "nuria.analyst-workspace.v1"
                and terms["mode"] == ("fixed" if arm == "hybrid_fixed" else "neural")
                and terms["window_ms"] == 100,
                "Neural experiment treatment or source identity differs",
            )
            for name, identity_hash in terms["implementation"].items():
                require(
                    freeze["files"].get("cognition/" + name) == identity_hash,
                    "Neural implementation identity differs",
                )
            for row in neural_rows:
                decision = row["neural_decision"]
                require(
                    decision["context_sha256"]
                    == digest(
                        {
                            "job_id": row["task_id"],
                            "model_identity": protocol["model_identity"],
                            "registry_sha256": digest(registry, ascii=False),
                            "source": freeze["files"],
                            "configuration": LIMITS,
                            "seed": 20261025,
                        }
                    ),
                    "Neural context differs from the fixed matched comparison",
                )
            neural[arm] = verify_capsules(directory, neural_rows)
        for phase in courses:
            require(
                summary["arms"][arm][phase]
                == summarize(
                    [
                        row
                        for row in selected
                        if row["arm"] == arm and row["phase"] == phase
                    ]
                ),
                "Published method summary differs from the complete denominator",
            )
    terminal_coverage = all(row["settlement"] is not None for row in selected)
    require(
        summary["all_terminal_results_settled"] == terminal_coverage
        and summary["complete"]
        == bool(
            terminal_coverage
            and summary["source_freeze_verified_at_end"]
            and not summary["allowance"]["stop_reason"]
        ),
        "Completion claim hides missing coverage or a stopped experiment",
    )
    primary_coverage = all(
        row["settlement"] is not None for row in selected if row["arm"] in GROUPS[0]
    )
    resource_stops = {
        None,
        "global_matched_block_reservation_limit",
        "global_completion_reservation_limit",
        "global_wall_limit",
    }
    require(
        summary["primary_arms"] == list(GROUPS[0])
        and summary["primary_terminal_results_settled"] == primary_coverage
        and summary["primary_complete"]
        == bool(
            primary_coverage
            and summary["source_freeze_verified_at_end"]
            and summary["allowance"]["stop_reason"] in resource_stops
        ),
        "Primary completion claim differs from its qualified paired coverage",
    )
    return {
        "schema": "nuria.grounded-comparison-verification.v1",
        "passed": True,
        "run_freeze_sha256": sha(run / "run-freeze.json"),
        "protocol_sha256": sha(protocol_path),
        "source_freeze_sha256": expected_freeze,
        "selected_records": len(selected),
        "attempted_records": len(proposals),
        "terminal_records": len(outcomes),
        "final_queries_replayed": replayed,
        "memories": memories,
        "neural": neural,
        "complete": summary["complete"],
        "primary_complete": summary["primary_complete"],
        "arms": summary["arms"],
        "scope": "Finite SQLite result replay and local record consistency; public benchmark pretraining contamination is possible. Token claims are internally reconciled, not externally attested. Separate journals do not authenticate cross-file wall-clock ordering. Frozen scheduling and within-arm memory order are checked. Neural capsule hashes and origin credit are checked without restoring or reexecuting spiking state.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--source-freeze", required=True, type=Path)
    parser.add_argument("--source-freeze-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify(
        args.run, args.protocol, args.source_freeze, args.source_freeze_sha256
    )
    descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "passed": True,
                "selected_records": result["selected_records"],
                "attempted_records": result["attempted_records"],
                "final_queries_replayed": result["final_queries_replayed"],
            }
        )
    )


if __name__ == "__main__":
    main()

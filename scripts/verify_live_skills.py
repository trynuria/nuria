#!/usr/bin/env python3
"""Verify source-bound local skill deliveries and their committed neural feedback.

Each SQLite view has its own read-only WAL transaction. This is not an atomic
snapshot across databases, a full cognitive-chain re-audit, an onchain proof or
independent witnessing. The only writes create new collector output files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HASH = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
MAX_JSON = 8 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition, label):
    if not condition:
        raise ValueError(label)


def timestamp(value):
    """Accept only an ISO timestamp, never arbitrary metadata in public output."""
    if value is None:
        return None
    require(type(value) is str and len(value) <= 40, "Timestamp is malformed")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Timestamp lacks a timezone")
    return parsed.isoformat()


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_JSON + 1)
    require(len(raw) <= MAX_JSON, "JSON evidence exceeds its inspection bound")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def sqlite_view(path):
    # Never use immutable=1 for a live WAL database: that could ignore committed
    # WAL frames. No constructor, schema migration or database backup is invoked.
    db = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def exists(db, table):
    return bool(
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def count(db, table):
    require(exists(db, table), "An expected financial ledger table is missing")
    return db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def commerce_integrity(db):
    previous, sequence = "0" * 64, 0
    for row in db.execute(
        "SELECT seq,previous_hash,hash,payload FROM events ORDER BY seq"
    ):
        sequence += 1
        require(
            row["seq"] == sequence and row["previous_hash"] == previous,
            "Commerce event sequence or parent changed",
        )
        require(
            hashlib.sha256((previous + row["payload"]).encode()).hexdigest()
            == row["hash"],
            "Commerce event hash failed",
        )
        json.loads(row["payload"])
        previous = row["hash"]
    return {
        "valid": True,
        "events": sequence,
        "head": previous,
        "mode": "full",
        "scope": "Every committed commerce event was reconstructed within this read-only snapshot; local continuity, not an independent witness",
    }


def financial_counts(db):
    expected = {
        "authorizations": {"job_id", "payload", "response"},
        "signing_requests": {"id", "month", "created"},
        "jobs": {"id", "settlement"},
        "action_tasks": {"id", "settlement"},
        "commissions": {"id", "settlement"},
    }
    for table, columns in expected.items():
        require(exists(db, table), "An expected financial ledger table is missing")
        actual = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        require(columns <= actual, "An expected financial ledger column is missing")
    transactions = set()
    for table in ("jobs", "action_tasks", "commissions"):
        require(exists(db, table), "An expected financial ledger table is missing")
        columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        require("settlement" in columns, "A financial settlement column is missing")
        for (raw,) in db.execute(
            f'SELECT settlement FROM "{table}" WHERE settlement IS NOT NULL'
        ):
            evidence = json.loads(raw)
            require(type(evidence) is dict, "A financial settlement is malformed")
            if evidence.get("transaction"):
                transactions.add(evidence["transaction"])
    return {
        "authorizations_count": count(db, "authorizations"),
        "signing_requests_count": count(db, "signing_requests"),
        "settled_payment_transaction_count": len(transactions),
        "settled_payment_set_sha256": digest(sorted(transactions)),
        "scope": "Counts in the inspected commerce ledger only; not a chain-wide wallet audit",
    }


def record_hash(row):
    require(
        hashlib.sha256((row["previous_hash"] + row["payload"]).encode()).hexdigest()
        == row["hash"],
        "Relevant cognitive record hash failed",
    )
    return json.loads(row["payload"])


def source_identity(root, release, expected_commit, services):
    require(COMMIT.fullmatch(expected_commit), "Expected source commit is invalid")
    require(
        release.get("commit") == expected_commit,
        "Published release does not identify the expected commit",
    )
    result = {"commit": expected_commit, "components": {}}
    for component in ("cognition", "commerce"):
        actual = hashlib.sha256(
            b"".join(
                path.read_bytes() for path in sorted((root / component).glob("*.py"))
            )
        ).hexdigest()
        reported = release.get("components", {}).get(component, {})
        require(
            reported.get("commit") == expected_commit
            and reported.get("source_sha256") == actual,
            "Running component source identity differs from release metadata",
        )
        shown = subprocess.run(
            [
                "systemctl",
                "show",
                services[component],
                "--property=MainPID",
                "--property=ActiveState",
                "--property=SubState",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        properties = dict(
            line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line
        )
        require(
            properties.get("ActiveState") == "active"
            and properties.get("SubState") == "running",
            "A relevant service is not running",
        )
        pid = properties.get("MainPID", "0")
        require(pid.isdecimal() and int(pid) > 0, "Running service PID is unknown")
        require(
            Path("/proc", pid, "cwd").resolve(strict=True) == root.resolve(),
            "Running service directory differs from inspected source",
        )
        result["components"][component] = {
            "commit": expected_commit,
            "source_sha256": actual,
            "deployed_utc": timestamp(reported.get("deployed_utc")),
            "service_running": True,
            "actual_process_source_directory_matches": True,
        }
    result["independent_interpreter_sha256"] = hashlib.sha256(
        (root / "scripts/verify_skill_expression_results.py").read_bytes()
    ).hexdigest()
    return result


def cache(path):
    value, raw_hash = read_json(path)
    age = time.time() - Path(path).stat().st_mtime
    phase = value.get("phase")
    require(
        phase is None or (type(phase) is str and re.fullmatch(r"[a-z_]{1,64}", phase)),
        "Cache phase is malformed",
    )
    return value, {
        "sha256": raw_hash,
        "age_seconds": age,
        "fresh": -2 <= age <= 30,
        "phase": phase,
        "updated_utc": timestamp(value.get("updated_utc")),
    }


def close(db):
    if db is not None:
        db.rollback()
        db.close()


def collect(args):
    # Import only the independent, data-only interpreter. No learner, network,
    # signer, application constructor or trusted pickle deserializer is used.
    from scripts.verify_skill_expression_results import outcome

    observed = datetime.now(timezone.utc).isoformat()
    release, release_hash = read_json(args.release_file)
    source = source_identity(
        Path(args.source_root),
        release,
        args.source_commit,
        {"cognition": args.cognition_service, "commerce": args.commerce_service},
    )
    public_commerce, commerce_cache = cache(args.commerce_cache)
    public_cognition, cognition_cache = cache(args.cognition_cache)
    baseline = read_json(args.baseline_json)[0] if args.baseline_json else None
    cdb = ndb = None
    try:
        cdb, ndb = sqlite_view(args.commerce_db), sqlite_view(args.cognition_db)
        # Pin both snapshots immediately; they remain separately consistent.
        integrity = commerce_integrity(cdb)
        checkpoint = ndb.execute(
            "SELECT metadata FROM checkpoint WHERE id=1"
        ).fetchone()
        require(checkpoint is not None, "Cognitive checkpoint is missing")
        metadata = json.loads(checkpoint[0])
        neural_head = ndb.execute(
            "SELECT seq,hash FROM records ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        require(
            neural_head is not None
            and (metadata["record_seq"], metadata["record_head"]) == tuple(neural_head),
            "Cognitive checkpoint and committed records disagree",
        )
        require(
            all(
                type(metadata.get(key)) is int and metadata[key] >= 0
                for key in ("record_seq", "cursor", "tick")
            )
            and HASH.fullmatch(metadata.get("record_head", "")),
            "Cognitive checkpoint metadata is malformed",
        )
        genesis = timestamp(metadata.get("genesis_utc"))
        agency = metadata.get("agency", {})
        committed_ack = agency.get("feedback_cursor", 0)
        require(
            type(committed_ack) is int, "Committed feedback acknowledgment is malformed"
        )
        state_row = cdb.execute(
            "SELECT source_id,config_sha256,state FROM local_skill_state WHERE provider=?",
            (args.provider,),
        ).fetchone()
        require(
            state_row is not None and state_row["source_id"] == args.skill_source,
            "Expected dedicated local skill source is missing",
        )
        library = json.loads(state_row["state"])
        require(
            library.get("source_id") == args.skill_source
            and library.get("config_sha256") == state_row["config_sha256"],
            "Retained library identity changed",
        )
        require(
            not library.get("macros"),
            "The inspected instance is not the frozen no-macro configuration",
        )
        programs = library.get("programs", {})
        require(
            type(programs) is dict and len(programs) <= 128,
            "Retained program collection exceeds its bound",
        )
        for ident, record in programs.items():
            require(
                HASH.fullmatch(ident) and digest(record["program"]) == ident,
                "Retained program commitment failed",
            )
            require(
                HASH.fullmatch(record["evidence_sha256"])
                and type(record["uses"]) is int
                and 0 < record["uses"] <= 10**12,
                "Retained program lacks bounded buyer evidence",
            )
        tasks = cdb.execute(
            "SELECT * FROM action_tasks WHERE provider=? ORDER BY created,id",
            (args.provider,),
        ).fetchall()
        require(
            0 < len(tasks) <= 512,
            "No bounded actual jobs exist for the expected provider",
        )
        outcomes = {}
        for row in ndb.execute(
            "SELECT seq,previous_hash,hash,payload FROM records WHERE kind='task_outcome' ORDER BY seq DESC LIMIT 2048"
        ):
            payload = record_hash(row)
            if payload.get("intent_id") in outcomes:
                raise ValueError(
                    "A neural task origin has multiple recorded outcome credits"
                )
            outcomes[payload.get("intent_id")] = {
                "journal_seq": row["seq"],
                "journal_hash": row["hash"],
                "payload": payload,
            }
        jobs, phases, learning, serving, failed = [], {}, 0, 0, 0
        final_rows = exact_rows = selected_work = validation_work = changed = tagged = (
            max_ack
        ) = 0
        gaps = []
        for task in tasks:
            phases[task["state"]] = phases.get(task["state"], 0) + 1
            contract, intent = json.loads(task["contract"]), json.loads(task["intent"])
            rule = contract["acceptance"]
            require(
                HASH.fullmatch(task["id"]) and HASH.fullmatch(task["intent_id"]),
                "Task identifiers are malformed",
            )
            require(
                task["amount"] == 0
                and contract["maximum_micro_usd"] == 0
                and contract["kind"] == "compute",
                "A selected job is not zero-payment local compute",
            )
            require(
                rule["config"]["max_macros"] == 0
                and digest(rule["config"]) == state_row["config_sha256"],
                "Task configuration differs from frozen retained library",
            )
            require(
                digest(contract) == intent["contract_sha256"]
                and digest({key: value for key, value in intent.items() if key != "id"})
                == intent["id"]
                == task["intent_id"],
                "Task and neural intent commitment disagree",
            )
            proposed = cdb.execute(
                "SELECT seq,hash,payload FROM events WHERE json_extract(payload,'$.action_task_id')=? AND json_extract(payload,'$.task_state')='proposed' ORDER BY seq LIMIT 1",
                (task["id"],),
            ).fetchone()
            require(proposed is not None, "Task lacks its original proposal event")
            proposal_event = json.loads(proposed["payload"])
            origin = ndb.execute(
                "SELECT seq,previous_hash,hash,payload FROM records WHERE hash=?",
                (proposal_event["origin_decision_hash"],),
            ).fetchone()
            require(origin is not None, "Original neural decision record is absent")
            original = record_hash(origin)
            require(
                original.get("intent") == intent,
                "Commerce intent differs from committed cognitive decision",
            )
            local = cdb.execute(
                "SELECT * FROM local_skill_jobs WHERE job_id=? AND provider=?",
                (task["id"], args.provider),
            ).fetchone()
            if task["state"] != "evaluated" or local is None or not local["delivery"]:
                gaps.append(
                    {
                        "job_id": task["id"],
                        "finding": "Job has not reached a checked, evaluated delivery",
                    }
                )
                continue
            acceptance, result = (
                json.loads(task["acceptance"]),
                json.loads(task["outcome"]),
            )
            settlement = json.loads(task["settlement"])
            require(
                settlement["basis"] == "included_capacity"
                and settlement["total_micro_usd"] == 0
                and settlement["job_id"] == task["id"],
                "Local job settlement indicates a financial payment or another job",
            )
            delivery = json.loads(local["delivery"])
            require(
                delivery["job_id"] == task["id"]
                and delivery["contract_sha256"] == digest(contract),
                "Actual delivery is not bound to this task",
            )
            require(
                hashlib.sha256(local["delivery"].encode()).hexdigest()
                == acceptance["artifact_sha256"],
                "Checked artifact differs from actual retained delivery",
            )
            selected = delivery["result"]
            work = selected["work"]["total"]
            require(
                type(work) is int and 0 <= work <= rule["maximum_work"],
                "Actual local work is invalid",
            )
            if rule["type"] == "transform_program":
                learning += 1
                require(
                    set(selected) == {"program", "proposal_id", "work"},
                    "Learning delivery has an unexpected shape",
                )
                independent = outcome(
                    selected["program"],
                    rule["heldout_inputs"],
                    rule["heldout_outputs"],
                    rule["config"],
                )
                require(
                    independent["tool_units"] == acceptance["verifier_work"],
                    "Independent final interpreter work differs from recorded buyer work",
                )
                actual_program = (
                    digest(selected["program"])
                    if selected["program"] is not None
                    else None
                )
                if acceptance["passed"]:
                    require(
                        actual_program in programs
                        and programs[actual_program]["program"] == selected["program"],
                        "Accepted program was not retained for reuse",
                    )
                source_bound = True
                input_hash = digest(rule["heldout_inputs"])
                expected_hash = digest(
                    {
                        "outputs": rule["heldout_outputs"],
                        "nonce": contract["evaluation_nonce"],
                    }
                )
                execution_status = None
            else:
                require(
                    rule["type"] == "transform_execution",
                    "Provider executed an unsupported task kind",
                )
                serving += 1
                require(
                    rule["source_id"] == selected["source_id"] == args.skill_source
                    and selected["skill_id"] == rule["skill_id"]
                    and selected["inputs_sha256"] == digest(rule["inputs"]),
                    "Serving source, skill or input commitment changed",
                )
                actual_program = selected["program_sha256"]
                if actual_program is not None:
                    require(
                        actual_program == rule["skill_id"]
                        and actual_program in programs,
                        "Operational serving did not use a retained verified program",
                    )
                    independent = outcome(
                        programs[actual_program]["program"],
                        rule["inputs"],
                        rule["expected_outputs"],
                        rule["config"],
                    )
                    require(
                        independent["outputs"] == selected["outputs"],
                        "Actual served values differ from independently executed retained AST",
                    )
                    if selected["status"] == "served":
                        lookup_work = 1 + max(
                            1,
                            (
                                len(
                                    json.dumps(
                                        rule["inputs"], separators=(",", ":")
                                    ).encode()
                                )
                                + 31
                            )
                            // 32,
                        )
                        require(
                            work == lookup_work + independent["tool_units"],
                            "Independent retained-program execution work differs from recorded serving work",
                        )
                else:
                    require(
                        selected["status"] == "skill_missing",
                        "Missing serving program has an unexplained status",
                    )
                    independent = outcome(
                        None, rule["inputs"], rule["expected_outputs"], rule["config"]
                    )
                require(
                    not any(
                        key in selected
                        for key in (
                            "training_inputs",
                            "training_outputs",
                            "expected_outputs",
                            "program",
                        )
                    ),
                    "Operational serving artifact includes training labels or a supplied replacement program",
                )
                source_bound = True
                input_hash, expected_hash = (
                    digest(rule["inputs"]),
                    digest(
                        {
                            "outputs": rule["expected_outputs"],
                            "nonce": contract["evaluation_nonce"],
                        }
                    ),
                )
                execution_status = selected["status"]
                comparison_work = sum(
                    1
                    + (
                        (len(actual.encode()) if actual is not None else 0)
                        + len(expected.encode())
                        + 31
                    )
                    // 32
                    for actual, expected in zip(
                        selected["outputs"], rule["expected_outputs"], strict=True
                    )
                )
                require(
                    comparison_work == acceptance["verifier_work"],
                    "Independent serving-output comparison cost differs",
                )
            require(
                independent["correct"] == acceptance["exact_rows"]
                and independent["rows"] == acceptance["evaluation_rows"],
                "Independent exact-output counts differ from recorded buyer checks",
            )
            require(
                independent["passed"] == acceptance["passed"]
                or acceptance.get("late") is True,
                "Independent output correctness differs from acceptance without a late-delivery explanation",
            )
            require(
                work + acceptance["verifier_work"]
                == acceptance["total_work"]
                == local["work"],
                "Recorded local work components disagree",
            )
            require(
                result["result_sha256"] == acceptance["artifact_sha256"]
                and result["cost_micro_usd"] == 0
                and result["evidence_level"] == "verified_outcome",
                "Outcome is not tied to this checked zero-payment artifact",
            )
            feedback = cdb.execute(
                "SELECT seq,hash FROM events WHERE json_extract(payload,'$.action_task_id')=? AND json_extract(payload,'$.task_state')='evaluated' ORDER BY seq LIMIT 1",
                (task["id"],),
            ).fetchone()
            require(
                feedback is not None, "Evaluated task lacks its durable feedback event"
            )
            neural = outcomes.get(task["intent_id"])
            if neural is None or committed_ack < feedback["seq"]:
                gaps.append(
                    {
                        "job_id": task["id"],
                        "finding": "Checked outcome has not yet been acknowledged by the committed neural checkpoint",
                    }
                )
                continue
            payload = neural["payload"]
            require(
                type(payload.get("reward")) in (int, float)
                and math.isfinite(payload["reward"])
                and -1 <= payload["reward"] <= 1,
                "Recorded neural reward is malformed",
            )
            require(
                payload["seq"] == feedback["seq"]
                and payload["contract_sha256"] == digest(contract)
                and payload["result_sha256"] == result["result_sha256"]
                and payload["reward"] == result["reward"],
                "Neural outcome differs from the actual commerce feedback",
            )
            credit = payload.get("neural_credit")
            require(
                type(credit) is dict
                and HASH.fullmatch(intent["eligibility_sha256"] or "")
                and HASH.fullmatch(intent["neural_state_sha256"] or "")
                and 0 <= credit["changed_synapses"] <= credit["tagged_synapses"] <= 256,
                "Originating eligibility or recorded bounded credit is missing",
            )
            require(
                HASH.fullmatch(credit["before_state_sha256"])
                and HASH.fullmatch(credit["after_state_sha256"]),
                "Neural credit state commitment is malformed",
            )
            final_rows += independent["rows"]
            exact_rows += independent["correct"]
            selected_work += work
            validation_work += acceptance["verifier_work"]
            failed += int(not acceptance["passed"])
            changed += credit["changed_synapses"]
            tagged += credit["tagged_synapses"]
            max_ack = max(max_ack, feedback["seq"])
            jobs.append(
                {
                    "job_id": task["id"],
                    "kind": rule["type"],
                    "intent_id": task["intent_id"],
                    "contract_sha256": digest(contract),
                    "source_bound": source_bound,
                    "origin_decision_sha256": origin["hash"],
                    "origin_neural_state_sha256": intent["neural_state_sha256"],
                    "eligibility_sha256": intent["eligibility_sha256"],
                    "program_sha256": actual_program,
                    "input_rows_sha256": input_hash,
                    "expected_rows_sha256": expected_hash,
                    "actual_outputs_sha256": independent["actual_sha256"],
                    "artifact_sha256": acceptance["artifact_sha256"],
                    "passed": acceptance["passed"],
                    "reward": payload["reward"],
                    "exact_rows": independent["correct"],
                    "evaluation_rows": independent["rows"],
                    "execution_status": execution_status,
                    "provider_work": work,
                    "buyer_final_check_work": acceptance["verifier_work"],
                    "total_job_work": local["work"],
                    "commerce_feedback_sequence": feedback["seq"],
                    "commerce_feedback_hash": feedback["hash"],
                    "neural_outcome_record_sequence": neural["journal_seq"],
                    "neural_outcome_hash": neural["journal_hash"],
                    "tagged_synapses_recorded": credit["tagged_synapses"],
                    "changed_synapses_recorded": credit["changed_synapses"],
                    "credit_before_state_sha256": credit["before_state_sha256"],
                    "credit_after_state_sha256": credit["after_state_sha256"],
                    "financial_micro_usd": 0,
                }
            )
        financial = financial_counts(cdb)
        baseline_head = None
        continuation = None
        if baseline:
            snapshots = baseline.get("snapshots", {})
            old_cognition = snapshots.get("cognition", {}).get("checkpoint", {})
            old_commerce = snapshots.get("commerce", {}).get("journal", {})
            if old_cognition:
                old_row = ndb.execute(
                    "SELECT hash FROM records WHERE seq=?",
                    (old_cognition.get("record_seq", -1),),
                ).fetchone()
                continuation = {
                    "genesis_preserved": old_cognition.get("genesis_utc")
                    == metadata.get("genesis_utc"),
                    "source_cursor_not_rewound": metadata.get("cursor", -1)
                    >= old_cognition.get("cursor", 0),
                    "record_sequence_not_rewound": metadata["record_seq"]
                    >= old_cognition.get("record_seq", 0),
                    "baseline_record_head_retained": old_row is not None
                    and old_row[0] == old_cognition.get("record_head"),
                }
                require(
                    all(continuation.values()),
                    "Cognitive history does not continue the supplied predeployment baseline",
                )
            if old_commerce:
                baseline_head = old_commerce.get("sequence")
                if baseline_head:
                    row = cdb.execute(
                        "SELECT hash FROM events WHERE seq=?", (baseline_head,)
                    ).fetchone()
                    require(
                        row is not None and row[0] == old_commerce["head"],
                        "Commerce history does not retain its baseline prefix",
                    )
                else:
                    require(
                        old_commerce.get("head") == "0" * 64,
                        "Empty commerce baseline head is invalid",
                    )
        financial["no_authorization_or_recorded_payment_in_entire_inspected_ledger"] = (
            financial["authorizations_count"]
            == financial["signing_requests_count"]
            == financial["settled_payment_transaction_count"]
            == 0
        )
        financial["new_financial_activity"] = (
            0
            if financial[
                "no_authorization_or_recorded_payment_in_entire_inspected_ledger"
            ]
            else None
        )
        financial["new_financial_activity_scope"] = (
            "Zero follows only when all inspected lifetime counters are zero; otherwise unknown without a separately counted financial baseline. This is ledger evidence, not an independent onchain balance/history check."
        )
        policy_disabled = (
            (public_commerce.get("policy") or {}).get("enabled") is False
            and public_commerce.get("financial_execution") is False
            and public_commerce.get("actions", {}).get("execution_enabled") is False
        )
        ready = (
            not gaps
            and learning >= args.minimum_learning
            and serving >= args.minimum_serving
            and len(jobs) == len(tasks)
            and bool(programs)
            and policy_disabled
            and commerce_cache["fresh"]
            and cognition_cache["fresh"]
            and public_cognition.get("phase") == "running"
            and financial["new_financial_activity"] == 0
            and (args.expected_learning is None or learning == args.expected_learning)
            and (args.expected_serving is None or serving == args.expected_serving)
        )
        retained = [
            {
                "program_sha256": ident,
                "learning_admissions": record["uses"],
                "buyer_evidence_sha256": record["evidence_sha256"],
            }
            for ident, record in sorted(programs.items())
        ]
        certificate = {
            "schema": "nuria.live-skill-loop-certificate.v1",
            "observed_utc": observed,
            "complete": ready,
            "complete_meaning": "Every selected job, including buyer-rejected outcomes and their recorded work and neural feedback, was checked. Completeness does not mean that every job succeeded.",
            "provider": args.provider,
            "source_id": args.skill_source,
            "source": source,
            "release_metadata_sha256": release_hash,
            "retained_programs": retained,
            "counts": {
                "actual_tasks": len(tasks),
                "checked_and_neurally_acknowledged_jobs": len(jobs),
                "learning_jobs": learning,
                "serving_jobs": serving,
                "failed_jobs_retained": failed,
                "learning_passed": sum(
                    job["passed"] for job in jobs if job["kind"] == "transform_program"
                ),
                "learning_failed": sum(
                    not job["passed"]
                    for job in jobs
                    if job["kind"] == "transform_program"
                ),
                "serving_passed": sum(
                    job["passed"]
                    for job in jobs
                    if job["kind"] == "transform_execution"
                ),
                "serving_failed": sum(
                    not job["passed"]
                    for job in jobs
                    if job["kind"] == "transform_execution"
                ),
                "retained_verified_programs": len(programs),
                "independently_checked_final_rows": final_rows,
                "exact_final_rows": exact_rows,
                "actual_provider_tool_units": selected_work,
                "buyer_final_check_units": validation_work,
                "tagged_synapses_recorded": tagged,
                "changed_synapses_recorded": changed,
            },
            "commerce_integrity": integrity,
            "cognition": {
                "genesis_utc": genesis,
                "source_cursor": metadata.get("cursor"),
                "committed_tick": metadata.get("tick"),
                "checkpoint_record_sequence": metadata["record_seq"],
                "checkpoint_record_head": metadata["record_head"],
                "committed_task_feedback_cursor": committed_ack,
                "largest_verified_feedback_sequence": max_ack,
                "checkpoint_matches_committed_head": True,
                "history_continuation": continuation,
                "scope": "Relevant origin and outcome record hashes were checked against the committed checkpoint view. No full cognitive-chain re-audit or independent synaptic-delta reconstruction is implied.",
            },
            "financial": {
                "policy_disabled_in_fresh_cache": policy_disabled,
                **financial,
            },
            "cache_evidence": {
                "commerce": commerce_cache,
                "cognition": cognition_cache,
            },
            "jobs": jobs,
            "gaps": gaps,
            "scope": "Program bodies, row values, private configuration and evaluation nonces are excluded from this certificate. Actual local, zero-payment safe-program learning and later unlabelled execution, independently replayed against buyer-held expected outputs. Real public observations may have declared generated formatting. Per-database read-only WAL snapshots are separately consistent, not globally atomic. Local records and source commitments are not an external witness, onchain proof, neural-superiority result or evidence of consciousness.",
        }
        private = {
            "schema": "nuria.live-skill-loop-private-evidence.v1",
            "observed_utc": observed,
            "read_only_sources": {
                "commerce": str(Path(args.commerce_db).resolve()),
                "cognition": str(Path(args.cognition_db).resolve()),
                "source_root": str(Path(args.source_root).resolve()),
            },
            "commerce_snapshot_mode": cdb.execute("PRAGMA journal_mode").fetchone()[0],
            "cognition_snapshot_mode": ndb.execute("PRAGMA journal_mode").fetchone()[0],
            "retained_library_state_sha256": digest(library),
            "retained_config_sha256": state_row["config_sha256"],
            "library_reported_work": library.get("total_work"),
            "job_phase_counts": phases,
            "baseline_commerce_sequence": baseline_head,
            "certificate_sha256": digest(certificate),
            "certificate": certificate,
            "inspection_scope": "Complete whitelisted per-job verification facts. Inputs, expected outputs, nonces, authentication material and private configuration are deliberately not copied; their source rows remain in the retained private databases.",
        }
        return private, certificate
    finally:
        close(cdb)
        close(ndb)


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def configure_paths(args):
    """Resolve explicit deployment roots without discovering credentials or hosts."""
    require(
        all(
            type(value) is str
            and 0 < len(value) <= 160
            and not any(ord(c) < 32 for c in value)
            for value in (args.provider, args.skill_source)
        ),
        "Provider and skill source identifiers are invalid",
    )
    data, public = Path(args.data_root), Path(args.public_root)
    args.commerce_db = data / "commerce/commerce.sqlite3"
    args.cognition_db = data / "cognition/cognition.sqlite3"
    args.commerce_cache = public / "commerce/status.json"
    args.cognition_cache = public / "cognition/status.json"
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        required=True,
        help="Parent of commerce/ and cognition/ databases",
    )
    parser.add_argument(
        "--public-root",
        required=True,
        help="Parent of commerce/ and cognition/ status caches",
    )
    parser.add_argument("--release-file", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--skill-source", required=True)
    parser.add_argument("--cognition-service", default="nuria-cognition.service")
    parser.add_argument("--commerce-service", default="nuria-commerce.service")
    parser.add_argument("--baseline-json")
    parser.add_argument("--minimum-learning", type=int, default=1)
    parser.add_argument("--minimum-serving", type=int, default=1)
    parser.add_argument("--expected-learning", type=int)
    parser.add_argument("--expected-serving", type=int)
    parser.add_argument("--output-directory", required=True)
    args = parser.parse_args()
    configure_paths(args)
    require(
        0 <= args.minimum_learning <= 256 and 1 <= args.minimum_serving <= 256,
        "Proof minimums are invalid",
    )
    require(
        all(
            value is None or 0 <= value <= 256
            for value in (args.expected_learning, args.expected_serving)
        ),
        "Expected task counts are invalid",
    )
    destination = Path(args.output_directory)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        private, public = collect(args)
        write_new(destination / "private-evidence.json", private)
        write_new(destination / "safe-public-certificate.json", public)
        print(
            canonical(
                {
                    "complete": public["complete"],
                    "counts": public["counts"],
                    "certificate_sha256": digest(public),
                    "financial_new_activity": public["financial"][
                        "new_financial_activity"
                    ],
                }
            )
        )
        return 0 if public["complete"] else 2
    except BaseException as error:
        # Never echo arbitrary data-bearing database/provider errors.
        write_new(
            destination / "collection-failure.json",
            {
                "phase": "evidence_incomplete",
                "exception_type": type(error).__name__,
                "finding": str(error)
                if isinstance(error, ValueError)
                else "Non-data exception details are retained by the trusted local caller only",
                "observed_utc": datetime.now(timezone.utc).isoformat(),
                "scope": "No readiness or successful verification is established; inspect the trusted local invocation privately.",
            },
        )
        print(
            canonical(
                {
                    "complete": False,
                    "phase": "evidence_incomplete",
                    "exception_type": type(error).__name__,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Post-hoc completion proposal for two originally selected nonlearning controls.

Default preflight reads no experimental artifacts and starts no inference.
Preparation and execution require the original comparison and post-job to have
ended. This supplements coverage; it never rewrites the original 200k study.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

VERSION = "nuria.supplementary-nonlearning-controls.v3"
ORIGINAL_PROTOCOL = "4e7b7a5c71890574f2b319cce6a493adca46e28c2992269e8132df58e75d29ed"
ORIGINAL_FREEZE = "a4cff862bda1b3f7136726106f468e5f8153af0372f5cbe437bc3c37965fe342"
DEADLINE = "2026-10-26T00:20:14+00:00"
CONDITIONS = ("schema_first", "stateless")
LIMITS = {
    "max_turns": 12,
    "max_output_tokens": 6000,
    "max_tool_calls": 12,
    "max_tokens_per_turn": 1536,
    "memory_bytes": 6000,
}
HEX = re.compile(r"[0-9a-f]{64}\Z")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    path = Path(path)
    require(
        not path.is_symlink()
        and path.is_file()
        and path.stat().st_size <= 64 * 1024 * 1024,
        "Evidence identity is missing, indirect or oversized",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    sha(path)
    return json.loads(Path(path).read_bytes())


def write(path, value):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def append(path, value):
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "a") as stream:
        stream.write(json.dumps(value, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def alive(pid):
    require(type(pid) is int and 1 < pid <= 2147483647, "Invalid process guard")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ended_guard(run, guard_pids, confirmed):
    # No run/protocol/checker body may be loaded before these guards.
    require(
        confirmed is True,
        "Explicit original/post-job completion confirmation is required",
    )
    require(
        guard_pids and all(not alive(pid) for pid in guard_pids),
        "Original inference or post-job process remains present",
    )
    require(
        (run / "summary.json").is_file() and (run / "selected-results.json").is_file(),
        "Original final artifacts are absent",
    )


def source_files(repository, freeze):
    require(
        type(freeze.get("files")) is dict and len(freeze["files"]) == 17,
        "The original 17-file source freeze is required",
    )
    root = repository.resolve()
    for relative, expected in freeze["files"].items():
        path = root / relative
        require(
            type(relative) is str
            and not Path(relative).is_absolute()
            and path.resolve().is_relative_to(root)
            and HEX.fullmatch(expected)
            and sha(path) == expected,
            "A frozen source identity differs",
        )


def initial_account(main_allowance, reservation=None, closed=None):
    def fields(value):
        keys = (
            "known_completion_tokens",
            "prior_unknown_completion_ceiling",
            "pending_completion_reservation",
        )
        require(
            all(type(value.get(k)) is int and value[k] >= 0 for k in keys),
            "Prior allowance counters are invalid",
        )
        known, ceiling, pending = (value[k] for k in keys)
        require(
            pending <= 6000
            and value.get("conservative_completion_charge")
            == known + ceiling + pending
            <= 200000
            and type(value.get("current_unknown_usage")) is bool,
            "Prior allowance conservation differs",
        )
        return known, ceiling, pending

    base_known, base_ceiling, base_pending = fields(main_allowance)
    selected = main_allowance
    if reservation is not None:
        selected = reservation["allowance"]
        known, ceiling, pending = fields(selected)
        require(
            known == base_known
            and ceiling == base_ceiling
            and pending == 6000
            and selected["current_unknown_usage"] is True
            and base_pending == 0,
            "Post-job reservation does not extend the original closed allowance",
        )
    if closed is not None:
        require(reservation is not None, "Post-job accounting lacks its reservation")
        selected = closed["allowance"]
        known, ceiling, pending = fields(selected)
        used = closed["model_usage"]["completion_tokens"]
        unknown = closed["model_usage"]["unknown_additional_usage"]
        require(
            type(used) is int
            and 0 <= used <= 6000
            and type(unknown) is bool
            and known == base_known + used
            and ceiling == base_ceiling
            and pending == (6000 - used if unknown else 0)
            and selected["current_unknown_usage"] == unknown,
            "Post-job observed usage does not reconcile its reservation",
        )
    known, ceiling, pending = fields(selected)
    return {
        "known_completion_tokens": known,
        "unknown_completion_ceiling": ceiling + pending,
        "prior_unresolved_usage": bool(
            main_allowance.get("prior_usage_unknown")
            or main_allowance.get("current_unknown_usage")
            or selected["current_unknown_usage"]
            or ceiling
            or pending
        ),
        "prior_conservative_charge": known + ceiling + pending,
        "post_job_reservation_present": reservation is not None,
        "post_job_usage_closed": closed is not None,
        "scope": "Historical pending liabilities remain fully charged; no old usage is released or relabelled as known.",
    }


def semantic_account(
    repository,
    post_root,
    base,
    protocol_path=None,
    expected_protocol=None,
    helper_path=None,
):
    """Close only pinned, source-bound semantic usage with a complete own trace."""
    if protocol_path is None:
        require(
            not (
                post_root / "semantic-input-01/semantic-work-trial-reservation-01.json"
            ).exists()
            and not (
                post_root / "semantic-input-01/semantic-work-trial-terminal-01.json"
            ).exists(),
            "A started semantic trial requires its pinned closed accounting before supplementary inference",
        )
        return (
            base,
            {},
            {
                "known_completion_tokens": 0,
                "reserved_completion_tokens": 6000,
                "closed": False,
            },
        )
    require(
        expected_protocol is not None
        and helper_path is not None
        and sha(protocol_path) == expected_protocol,
        "Semantic protocol and helper identities are required",
    )
    protocol = load(protocol_path)
    require(
        protocol.get("schema") == "nuria.semantic-work-trial.v1"
        and protocol.get("host_helper_sha256") == sha(helper_path)
        and protocol.get("maximum_completion_tokens") == 6000
        and protocol.get("maximum_additional_cpu_encodes") == 1,
        "Semantic trial protocol differs",
    )
    require(
        protocol.get("host_checker_sha256")
        == sha(post_root / "supervise_postrun_04.py"),
        "Semantic host checker source changed",
    )
    for relative, expected in protocol["new_source"].items():
        path = repository / relative
        require(
            path.resolve().is_relative_to(repository.resolve())
            and sha(path) == expected,
            "Semantic source binding changed",
        )
    root = post_root / "semantic-input-01"
    terminal_path = root / "semantic-work-trial-terminal-01.json"
    terminal = load(terminal_path)
    require(
        terminal.get("protocol_sha256") == expected_protocol,
        "Semantic terminal binds another protocol",
    )
    job = "semantic-recorded-work-audit-01"
    key = hashlib.sha256(job.encode()).hexdigest()
    paths = {
        "semantic_protocol": protocol_path,
        "semantic_helper": helper_path,
        "semantic_terminal": terminal_path,
    }
    for kind in ("result", "artifact", "decision"):
        paths["semantic_" + kind] = (
            root / "semantic-work-trial-01/jobs" / (key + "." + kind + ".json")
        )
    result, artifact = load(paths["semantic_result"]), load(paths["semantic_artifact"])

    def canonical(value):
        return hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()

    require(
        result["result_sha256"]
        == canonical({k: v for k, v in result.items() if k != "result_sha256"})
        and artifact["result_sha256"]
        == canonical({k: v for k, v in artifact.items() if k != "result_sha256"}),
        "Semantic analytical record checksum differs",
    )
    require(
        result["job_id"] == artifact["job_id"] == job
        and result["model_identity"] == protocol["model_identity"]
        and result["artifact_sha256"] == sha(paths["semantic_artifact"])
        and result["decision_file_sha256"] == sha(paths["semantic_decision"]),
        "Semantic own artifact or model binding differs",
    )
    usage = result["model_usage"]
    require(
        usage == artifact["model_usage"]
        and usage["unknown_additional_usage"] is False
        and type(usage["completion_tokens"]) is int
        and 0 <= usage["completion_tokens"] <= 6000
        and type(usage["prompt_tokens"]) is int
        and usage["prompt_tokens"] >= 0
        and usage["total_tokens"]
        == usage["prompt_tokens"] + usage["completion_tokens"],
        "Semantic usage is not closed",
    )
    prompt = completion = 0
    for event in artifact["trace"]:
        require(
            not event.get("usage_unknown"), "Semantic trace contains unresolved usage"
        )
        observed = (
            event.get("model", {}).get("usage")
            if "model" in event
            else event.get("usage")
        )
        if observed is not None:
            require(
                type(observed.get("prompt_tokens")) is int
                and observed["prompt_tokens"] >= 0
                and type(observed.get("completion_tokens")) is int
                and observed["completion_tokens"] >= 0,
                "Semantic trace usage is malformed",
            )
            prompt += observed["prompt_tokens"]
            completion += observed["completion_tokens"]
    require(
        (prompt, completion) == (usage["prompt_tokens"], usage["completion_tokens"]),
        "Semantic usage does not reconcile its independent retained call trace",
    )
    allowance = terminal["allowance"]
    require(
        allowance["known_completion_tokens"]
        == base["known_completion_tokens"] + completion
        and allowance["prior_unknown_completion_ceiling"]
        == base["unknown_completion_ceiling"]
        and allowance["conservative_completion_charge"]
        == base["prior_conservative_charge"] + completion
        and allowance["pending_completion_reservation"] == 0
        and allowance["current_unknown_usage"] is False
        and allowance["reserved_cpu_encodes"] == allowance["completed_cpu_encodes"] == 1
        and allowance["unresolved_cpu_encode_ceiling"] == 0
        and allowance["conservative_completion_charge"] <= 200000,
        "Semantic usage or CPU reservation conservation differs",
    )
    require(
        type(terminal["qualification"]["accepted"]) is bool
        and terminal["qualification"]["terminal"]["accepted"]
        == terminal["qualification"]["accepted"]
        and terminal["qualification"]["terminal"]["episode_id"] == result["episode_id"]
        and terminal["qualification"]["terminal"]["proposal_sha256"]
        == artifact["proposal_sha256"],
        "Semantic terminal qualification is unresolved",
    )
    closed = {
        **base,
        "known_completion_tokens": base["known_completion_tokens"] + completion,
        "prior_conservative_charge": base["prior_conservative_charge"] + completion,
    }
    return (
        closed,
        {name: sha(path) for name, path in paths.items()},
        {
            "known_completion_tokens": completion,
            "reserved_completion_tokens": 0,
            "closed": True,
            "protocol_sha256": expected_protocol,
            "trace_usage_reconciled": True,
            "source_bindings_verified": True,
        },
    )


def starting_evidence(
    *,
    repository,
    original_protocol,
    original_freeze,
    main_run,
    main_check,
    post_root,
    guard_pids,
    confirmed,
    semantic_protocol=None,
    semantic_protocol_sha256=None,
    semantic_helper=None,
):
    ended_guard(main_run, guard_pids, confirmed)
    require(
        sha(original_protocol) == ORIGINAL_PROTOCOL
        and sha(original_freeze) == ORIGINAL_FREEZE,
        "Original protocol or source identity differs",
    )
    protocol, freeze = load(original_protocol), load(original_freeze)
    source_files(repository, freeze)
    check = load(main_check)
    require(
        check.get("schema") == "nuria.grounded-comparison-verification.v1"
        and check.get("passed") is True
        and check.get("protocol_sha256") == ORIGINAL_PROTOCOL
        and check.get("source_freeze_sha256") == ORIGINAL_FREEZE
        and check.get("selected_records") == 120,
        "Original independent record verification did not pass",
    )
    summary, selected = (
        load(main_run / "summary.json"),
        load(main_run / "selected-results.json"),
    )
    require(
        type(selected) is list and len(selected) == 120,
        "Original selected denominator changed",
    )
    # An older bounded uncertainty ceiling remains charged, but fresh ambiguous
    # calls are not permission to start another experiment. Reconcile the current
    # comparison before disclosure; a conservative cap alone cannot close it.
    require(
        summary["allowance"].get("current_unknown_usage") is False
        and summary["allowance"].get("pending_completion_reservation") == 0,
        "Fresh main inference usage is unresolved; supplementary calls refused",
    )
    task_ids = protocol["selected_task_ids"]["reserved"]
    require(
        len(task_ids) == 12 and len(set(task_ids)) == 12,
        "Original reserved identifiers differ",
    )
    coverage = {}
    # Coverage alone selects whether a condition needs completion. Scores,
    # answer contents and question difficulty do not enter this selection.
    for condition in CONDITIONS:
        rows = [
            r for r in selected if r["arm"] == condition and r["phase"] == "reserved"
        ]
        require(
            len(rows) == 12 and {r["task_id"] for r in rows} == set(task_ids),
            "Original control selection differs",
        )
        done = [r["task_id"] for r in rows if r.get("agent") is not None]
        coverage[condition] = {
            "original_executed_ids": done,
            "original_executed": len(done),
            "skip_original_complete": len(done) == 12,
        }
    paths = {
        "original_protocol": original_protocol,
        "original_source_freeze": original_freeze,
        "main_summary": main_run / "summary.json",
        "main_selected_results": main_run / "selected-results.json",
        "main_independent_check": main_check,
        "post_lifecycle": post_root / "postrun-04-lifecycle.jsonl",
    }
    lifecycle = [
        json.loads(line)
        for line in Path(paths["post_lifecycle"]).read_text().splitlines()
    ]
    require(
        sum(row.get("event") == "useful_source_reserved" for row in lifecycle) <= 1
        and sum(row.get("event") == "useful_source_usage_closed" for row in lifecycle)
        <= 1,
        "Post-job lifecycle repeats a supposedly unique attempt",
    )
    reservation_path = post_root / "useful-source-01/postrun-04-reservation.json"
    closed_path = post_root / "useful-source-01/postrun-04-accounting.json"
    reservation = closed = None
    if reservation_path.exists():
        reservation = load(reservation_path)
        paths["post_reservation"] = reservation_path
        require(
            reservation.get("schema") == "nuria.useful-source-reservation.v1"
            and reservation.get("job_id") == "recorded-work-audit-01",
            "Post-job reservation identifies another job",
        )
    if closed_path.exists():
        closed = load(closed_path)
        paths["post_accounting"] = closed_path
        require(
            closed.get("job_id") == "recorded-work-audit-01",
            "Post-job accounting identifies another job",
        )
    terminal = post_root / "useful-source-01/terminal-check-04.json"
    if terminal.exists():
        paths["post_terminal"] = terminal
        require(
            closed is not None and load(terminal)["allowance"] == closed["allowance"],
            "Post-job terminal receipt does not bind its closed usage",
        )
    require(
        reservation is None
        or (
            closed is not None
            and closed["allowance"].get("current_unknown_usage") is False
            and closed["allowance"].get("pending_completion_reservation") == 0
            and closed["model_usage"].get("unknown_additional_usage") is False
        ),
        "Fresh post-job inference usage is unresolved; supplementary calls refused",
    )
    account = initial_account(summary["allowance"], reservation, closed)
    account, semantic_identities, semantic_usage = semantic_account(
        repository,
        post_root,
        account,
        semantic_protocol,
        semantic_protocol_sha256,
        semantic_helper,
    )
    return {
        "original_protocol": protocol,
        "source_freeze": freeze,
        "identities": {
            **{key: sha(path) for key, path in paths.items()},
            **semantic_identities,
        },
        "coverage": coverage,
        "starting_account": account,
        "semantic_trial_usage": semantic_usage,
    }


def prepare_protocol(*, output, **context):
    evidence = starting_evidence(**context)
    source = evidence["original_protocol"]
    result = {
        "schema": VERSION,
        "scope": "Post-hoc coverage completion of the original selected nonlearning conditions, not a new unseen preregistration. Original study, 200k allowance and all120 selected records remain unchanged.",
        "condition_order": list(CONDITIONS),
        "selected_task_ids": source["selected_task_ids"]["reserved"],
        "planned_records": 24,
        "skip_policy": "Skip a condition only if all twelve original reserved proposals were completed; otherwise repeat all twelve in fresh none-memory state without teaching calls.",
        "grading_policy": "All supplementary inference ends before any supplementary oracle is opened. Only complete twelve-proposal conditions are graded. Incomplete conditions retain unknown acceptance; no terminal experience or neural credit is applied.",
        "limits": LIMITS,
        "memory_mode": "none",
        "neural_context": None,
        "initial_section": "schema",
        "seed": 20261025,
        "endpoint": source["endpoint"],
        "model_alias": source["model_alias"],
        "model_identity": source["model_identity"],
        "enable_thinking": source["enable_thinking"],
        "additional_completion_cap": 60000,
        "other_supplementary_reserved_completion_tokens": evidence[
            "semantic_trial_usage"
        ]["reserved_completion_tokens"],
        "other_supplementary_known_completion_tokens": evidence["semantic_trial_usage"][
            "known_completion_tokens"
        ],
        "semantic_trial_usage": evidence["semantic_trial_usage"],
        "cumulative_completion_cap": 260000,
        "maximum_wall_seconds": 3600,
        "absolute_deadline_utc": DEADLINE,
        "original_protocol_sha256": ORIGINAL_PROTOCOL,
        "original_source_freeze_sha256": ORIGINAL_FREEZE,
        "frozen_source_files": evidence["source_freeze"]["files"],
        "starting_evidence": evidence["identities"],
        "original_coverage": evidence["coverage"],
        "starting_account": evidence["starting_account"],
        "artifacts": source["artifacts"],
        "helper_sha256": sha(Path(__file__)),
        "prepared_at_utc": utcnow().isoformat(),
        "order_effects": "Conditions run schema-first then stateless in fixed predeclared order. Hardware, runtime order and post-hoc coverage selection can affect the comparison; this is not a newly randomized or unseen preregistered trial.",
        "input_equivalence_scope": "With the same host observations, none-memory first requests do not depend on settled teaching records. Real tool timing, hashes, prompt lengths, floating-point generation and latency may vary. No bit-exact model reproduction is promised.",
        "financial_actions": 0,
    }
    write(output, result)
    return {
        "schema": VERSION,
        "protocol_sha256": sha(output),
        "planned_records": 24,
        "skip_conditions": [
            key
            for key, value in evidence["coverage"].items()
            if value["skip_original_complete"]
        ],
        "starting_account": evidence["starting_account"],
        "model_requests": 0,
    }


class Allowance:
    def __init__(self, protocol, clock=None, now=None):
        clock = time.monotonic if clock is None else clock
        now = utcnow if now is None else now
        self.protocol, self.clock, self.now = protocol, clock, now
        self.started = clock()
        self.deadline = dt.datetime.fromisoformat(protocol["absolute_deadline_utc"])
        self.prior = protocol["starting_account"]["prior_conservative_charge"]
        self.held = protocol["other_supplementary_reserved_completion_tokens"]
        self.other_used = protocol["other_supplementary_known_completion_tokens"]
        require(
            type(self.held) is int
            and type(self.other_used) is int
            and 0 <= self.held + self.other_used <= 6000
            and (self.held == 6000 or self.held == 0),
            "Semantic trial charge and reservation exceed their shared bound",
        )
        self.known = self.prompt = self.pending = 0
        self.unknown, self.stop = False, None
        require(
            type(self.prior) is int and 0 <= self.prior <= 260000,
            "Starting cumulative charge is invalid",
        )

    def remaining_seconds(self):
        return min(
            3600 - (self.clock() - self.started),
            (self.deadline - self.now()).total_seconds(),
        )

    def reserve(self):
        if self.stop:
            return False
        if self.remaining_seconds() <= 1:
            self.stop = "supplementary_wall_or_runtime_deadline"
        elif self.known + self.other_used + self.held + 6000 > 60000:
            self.stop = "additional_completion_reservation_limit"
        elif self.prior + self.known + self.held + 6000 > 260000:
            self.stop = "cumulative_completion_reservation_limit"
        else:
            self.pending = 6000
            return True
        return False

    def finish(self, used, prompt, unknown):
        require(
            type(used) is int
            and 0 <= used <= self.pending
            and type(prompt) is int
            and prompt >= 0
            and type(unknown) is bool,
            "Supplementary usage exceeds reservation",
        )
        self.known += used
        self.prompt += prompt
        self.pending = self.pending - used if unknown else 0
        self.unknown = unknown
        if unknown:
            self.stop = "new_unreconciled_model_usage"

    def state(self):
        return {
            "new_known_completion_tokens": self.known,
            "new_known_prompt_tokens": self.prompt,
            "pending_completion_reservation": self.pending,
            "new_unknown_usage": self.unknown,
            "other_supplementary_reserved_completion_tokens": self.held,
            "other_supplementary_known_completion_tokens": self.other_used,
            "control_conservative_charge": self.known + self.pending,
            "additional_conservative_charge": self.known
            + self.pending
            + self.other_used
            + self.held,
            "prior_conservative_charge": self.prior,
            "cumulative_conservative_charge": self.prior
            + self.known
            + self.pending
            + self.held,
            "stop_reason": self.stop,
            "elapsed_seconds": self.clock() - self.started,
            "remaining_seconds": self.remaining_seconds(),
        }


def bound_input(root, record):
    path = root / record["path"]
    require(
        not Path(record["path"]).is_absolute()
        and path.resolve().is_relative_to(root.resolve())
        and sha(path) == record["sha256"],
        "Frozen input identity differs or escapes source root",
    )
    return path


def run_supplement(
    *,
    protocol_path,
    protocol_sha256,
    publication,
    publication_sha256,
    output,
    inputs_root,
    **context,
):
    # The same completion guards also precede supplement protocol/course access.
    ended_guard(context["main_run"], context["guard_pids"], context["confirmed"])
    require(
        sha(protocol_path) == protocol_sha256, "Supplementary protocol identity differs"
    )
    protocol = load(protocol_path)
    evidence = starting_evidence(**context)
    require(
        protocol["schema"] == VERSION
        and protocol["helper_sha256"] == sha(Path(__file__))
        and protocol["starting_evidence"] == evidence["identities"]
        and protocol["starting_account"] == evidence["starting_account"]
        and protocol["original_coverage"] == evidence["coverage"]
        and protocol["frozen_source_files"] == evidence["source_freeze"]["files"],
        "Published supplementary source or starting evidence differs",
    )
    require(
        protocol["condition_order"] == list(CONDITIONS)
        and protocol["limits"] == LIMITS
        and protocol["planned_records"] == 24
        and protocol["additional_completion_cap"] == 60000
        and protocol["other_supplementary_reserved_completion_tokens"]
        == evidence["semantic_trial_usage"]["reserved_completion_tokens"]
        and protocol["other_supplementary_known_completion_tokens"]
        == evidence["semantic_trial_usage"]["known_completion_tokens"]
        and protocol["semantic_trial_usage"] == evidence["semantic_trial_usage"]
        and protocol["cumulative_completion_cap"] == 260000
        and protocol["maximum_wall_seconds"] == 3600
        and protocol["absolute_deadline_utc"] == DEADLINE
        and protocol["memory_mode"] == "none"
        and protocol["neural_context"] is None
        and protocol["initial_section"] == "schema"
        and protocol["seed"] == 20261025,
        "Supplementary conditions or allowances changed",
    )
    original = evidence["original_protocol"]
    require(
        all(
            protocol[key] == original[key]
            for key in (
                "endpoint",
                "model_alias",
                "model_identity",
                "enable_thinking",
                "artifacts",
            )
        ),
        "Frozen model or tools changed",
    )
    require(
        sha(publication) == publication_sha256,
        "Prospective publication receipt differs",
    )
    published = load(publication)
    require(
        published.get("schema") == "nuria.supplementary-controls-publication.v1"
        and published.get("exact_bytes_verified") is True
        and published.get("helper_sha256") == sha(Path(__file__))
        and published.get("protocol_sha256") == protocol_sha256
        and re.fullmatch(r"[0-9a-f]{40}", published.get("public_commit", "")),
        "Supplementary code and protocol require verified prior publication",
    )
    sys.path.insert(0, str(context["repository"]))
    from cognition.analyst_workspace import SCHEMA, AnalystWorkspace, grade_result
    from cognition.grounded_agent import GroundedAnalyst
    from cognition.procedural_memory import ProceduralMemory
    from cognition.reasoner_client import LocalReasoner, ReasonerError, digest

    allowance = Allowance(protocol)
    require(
        allowance.remaining_seconds() > 1,
        "Existing model lifetime or supplementary wall deadline has expired",
    )
    registry = bound_input(inputs_root, protocol["artifacts"]["registry"])
    course_path = bound_input(inputs_root, protocol["artifacts"]["reserved_course"])
    course = load(course_path)["tasks"]
    require(
        len(course) == 12
        and [row["id"] for row in course] == protocol["selected_task_ids"],
        "Original selected course differs",
    )
    workspace = AnalystWorkspace(registry)
    source_id = load(registry)["revision"]
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write(
        output / "run-freeze.json",
        {
            "protocol": protocol,
            "protocol_sha256": protocol_sha256,
            "publication_receipt_sha256": publication_sha256,
            "source_freeze_sha256": ORIGINAL_FREEZE,
        },
    )
    # Bind execution to the reviewed protocol, not merely to an output path.
    # A crash or a second output directory cannot renew the same allowance.
    reservations = context["post_root"] / "supplementary-control-reservations"
    require(not reservations.is_symlink(), "Indirect run reservation directory")
    reservations.mkdir(mode=0o700, exist_ok=True)
    require(
        reservations.is_dir() and reservations.stat().st_mode & 0o077 == 0,
        "Run reservations require a private mode-0700 directory",
    )
    write(
        reservations / (protocol_sha256 + ".execution.json"),
        {
            "schema": "nuria.supplementary-execution-reservation.v1",
            "protocol_sha256": protocol_sha256,
            "publication_receipt_sha256": publication_sha256,
            "output": str(output.resolve()),
            "pid": os.getpid(),
            "reserved_at_utc": utcnow().isoformat(),
            "starting_account": allowance.state(),
            "scope": "One execution identity; interrupted or completed attempts remain reserved. A different output path does not authorize a retry.",
        },
    )
    append(
        output / "events.jsonl",
        {
            "event": "supplementary_started",
            "original_study_mutated": False,
            "account": allowance.state(),
        },
    )
    rows = [
        {
            "condition": condition,
            "task_id": task["id"],
            "db_id": task["db_id"],
            "status": "not_executed",
            "agent": None,
            "evaluation": None,
            "observed_usage": {
                "completion_tokens": 0,
                "prompt_tokens": 0,
                "unknown_additional_usage": False,
            },
        }
        for condition in CONDITIONS
        for task in course
    ]
    memories, source_verified, terminal_error = {}, False, None

    def frozen():
        require(
            sha(protocol_path) == protocol_sha256
            and sha(Path(__file__)) == protocol["helper_sha256"],
            "Supplementary code or protocol changed",
        )
        source_files(context["repository"], evidence["source_freeze"])

    class Metered:
        enable_thinking = protocol["enable_thinking"]

        def __init__(self, base):
            self.base = base
            self.completion = self.prompt = 0
            self.unknown = False

        def complete(self, messages, **kwargs):
            remaining = allowance.remaining_seconds()
            require(remaining > 1, "Deadline ended before disclosure")
            self.base.timeout = min(120, max(1, int(remaining)))
            try:
                result = self.base.complete(messages, **kwargs)
            except ReasonerError as error:
                if error.usage is not None:
                    self.completion += error.usage["completion_tokens"]
                    self.prompt += error.usage["prompt_tokens"]
                self.unknown = self.unknown or error.usage_unknown
                raise
            except BaseException:
                self.unknown = True
                raise
            self.completion += result["usage"]["completion_tokens"]
            self.prompt += result["usage"]["prompt_tokens"]
            return result

    try:
        for condition in CONDITIONS:
            directory = output / condition
            directory.mkdir(mode=0o700)
            memory = ProceduralMemory(
                directory / "memory.sqlite",
                source_id=source_id,
                model_id=protocol["model_identity"],
                tool_version=SCHEMA,
            )
            memories[condition] = memory
            base = LocalReasoner(
                protocol["endpoint"],
                protocol["model_alias"],
                enable_thinking=protocol["enable_thinking"],
                max_input_bytes=120000,
                max_response_bytes=524288,
            )
            for task in course:
                row = next(
                    row
                    for row in rows
                    if row["condition"] == condition and row["task_id"] == task["id"]
                )
                if protocol["original_coverage"][condition]["skip_original_complete"]:
                    row["reason"] = "original_twelve_already_completed"
                    continue
                if not allowance.reserve():
                    row["reason"] = allowance.stop
                    continue
                append(
                    output / "allowance.jsonl",
                    {
                        "event": "job_reserved",
                        "condition": condition,
                        "task_id": task["id"],
                        **allowance.state(),
                    },
                )
                metered = Metered(base)
                try:
                    frozen()
                    agent = GroundedAnalyst(
                        metered,
                        workspace,
                        memory,
                        **LIMITS,
                        memory_mode="none",
                        neural_context=None,
                        initial_tool="read_knowledge"
                        if condition == "schema_first"
                        else None,
                        initial_section="schema",
                    )
                    row["agent"] = agent.run(
                        job_id=task["id"],
                        question=task["question"],
                        db_id=task["db_id"],
                        seed=20261025,
                    )
                    frozen()
                    row["status"] = row["agent"]["status"]
                except BaseException as error:  # noqa: BLE001
                    row["status"] = "host_error"
                    row["error_type"] = type(error).__name__
                    allowance.stop = "supplementary_host_error_requires_review"
                finally:
                    allowance.finish(
                        metered.completion, metered.prompt, metered.unknown
                    )
                    row["observed_usage"] = {
                        "completion_tokens": metered.completion,
                        "prompt_tokens": metered.prompt,
                        "unknown_additional_usage": metered.unknown,
                    }
                    append(output / "proposals.jsonl", row)
                    append(
                        output / "allowance.jsonl",
                        {"event": "job_closed", **allowance.state()},
                    )
        frozen()
        append(
            output / "events.jsonl",
            {
                "event": "all_supplementary_inference_ended",
                "account": allowance.state(),
            },
        )
        # Oracle access is after the last possible model call. No terminal
        # labels are written into memory or used to choose another condition.
        oracle_path = bound_input(inputs_root, protocol["artifacts"]["oracle"])
        oracle = load(oracle_path)["tasks"]
        for condition in CONDITIONS:
            batch = [row for row in rows if row["condition"] == condition]
            complete = len(batch) == 12 and all(
                row["agent"] is not None for row in batch
            )
            if not complete:
                continue
            for row in batch:
                expected = oracle[row["task_id"]]
                result = row["agent"]
                accepted = bool(
                    result["final_sql"] is not None
                    and grade_result(
                        result["selected_receipt"],
                        expected["expected_rows"],
                        ordered=expected["ordered"],
                        expected_columns=expected["column_count"],
                    )
                )
                row["evaluation"] = {
                    "accepted": accepted,
                    "checker_id": "posthoc-finite-sqlite-result-v1",
                    "proposal_sha256": result["proposal_sha256"],
                    "oracle_sha256": digest(expected),
                    "scope": "Complete original twelve-task condition after all supplementary inference ended; no experience settlement or neural credit",
                }
                append(output / "outcomes.jsonl", row)
        frozen()
        source_verified = True
    except BaseException as error:  # noqa: BLE001
        terminal_error = type(error).__name__
        allowance.stop = (
            allowance.stop or "supplementary_finalization_failure_requires_review"
        )
        append(
            output / "events.jsonl",
            {
                "event": "supplementary_finalization_failure",
                "error_type": terminal_error,
            },
        )
    finally:
        for condition, memory in memories.items():
            try:
                write(output / condition / "memory-export.json", memory.export())
            except BaseException as error:  # noqa: BLE001
                terminal_error = terminal_error or type(error).__name__
                allowance.stop = (
                    allowance.stop or "memory_export_failure_requires_review"
                )
                append(
                    output / "events.jsonl",
                    {
                        "event": "memory_export_failure",
                        "condition": condition,
                        "error_type": type(error).__name__,
                    },
                )
            finally:
                memory.close()
        summary = {
            "schema": VERSION,
            "posthoc": True,
            "original_study_mutated": False,
            "planned_records": 24,
            "conditions": {},
            "allowance": allowance.state(),
            "source_verified_at_end": source_verified,
            "terminal_error": terminal_error,
            "financial_transactions": 0,
            "scope": "Post-hoc coverage completion of selected nonlearning controls. No unseen preregistration or bit-exact model reproduction claim.",
        }
        for condition in CONDITIONS:
            batch = [row for row in rows if row["condition"] == condition]
            summary["conditions"][condition] = {
                "selected": 12,
                "executed": sum(row["agent"] is not None for row in batch),
                "skipped_original_complete": protocol["original_coverage"][condition][
                    "skip_original_complete"
                ],
                "graded": sum(row["evaluation"] is not None for row in batch),
                "accepted": sum(
                    row["evaluation"]["accepted"]
                    for row in batch
                    if row["evaluation"] is not None
                ),
                "abstentions": sum(
                    row["agent"] is not None and row["agent"]["final_sql"] is None
                    for row in batch
                ),
                "unknown_or_unexecuted": sum(
                    row["evaluation"] is None for row in batch
                ),
            }
        summary["complete"] = bool(
            source_verified
            and not terminal_error
            and not allowance.unknown
            and all(
                value["skipped_original_complete"] or value["graded"] == 12
                for value in summary["conditions"].values()
            )
        )
        write(output / "selected-results.json", rows)
        write(output / "summary.json", summary)
    return summary


def preflight():
    return {
        "schema": VERSION,
        "mode": "proposal_preflight",
        "artifact_bodies_read": False,
        "model_requests": 0,
        "original_study_mutated": False,
        "condition_order": list(CONDITIONS),
        "planned_records": 24,
        "limits": LIMITS,
        "additional_completion_cap": 60000,
        "other_supplementary_reserved_completion_tokens": 6000,
        "cumulative_completion_cap": 260000,
        "maximum_wall_seconds": 3600,
        "absolute_deadline_utc": DEADLINE,
        "requires": "Explicit original/post-job end confirmation, absent guarded processes, passed original independent receipt, fresh accounting of all liabilities, publication of exact helper/protocol bytes and source review before launch",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("preflight", "prepare", "run"), nargs="?", default="preflight"
    )
    for name in (
        "repository",
        "original-protocol",
        "original-freeze",
        "main-run",
        "main-check",
        "post-root",
        "protocol-output",
        "protocol",
        "publication",
        "output",
        "inputs-root",
        "semantic-protocol",
        "semantic-helper",
    ):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--guard-pid", type=int, action="append")
    parser.add_argument("--confirmed-original-ended", action="store_true")
    parser.add_argument("--protocol-sha256")
    parser.add_argument("--publication-sha256")
    parser.add_argument("--semantic-protocol-sha256")
    args = parser.parse_args(argv)
    if args.mode == "preflight":
        print(json.dumps(preflight(), indent=2))
        return 0
    context = {
        key: getattr(args, key)
        for key in (
            "repository",
            "original_protocol",
            "original_freeze",
            "main_run",
            "main_check",
            "post_root",
        )
    }
    require(
        all(value is not None for value in context.values()),
        "All explicit original evidence paths are required",
    )
    context.update(
        semantic_protocol=args.semantic_protocol,
        semantic_protocol_sha256=args.semantic_protocol_sha256,
        semantic_helper=args.semantic_helper,
    )
    require(
        args.guard_pid,
        "Explicit original, post-job and semantic process guards are required",
    )
    context.update(
        guard_pids=args.guard_pid,
        confirmed=args.confirmed_original_ended,
    )
    if args.mode == "prepare":
        require(
            args.protocol_output is not None,
            "A new explicit protocol output is required",
        )
        result = prepare_protocol(output=args.protocol_output, **context)
    else:
        require(
            all(
                getattr(args, key) is not None
                for key in (
                    "protocol",
                    "protocol_sha256",
                    "publication",
                    "publication_sha256",
                    "output",
                    "inputs_root",
                )
            ),
            "Exact reviewed protocol/publication identities and new output are required",
        )
        result = run_supplement(
            protocol_path=args.protocol,
            protocol_sha256=args.protocol_sha256,
            publication=args.publication,
            publication_sha256=args.publication_sha256,
            output=args.output,
            inputs_root=args.inputs_root,
            **context,
        )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

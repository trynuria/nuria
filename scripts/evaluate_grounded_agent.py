"""Prospective, bounded analytical-agent comparison with delayed heldout credit.

The host alone opens reference results. This is finite SQLite execution agreement,
not official Spider accuracy, SQL equivalence or evidence of consciousness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import time
from pathlib import Path

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace, grade_result
from cognition.grounded_agent import GroundedAnalyst
from cognition.hybrid_neural import STRATEGIES, HybridNeuralBridge
from cognition.procedural_memory import ProceduralMemory
from cognition.reasoner_client import LocalReasoner, ReasonerError, digest

VERSION = "nuria.grounded-comparison.v1"
SCHEDULE = "primary-pair-then-secondary-blocks-v2"
GROUPS = (
    ("hybrid_fixed", "hybrid_neural"),
    ("stateless", "episodes", "procedures", "schema_first"),
)
ARMS = {
    "stateless": ("none", None, None),
    "episodes": ("episode", None, None),
    "procedures": ("procedure", None, None),
    "schema_first": ("none", "read_knowledge", None),
    "hybrid_fixed": ("procedure", None, "fixed"),
    "hybrid_neural": ("procedure", None, "neural"),
}
REQUIRED_SOURCE = (
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
LIMITS = {
    "max_turns": 12,
    "max_output_tokens": 6000,
    "max_tool_calls": 12,
    "max_tokens_per_turn": 1536,
    "memory_bytes": 6000,
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    with Path(path).open("x") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def append(path, value):
    with Path(path).open("a") as stream:
        os.chmod(path, 0o600)
        stream.write(json.dumps(value, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def current_commit(repository):
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()


def verify_freeze(protocol_path, freeze_path, expected_freeze, repository):
    """Run before reading any course, database registry or reference bodies."""
    if sha(freeze_path) != expected_freeze:
        raise ValueError("Source-freeze identity differs from the reviewed identity")
    freeze = read(freeze_path)
    if (
        freeze.get("schema") != VERSION
        or freeze.get("protocol_sha256") != sha(protocol_path)
        or freeze.get("parent_commit") != current_commit(repository)
    ):
        raise ValueError("Protocol or creator commit differs from the source freeze")
    files = freeze.get("files", {})
    if not set(REQUIRED_SOURCE) <= set(files):
        raise ValueError("Source freeze omits an evaluation dependency")
    for relative, expected in files.items():
        path = (repository / relative).resolve()
        if (
            not path.is_relative_to(repository.resolve())
            or not path.is_file()
            or sha(path) != expected
        ):
            raise ValueError("Frozen source differs: " + relative)
    return freeze


def protocol_files(protocol, directory):
    result = {}
    for name in ("registry", "teaching_course", "reserved_course", "oracle"):
        item = protocol["artifacts"][name]
        path = directory / item["path"]
        if sha(path) != item["sha256"]:
            raise ValueError("Frozen artifact differs: " + name)
        result[name] = path
    return result


class Allowance:
    """Reserve a whole job before inference; ambiguous usage remains charged."""

    def __init__(self, protocol, clock=time.monotonic):
        self.clock, self.started = clock, clock()
        self.known = protocol["prior_known_completion_tokens"]
        self.prior_unknown_ceiling = protocol["prior_unknown_completion_ceiling"]
        self.prior_unknown = protocol["prior_usage_unknown"]
        self.prior_prompt = protocol.get("prior_known_prompt_tokens")
        self.prior_prompt_unknown = protocol.get("prior_prompt_tokens_unknown", True)
        self.prior_wall = protocol["prior_wall_seconds"]
        self.cap, self.seconds = 200000, 10800
        self.pending, self.unknown, self.stopped = 0, False, None
        if (
            type(self.known) is not int
            or not 0 <= self.known <= self.cap
            or type(self.prior_unknown_ceiling) is not int
            or not 0 <= self.prior_unknown_ceiling <= self.cap - self.known
            or type(self.prior_unknown) is not bool
            or self.prior_unknown != (self.prior_unknown_ceiling > 0)
            or self.prior_prompt is not None
            and (type(self.prior_prompt) is not int or self.prior_prompt < 0)
            or type(self.prior_prompt_unknown) is not bool
            or not self.prior_prompt_unknown
            and self.prior_prompt is None
            or type(self.prior_wall) not in (int, float)
            or not math.isfinite(self.prior_wall)
            or not 0 <= self.prior_wall
        ):
            raise ValueError("Prior known usage and conservative liability disagree")

    def remaining_seconds(self):
        return self.seconds - self.prior_wall - (self.clock() - self.started)

    def block_fits(self, count):
        """Require worst-case headroom for a matched block before its first call."""
        if self.stopped:
            return False
        if self.remaining_seconds() <= 0:
            self.stopped = "global_wall_limit"
        elif self.known + self.prior_unknown_ceiling + 6000 * count > self.cap:
            self.stopped = "global_matched_block_reservation_limit"
        return self.stopped is None

    def reserve(self):
        if self.stopped:
            return False
        if self.remaining_seconds() <= 0:
            self.stopped = "global_wall_limit"
        elif self.known + self.prior_unknown_ceiling + 6000 > self.cap:
            self.stopped = "global_completion_reservation_limit"
        else:
            self.pending = 6000
            return True
        return False

    def finish(self, used, unknown):
        if type(used) is not int or not 0 <= used <= self.pending:
            raise ValueError("Observed completion accounting exceeds its reservation")
        self.known += used
        self.pending = self.pending - used if unknown else 0
        self.unknown = bool(unknown)
        if unknown:
            self.stopped = "unreconciled_model_usage"

    def state(self):
        return {
            "known_completion_tokens": self.known,
            "prior_unknown_completion_ceiling": self.prior_unknown_ceiling,
            "prior_usage_unknown": self.prior_unknown,
            "prior_known_prompt_tokens": self.prior_prompt,
            "prior_prompt_tokens_unknown": self.prior_prompt_unknown,
            "pending_completion_reservation": self.pending,
            "conservative_completion_charge": self.known
            + self.prior_unknown_ceiling
            + self.pending,
            "unknown_usage": self.unknown or self.prior_unknown,
            "current_unknown_usage": self.unknown,
            "stop_reason": self.stopped,
            "elapsed_global_seconds": self.seconds - self.remaining_seconds(),
        }


class MeteredReasoner:
    def __init__(self, reasoner, allowance):
        self.base, self.allowance = reasoner, allowance
        self.enable_thinking = reasoner.enable_thinking
        self.completion = self.prompt = 0
        self.unknown = False

    def complete(self, messages, **kwargs):
        remaining = self.allowance.remaining_seconds()
        if remaining < 1:
            raise ValueError("Global wall allowance exhausted before inference")
        if hasattr(self.base, "timeout"):
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
            # An interruption after disclosure may leave a request running. Its
            # reservation remains a liability until independently reconciled.
            self.unknown = True
            raise
        self.completion += result["usage"]["completion_tokens"]
        self.prompt += result["usage"]["prompt_tokens"]
        return result


def neural_view(decision):
    # Both neural arms see the same kinds of measured data. Mode, lab identity,
    # provenance hashes and mode-specific score presentation are host evidence.
    return {
        key: decision[key]
        for key in ("first_tool", "utilities", "action_spikes", "window_ms")
    }


def settle(memory, bridge, task, result, expected):
    def checker(request):
        if (
            request["proposal_sha256"] != result["proposal_sha256"]
            or request["final_sql"] != result["final_sql"]
        ):
            raise ValueError("Checker request differs from the committed proposal")
        accepted = bool(
            not request["abstained"]
            and grade_result(
                result["selected_receipt"],
                expected["expected_rows"],
                ordered=expected["ordered"],
                expected_columns=expected["column_count"],
            )
        )
        return {
            "accepted": accepted,
            "checker_id": "strict-finite-sqlite-v1",
            "evidence_sha256": digest(
                {
                    "task": task["id"],
                    "proposal": result["proposal_sha256"],
                    "oracle": digest(expected),
                    "receipt": result["selected_receipt"],
                    "accepted": accepted,
                }
            ),
        }

    terminal = memory.settle(result["episode_id"], verify_terminal=checker)
    if type(terminal.get("accepted")) is not bool:
        raise ValueError("Independent terminal settlement is unresolved")
    record = {"settlement": terminal, "neural_credit": None}
    if bridge is not None:
        try:
            record["neural_credit"] = bridge.settle(
                task["id"], terminal["accepted"], terminal["receipt_sha256"]
            )
        except Exception as error:
            record["neural_credit_error"] = type(error).__name__
    return record


def summarize(rows):
    settled = [row for row in rows if row.get("settlement") is not None]
    executed = [row for row in rows if row.get("agent") is not None]
    attempted = sum(row["attempted"] for row in rows)
    return {
        "selected": len(rows),
        "descriptively_graded": sum("descriptive_evaluation" in row for row in rows),
        "descriptively_accepted": sum(
            row.get("descriptive_evaluation", {}).get("accepted", False) for row in rows
        ),
        "executed": len(executed),
        "attempted": attempted,
        "not_executed": len(rows) - attempted,
        "unfinished_attempts": attempted - len(executed),
        "settled": len(settled),
        "unsettled_executed": len(executed) - len(settled),
        "accepted": sum(row["settlement"]["accepted"] for row in settled),
        "wrong_submissions": sum(
            row["agent"]["final_sql"] is not None and not row["settlement"]["accepted"]
            for row in settled
        ),
        "abstentions": sum(row["agent"]["final_sql"] is None for row in executed),
        "prompt_tokens": sum(row["observed_usage"]["prompt_tokens"] for row in rows),
        "completion_tokens": sum(
            row["observed_usage"]["completion_tokens"] for row in rows
        ),
        "unknown_usage_jobs": sum(
            row["observed_usage"]["unknown_additional_usage"] for row in rows
        ),
        "tool_calls": sum(row["agent"].get("tool_calls", 0) for row in executed),
        "job_wall_seconds": sum(
            row["agent"].get("wall_seconds", 0) for row in executed
        ),
        "status_counts": {
            status: sum(row["status"] == status for row in rows)
            for status in sorted({row["status"] for row in rows})
        },
    }


def evaluate(
    protocol_path,
    freeze_path,
    expected_freeze,
    output,
    *,
    repository=None,
    dependencies=None,
    clock=time.monotonic,
):
    repository = Path(repository or Path(__file__).resolve().parents[1])
    protocol_path, freeze_path, output = map(Path, (protocol_path, freeze_path, output))
    freeze = verify_freeze(protocol_path, freeze_path, expected_freeze, repository)
    protocol = read(protocol_path)
    if (
        protocol.get("schema") != VERSION
        or protocol.get("arms") != list(ARMS)
        or protocol.get("execution_schedule") != SCHEDULE
        or protocol.get("comparison_groups") != [list(group) for group in GROUPS]
        or protocol.get("seed") != 20261025
        or protocol.get("limits") != LIMITS
        or protocol.get("global_completion_cap") != 200000
        or protocol.get("global_wall_seconds") != 10800
    ):
        raise ValueError("Protocol differs from the six-arm prospective design")
    allowance = Allowance(protocol, clock)
    files = protocol_files(protocol, protocol_path.parent)
    courses = {"teaching": read(files["teaching_course"])["tasks"]}
    # The heldout body is opened only after both reviewed identities verified.
    courses["reserved"] = read(files["reserved_course"])["tasks"]
    if len(courses["teaching"]) != 8 or len(courses["reserved"]) != 12:
        raise ValueError("Prospective course denominators must be 8 and 12")
    ids = [task["id"] for course in courses.values() for task in course]
    if len(set(ids)) != 20 or protocol.get("selected_task_ids") != {
        phase: [task["id"] for task in course] for phase, course in courses.items()
    }:
        raise ValueError("Selected task identities differ or overlap")
    oracle = read(files["oracle"])["tasks"]
    for phase, course in courses.items():
        for task in course:
            entry = oracle[task["id"]]
            if (
                entry.get("gradeable") is not True
                or entry.get("db_id") != task["db_id"]
                or entry.get("split") != phase
            ):
                raise ValueError("Task and private checker metadata disagree")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write(
        output / "run-freeze.json",
        {
            "protocol": protocol,
            "source_freeze": freeze,
            "source_freeze_sha256": expected_freeze,
        },
    )
    dep = {
        "workspace": AnalystWorkspace,
        "memory": ProceduralMemory,
        "reasoner": LocalReasoner,
        "agent": GroundedAnalyst,
        "bridge": HybridNeuralBridge,
    } | (dependencies or {})
    workspace = dep["workspace"](files["registry"])
    source_id = read(files["registry"])["revision"]
    base = dep["reasoner"](
        protocol["endpoint"],
        protocol["model_alias"],
        enable_thinking=protocol["enable_thinking"],
        max_input_bytes=120000,
        max_response_bytes=524288,
    )
    rows = []

    def finish(memory, bridge, task, row):
        try:
            row.update(settle(memory, bridge, task, row["agent"], oracle[task["id"]]))
            if row.get("neural_credit_error"):
                allowance.stopped = "neural_credit_failure_requires_review"
        except Exception as error:
            row["settlement_error"] = type(error).__name__
            allowance.stopped = "terminal_checker_failure_requires_review"
        append(output / "outcomes.jsonl", row)

    states, reserved_pending = {}, {arm: [] for arm in ARMS}
    try:
        for arm, (memory_mode, first_tool, brain_mode) in ARMS.items():
            directory = output / arm
            directory.mkdir(mode=0o700)
            state = {
                "memory": None,
                "bridge": None,
                "memory_mode": memory_mode,
                "first_tool": first_tool,
                "directory": directory,
            }
            states[arm] = state
            try:
                state["memory"] = dep["memory"](
                    directory / "memory.sqlite",
                    source_id=source_id,
                    model_id=protocol["model_identity"],
                    tool_version=SCHEMA,
                )
                if brain_mode and not allowance.stopped:
                    state["bridge"] = dep["bridge"](
                        directory / "brain",
                        source_id=source_id,
                        model_id=protocol["model_identity"],
                        tool_version=SCHEMA,
                        mode=brain_mode,
                    )
            except Exception as error:
                allowance.stopped = "arm_initialization_failure_requires_review"
                append(
                    output / "events.jsonl",
                    {
                        "event": "arm_initialization_error",
                        "arm": arm,
                        "error_type": type(error).__name__,
                    },
                )
        for group_index, group in enumerate(GROUPS):
            block_number = 0
            for phase, course in courses.items():
                for task in course:
                    names = list(group)
                    offset = block_number % len(names)
                    order = names[offset:] + names[:offset]
                    block_number += 1
                    permitted = allowance.block_fits(len(group))
                    append(
                        output / "events.jsonl",
                        {
                            "event": "matched_block",
                            "phase": phase,
                            "task_id": task["id"],
                            "arm_order": order,
                            "permitted": permitted,
                            "required_completion_headroom": 6000 * len(group),
                            **allowance.state(),
                        },
                    )
                    committed = []
                    for arm in order:
                        state = states[arm]
                        memory, bridge = state["memory"], state["bridge"]
                        row = {
                            "arm": arm,
                            "comparison_group": group_index,
                            "phase": phase,
                            "task_id": task["id"],
                            "db_id": task["db_id"],
                            "agent": None,
                            "settlement": None,
                            "observed_usage": {
                                "completion_tokens": 0,
                                "prompt_tokens": 0,
                                "unknown_additional_usage": False,
                            },
                            "status": "not_executed",
                            "attempted": False,
                        }
                        rows.append(row)
                        if not permitted or not allowance.reserve():
                            row["reason"] = allowance.stopped
                            continue
                        append(
                            output / "allowance.jsonl",
                            {
                                "event": "job_reserved",
                                "arm": arm,
                                "comparison_group": group_index,
                                "phase": phase,
                                "task_id": task["id"],
                                **allowance.state(),
                            },
                        )
                        metered, decision = MeteredReasoner(base, allowance), None
                        row["attempted"] = True
                        try:
                            verify_freeze(
                                protocol_path, freeze_path, expected_freeze, repository
                            )
                            if bridge:
                                context = digest(
                                    {
                                        "job_id": task["id"],
                                        "model_identity": protocol["model_identity"],
                                        "registry_sha256": workspace.registry_sha256,
                                        "source": freeze["files"],
                                        "configuration": LIMITS,
                                        "seed": protocol["seed"],
                                    }
                                )
                                decision = bridge.choose(
                                    task["id"],
                                    context,
                                    task["question"],
                                    {
                                        tool: memory.outcome_stats(tool)
                                        for tool in STRATEGIES
                                    },
                                )
                            initial = (
                                decision["first_tool"]
                                if decision
                                else state["first_tool"]
                            )
                            agent = dep["agent"](
                                metered,
                                workspace,
                                memory,
                                **LIMITS,
                                memory_mode=state["memory_mode"],
                                initial_tool=initial,
                                initial_section="schema",
                                neural_context=neural_view(decision)
                                if decision
                                else None,
                            )
                            row["agent"] = agent.run(
                                job_id=task["id"],
                                question=task["question"],
                                db_id=task["db_id"],
                                seed=protocol["seed"],
                            )
                            verify_freeze(
                                protocol_path, freeze_path, expected_freeze, repository
                            )
                            row["status"] = row["agent"]["status"]
                        except Exception as error:
                            row["status"], row["error_type"] = (
                                "host_error",
                                type(error).__name__,
                            )
                            allowance.stopped = "host_error_requires_review"
                        finally:
                            allowance.finish(metered.completion, metered.unknown)
                            row["observed_usage"] = {
                                "completion_tokens": metered.completion,
                                "prompt_tokens": metered.prompt,
                                "unknown_additional_usage": metered.unknown,
                            }
                            row["neural_decision"] = decision
                            append(output / "proposals.jsonl", row)
                            append(
                                output / "allowance.jsonl",
                                {"event": "job_closed", **allowance.state()},
                            )
                        if row["agent"] is not None:
                            committed.append(row)
                            if phase == "reserved":
                                reserved_pending[arm].append((task, row))
                    # Release a teaching item's feedback only after the complete
                    # matched block, never between arms evaluating that same item.
                    if phase == "teaching" and len(committed) == len(group):
                        for row in committed:
                            state = states[row["arm"]]
                            finish(state["memory"], state["bridge"], task, row)
                if phase == "reserved":
                    # No reserved terminal label is used while any inference for
                    # this group can still run. Other groups own isolated memories.
                    for arm in group:
                        pending = reserved_pending[arm]
                        if len(pending) == 12:
                            append(
                                output / "events.jsonl",
                                {
                                    "event": "heldout_batch_committed",
                                    "arm": arm,
                                    "comparison_group": group_index,
                                    "count": 12,
                                },
                            )
                            for task, row in pending:
                                finish(
                                    states[arm]["memory"],
                                    states[arm]["bridge"],
                                    task,
                                    row,
                                )
        # A resource stop may leave a fully committed paired subset. Grade it
        # descriptively after inference has ended, without settling memory or
        # applying neural credit. All unexecuted selected jobs stay in the record.
        for task in courses["reserved"]:
            for group_index, group in enumerate(GROUPS):
                matched = [
                    row
                    for row in rows
                    if row["phase"] == "reserved"
                    and row["task_id"] == task["id"]
                    and row["arm"] in group
                ]
                if len(matched) == len(group) and all(
                    row["agent"] is not None for row in matched
                ):
                    expected = oracle[task["id"]]
                    for row in matched:
                        if row["settlement"] is None:
                            result = row["agent"]
                            row["descriptive_evaluation"] = {
                                "accepted": bool(
                                    result["final_sql"] is not None
                                    and grade_result(
                                        result["selected_receipt"],
                                        expected["expected_rows"],
                                        ordered=expected["ordered"],
                                        expected_columns=expected["column_count"],
                                    )
                                ),
                                "scope": "Post-stop matched subset only; no memory settlement or neural credit",
                                "oracle_sha256": digest(expected),
                            }
        for state in states.values():
            if state["memory"]:
                write(
                    state["directory"] / "memory-export.json", state["memory"].export()
                )
    finally:
        for state in states.values():
            if state["bridge"]:
                state["bridge"].close()
            if state["memory"]:
                state["memory"].close()
    source_verified = False
    try:
        verify_freeze(protocol_path, freeze_path, expected_freeze, repository)
        source_verified = True
    except ValueError:
        allowance.stopped = "final_source_freeze_changed"
        append(output / "events.jsonl", {"event": "final_source_freeze_changed"})
    terminal_coverage = all(row["settlement"] is not None for row in rows)
    primary_coverage = all(
        row["settlement"] is not None for row in rows if row["arm"] in GROUPS[0]
    )
    resource_stops = {
        None,
        "global_matched_block_reservation_limit",
        "global_completion_reservation_limit",
        "global_wall_limit",
    }
    summary = {
        "schema": VERSION,
        "allowance": allowance.state(),
        "arms": {
            arm: {
                phase: summarize(
                    [row for row in rows if row["arm"] == arm and row["phase"] == phase]
                )
                for phase in courses
            }
            for arm in ARMS
        },
        "all_terminal_results_settled": terminal_coverage,
        "primary_arms": list(GROUPS[0]),
        "primary_terminal_results_settled": primary_coverage,
        "primary_complete": primary_coverage
        and source_verified
        and allowance.stopped in resource_stops,
        "source_freeze_verified_at_end": source_verified,
        "complete": terminal_coverage and source_verified and not allowance.stopped,
        "scope": "Finite exact SQLite agreement on a small public-data cohort; prompt tokens vary and are measured; public benchmark pretraining contamination is possible",
    }
    write(output / "selected-results.json", rows)
    write(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("protocol", "source-freeze", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--source-freeze-sha256", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate(
                args.protocol,
                args.source_freeze,
                args.source_freeze_sha256,
                args.output,
            )
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

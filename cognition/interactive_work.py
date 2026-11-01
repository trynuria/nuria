"""Adaptive program execution using the existing isolated work boundary.

The agent chooses whether to inspect, probe, revise or submit after each actual
execution. Terminal acceptance stays outside its conversation. Original fixed
work protocols remain unchanged for comparison and historical reproduction.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from cognition.experience_navigation import ExperienceIndex
from cognition.outcome_worker import BASE_POLICY, _proposal, identity
from cognition.outcome_worker import VERSION as OUTCOME_VERSION
from cognition.reasoner_client import ReasonerError

VERSION = "nuria.interactive-work.v1"
MAX_CALLS = 5
MAX_COMPLETION_TOKENS = 7200
MAX_CALL_TOKENS = 2400
POLICY = (
    BASE_POLICY
    + """
Work by executing useful probes, reading their actual output and deciding what
to do next. Inspect enrolled files and relevant library behavior when an
assumption is uncertain. You can print observations in checks. Every execution
has a fresh directory containing the same read-only inputs; carry any program
you need forward in the next complete code field. No other job files persist.
Start with a useful diagnostic and read its actual output before finalizing a
solution. Test a concrete library or data assumption, not merely a trivial case.

Return only JSON with exactly code, checks, lesson, done. code is the complete
current implementation including imports. checks is executable Python after
that code: use it to inspect inputs or exercise behavior justified by the task.
lesson is a short proposed reusable lesson. done is a JSON boolean: false asks
for another tool result, true submits your current implementation. A submission
does not establish acceptance. The hidden host checker is never available.

Use the finite steps deliberately. Do not make up missing data, relax a
requirement, inspect a grader, override test machinery or claim a probe proves
correctness. Tool feedback may reveal a bad assumption in your probe itself.
"""
)


def _step(text):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    value = json.loads(text)
    if (
        type(value) is not dict
        or set(value) != {"code", "checks", "lesson", "done"}
        or type(value["done"]) is not bool
    ):
        raise ValueError("Expected a complete program, probe, lesson and boolean done")
    return _proposal(
        json.dumps({k: value[k] for k in ("code", "checks", "lesson")})
    ), value["done"]


def run_interactive_work(
    reasoner, executor, store, task, *, job_id, experience=None, grade=True
):
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id)
        or not re.fullmatch(r"[0-9a-f]{64}", task["source_sha256"])
        or type(grade) is not bool
        or any(
            type(task[name]) is not str or len(task[name].encode()) > 65536
            for name in ("prompt", "checker")
        )
    ):
        raise ValueError("A finite source-bound job is required")
    automatic = experience is None
    contract = {
        "version": VERSION,
        "implementation_sha256": identity(Path(__file__).read_text()),
        "task": task,
        "model": reasoner.configuration,
        "experience": "persistent-prior-outcomes"
        if automatic
        else identity(experience),
        "maximum_calls": MAX_CALLS,
        "maximum_completion_tokens": MAX_COMPLETION_TOKENS,
        "maximum_call_tokens": MAX_CALL_TOKENS,
        "grade": grade,
    }
    store.verify()
    replay = store.begin(job_id, contract)
    if replay is not None:
        return {**replay, "replayed": True}
    if automatic:
        experience = [
            row for row in store.experiences() if type(row.get("accepted")) is bool
        ]
    memory = ExperienceIndex(experience)
    try:
        selected = memory.search([task["prompt"]], limit=12)[:3]
        evidence = memory.view(selected)
    finally:
        memory.close()
    messages = [
        {"role": "system", "content": POLICY},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": task["prompt"],
                    "experience": evidence,
                    "runtime": "Python 3.14; offline isolated workspace; enrolled libraries only",
                }
            ),
        },
    ]
    attempts, proposal = [], None
    stop_reason = "completion_plan_exhausted"
    completion_used = 0
    issued_limits = []
    for index in range(MAX_CALLS):
        tokens = min(MAX_CALL_TOKENS, MAX_COMPLETION_TOKENS - completion_used)
        if tokens < 256:
            break
        issued_limits.append(tokens)
        try:
            response = reasoner.complete(messages, max_tokens=tokens, seed=0)
        except ReasonerError as error:
            if error.usage is None:
                raise  # Pending or ambiguous liability is never retried.
            attempts.append(
                {
                    "stage": "interactive",
                    "usage": error.usage,
                    "model_error": error.code,
                    "diagnostic": error.diagnostic,
                }
            )
            completion_used += error.usage["completion_tokens"]
            stop_reason = error.code
            if error.code not in ("model_output_incomplete", "model_protocol_error"):
                raise
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "model_output_error": error.code,
                            "remaining_calls": MAX_CALLS - index - 1,
                            "remaining_completion_allowance": MAX_COMPLETION_TOKENS
                            - completion_used,
                            "next_step": "The last response did not provide a complete executable JSON proposal. Return a concise complete code/checks/lesson/done object.",
                        }
                    ),
                }
            )
            continue
        completion_used += response["usage"]["completion_tokens"]
        attempt = {
            "stage": "interactive",
            "usage": response["usage"],
            "response_sha256": identity(response["content"]),
            "content": response["content"],
            "completion_limit": tokens,
        }
        try:
            current, done = _step(response["content"])
            checked = executor.run(
                current["code"],
                current["checks"],
                label=f"{job_id}-probe-{index}",
                inputs=task.get("inputs"),
            )
            proposal = current
            attempt.update(probe=checked, submitted=done)
            feedback = {"execution": checked}
        except (ValueError, SyntaxError, json.JSONDecodeError) as error:
            done = False
            feedback = {"format_error": type(error).__name__}
            attempt["format_error"] = type(error).__name__
        attempts.append(attempt)
        if done:
            stop_reason = "submitted"
            break
        messages.extend(
            [
                {"role": "assistant", "content": response["content"]},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            **feedback,
                            "remaining_calls": MAX_CALLS - index - 1,
                            "remaining_completion_allowance": MAX_COMPLETION_TOKENS
                            - completion_used,
                            "next_step": "Inspect the observations, revise the complete program if needed, or submit. The terminal host check is still withheld.",
                        }
                    ),
                },
            ]
        )
    checker = None
    if grade:
        checker = (
            executor.run(
                proposal["code"],
                task["checker"],
                label=job_id + "-check",
                inputs=task.get("inputs"),
            )
            if proposal
            else {"passed": False, "executed": False, "reason": "no_valid_proposal"}
        )
    result = {
        "version": OUTCOME_VERSION,
        "execution_route": VERSION,
        "implementation_sha256": contract["implementation_sha256"],
        "job_id": job_id,
        "task_id": task["task_id"],
        "source_sha256": task["source_sha256"],
        "family": task.get("family"),
        "prompt": task["prompt"],
        "policy_sha256": identity(POLICY),
        "experience_sha256": identity(experience),
        "experience_ids": selected,
        "attempts": attempts,
        "code": proposal["code"] if proposal else "",
        "lesson": proposal["lesson"] if proposal else "",
        "proposed_code_sha256": identity(proposal["code"] if proposal else ""),
        "proposal_error": None if proposal else "no_valid_proposal",
        "stop_reason": stop_reason,
        "issued_completion_limits": issued_limits,
        "maximum_completion_tokens": MAX_COMPLETION_TOKENS,
        "completion_tokens_used": completion_used,
        "checker": checker,
        # An inspection stub is not a failed attempted solution. Only final
        # checked jobs can support a later cross-job correction witness.
        "checked_correction": None,
        "accepted": bool(checker["passed"]) if checker else None,
        "replayed": False,
    }
    store.finish(job_id, result)
    return result

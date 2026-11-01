"""Acquire checked program corrections from actual execution outcomes.

A correction is an observed before/after witness under one unchanged checker.
It is not a claim that the edit generalizes. Fresh work must establish transfer.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections import defaultdict

from cognition.outcome_worker import identity

VERSION = "nuria.checked-corrections.v1"


def expanded_experience(records):
    """Expose a checked failed initial attempt without inventing a new outcome."""
    result = list(records)
    for record in records:
        witness = record.get("checked_correction") or {}
        if witness.get("corrected_failure") is not True:
            continue
        initial = record["attempts"][0].get("content", "")
        if initial.strip().startswith("```json"):
            initial = initial.strip()[7:-3].strip()
        initial_code = json.loads(initial)["code"]
        checked = witness["initial_check"]
        if (
            record["accepted"] is not True
            or witness["source_sha256"] != record["source_sha256"]
            or checked["passed"] is not False
            or checked["code_sha256"]
            != hashlib.sha256(initial_code.encode()).hexdigest()
            or witness["initial_code_sha256"] != checked["code_sha256"]
            or witness["final_code_sha256"]
            != hashlib.sha256(record["code"].encode()).hexdigest()
            or checked["checks_sha256"] != record["checker"]["checks_sha256"]
        ):
            raise ValueError("Observed correction differs from its checked programs")
        result.append(
            {
                **record,
                "job_id": "initial-"
                + identity({"parent": record["job_id"], "code": initial_code})[:24],
                "parent_job_id": record["job_id"],
                "code": initial_code,
                "proposed_code_sha256": identity(initial_code),
                "accepted": False,
                "checker": checked,
                "checked_correction": None,
                "lesson": "Rejected initial program; only the recorded source outcome is established",
            }
        )
    return result


def _candidate(before, after):
    return {
        "task_id": before["task_id"],
        "source_sha256": before["source_sha256"],
        "family": before.get("family"),
        "before_job_id": before["job_id"],
        "after_job_id": after["job_id"],
        "before_record_sha256": identity(before),
        "after_record_sha256": identity(after),
        "before_code": before["code"],
        "after_code": after["code"],
        "diff": "".join(
            difflib.unified_diff(
                before["code"].splitlines(True),
                after["code"].splitlines(True),
                fromfile="rejected.py",
                tofile="accepted.py",
            )
        ),
        "generalization": "unverified",
    }


def correction_candidates(records):
    groups = defaultdict(list)
    for record in expanded_experience(records):
        if type(record.get("accepted")) is bool and record.get("code"):
            groups[(record["task_id"], record["source_sha256"])].append(record)
    result = []
    for (task_id, source), variants in sorted(groups.items()):
        failed = [x for x in variants if not x["accepted"]]
        passed = [x for x in variants if x["accepted"]]
        pairs = [(a, b) for a in failed for b in passed if a["code"] != b["code"]]
        if not pairs:
            continue
        # The closest recorded pair reduces unrelated changes. The rule is
        # fixed before replay; no winning seed or new-task result is consulted.
        before, after = min(
            pairs,
            key=lambda pair: (
                -difflib.SequenceMatcher(
                    None, pair[0]["code"], pair[1]["code"], autojunk=False
                ).ratio(),
                pair[0]["job_id"],
                pair[1]["job_id"],
            ),
        )
        result.append(_candidate(before, after))
    return result


def acquire_corrections(records, tasks, executor):
    """Replay every selected pair, retaining unsuccessful candidates as well."""
    tasks = {task["task_id"]: task for task in tasks}
    rows = []
    for index, candidate in enumerate(correction_candidates(records)):
        task = tasks[candidate["task_id"]]
        if task["source_sha256"] != candidate["source_sha256"]:
            raise ValueError("Correction examples belong to another source")
        before = executor.run(
            candidate["before_code"],
            task["checker"],
            label=f"correction-{index:03}-before",
            inputs=task.get("inputs"),
        )
        after = executor.run(
            candidate["after_code"],
            task["checker"],
            label=f"correction-{index:03}-after",
            inputs=task.get("inputs"),
        )
        rows.append(
            {
                **candidate,
                "checks_sha256": hashlib.sha256(task["checker"].encode()).hexdigest(),
                "before_check": before,
                "after_check": after,
                "verified": before["passed"] is False and after["passed"] is True,
            }
        )
    body = {
        "version": VERSION,
        "history_sha256": identity(records),
        "selection": "Closest differing rejected/accepted pair per source; all selected pairs retained",
        "corrections": rows,
    }
    return {**body, "artifact_sha256": identity(body)}


def validate_corrections(catalog, records):
    if catalog.get("version") != VERSION or catalog.get("artifact_sha256") != identity(
        {k: v for k, v in catalog.items() if k != "artifact_sha256"}
    ):
        raise ValueError("Correction catalog identity differs")
    by_id = {row["job_id"]: row for row in expanded_experience(records)}
    admitted = {}
    for row in catalog["corrections"]:
        before, after = by_id[row["before_job_id"]], by_id[row["after_job_id"]]
        if (
            identity(before) != row["before_record_sha256"]
            or identity(after) != row["after_record_sha256"]
        ):
            raise ValueError("A correction's source experience changed")
        if before["accepted"] is not False or after["accepted"] is not True:
            raise ValueError("Correction provenance lacks both observed outcomes")
        if (
            row["diff"] != _candidate(before, after)["diff"]
            or row["generalization"] != "unverified"
        ):
            raise ValueError(
                "Correction edit or scope differs from its source programs"
            )
        for name, record in [("before", before), ("after", after)]:
            if (
                record["task_id"] != row["task_id"]
                or record["source_sha256"] != row["source_sha256"]
                or record["code"] != row[name + "_code"]
            ):
                raise ValueError("Correction source or program differs")
            check = row[name + "_check"]
            if (
                check["code_sha256"]
                != hashlib.sha256(record["code"].encode()).hexdigest()
                or check["checks_sha256"] != row["checks_sha256"]
            ):
                raise ValueError("Correction replay executed a different contract")
        expected = (
            row["before_check"]["passed"] is False
            and row["after_check"]["passed"] is True
        )
        if row["verified"] is not expected:
            raise ValueError("Correction admission differs from actual replay")
        if expected:
            admitted[(row["task_id"], row["source_sha256"])] = row
    return admitted


def observed_catalog(records):
    """Build future memory from before/after checks already in job receipts."""
    expanded = expanded_experience(records)
    by_id = {row["job_id"]: row for row in expanded}
    corrections = []
    for before in expanded:
        if not before.get("parent_job_id"):
            continue
        after = by_id[before["parent_job_id"]]
        candidate = _candidate(before, after)
        corrections.append(
            {
                **candidate,
                "checks_sha256": after["checker"]["checks_sha256"],
                "before_check": before["checker"],
                "after_check": after["checker"],
                "verified": True,
            }
        )
    body = {
        "version": VERSION,
        "history_sha256": identity(records),
        "selection": "Checked within-job correction receipts only; no new model call or inferred acceptance",
        "corrections": corrections,
    }
    return {**body, "artifact_sha256": identity(body)}


def observe_own_correction(
    task, initial_code, final_code, final_check, executor, *, label
):
    """After final acceptance, establish whether revision corrected a failure."""
    if (
        not initial_code
        or initial_code == final_code
        or final_check.get("passed") is not True
    ):
        return None
    if (
        final_check.get("code_sha256")
        != hashlib.sha256(final_code.encode()).hexdigest()
        or final_check.get("checks_sha256")
        != hashlib.sha256(task["checker"].encode()).hexdigest()
    ):
        raise ValueError("Final acceptance is not bound to this program and checker")
    before = executor.run(
        initial_code, task["checker"], label=label, inputs=task.get("inputs")
    )
    return {
        "source_sha256": task["source_sha256"],
        "initial_code_sha256": hashlib.sha256(initial_code.encode()).hexdigest(),
        "final_code_sha256": hashlib.sha256(final_code.encode()).hexdigest(),
        "initial_check": before,
        "corrected_failure": before["passed"] is False,
        "scope": "Same enrolled source only; transfer not established",
    }

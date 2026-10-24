"""Create one private four-audit course from an authored engineering corpus.

All corpus entries stay in their supplied order. Four diagnostic rows and all
twenty-four final rows form each contract. The remaining two original diagnostic
rows are not promoted to final tests. Recipes, program identities, family names,
source identities and negative-control markers remain outside contractor briefs.

This is generated engineering work, not customer traffic, external observations,
token activity or paid purchases. A separately selected final source entry may
become an explicit unsupported SHA256 control; its labels use hashlib directly.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import secrets
from pathlib import Path

from cognition.text_skills import normalize_config
from commerce.composed_skill_runtime import (
    COMPOSED_POLICY,
    COMPOSED_PROVIDER,
    COMPOSED_SOURCE,
)
from commerce.skill_queue import MAX_BYTES, MAX_TASKS, VERSION, private_destination
from commerce.tasks import HASH, active_verifier_ceiling, digest, validate_task
from scripts.build_guarded_skill_tasks import (
    CONFIG,
    DIAGNOSTIC_ROWS,
    SCHEMA,
    SCORING_ROWS,
    program,
    target,
)

COURSE_SCHEMA = "nuria.composed-skill-course.v1"
AUDIT_ROWS = 4


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _entry(entry, seed, ordinal, config):
    require(
        type(entry) is dict and type(entry.get("private_recipe")) is dict,
        "Engineering entry lacks its private generator provenance",
    )
    recipe = entry["private_recipe"]
    try:
        original_program = program(recipe)
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ValueError("Engineering recipe provenance is invalid") from error
    require(
        entry.get("unsupported") is None
        and recipe.get("program") == original_program
        and recipe.get("program_sha256") == digest(original_program)
        and entry.get("family") == recipe.get("family"),
        "Engineering program provenance changed",
    )
    rows, outputs = [], []
    for partition, expected in (
        ("diagnostic", DIAGNOSTIC_ROWS),
        ("score", SCORING_ROWS),
    ):
        inputs = entry.get(partition + "_inputs")
        targets = entry.get(partition + "_outputs")
        require(
            type(inputs) is list
            and type(targets) is list
            and len(inputs) == len(targets) == expected,
            "Engineering partitions must retain six diagnostic and twenty-four scoring rows",
        )
        for value, expected_output in zip(inputs, targets, strict=True):
            require(
                all(
                    type(item) is str
                    and len(item.encode()) <= config["max_input_bytes"]
                    for item in (value, expected_output)
                ),
                "Engineering row exceeds its text bounds",
            )
            try:
                fields = json.loads(value)
            except (ValueError, RecursionError) as error:
                raise ValueError("Engineering input is not a JSON record") from error
            require(
                type(fields) is list
                and len(fields) == 6
                and all(type(field) is str for field in fields),
                "Engineering input needs six text fields",
            )
            try:
                independently_derived = target(recipe, fields)
            except (KeyError, TypeError, ValueError, IndexError) as error:
                raise ValueError(
                    "Engineering target cannot be independently derived"
                ) from error
            require(
                independently_derived == expected_output,
                "Engineering target differs from the generator oracle",
            )
        rows.extend(inputs)
        outputs.extend(targets)
    require(
        len(set(rows)) == len(rows),
        "Diagnostic and final inputs must be distinct and disjoint",
    )
    expected_ids = [
        digest({"seed": seed, "task": ordinal, "row": index})
        for index in range(len(rows))
    ]
    require(
        entry.get("row_ids")
        == {
            "diagnostic": expected_ids[:DIAGNOSTIC_ROWS],
            "score": expected_ids[DIAGNOSTIC_ROWS:],
        }
        and entry.get("id")
        == digest(
            {
                "schema": SCHEMA,
                "seed": seed,
                "ordinal": ordinal,
                "row_ids": expected_ids,
                "inputs_sha256": digest(rows),
            }
        ),
        "Engineering source row identities changed",
    )
    return rows, outputs


def proof_manifest(queue):
    """Opaque contract commitments; this is a manifest, not a result certificate."""
    require(
        type(queue) is dict
        and set(queue) == {"schema", "source", "tasks"}
        and queue["schema"] == VERSION
        and type(queue["source"]) is dict,
        "Composed queue manifest is malformed",
    )
    tasks = queue["tasks"]
    require(
        type(tasks) is list and 1 <= len(tasks) <= MAX_TASKS,
        "Composed queue must be finite",
    )
    identities = []
    require(
        queue["source"].get("provider") == COMPOSED_PROVIDER
        and queue["source"].get("source_id") == COMPOSED_SOURCE
        and queue["source"].get("policy") == COMPOSED_POLICY
        and queue["source"].get("customer_tasks") == 0
        and queue["source"].get("external_observation_tasks") == 0
        and queue["source"].get("cost_authority", {}).get("financial_micro_usd") == 0,
        "Composed manifest source or nonfinancial scope changed",
    )
    for contract in tasks:
        validate_task(contract)
        require(
            contract["provider"] == COMPOSED_PROVIDER
            and contract["kind"] == "compute"
            and contract["maximum_micro_usd"] == 0
            and contract["acceptance"].get("policy") == COMPOSED_POLICY
            and contract["acceptance"].get("source_id") == COMPOSED_SOURCE
            and contract["acceptance"]["config"] == CONFIG,
            "Composed course exceeds its explicit source or authority",
        )
        identities.append(digest(contract))
    require(
        len(set(identities)) == len(identities), "Composed course repeats a contract"
    )
    return {
        "schema": COURSE_SCHEMA,
        "provider": COMPOSED_PROVIDER,
        "source_id": COMPOSED_SOURCE,
        "config_sha256": digest(CONFIG),
        "tasks": [{"contract_sha256": identity} for identity in identities],
        "expected_count": len(tasks),
    }


def build(
    corpus,
    provider=COMPOSED_PROVIDER,
    source_id=COMPOSED_SOURCE,
    *,
    lanes=4,
    maximum_work=1050000,
    synthesis_work=50000,
    include_hash_control=False,
    control_position=None,
):
    """Return a queue and separate commitment manifest, without executing jobs.

    There is no task-subset, family selection or outcome-based filtering option.
    If a control is requested it must use the last, separate source entry. Thus
    twenty-five generated entries yield twenty-four supported jobs and one
    separately disclosed negative control, all inside the complete denominator.
    """
    require(
        provider == COMPOSED_PROVIDER and source_id == COMPOSED_SOURCE,
        "Composed course needs its dedicated provider and source",
    )
    require(
        type(lanes) is int and lanes == 4,
        "Composed course uses exactly four goal lanes",
    )
    require(
        type(corpus) is dict
        and corpus.get("schema") == SCHEMA
        and type(corpus.get("source")) is dict,
        "Supply the versioned authored engineering corpus",
    )
    config = normalize_config(corpus.get("config"))
    require(
        config == CONFIG, "Course configuration must match the reviewed text corpus"
    )
    entries, provenance = corpus.get("tasks"), corpus["source"]
    seed = provenance.get("seed")
    require(
        type(seed) is int
        and 0 <= seed <= 2**256 - 1
        and type(entries) is list
        and 1 <= len(entries) <= min(MAX_TASKS, 128)
        and type(provenance.get("task_count")) is int
        and provenance["task_count"] == len(entries)
        and provenance.get("diagnostic_rows_per_task") == DIAGNOSTIC_ROWS
        and provenance.get("score_rows_per_task") == SCORING_ROWS,
        "Engineering source seed, count or partition provenance is invalid",
    )
    require(type(include_hash_control) is bool, "Negative control must be explicit")
    require(
        (
            include_hash_control
            and len(entries) >= 2
            and type(control_position) is int
            and control_position == len(entries) - 1
        )
        or (not include_hash_control and control_position is None),
        "Negative control needs the last separate source entry",
    )
    require(
        type(synthesis_work) is int and 1 <= synthesis_work <= 1000000,
        "Synthesis authority must have a finite ceiling",
    )
    verifier_ceiling = active_verifier_ceiling(config)
    probe_ceiling = AUDIT_ROWS * (1 + (2 * config["max_input_bytes"] + 31) // 32)
    require(
        type(maximum_work) is int
        and verifier_ceiling + probe_ceiling + synthesis_work
        < maximum_work
        <= 10000000,
        "Job allowance must reserve the text verifier, four labels and synthesis",
    )
    tasks, commitments, seen_files, nonces = [], [], set(), set()
    for ordinal, entry in enumerate(entries):
        rows, outputs = _entry(entry, seed, ordinal, config)
        original_identity = digest(list(zip(rows, outputs, strict=True)))
        set_identity = digest(sorted(zip(rows, outputs, strict=True)))
        require(
            set_identity not in seen_files, "One source file cannot be queued twice"
        )
        seen_files.add(set_identity)
        control = include_hash_control and ordinal == control_position
        if control:
            outputs = [
                hashlib.sha256(value.encode("utf-8")).hexdigest() for value in rows
            ]
        diagnostic = list(zip(rows[:AUDIT_ROWS], outputs[:AUDIT_ROWS], strict=True))
        heldout = list(
            zip(rows[DIAGNOSTIC_ROWS:], outputs[DIAGNOSTIC_ROWS:], strict=True)
        )
        nonce = secrets.token_hex(32)
        require(
            type(nonce) is str and HASH.fullmatch(nonce) and nonce not in nonces,
            "Evaluation nonce is invalid or repeated",
        )
        nonces.add(nonce)
        contract = {
            "id": f"composed-record-{ordinal % lanes}",
            "title": f"Record repair batch {ordinal + 1:03d}",
            "purpose": "Produce a bounded text transform for a declared authored engineering batch and check it on separate records.",
            "brief": "Request four diagnostic answers, then return one declarative text program or abstain. Final evaluation remains with the buyer. This is generated engineering work using included capacity; no wallet funds or external purchase are involved.",
            "kind": "compute",
            "provider": provider,
            "action": "experiment",
            "maximum_micro_usd": 0,
            "delivery_seconds": 3600,
            "evaluation_nonce": nonce,
            "acceptance": {
                "type": "transform_active",
                "policy": COMPOSED_POLICY,
                "source_id": source_id,
                "config": copy.deepcopy(config),
                "diagnostic_inputs": [value for value, _ in diagnostic],
                "diagnostic_outputs": [value for _, value in diagnostic],
                "heldout_inputs": [value for value, _ in heldout],
                "heldout_outputs": [value for _, value in heldout],
                "maximum_work": maximum_work,
                "maximum_probes": AUDIT_ROWS,
                "synthesis_work": synthesis_work,
                "minimum_evidence": AUDIT_ROWS,
            },
        }
        validate_task(contract)
        tasks.append(contract)
        commitments.append(
            {
                "source_position": ordinal,
                "entry_sha256": digest(entry),
                "original_io_sha256": original_identity,
                "course_io_sha256": digest(diagnostic + heldout),
                "diagnostic_rows": AUDIT_ROWS,
                "scoring_rows": SCORING_ROWS,
                "unused_original_diagnostic_rows": DIAGNOSTIC_ROWS - AUDIT_ROWS,
                "kind": "unsupported-sha256-negative-control"
                if control
                else "authored-engineering-workload",
                "customer_task": False,
                "supported_acquisition": not control,
                **(
                    {
                        "label_derivation": "hashlib.sha256(exact_input_utf8).hexdigest()",
                        "unsupported_operation": "SHA256 is absent from safe-text-v1",
                        "expected_outcome": "search failure or abstention; buyer rejection",
                    }
                    if control
                    else {}
                ),
            }
        )
    queue = {
        "schema": VERSION,
        "source": {
            "corpus_sha256": digest(corpus),
            "provenance": copy.deepcopy(provenance),
            "source_id": source_id,
            "provider": provider,
            "policy": COMPOSED_POLICY,
            "course": "finite-composed-engineering-v1",
            "supported_engineering_tasks": len(tasks) - int(include_hash_control),
            "unsupported_negative_controls": int(include_hash_control),
            "customer_tasks": 0,
            "external_observation_tasks": 0,
            "files": commitments,
            "cost_authority": {
                "financial_micro_usd": 0,
                "maximum_work_per_job": maximum_work,
                "maximum_course_work": maximum_work * len(tasks),
                "maximum_probes_per_job": AUDIT_ROWS,
                "maximum_synthesis_work_per_job": synthesis_work,
                "reserved_final_verifier_work_per_job": verifier_ceiling,
            },
            "scope": "Finite authored synthetic engineering stream, not customer work, independently authored external observations, token activity or paid purchases. All supplied entries remain ordered in the complete denominator. Only the first four original diagnostic rows enter each task; all twenty-four original scoring rows remain separate. Recipes, family names, program identities, source row identities and negative-control markers remain buyer-only. A separate optional SHA256 control derives its targets from exact UTF-8 inputs with hashlib. The commitment manifest fixes requested work; it does not establish successful delivery or learning.",
        },
        "tasks": tasks,
    }
    return queue, proof_manifest(queue)


def _payload(manifest):
    payload = (
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    require(
        len(payload) <= MAX_BYTES, "Composed manifest exceeds the finite file bound"
    )
    return payload


def write_manifests(queue_path, course_path, queue, course):
    """Create two private new files; preserve every existing or partial file."""
    require(
        course == proof_manifest(queue), "Course commitments do not match the queue"
    )
    destinations = [private_destination(queue_path), private_destination(course_path)]
    require(destinations[0] != destinations[1], "Queue and proof need separate files")
    for destination in destinations:
        if destination.exists():
            raise FileExistsError("Manifest output already exists: " + destination.name)
    payloads = [_payload(queue), _payload(course)]
    hashes = []
    for destination, payload in zip(destinations, payloads, strict=True):
        with destination.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        hashes.append(hashlib.sha256(payload).hexdigest())
    return {
        "queue_sha256": hashes[0],
        "course_sha256": hashes[1],
        "tasks": course["expected_count"],
        "financial_micro_usd": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("queue", type=Path)
    parser.add_argument("course", type=Path)
    parser.add_argument("--maximum-work", type=int, default=1050000)
    parser.add_argument("--synthesis-work", type=int, default=50000)
    parser.add_argument("--hash-control", action="store_true")
    parser.add_argument("--control-position", type=int)
    args = parser.parse_args()
    with args.corpus.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "Engineering corpus exceeds its input bound")
    queue, course = build(
        json.loads(raw),
        maximum_work=args.maximum_work,
        synthesis_work=args.synthesis_work,
        include_hash_control=args.hash_control,
        control_position=args.control_position,
    )
    print(json.dumps(write_manifests(args.queue, args.course, queue, course)))


if __name__ == "__main__":
    main()

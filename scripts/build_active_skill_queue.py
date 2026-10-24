"""Build a private finite active-routing course from a pinned numeric corpus.

The manifest contains buyer-held labels and final checks. Its contractor briefs
supply only diagnostic inputs and bounded authority, with no selected program or
format identity. Source provenance describes the supplied development corpus;
this builder neither creates token activity nor independently verifies its data.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import secrets
from pathlib import Path

from cognition.skills import SOURCE, normalize_config
from commerce.skill_queue import MAX_BYTES, MAX_TASKS, VERSION, private_destination
from commerce.tasks import HASH, IDENT, active_verifier_ceiling, digest, validate_task

PREVIOUS_PROVIDER = "local-record-skills-v2"
PREVIOUS_SOURCE = "nuria:public-record-repair-v2"
DIAGNOSTIC_ROWS = 4


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _file(entry, config):
    require(type(entry) is dict, "Corpus file is not a record")
    inputs, outputs = entry.get("inputs"), entry.get("outputs")
    require(
        type(inputs) is list
        and type(outputs) is list
        and len(inputs) == len(outputs)
        and 12 <= len(inputs) <= 128,
        "Corpus file needs a bounded paired input/output collection",
    )
    identifiers = entry.get("row_ids")
    if identifiers is not None:
        require(
            type(identifiers) is list
            and len(identifiers) == len(inputs)
            and all(type(value) is int and value >= 1 for value in identifiers),
            "Source row identities are not aligned and bounded",
        )
    seen, rows, by_identity = {}, [], {}
    for index, (value, target) in enumerate(zip(inputs, outputs, strict=True)):
        require(
            all(
                type(text) is str and len(text.encode()) <= config["max_input_bytes"]
                for text in (value, target)
            ),
            "Numeric corpus text exceeds its bound",
        )
        try:
            fields = json.loads(value)
        except (ValueError, RecursionError) as error:
            raise ValueError("Numeric input is not a JSON record") from error
        require(
            type(fields) is list
            and len(fields) == 4
            and all(type(field) is str for field in fields),
            "Numeric input needs exactly four string fields",
        )
        if identifiers is not None:
            ident = identifiers[index]
            require(
                ident not in by_identity or by_identity[ident] == (value, target),
                "One source row has conflicting input/output observations",
            )
            by_identity[ident] = (value, target)
        if value in seen:
            require(
                seen[value] == target,
                "Identical visible inputs have conflicting targets",
            )
            continue
        seen[value] = target
        rows.append((value, target))
    require(
        DIAGNOSTIC_ROWS + 8 <= len(rows) <= DIAGNOSTIC_ROWS + config["max_rows"],
        "A file needs four distinct diagnostic inputs and at least eight separate final inputs",
    )
    return rows, len(inputs) - len(rows)


def build(
    corpus,
    provider,
    source_id,
    *,
    positions=None,
    lanes=4,
    maximum_work=500000,
    synthesis_work=100000,
    maximum_probes=4,
    minimum_evidence=2,
    include_hash_control=False,
    control_position=None,
):
    """Return a finite queue manifest without reading state or selecting a skill.

    ``positions`` is an optional explicit source-file subset. Only its declared
    order determines goal lanes; generator metadata never determines routing.
    Duplicate source files and contradictory observations fail before any output.
    The work ceilings are caps, not expected expenditure or financial authority.
    An optional, explicitly selected extra file becomes an unsupported SHA256
    negative control. Its labels come from hashlib, not the source transform;
    the contractor receives no control label or desired operation identity.
    """
    require(
        type(provider) is str
        and IDENT.fullmatch(provider)
        and provider != PREVIOUS_PROVIDER,
        "A fresh local provider identity is required",
    )
    require(
        type(source_id) is str
        and SOURCE.fullmatch(source_id)
        and source_id != PREVIOUS_SOURCE,
        "A fresh source namespace is required",
    )
    require(
        type(lanes) is int and 1 <= lanes <= 16,
        "Goal lanes must fit the neural catalogue",
    )
    require(
        type(corpus) is dict and type(corpus.get("source")) is dict,
        "Corpus provenance is required",
    )
    config = normalize_config(corpus["config"])
    require(
        config["version"] == "safe-expression-v1",
        "This course builder accepts the reviewed numeric language only",
    )
    entries = corpus.get("tasks")
    require(
        type(entries) is list and 1 <= len(entries) <= MAX_TASKS,
        "Supply a finite declared numeric corpus",
    )
    positions = list(range(len(entries))) if positions is None else positions
    require(
        type(positions) is list
        and 1 <= len(positions) <= MAX_TASKS
        and all(type(index) is int and 0 <= index < len(entries) for index in positions)
        and len(set(positions)) == len(positions),
        "Source file selection is invalid or repeated",
    )
    require(type(include_hash_control) is bool, "Hash control must be explicit")
    require(
        (
            include_hash_control
            and type(control_position) is int
            and 0 <= control_position < len(entries)
            and control_position not in positions
            and len(positions) < MAX_TASKS
        )
        or (not include_hash_control and control_position is None),
        "Hash control needs a separate, explicitly selected source position within the task cap",
    )
    require(
        type(synthesis_work) is int and 1 <= synthesis_work <= 1000000,
        "Synthesis work must have a finite ceiling",
    )
    require(
        type(maximum_probes) is int
        and 1 <= maximum_probes <= DIAGNOSTIC_ROWS
        and type(minimum_evidence) is int
        and 1 <= minimum_evidence <= maximum_probes,
        "Diagnostic authority is invalid",
    )
    final_ceiling = active_verifier_ceiling(config)
    probe_ceiling = maximum_probes * (1 + (2 * config["max_input_bytes"] + 31) // 32)
    require(
        type(maximum_work) is int
        and final_ceiling + probe_ceiling + synthesis_work < maximum_work <= 10000000,
        "Job work must reserve synthesis, diagnostic disclosure and final verification",
    )
    tasks, commitments, files, original_files, nonces = [], [], set(), set(), set()
    selected = [(position, False) for position in positions]
    if include_hash_control:
        selected.append((control_position, True))
    for ordinal, (position, hash_control) in enumerate(selected):
        entry = entries[position]
        rows, duplicates = _file(entry, config)
        original_identity = digest(rows)
        original_set_identity = digest(sorted(rows))
        require(
            original_set_identity not in original_files,
            "The same deduplicated file (original source observations) cannot be queued twice",
        )
        original_files.add(original_set_identity)
        if hash_control:
            rows = [
                (value, hashlib.sha256(value.encode("utf-8")).hexdigest())
                for value, _ in rows
            ]
        identity = digest(rows)
        identity_set = digest(sorted(rows))
        require(
            identity_set not in files,
            "The same deduplicated file cannot be queued twice",
        )
        files.add(identity_set)
        diagnostic, heldout = rows[:DIAGNOSTIC_ROWS], rows[DIAGNOSTIC_ROWS:]
        nonce = secrets.token_hex(32)
        require(
            HASH.fullmatch(nonce) and nonce not in nonces,
            "Evaluation nonce is invalid or repeated",
        )
        nonces.add(nonce)
        task = {
            "id": f"active-record-{ordinal % lanes}",
            "title": f"Public-record batch {ordinal + 1:03d}",
            "purpose": "Select or acquire an executable transform for a declared public-observation batch using bounded diagnostic feedback.",
            "brief": "Request diagnostic labels only when useful, then select one safe program or abstain. Final rows remain with the buyer. No program or format identity is supplied. Included capacity spends no wallet funds.",
            "kind": "compute",
            "provider": provider,
            "action": "experiment",
            "maximum_micro_usd": 0,
            "delivery_seconds": 3600,
            "evaluation_nonce": nonce,
            "acceptance": {
                "type": "transform_active",
                "config": config,
                "diagnostic_inputs": [value for value, _ in diagnostic],
                "diagnostic_outputs": [value for _, value in diagnostic],
                "heldout_inputs": [value for value, _ in heldout],
                "heldout_outputs": [value for _, value in heldout],
                "maximum_work": maximum_work,
                "maximum_probes": maximum_probes,
                "synthesis_work": synthesis_work,
                "minimum_evidence": minimum_evidence,
            },
        }
        validate_task(task)
        tasks.append(task)
        commitments.append(
            {
                "entry_sha256": digest(entry),
                "source_position": position,
                "original_deduplicated_io_sha256": original_identity,
                "deduplicated_io_sha256": identity,
                "io_set_sha256": identity_set,
                "diagnostic_inputs_sha256": digest(
                    task["acceptance"]["diagnostic_inputs"]
                ),
                "heldout_inputs_sha256": digest(task["acceptance"]["heldout_inputs"]),
                "diagnostic_rows": len(diagnostic),
                "heldout_rows": len(heldout),
                "duplicate_rows_removed": duplicates,
                "kind": (
                    "unsupported-sha256-negative-control"
                    if hash_control
                    else "supported-development-workload"
                ),
                "customer_task": False,
                "supported_acquisition": not hash_control,
                **(
                    {
                        "label_derivation": "hashlib.sha256(exact_input_utf8).hexdigest()",
                        "expected_outcome": "search failure or abstention; buyer rejection",
                        "unsupported_operation": "SHA256 is absent from the configured expression grammar",
                    }
                    if hash_control
                    else {}
                ),
            }
        )
    return {
        "schema": VERSION,
        "source": {
            "corpus_sha256": digest(corpus),
            "provenance": copy.deepcopy(corpus["source"]),
            "source_id": source_id,
            "provider": provider,
            "course": "finite-active-numeric-development-v1",
            "supported_development_tasks": len(positions),
            "unsupported_negative_controls": int(include_hash_control),
            "customer_tasks": 0,
            "files": commitments,
            "cost_authority": {
                "financial_micro_usd": 0,
                "maximum_work_per_job": maximum_work,
                "maximum_course_work": maximum_work * len(tasks),
                "maximum_probes_per_job": maximum_probes,
                "maximum_synthesis_work_per_job": synthesis_work,
                "reserved_final_verifier_work_per_job": final_ceiling,
            },
            "scope": "Development workload over declared public observations with generated formatting and arithmetic. Supported-task inputs and targets come from the pinned corpus. An optional separately declared unsupported negative control hashes a different source file's exact UTF-8 inputs with hashlib; it is neither a customer task nor a supported acquisition. No file recipe, family, format, skill ID, control kind or source row identity is passed to the contractor; only explicitly requested diagnostic labels may be revealed. This builder does not independently verify source truth or generate token activity.",
        },
        "tasks": tasks,
    }


def write_manifest(path, manifest):
    """Create a new owner-private catalogue; never replace a previous course."""
    destination = private_destination(path)
    payload = (
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    require(len(payload) <= MAX_BYTES, "Result exceeds the finite manifest bound")
    with destination.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(payload).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument(
        "--positions",
        help="Comma-separated zero-based source file positions; default all",
    )
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument("--maximum-work", type=int, default=500000)
    parser.add_argument("--synthesis-work", type=int, default=100000)
    parser.add_argument("--maximum-probes", type=int, default=4)
    parser.add_argument("--minimum-evidence", type=int, default=2)
    parser.add_argument(
        "--hash-control",
        action="store_true",
        help="Append one declared unsupported SHA256 negative control",
    )
    parser.add_argument(
        "--control-position",
        type=int,
        help="Zero-based control source position, excluded from --positions",
    )
    args = parser.parse_args()
    with args.corpus.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "Corpus exceeds its input bound")
    positions = (
        None
        if args.positions is None
        else [int(part) for part in args.positions.split(",")]
    )
    manifest = build(
        json.loads(raw),
        args.provider,
        args.source_id,
        positions=positions,
        lanes=args.lanes,
        maximum_work=args.maximum_work,
        synthesis_work=args.synthesis_work,
        maximum_probes=args.maximum_probes,
        minimum_evidence=args.minimum_evidence,
        include_hash_control=args.hash_control,
        control_position=args.control_position,
    )
    identity = write_manifest(args.output, manifest)
    print(
        json.dumps(
            {
                "sha256": identity,
                "tasks": len(manifest["tasks"]),
                "maximum_course_work": manifest["source"]["cost_authority"][
                    "maximum_course_work"
                ],
                "financial_micro_usd": 0,
            }
        )
    )


if __name__ == "__main__":
    main()

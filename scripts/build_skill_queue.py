"""Convert a committed public-data corpus into private buyer task contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path

from cognition.skills import SOURCE, normalize_config
from commerce.skill_queue import MAX_BYTES, MAX_TASKS, VERSION
from commerce.tasks import (
    HASH,
    IDENT,
    digest,
    transform_verifier_ceiling,
    validate_task,
)


def learning_title(position):
    return f"Public-record repair {position + 1:03d}"


def build(corpus, provider, *, solver_work=2400, lanes=4):
    if not isinstance(provider, str) or not IDENT.fullmatch(provider):
        raise ValueError("A local skill provider identity is required")
    if type(lanes) is not int or not 1 <= lanes <= 16:
        raise ValueError("Goal lanes must fit the neural catalogue")
    if type(solver_work) is not int or not 1 <= solver_work <= 1_000_000:
        raise ValueError("Search allowance must be finite")
    config = normalize_config(corpus["config"])
    entries = corpus["tasks"]
    if type(entries) is not list or not 1 <= len(entries) <= MAX_TASKS:
        raise ValueError("Supply a finite declared task corpus")
    tasks = []
    for position, entry in enumerate(entries):
        inputs, outputs = entry["inputs"], entry["outputs"]
        if len(inputs) != 24 or len(outputs) != 24:
            raise ValueError(
                "Declared corpus needs four examples, eight checks and twelve final rows"
            )
        task = {
            "id": f"data-repair-{position % lanes}",
            "title": learning_title(position),
            "purpose": "Acquire and reuse a verified data-repair program on real public observations with declared generated formatting.",
            "brief": "Infer a safe declarative program from the training examples. Buyer validation and final rows remain withheld. The job uses included server capacity and spends no wallet funds.",
            "kind": "compute",
            "provider": provider,
            "action": "experiment",
            "maximum_micro_usd": 0,
            "delivery_seconds": 3600,
            "evaluation_nonce": secrets.token_hex(32),
            "acceptance": {
                "type": "transform_program",
                "config": config,
                "training_inputs": inputs[:4],
                "training_outputs": outputs[:4],
                "validation_inputs": inputs[4:12],
                "validation_outputs": outputs[4:12],
                "heldout_inputs": inputs[12:],
                "heldout_outputs": outputs[12:],
                "maximum_work": solver_work + 3 * transform_verifier_ceiling(config),
            },
        }
        validate_task(task)
        tasks.append(task)
    return {
        "schema": VERSION,
        "source": {
            "corpus_sha256": digest(corpus),
            "provenance": corpus["source"],
            "scope": "Real public numeric observations; declared generated repair workloads. No task-generator metadata is supplied to synthesis.",
        },
        "tasks": tasks,
    }


def build_execution(
    corpus,
    learned,
    provider,
    source_id,
    *,
    entries,
    prior_entries=(),
    lanes=4,
    maximum_work=10000,
):
    """Bind fresh files to successful learned programs by explicit format identity.

    ``learned`` contains trusted buyer receipt projections with exactly ``title``,
    ``skill_id``, ``source_id`` and ``accepted``. Titles identify the original
    learning contracts, never the position of a shuffled serving file. Receipt
    authenticity remains the caller's responsibility. Expected outputs stay in
    the private buyer contract; the execution provider receives inputs only.
    """
    if not isinstance(provider, str) or not IDENT.fullmatch(provider):
        raise ValueError("A local skill provider identity is required")
    if type(source_id) is not str or not SOURCE.fullmatch(source_id):
        raise ValueError("A retained-library source identity is required")
    if type(lanes) is not int or not 1 <= lanes <= 16:
        raise ValueError("Goal lanes must fit the neural catalogue")
    if type(maximum_work) is not int or not 1 <= maximum_work <= 10_000_000:
        raise ValueError("Execution allowance must be finite")
    config = normalize_config(corpus["config"])
    original = corpus["tasks"]
    if type(original) is not list or not 1 <= len(original) <= MAX_TASKS:
        raise ValueError("Supply a finite declared task corpus")
    if type(entries) is not list or not 1 <= len(entries) <= MAX_TASKS:
        raise ValueError("Supply a finite fresh serving batch")
    if type(prior_entries) not in (list, tuple) or len(prior_entries) > MAX_TASKS:
        raise ValueError("Prior serving history exceeds its inspection bound")
    if type(learned) is not list or not 1 <= len(learned) <= MAX_TASKS:
        raise ValueError("Supply finite successful buyer receipt projections")
    receipts = {}
    for receipt in learned:
        if (
            type(receipt) is not dict
            or set(receipt) != {"title", "skill_id", "source_id", "accepted"}
            or type(receipt["title"]) is not str
            or receipt["title"] in receipts
            or receipt["source_id"] != source_id
            or receipt["accepted"] is not True
            or type(receipt["skill_id"]) is not str
            or not HASH.fullmatch(receipt["skill_id"])
        ):
            raise ValueError(
                "Successful learned-program receipt is ambiguous or unbound"
            )
        receipts[receipt["title"]] = receipt

    def inspect(entry):
        if (
            type(entry) is not dict
            or type(entry.get("format_index")) is not int
            or entry["format_index"] < 0
            or type(entry.get("recipe")) is not dict
            or type(entry.get("row_ids")) is not list
            or len(entry["row_ids"]) != 24
            or any(type(row) is not int or row < 1 for row in entry["row_ids"])
            or len(set(entry["row_ids"])) != 24
            or type(entry.get("inputs")) is not list
            or type(entry.get("outputs")) is not list
            or len(entry["inputs"]) != 24
            or len(entry["outputs"]) != 24
            or any(
                type(value) is not str for value in entry["inputs"] + entry["outputs"]
            )
        ):
            raise ValueError("Declared format, recipe or row identity is malformed")
        return entry["format_index"], digest(entry["recipe"])

    formats = {}
    for position, entry in enumerate(original):
        if entry.get("phase") != "unseen":
            continue
        index, recipe_hash = inspect(entry)
        if index in formats:
            raise ValueError("Learning corpus has duplicate unseen format identities")
        title = learning_title(position)
        if title not in receipts:
            raise ValueError("A successful learned-program receipt is missing")
        formats[index] = (entry, title, recipe_hash)
    if not formats:
        raise ValueError("Learning corpus has no explicit unseen formats")

    declarations = {digest(entry) for entry in original}
    selected, seen, bindings = [], set(), []
    for position, entry in enumerate(entries):
        index, recipe_hash = inspect(entry)
        if entry.get("phase") != "serve" or index not in formats or index in seen:
            raise ValueError(
                "Serving format identity is missing, duplicated or unknown"
            )
        seen.add(index)
        learned_entry, title, expected_recipe = formats[index]
        if recipe_hash != expected_recipe:
            raise ValueError("Serving recipe differs from its learned format")
        if digest(entry) not in declarations:
            raise ValueError("Serving file is absent from the committed corpus")
        for prior in [learned_entry, *prior_entries]:
            if prior.get("phase") not in ("unseen", "serve"):
                continue
            prior_index, prior_recipe = inspect(prior)
            if prior_index != index:
                continue
            if prior_recipe != expected_recipe:
                raise ValueError("Prior recipe differs from its learned format")
            if set(entry["row_ids"]) & set(prior["row_ids"]) or set(
                entry["inputs"]
            ) & set(prior["inputs"]):
                raise ValueError("Fresh serving file overlaps prior same-format rows")
        receipt = receipts[title]
        task = {
            "id": f"data-repair-{position % lanes}",
            "title": f"Retained-program execution {index:03d}",
            "purpose": "Apply a buyer-verified retained program to a fresh public-record file with the same declared format.",
            "brief": "Execute the retained skill on the supplied inputs. No synthesis or fallback repair is authorized. Buyer outputs remain withheld. Included server capacity spends no wallet funds.",
            "kind": "compute",
            "provider": provider,
            "action": "experiment",
            "maximum_micro_usd": 0,
            "delivery_seconds": 3600,
            "evaluation_nonce": secrets.token_hex(32),
            "acceptance": {
                "type": "transform_execution",
                "config": config,
                "source_id": source_id,
                "skill_id": receipt["skill_id"],
                "inputs": list(entry["inputs"]),
                "expected_outputs": list(entry["outputs"]),
                "maximum_work": maximum_work,
            },
        }
        validate_task(task)
        selected.append(task)
        bindings.append(
            {
                "format_index": index,
                "learning_title": title,
                "skill_id": receipt["skill_id"],
                "recipe_sha256": recipe_hash,
                "entry_sha256": digest(entry),
            }
        )
    return {
        "schema": VERSION,
        "source": {
            "corpus_sha256": digest(corpus),
            "provenance": corpus["source"],
            "source_id": source_id,
            "prior_entries_sha256": digest(list(prior_entries)),
            "bindings": bindings,
            "scope": "Caller-selected retained-program execution on new rows of explicitly matched generated formats. No autonomous format recognition or new learning is implied.",
        },
        "tasks": selected,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--solver-work", type=int, default=2400)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output path; previous contracts are retained")
    with args.corpus.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        parser.error("Corpus exceeds its input bound")
    manifest = build(json.loads(raw), args.provider, solver_work=args.solver_work)
    payload = (json.dumps(manifest, indent=2, allow_nan=False) + "\n").encode()
    if len(payload) > MAX_BYTES:
        parser.error("Result exceeds its manifest bound")
    with args.output.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(payload)
    print(
        json.dumps(
            {
                "path": str(args.output),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "tasks": len(manifest["tasks"]),
            }
        )
    )


if __name__ == "__main__":
    main()

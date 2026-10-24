"""Convert a committed public-data corpus into private buyer task contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path

from cognition.skills import normalize_config
from commerce.skill_queue import MAX_BYTES, MAX_TASKS, VERSION
from commerce.tasks import IDENT, digest, transform_verifier_ceiling, validate_task


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
            "title": f"Public-record repair {position + 1:03d}",
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

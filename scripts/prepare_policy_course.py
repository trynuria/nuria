"""Select a finite reasoning-policy course before decoding task bodies.

This helper reads Spider row indices and database IDs with a lexical JSON
scanner. Question, SQL and SQL-AST values are skipped, not decoded, during
selection. The supplied prior records conservatively exclude every Spider ID
they mention. The caller must supply the complete reviewed prior-record list;
this module does not search private directories or inspect another benchmark.

The resulting freeze is local integrity evidence, not external preregistration.
Spider is public and may occur in model pretraining. No inference runs here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from cognition.analyst_workspace import ordered_query

VERSION = "nuria.policy-course.v1"
TASK = re.compile(r"spider-dev-\d{4}\Z")
TASK_BYTES = re.compile(rb"(?<![A-Za-z0-9_-])spider-dev-\d{4}(?![A-Za-z0-9_-])")
IDENTIFIER = re.compile(r"[A-Za-z0-9_]+\Z")
NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
METHODS = ("schema_first", "learned_policy", "fixed_review")
PHASE_METHODS = {
    "train": ("schema_first",),
    "validation": ("schema_first", "candidate_1", "candidate_2"),
    "test": METHODS,
}
PHASE_COUNTS = {"train": 6, "validation": 4, "test": 12}
LIMITS = {
    "max_turns": 12,
    "max_tool_calls": 12,
    "max_output_tokens": 6000,
    "max_tokens_per_turn": 2048,
    "max_candidates": 2,
}
MAX_DATASET_BYTES = 32 * 1024 * 1024
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, limit):
    path = Path(path).resolve(strict=True)
    if any("duet" in part.lower() for part in path.parts):
        raise ValueError("This course helper cannot open Duet paths")
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Course input is absent or exceeds its byte limit")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Course input exceeds its byte limit")
    return path, raw


def _space(raw, offset):
    while offset < len(raw) and raw[offset] in b" \t\r\n":
        offset += 1
    return offset


def _string(raw, offset):
    """Find a JSON string boundary without interpreting its content."""
    if offset >= len(raw) or raw[offset] != 34:
        raise ValueError("Expected a JSON string")
    offset += 1
    while offset < len(raw):
        char = raw[offset]
        if char == 34:
            return offset + 1
        if char < 32:
            raise ValueError("Unescaped control character in JSON string")
        if char == 92:
            offset += 1
            if offset >= len(raw):
                break
            escaped = raw[offset]
            if escaped == 117:
                value = raw[offset + 1 : offset + 5]
                if len(value) != 4 or any(
                    c not in b"0123456789abcdefABCDEF" for c in value
                ):
                    raise ValueError("Malformed JSON Unicode escape")
                offset += 4
            elif escaped not in b'"\\/bfnrt':
                raise ValueError("Malformed JSON string escape")
        offset += 1
    raise ValueError("Unterminated JSON string")


def _skip(raw, offset, depth=0):
    if depth > 32:
        raise ValueError("Course JSON exceeds its nesting limit")
    offset = _space(raw, offset)
    if offset >= len(raw):
        raise ValueError("Truncated JSON value")
    char = raw[offset]
    if char == 34:
        return _string(raw, offset)
    if char in (91, 123):
        close = 93 if char == 91 else 125
        offset = _space(raw, offset + 1)
        if offset < len(raw) and raw[offset] == close:
            return offset + 1
        while True:
            if char == 123:
                offset = _space(raw, _string(raw, offset))
                if offset >= len(raw) or raw[offset] != 58:
                    raise ValueError("Expected a JSON object separator")
                offset += 1
            offset = _space(raw, _skip(raw, offset, depth + 1))
            if offset < len(raw) and raw[offset] == close:
                return offset + 1
            if offset >= len(raw) or raw[offset] != 44:
                raise ValueError("Expected a JSON item separator")
            offset = _space(raw, offset + 1)
    for literal in (b"true", b"false", b"null"):
        if raw.startswith(literal, offset):
            return offset + len(literal)
    number = NUMBER.match(raw, offset)
    if number:
        return number.end()
    raise ValueError("Malformed JSON value")


def spider_metadata(raw):
    """Return row IDs, database IDs and byte spans; skip all answer values."""
    if type(raw) is not bytes or len(raw) > MAX_DATASET_BYTES:
        raise ValueError("Spider metadata input exceeds its byte limit")
    offset = _space(raw, 0)
    if offset >= len(raw) or raw[offset] != 91:
        raise ValueError("Spider source must be one JSON row array")
    offset = _space(raw, offset + 1)
    rows = []
    if offset < len(raw) and raw[offset] == 93:
        offset += 1
    else:
        while True:
            start = offset
            if offset >= len(raw) or raw[offset] != 123:
                raise ValueError("Each Spider row must be an object")
            offset = _space(raw, offset + 1)
            database = None
            keys = set()
            while True:
                key_start = offset
                key_end = _string(raw, offset)
                key = json.loads(raw[key_start:key_end])
                if key in keys:
                    raise ValueError("Duplicate Spider row key")
                keys.add(key)
                offset = _space(raw, key_end)
                if offset >= len(raw) or raw[offset] != 58:
                    raise ValueError("Expected a Spider field separator")
                offset = _space(raw, offset + 1)
                end = _skip(raw, offset)
                if key == "db_id":
                    database = json.loads(raw[offset:end])
                    if type(database) is not str or not IDENTIFIER.fullmatch(database):
                        raise ValueError("Malformed Spider database ID")
                offset = _space(raw, end)
                if offset < len(raw) and raw[offset] == 125:
                    offset += 1
                    break
                if offset >= len(raw) or raw[offset] != 44:
                    raise ValueError("Expected a Spider field separator")
                offset = _space(raw, offset + 1)
            if database is None or len(rows) >= 10000:
                raise ValueError("Spider metadata is incomplete or too large")
            rows.append(
                {
                    "task_id": f"spider-dev-{len(rows):04d}",
                    "db_id": database,
                    "start": start,
                    "end": offset,
                    "row_sha256": _hash(raw[start:offset]),
                }
            )
            offset = _space(raw, offset)
            if offset < len(raw) and raw[offset] == 93:
                offset += 1
                break
            if offset >= len(raw) or raw[offset] != 44:
                raise ValueError("Expected a Spider row separator")
            offset = _space(raw, offset + 1)
    if _space(raw, offset) != len(raw):
        raise ValueError("Trailing data after Spider rows")
    return rows


def _order(seed, label):
    return _hash((seed + ":" + label).encode())


def _write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return _hash(path.read_bytes())


def prepare_policy_course(
    dataset,
    registry,
    prior_records,
    source_files,
    output,
    *,
    repository,
    model_identity,
    seed="nuria-reasoning-policy-20261026-v1",
):
    """Write a prospective private freeze and a path-free public projection.

    ``prior_records`` is explicit and reviewed, rather than a directory search.
    It should include the original selected-ID metadata even for unused prior
    jobs. Source files must include the complete host runner/controller closure;
    their hashes must still match when any selected question is loaded.
    """
    if type(seed) is not str or not 1 <= len(seed.encode()) <= 256:
        raise ValueError("A bounded selection seed is required")
    if type(model_identity) is not str or not 1 <= len(model_identity.encode()) <= 256:
        raise ValueError("An explicit bounded reasoning-model identity is required")
    repository = Path(repository).resolve(strict=True)
    dataset, dataset_raw = _read(dataset, MAX_DATASET_BYTES)
    registry, registry_raw = _read(registry, MAX_RECORD_BYTES)
    registry_value = json.loads(registry_raw)
    databases = registry_value.get("databases")
    if (
        type(databases) is not dict
        or len(databases) != 6
        or any(not IDENTIFIER.fullmatch(name) for name in databases)
    ):
        raise ValueError("The course requires the six reviewed registered databases")
    excluded = set()
    prior = []
    for index, path in enumerate(prior_records):
        path, raw = _read(path, MAX_RECORD_BYTES)
        ids = sorted({match.decode() for match in TASK_BYTES.findall(raw)})
        if not ids:
            raise ValueError("A prior record does not identify any Spider tasks")
        excluded.update(ids)
        prior.append(
            {
                "record_id": f"prior-{index + 1:03d}",
                "path": str(path),
                "sha256": _hash(raw),
                "task_ids": ids,
            }
        )
    if not prior:
        raise ValueError("Explicit prior-selection evidence is required")
    sources = {}
    all_sources = [*source_files, Path(__file__)]
    for source in all_sources:
        source, raw = _read(source, MAX_SOURCE_BYTES)
        if not source.is_relative_to(repository):
            raise ValueError("Frozen code must be inside the source repository")
        sources[source.relative_to(repository).as_posix()] = _hash(raw)
    if len(sources) < 2:
        raise ValueError("Freeze the actual host runner in addition to this helper")
    rows = spider_metadata(dataset_raw)
    eligible = [
        row
        for row in rows
        if row["db_id"] in databases and row["task_id"] not in excluded
    ]
    database_order = sorted(databases, key=lambda name: _order(seed, "db:" + name))
    selected, used = {}, set()
    for phase, count in PHASE_COUNTS.items():
        chosen = []
        for index in range(count):
            database = database_order[index % len(database_order)]
            choices = sorted(
                (
                    row
                    for row in eligible
                    if row["db_id"] == database and row["task_id"] not in used
                ),
                key=lambda row: _order(seed, phase + ":" + row["task_id"]),
            )
            if not choices:
                raise ValueError("Insufficient disjoint metadata-selected tasks")
            row = choices[0]
            chosen.append(row)
            used.add(row["task_id"])
        selected[phase] = chosen
    expected = {
        "train": [
            {"method": "schema_first", "task_id": row["task_id"]}
            for row in selected["train"]
        ],
        **{
            phase: [
                {"method": method, "task_id": row["task_id"]}
                for row in selected[phase]
                for method in PHASE_METHODS[phase]
            ]
            for phase in ("validation", "test")
        },
    }
    public = {
        "schema": VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": "Spider 1.0 development",
        "source_url": "https://yale-lily.github.io/spider",
        "license": "CC-BY-SA-4.0",
        "dataset_sha256": _hash(dataset_raw),
        "registry_sha256": _hash(registry_raw),
        "model_identity": model_identity,
        "seed": seed,
        "inference_seed": 20261026,
        "selection_rule": "Lexical row-index/database-ID metadata only; balanced fixed quotas and SHA256 order; no question, SQL, AST, difficulty or outcome inspection",
        "database_order": database_order,
        "prior_records": [
            {k: v for k, v in record.items() if k != "path"} for record in prior
        ],
        "excluded_task_ids": sorted(excluded),
        "metadata_sha256": _hash(
            json.dumps(
                [{k: row[k] for k in ("task_id", "db_id")} for row in rows],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ),
        "selected": {
            phase: [{k: row[k] for k in ("task_id", "db_id")} for row in phase_rows]
            for phase, phase_rows in selected.items()
        },
        "source_files": sources,
        "methods": list(METHODS),
        "phase_methods": {
            phase: list(methods) for phase, methods in PHASE_METHODS.items()
        },
        "limits_per_job": LIMITS,
        "expected_jobs": expected,
        "expected_job_count": 54,
        "expected_test_jobs": 36,
        "development_critic_calls": 2,
        "development_critic_completion_cap": 2048,
        "maximum_planned_completion_tokens": 328096,
        "maximum_model_wall_seconds": 5400,
        "maximum_new_retained_bytes": 100 * 1024 * 1024,
        "minimum_free_disk_bytes": 8 * 1024**3,
        "feedback_policy": "Training labels may inform a frozen policy. Validation selects one fixed policy before test. No test label is disclosed until every selected test job has a retained terminal record. No outcome-based task replacement.",
        "fairness": "All methods receive identical training records and use the same pinned model, registered data/tools and collective per-job ceilings. Fixed review adds a conventional handwritten review policy. Learned policy is frozen before test. Equal caps do not mean equal actual compute.",
        "grading_scope": "Finite executed SQL rows, multiplicity, NULLs, column count and declared ordering; not universal SQL equivalence or official Spider test-suite accuracy",
        "limits": "New local task IDs remain the same public benchmark and the same six databases. Foundation-model pretraining contamination is possible. No difficulty filtering or minimum difficulty is established. This freeze does not authorize paid inference or attest external preregistration.",
    }
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.chmod(output, 0o700)
    public_path = output / "protocol-public.json"
    public_hash = _write(public_path, public)
    private = {
        "schema": VERSION,
        "repository": str(repository),
        "dataset": str(dataset),
        "dataset_sha256": _hash(dataset_raw),
        "registry": str(registry),
        "registry_sha256": _hash(registry_raw),
        "prior_records": prior,
        "source_files": sources,
        "selected": selected,
        "public_protocol": str(public_path),
        "public_protocol_sha256": public_hash,
    }
    private_path = output / "protocol-private.json"
    private_hash = _write(private_path, private)
    return {
        "private_protocol": str(private_path),
        "private_protocol_sha256": private_hash,
        "public_protocol": str(public_path),
        "public_protocol_sha256": public_hash,
        "selected": public["selected"],
        "expected_jobs": expected,
    }


def verify_freeze(protocol_path, expected_protocol_sha256):
    """Verify the exact private freeze and all inputs before exposing a task."""
    _, raw = _read(protocol_path, MAX_RECORD_BYTES)
    if _hash(raw) != expected_protocol_sha256:
        raise ValueError("Course freeze identity differs")
    protocol = json.loads(raw)
    if protocol.get("schema") != VERSION:
        raise ValueError("Unknown policy-course protocol")
    repository = Path(protocol["repository"]).resolve(strict=True)
    for relative, expected in protocol["source_files"].items():
        source = (repository / relative).resolve(strict=True)
        if not source.is_relative_to(repository):
            raise ValueError("Frozen source escapes the repository")
        if _hash(_read(source, MAX_SOURCE_BYTES)[1]) != expected:
            raise ValueError("Frozen source has changed: " + relative)
    for field, limit in (
        ("dataset", MAX_DATASET_BYTES),
        ("registry", MAX_RECORD_BYTES),
        ("public_protocol", MAX_RECORD_BYTES),
    ):
        if _hash(_read(protocol[field], limit)[1]) != protocol[field + "_sha256"]:
            raise ValueError("Frozen course input has changed: " + field)
    for record in protocol["prior_records"]:
        if _hash(_read(record["path"], MAX_RECORD_BYTES)[1]) != record["sha256"]:
            raise ValueError("Prior-selection evidence has changed")
    return protocol


def _selected_row(protocol_path, expected_protocol_sha256, task_id, phase):
    protocol = verify_freeze(protocol_path, expected_protocol_sha256)
    if phase not in PHASE_COUNTS or not TASK.fullmatch(task_id):
        raise ValueError("Unknown course phase or task identity")
    choices = [row for row in protocol["selected"][phase] if row["task_id"] == task_id]
    if len(choices) != 1:
        raise ValueError("The task is not selected in this frozen phase")
    selected = choices[0]
    raw = _read(protocol["dataset"], MAX_DATASET_BYTES)[1]
    row_raw = raw[selected["start"] : selected["end"]]
    if _hash(row_raw) != selected["row_sha256"]:
        raise ValueError("Selected row differs from the freeze")
    row = json.loads(row_raw)
    if row.get("db_id") != selected["db_id"]:
        raise ValueError("Selected database differs from the freeze")
    return row


def load_selected_task(protocol_path, expected_protocol_sha256, task_id, *, phase):
    """Expose only the selected learner question after exact freeze validation."""
    row = _selected_row(protocol_path, expected_protocol_sha256, task_id, phase)
    question = row.get("question")
    if type(question) is not str or not question or len(question.encode()) > 16384:
        raise ValueError("Selected learner question exceeds its contract")
    return {"task_id": task_id, "db_id": row["db_id"], "question": question}


def load_selected_reference(
    protocol_path,
    expected_protocol_sha256,
    task_id,
    *,
    phase,
    terminal_jobs=(),
):
    """Host-only reference access after a complete phase, except training.

    ``terminal_jobs`` contains ``(method, task_id)`` pairs, including failures
    and abstentions. This checks the host's declaration; it is not a filesystem
    security boundary against the host that already owns the public dataset.
    """
    protocol = verify_freeze(protocol_path, expected_protocol_sha256)
    if phase not in PHASE_COUNTS:
        raise ValueError("Unknown reference phase")
    if phase != "train":
        expected = {
            (method, row["task_id"])
            for row in protocol["selected"][phase]
            for method in PHASE_METHODS[phase]
        }
        declared = {tuple(item) for item in terminal_jobs}
        if declared != expected:
            raise ValueError("Every selected phase job needs a terminal record first")
    row = _selected_row(protocol_path, expected_protocol_sha256, task_id, phase)
    query = row.get("query")
    if type(query) is not str or not query or len(query.encode()) > 32768:
        raise ValueError("Selected reference SQL exceeds its contract")
    return {
        "task_id": task_id,
        "db_id": row["db_id"],
        "query": query,
        "ordered": ordered_query(query),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--prior-record", required=True, action="append", type=Path)
    parser.add_argument("--source-file", required=True, action="append", type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-identity", required=True)
    parser.add_argument("--seed", default="nuria-reasoning-policy-20261026-v1")
    args = parser.parse_args()
    result = prepare_policy_course(
        args.dataset,
        args.registry,
        args.prior_record,
        args.source_file,
        args.output,
        repository=args.repository,
        model_identity=args.model_identity,
        seed=args.seed,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

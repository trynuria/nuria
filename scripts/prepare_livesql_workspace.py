"""Prepare a pinned LiveSQLBench SQLite course with evaluator-only answers.

Dataset: birdsql/livesqlbench-base-lite-sqlite, CC BY-SA 4.0.
Only public benchmark data is downloaded. This is not customer activity.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from cognition.analyst_workspace import (
    SCHEMA,
    AnalystWorkspace,
    ordered_query,
    select_only,
)

DATASET = "birdsql/livesqlbench-base-lite-sqlite"
REVISION = "0664a2f28555faa0dd2947c8c23288df79bcc06b"
LICENSE = "CC-BY-SA-4.0"
SEED = "nuria-livesql-hybrid-v1"
DATABASES = ("museum", "solar", "archeology", "gaming", "robot", "polar")
MAX_TOTAL_BYTES = 30 * 1024 * 1024
SPIDER_URL = "https://drive.usercontent.google.com/download?id=1403EGqzIDoHMdQF4c9Bkyl7dZLZ5Wt6J&export=download&authuser=0&confirm=t"
SPIDER_HOME = "https://yale-lily.github.io/spider"
SPIDER_METADATA_URL = "https://datasets-server.huggingface.co/statistics?dataset=xlangai/spider&config=spider&split=validation"


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    )


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _order(value):
    return _hash((SEED + ":" + value).encode())


def _write(path, value, *, private=False):
    with path.open("x") as stream:
        stream.write(_json(value) + "\n")
    if private:
        os.chmod(path, 0o600)


def _task_value(task, names):
    for name in names:
        if name in task:
            return task[name]
    raise ValueError("Required public benchmark field is absent: " + "/".join(names))


def prepare(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    for folder in ("source", "learner", "evaluator", "sealed"):
        (output / folder).mkdir()
    db_order = sorted(DATABASES, key=_order)
    splits = {
        "development": db_order[:2],
        "teaching": db_order[2:4],
        "reserved": db_order[4:6],
    }
    freeze = {
        "schema": "nuria.livesql-source-selection.v1",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": DATASET,
        "revision": REVISION,
        "license": LICENSE,
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_url": "https://huggingface.co/datasets/" + DATASET,
        "seed": SEED,
        "candidate_database_ids": list(DATABASES),
        "database_order": db_order,
        "splits": splits,
        "task_selection": "First hash-ordered syntactically SELECT/WITH-eligible task IDs per database, without difficulty selection or result-dependent replacement",
        "per_database_quota": {"development": 3, "teaching": 4, "reserved": 6},
        "initial_pilot_maximum": 6,
        "maximum_download_bytes": MAX_TOTAL_BYTES,
        "reserved_policy": "Question bodies and answers remain sealed until the model and policy protocol is frozen",
        "scope": "Externally authored benchmark database execution, not customer work or universal SQL equivalence",
        "preparation_source_sha256": _hash(Path(__file__).read_bytes()),
    }
    # This write precedes every question, solution and database-content fetch.
    _write(output / "selection-freeze.json", freeze)
    downloaded = 0
    source_records = {}

    def fetch(relative, maximum):
        nonlocal downloaded
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            "https://huggingface.co/datasets/"
            + DATASET
            + "/resolve/"
            + REVISION
            + "/"
            + relative,
            headers={"User-Agent": "Nuria-benchmark-preparation"},
        )
        with (
            urllib.request.urlopen(request, timeout=30) as response,
            target.open("xb") as stream,
        ):
            size = 0
            while chunk := response.read(65536):
                size += len(chunk)
                downloaded += len(chunk)
                if size > maximum or downloaded > MAX_TOTAL_BYTES:
                    raise ValueError(
                        "Pinned benchmark download exceeds its declared limit"
                    )
                stream.write(chunk)
        source_records[relative] = {"bytes": size, "sha256": _hash(target.read_bytes())}
        return target

    readme = fetch("README.md", 65536)
    if "cc-by-sa-4.0" not in readme.read_text().lower():
        raise ValueError(
            "Pinned dataset license no longer matches the recorded license"
        )
    registry = {
        "schema": SCHEMA,
        "dataset": DATASET,
        "revision": REVISION,
        "license": LICENSE,
        "attribution": "LiveSQLBench / BIRD authors; public dataset licensed CC BY-SA 4.0",
        "databases": {},
    }
    for db_id in db_order:
        database_path = fetch(f"{db_id}/{db_id}_template.sqlite", 5 * 1024 * 1024)
        documents = {}
        for section, suffix in (
            ("business", "_kb.jsonl"),
            ("columns", "_column_meaning_base.json"),
            ("schema", "_schema.txt"),
        ):
            path = fetch(f"{db_id}/{db_id}{suffix}", 128 * 1024)
            documents[section] = {
                "path": str(path.relative_to(output)),
                "sha256": _hash(path.read_bytes()),
            }
        registry["databases"][db_id] = {
            "database": {
                "path": str(database_path.relative_to(output)),
                "sha256": _hash(database_path.read_bytes()),
            },
            "documents": documents,
        }
    _write(output / "registry.json", registry)
    # Original task records (including answers) are never placed in learner/.
    tasks_path = fetch("livesqlbench_data_sqlite.jsonl", 1024 * 1024)
    os.chmod(tasks_path, 0o600)
    records = [
        json.loads(line) for line in tasks_path.read_text().splitlines() if line.strip()
    ]
    if all(not record.get("sol_sql") for record in records):
        summary = {
            "status": "reference_unavailable",
            "dataset": DATASET,
            "revision": REVISION,
            "downloaded_bytes": downloaded,
            "records": len(records),
            "records_with_public_solution": 0,
            "scope": "The public release contains empty solution/test arrays. No answers were invented and no learner was evaluated.",
        }
        _write(output / "preparation-summary.json", summary)
        return summary
    with (output / "evaluator" / "record-fields.json").open("x") as stream:
        json.dump(
            {"fields": sorted({key for record in records for key in record})},
            stream,
            indent=2,
        )
    grouped = {name: [] for name in db_order}
    excluded = Counter()
    for record in records:
        db_id = _task_value(record, ("db_id", "database_id", "db_name"))
        if db_id not in grouped:
            continue
        sql = _task_value(record, ("SQL", "sql", "query", "SQL_annotated"))
        task_id = str(
            _task_value(record, ("question_id", "task_id", "instance_id", "id"))
        )
        if type(sql) is not str or not select_only(sql):
            excluded[db_id + ":not_select_prefix"] += 1
            continue
        grouped[db_id].append((task_id, record, sql))
    for rows in grouped.values():
        rows.sort(key=lambda row: _order(row[0]))
    selection = {}
    course_ids = {}
    for split, names in splits.items():
        quota = freeze["per_database_quota"][split]
        rows = []
        for db_id in names:
            selected = grouped[db_id][:quota]
            selection[db_id] = [row[0] for row in selected]
            rows.extend((db_id, *row) for row in selected)
        rows.sort(key=lambda row: _order(split + ":" + row[1]))
        course_ids[split] = [row[1] for row in rows]
    # The exact selected IDs are committed before running reference queries.
    _write(
        output / "selected-ids.json",
        {
            "source_selection_sha256": _hash(
                (output / "selection-freeze.json").read_bytes()
            ),
            "selection": selection,
            "course_ids": course_ids,
            "eligible_counts": {k: len(v) for k, v in grouped.items()},
            "excluded_prefix_counts": dict(excluded),
        },
    )
    workspace = AnalystWorkspace(
        output / "registry.json",
        vm_steps=10000000,
        row_limit=10000,
        byte_limit=1024 * 1024,
        deadline_ms=30000,
    )
    oracle, summaries = {}, {}
    for split, names in splits.items():
        learner = []
        quota = freeze["per_database_quota"][split]
        for db_id in names:
            for task_id, record, sql in grouped[db_id][:quota]:
                question = _task_value(
                    record, ("question", "query_prompt", "user_query")
                )
                if type(question) is not str or not question.strip():
                    raise ValueError("Selected question is malformed")
                receipt = workspace.call(db_id, "query", {"sql": sql})
                oracle[task_id] = {
                    "task_id": task_id,
                    "db_id": db_id,
                    "split": split,
                    "reference_sql": sql,
                    "ordered": ordered_query(sql),
                    "reference_receipt": receipt,
                    "expected_rows": receipt["rows"] if receipt["complete"] else None,
                    "column_count": len(receipt["columns"])
                    if receipt["complete"]
                    else None,
                    "gradeable": receipt["complete"],
                }
                learner.append(
                    {
                        "id": task_id,
                        "db_id": db_id,
                        "question": question,
                        "split": split,
                        "dataset_revision": REVISION,
                        "supplied_knowledge": ["business", "columns", "schema"],
                    }
                )
        learner.sort(key=lambda row: _order(split + ":" + row["id"]))
        location = (
            output
            / ("sealed" if split == "reserved" else "learner")
            / (split + ".json")
        )
        _write(
            location,
            {
                "schema": "nuria.analyst-course.v1",
                "dataset": DATASET,
                "revision": REVISION,
                "split": split,
                "tasks": learner,
            },
            private=True,
        )
        summaries[split] = {
            "tasks": len(learner),
            "database_ids": names,
            "question_file_sha256": _hash(location.read_bytes()),
            "gradeable": sum(oracle[row["id"]]["gradeable"] for row in learner),
        }
    _write(
        output / "evaluator" / "oracle.json",
        {"schema": "nuria.analyst-oracle.v1", "revision": REVISION, "tasks": oracle},
        private=True,
    )
    _write(
        output / "source-manifest.json",
        {
            "dataset": DATASET,
            "revision": REVISION,
            "license": LICENSE,
            "downloaded_bytes": downloaded,
            "files": source_records,
        },
    )
    summary = {
        "schema": "nuria.analyst-preparation.v1",
        "dataset": DATASET,
        "revision": REVISION,
        "license": LICENSE,
        "downloaded_bytes": downloaded,
        "splits": summaries,
        "reference_execution_errors": {
            task_id: value["reference_receipt"]["status"]
            for task_id, value in oracle.items()
            if not value["gradeable"]
        },
        "reserved_questions_opened_by_agent": False,
        "scope": freeze["scope"],
    }
    _write(output / "preparation-summary.json", summary)
    return summary


class RangeArchive(io.RawIOBase):
    """Seekable public archive reads with exact HTTP range validation."""

    def __init__(self):
        self.position = 0
        self.downloaded = 0
        request = urllib.request.Request(
            SPIDER_URL,
            headers={"Range": "bytes=0-0", "User-Agent": "Nuria-benchmark-preparation"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            content_range = response.headers.get("Content-Range", "")
            match = re.fullmatch(r"bytes 0-0/(\d+)", content_range)
            if response.status != 206 or match is None or response.read(2) != b"P":
                raise ValueError(
                    "Canonical archive does not provide validated byte ranges"
                )
            self.size = int(match[1])
            if not 1 <= self.size <= 2 * 1024**3:
                raise ValueError("Canonical archive exceeds its declared bound")
            self.etag = response.headers.get("ETag")
            self.modified = response.headers.get("Last-Modified")

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = (
            offset
            if whence == 0
            else self.position + offset
            if whence == 1
            else self.size + offset
        )
        if not 0 <= position <= self.size:
            raise ValueError("Archive seek exceeds source bounds")
        self.position = position
        return position

    def read(self, amount=-1):
        amount = (
            self.size - self.position
            if amount < 0
            else min(amount, self.size - self.position)
        )
        if not amount:
            return b""
        if amount > 8 * 1024**2 or self.downloaded + amount > MAX_TOTAL_BYTES:
            raise ValueError("Selective archive retrieval exceeds its byte bound")
        start, end = self.position, self.position + amount - 1
        request = urllib.request.Request(
            SPIDER_URL,
            headers={
                "Range": f"bytes={start}-{end}",
                "User-Agent": "Nuria-benchmark-preparation",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            if (
                response.status != 206
                or response.headers.get("Content-Range")
                != f"bytes {start}-{end}/{self.size}"
            ):
                raise ValueError("Archive byte-range response differs")
            if (
                response.headers.get("ETag") != self.etag
                or response.headers.get("Last-Modified") != self.modified
            ):
                raise ValueError(
                    "Canonical archive generation changed during retrieval"
                )
            payload = response.read(amount + 1)
        if len(payload) != amount:
            raise ValueError("Canonical archive byte range is incomplete")
        self.position += amount
        self.downloaded += amount
        return payload


def prepare_spider(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    for folder in ("source", "learner", "evaluator", "sealed"):
        (output / folder).mkdir()
    request = urllib.request.Request(
        SPIDER_METADATA_URL, headers={"User-Agent": "Nuria-benchmark-preparation"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        metadata = json.load(response)
    frequencies = next(
        value["column_statistics"]["frequencies"]
        for value in metadata["statistics"]
        if value["column_name"] == "db_id"
    )
    source = RangeArchive()
    archive = zipfile.ZipFile(source)
    files = archive.infolist()
    database_members = {}
    for entry in files:
        name = Path(entry.filename)
        if (
            name.suffix == ".sqlite"
            and name.stem == name.parent.name
            and name.stem in frequencies
            and len(name.parts) >= 3
            and name.parts[-3] == "database"
        ):
            if name.stem in database_members:
                raise ValueError("Canonical archive has ambiguous database members")
            database_members[name.stem] = entry
    eligible = [
        name
        for name, count in frequencies.items()
        if count >= 6
        and name in database_members
        and database_members[name].file_size <= 5 * 1024**2
    ]
    db_order = sorted(eligible, key=lambda value: _order("spider:" + value))[:6]
    if len(db_order) != 6:
        raise ValueError("Fewer than six eligible canonical development databases")
    splits = {
        "development": db_order[:2],
        "teaching": db_order[2:4],
        "reserved": db_order[4:],
    }
    dev_members = [entry for entry in files if Path(entry.filename).name == "dev.json"]
    if len(dev_members) != 1:
        raise ValueError("Canonical archive has no unique development split")
    freeze = {
        "schema": "nuria.spider-source-selection.v1",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": "Spider 1.0",
        "license": LICENSE,
        "source_url": SPIDER_HOME,
        "canonical_archive_url": SPIDER_URL,
        "archive_size": source.size,
        "archive_etag": source.etag,
        "archive_last_modified": source.modified,
        "metadata_url": SPIDER_METADATA_URL,
        "metadata_sha256": _hash(_json(metadata).encode()),
        "seed": SEED,
        "database_selection": "First six SHA256(seed+':spider:'+db_id)-ordered official development DB IDs with >=6 metadata-counted questions and <=5MiB SQLite member; no difficulty or execution-result selection",
        "database_order": db_order,
        "splits": splits,
        "eligible_database_ids": sorted(eligible),
        "members": {
            name: {
                "archive_path": database_members[name].filename,
                "bytes": database_members[name].file_size,
                "crc32": database_members[name].CRC,
            }
            for name in db_order
        },
        "questions_member": {
            "archive_path": dev_members[0].filename,
            "bytes": dev_members[0].file_size,
            "crc32": dev_members[0].CRC,
        },
        "task_selection": "First hash-ordered syntactically SELECT/WITH-eligible original development row indices per selected DB; no result-dependent replacement",
        "per_database_quota": {"development": 3, "teaching": 4, "reserved": 6},
        "reserved_policy": "Question bodies remain sealed until model and policy freeze",
        "contamination_limit": "Spider is public since 2018 and may occur in backbone pretraining. This is a frozen agent comparison, not new backbone knowledge or a leaderboard submission.",
        "grading_scope": "Strict finite result rows, duplicates, NULLs, column count and top-level ordered output. Not the complete official Spider test-suite metric or universal SQL equivalence.",
        "preparation_source_sha256": _hash(Path(__file__).read_bytes()),
    }
    _write(output / "selection-freeze.json", freeze)
    _write(output / "metadata-only.json", metadata)
    # No question or database contents have been read before the freeze above.
    payload = archive.read(dev_members[0])
    tasks_path = output / "evaluator" / "dev-original.json"
    tasks_path.write_bytes(payload)
    os.chmod(tasks_path, 0o600)
    tasks = json.loads(payload)
    revision = "sha256:" + _hash(payload)
    registry = {
        "schema": SCHEMA,
        "dataset": "Spider 1.0",
        "revision": revision,
        "license": LICENSE,
        "attribution": "Yu et al. 2018, Yale Spider; CC BY-SA 4.0; canonical Yale distribution",
        "databases": {},
    }
    source_records = {
        "dev-original.json": {"bytes": len(payload), "sha256": _hash(payload)}
    }
    for db_id in db_order:
        directory = output / "source" / db_id
        directory.mkdir()
        database = directory / (db_id + ".sqlite")
        database.write_bytes(archive.read(database_members[db_id]))
        connection = sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True)
        schema = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        columns = {
            row[0]: connection.execute(
                'PRAGMA table_xinfo("' + row[0].replace('"', '""') + '")'
            ).fetchall()
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        connection.close()
        documents = {}
        for section, value in (
            ("schema", "\n\n".join(row[0] for row in schema if row[0])),
            ("columns", _json(columns)),
            (
                "business",
                "Spider 1.0 provides no additional task-specific external business knowledge. Use the database schema and question.",
            ),
        ):
            path = directory / (section + ".txt")
            path.write_text(value)
            documents[section] = {
                "path": str(path.relative_to(output)),
                "sha256": _hash(path.read_bytes()),
            }
        registry["databases"][db_id] = {
            "database": {
                "path": str(database.relative_to(output)),
                "sha256": _hash(database.read_bytes()),
            },
            "documents": documents,
        }
        source_records[db_id] = {
            "archive_member": database_members[db_id].filename,
            "bytes": database.stat().st_size,
            "sha256": _hash(database.read_bytes()),
        }
    _write(output / "registry.json", registry)
    indexed = [
        (f"spider-dev-{index:04d}", task)
        for index, task in enumerate(tasks)
        if task["db_id"] in db_order and select_only(task["query"])
    ]
    selected = {}
    for split, names in splits.items():
        quota = freeze["per_database_quota"][split]
        for name in names:
            selected[name] = sorted(
                [
                    (identity, task)
                    for identity, task in indexed
                    if task["db_id"] == name
                ],
                key=lambda row: _order("spider-task:" + row[0]),
            )[:quota]
    _write(
        output / "selected-ids.json",
        {
            "source_selection_sha256": _hash(
                (output / "selection-freeze.json").read_bytes()
            ),
            "selection": {
                name: [row[0] for row in rows] for name, rows in selected.items()
            },
        },
    )
    workspace = AnalystWorkspace(
        output / "registry.json",
        vm_steps=10000000,
        row_limit=10000,
        byte_limit=1024 * 1024,
        deadline_ms=30000,
    )
    oracle, summaries = {}, {}
    for split, names in splits.items():
        learner = []
        for name in names:
            for task_id, task in selected[name]:
                receipt = workspace.call(name, "query", {"sql": task["query"]})
                oracle[task_id] = {
                    "task_id": task_id,
                    "db_id": name,
                    "split": split,
                    "reference_sql": task["query"],
                    "ordered": ordered_query(task["query"]),
                    "reference_receipt": receipt,
                    "expected_rows": receipt["rows"] if receipt["complete"] else None,
                    "column_count": len(receipt["columns"])
                    if receipt["complete"]
                    else None,
                    "gradeable": receipt["complete"],
                }
                learner.append(
                    {
                        "id": task_id,
                        "db_id": name,
                        "question": task["question"],
                        "split": split,
                        "dataset_revision": revision,
                        "supplied_knowledge": ["schema", "columns", "business"],
                    }
                )
        learner.sort(key=lambda row: _order("spider-course:" + row["id"]))
        location = (
            output
            / ("sealed" if split == "reserved" else "learner")
            / (split + ".json")
        )
        _write(
            location,
            {
                "schema": "nuria.analyst-course.v1",
                "dataset": "Spider 1.0",
                "revision": revision,
                "split": split,
                "tasks": learner,
            },
            private=True,
        )
        summaries[split] = {
            "tasks": len(learner),
            "database_ids": names,
            "question_file_sha256": _hash(location.read_bytes()),
            "gradeable": sum(oracle[row["id"]]["gradeable"] for row in learner),
        }
    _write(
        output / "evaluator" / "oracle.json",
        {"schema": "nuria.analyst-oracle.v1", "revision": revision, "tasks": oracle},
        private=True,
    )
    _write(
        output / "source-manifest.json",
        {
            "dataset": "Spider 1.0",
            "revision": revision,
            "license": LICENSE,
            "range_download_bytes": source.downloaded,
            "archive_bytes": source.size,
            "files": source_records,
        },
    )
    summary = {
        "schema": "nuria.analyst-preparation.v1",
        "dataset": "Spider 1.0",
        "revision": revision,
        "license": LICENSE,
        "range_download_bytes": source.downloaded,
        "splits": summaries,
        "reference_execution_errors": {
            key: value["reference_receipt"]["status"]
            for key, value in oracle.items()
            if not value["gradeable"]
        },
        "contamination_limit": freeze["contamination_limit"],
        "grading_scope": freeze["grading_scope"],
    }
    _write(output / "preparation-summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="A new private preparation directory")
    parser.add_argument("--source", choices=("spider", "livesql"), default="spider")
    args = parser.parse_args()
    print(
        _json(
            prepare_spider(args.output)
            if args.source == "spider"
            else prepare(args.output)
        )
    )


if __name__ == "__main__":
    main()

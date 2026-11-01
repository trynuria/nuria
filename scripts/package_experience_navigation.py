"""Export a completed experience course with exact records and portable proof.

The exporter is read-only with respect to work journals. It refuses partial or
ungraded courses, preserves rejected programs, and never reads credentials.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path

from cognition.experience_navigation import METHODS
from cognition.outcome_worker import identity
from scripts.evaluate_experience_navigation import read_rows
from scripts.verify_experience_navigation import verify

PRIVATE_TEXT = re.compile(
    r"/Users/[^/\s]+|sk-proj-[A-Za-z0-9_-]{20,}|"
    r"privy_app_secret_[A-Za-z0-9_-]{20,}|wallet-auth:[A-Za-z0-9+/=]{20,}|"
    r"(?:api[_-]?key|authorization|password)\s*[=:]\s*[\"']?[A-Za-z0-9_./+:-]{24,}",
    re.IGNORECASE,
)


def read_store(path):
    """Take one consistent read-only snapshot and check its original journal."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as database:
        database.execute("BEGIN")
        jobs = database.execute("SELECT id,contract,state,body FROM jobs").fetchall()
        events = database.execute(
            "SELECT seq,previous,sha256,body FROM events ORDER BY seq"
        ).fetchall()
    previous, expected, contracts = "0" * 64, {}, {}
    journal = []
    for seq, prior, sha, body in events:
        event = json.loads(body)
        if prior != previous or sha != identity({"previous": prior, "event": event}):
            raise ValueError("Outcome journal changed")
        if event["event"] == "begin":
            contracts[event["job"]] = event["contract_sha256"]
        if event["event"] in ("outcome", "acceptance"):
            expected[event["job"]] = event["result_sha256"]
        journal.append({"seq": seq, "previous": prior, "sha256": sha, "event": event})
        previous = sha
    records = []
    for job, contract, state, body in jobs:
        if state != "finished":
            raise ValueError("Finish every enrolled proposal before export")
        record = json.loads(body)
        if expected.get(job) != identity(record) or contracts.get(job) != contract:
            raise ValueError("Stored outcome differs from its original journal")
        if record.get("job_id") != job or type(record.get("accepted")) is not bool:
            raise ValueError("Settle every terminal outcome before export")
        records.append(record)
    return sorted(records, key=lambda row: row["job_id"]), journal


def check_public(text):
    if PRIVATE_TEXT.search(text):
        raise ValueError("Export contains a private path or credential-like text")


def check_archive(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if (
            len(entries) > 64
            or sum(item.file_size for item in entries) > 8 * 1024 * 1024
        ):
            raise ValueError("Source archive exceeds its inspection allowance")
        for item in entries:
            if item.is_dir():
                continue
            check_public(archive.read(item).decode())


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def compressed_rows(rows):
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    return gzip.compress(text.encode(), mtime=0)


def transcripts(root):
    result = []
    for file in sorted(
        (root / "provider-transcripts").glob("*.json"), key=lambda p: int(p.stem)
    ):
        item = json.loads(file.read_text())
        response = item["response"]
        result.append(
            {
                "request": item["request"],
                "response": {
                    key: response[key]
                    for key in (
                        "content",
                        "usage",
                        "reported_model",
                        "configured_model",
                        "accounted_cost_micro_usd",
                        "wall_seconds",
                    )
                    if key in response
                },
            }
        )
    return result


def package(root, output, *, maximum_bytes=16 * 1024 * 1024):
    root, output = Path(root), Path(output)
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= 64 * 1024 * 1024:
        raise ValueError("A finite public export byte allowance is required")
    if not (root / "held-out-results.json").is_file():
        raise ValueError("Complete terminal grading before export")
    enrollment = json.loads((root / "test-enrollment.json").read_text())
    expected = set(enrollment["task_ids"])
    records, journal = [], []
    for method in METHODS:
        rows, events = read_store(root / ("test-" + method + ".sqlite"))
        if (
            len(rows) != len(expected)
            or {row["task_id"] for row in rows} != expected
            or any(row["method"] != method for row in rows)
        ):
            raise ValueError("A method omits or duplicates an enrolled task")
        records.extend(rows)
        journal.extend({"method": method, **event} for event in events)
    files = {}
    for name in ("tasks.jsonl.gz", "upstream-tasks.jsonl.gz"):
        rows = read_rows(root / name)
        files[name] = compressed_rows(rows)
    history = json.loads((root / "test-experience.json").read_text())
    files["test-experience.jsonl.gz"] = compressed_rows(history)
    files["executions.jsonl.gz"] = compressed_rows(records)
    files["journal-events.jsonl.gz"] = compressed_rows(journal)
    files["provider-text.jsonl.gz"] = compressed_rows(transcripts(root))
    for name in (
        "test-enrollment.json",
        "held-out-results.json",
        "corrections.json",
        "method-amendment-v3.json",
        "protocol.json",
        "development-results.json",
        "reference-validity-before-models.json",
        "preflight-environment-amendment.json",
        "execution-source-before-test.json",
        "execution-source-before-test.zip",
        "public-primary-freeze.json",
    ):
        path = root / name
        if path.exists():
            files[name] = path.read_bytes()
    for name, data in files.items():
        if name.endswith(".zip"):
            check_archive(data)
            continue
        text = gzip.decompress(data).decode() if name.endswith(".gz") else data.decode()
        check_public(text)
    total = sum(map(len, files.values()))
    if total > maximum_bytes:
        raise ValueError("Public export exceeds its retained-byte allowance")
    if output.exists():
        raise ValueError(
            "Use a fresh export directory; previous artifacts are preserved"
        )
    output.mkdir(parents=True)
    for name, data in files.items():
        (output / name).write_bytes(data)
    checked = verify(output)
    report = {
        "schema": "nuria.navigation-export.v1",
        "tasks_per_method": len(expected),
        "programs": len(records),
        "rejected_programs": sum(row["accepted"] is False for row in records),
        "journal_events": len(journal),
        "payload_bytes": total,
        "verification": checked,
        "files": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(files.items())
        },
        "model_calls": 0,
        "native_program_replays": 0,
        "provider_cost_basis": "Configured token accounting, not an independently reconciled provider invoice",
    }
    (output / "export.json").write_bytes(json_bytes(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.root, args.output), sort_keys=True))


if __name__ == "__main__":
    main()

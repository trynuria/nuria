"""Package explicitly retained analytical evidence without operational paths.

The input directory is a private run collection supplied by the host. Selected
primary rows are copied by frozen byte span. Only twelve explicitly selected
secondary oracle values are decoded. This helper performs no inference.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path


def hashed(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        + "\n"
    ).encode()


def selected_value(data, identity):
    matches = list(re.finditer(rb'^    "' + identity.encode() + rb'":\s*', data, re.M))
    if len(matches) != 1:
        raise ValueError("Selected oracle identity is not unique")
    start = matches[0].end()
    depth, quoted, escaped = 0, False, False
    for offset in range(start, len(data)):
        char = data[offset]
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (123, 91):
            depth += 1
        elif char in (125, 93):
            depth -= 1
            if depth == 0:
                result = data[start : offset + 1]
                json.loads(result)
                return result
    raise ValueError("Selected oracle value is incomplete")


def build(phase, repository, output):
    phase, repository, output = map(Path, (phase, repository, output))
    files = {}

    def add(name, data):
        if name in files:
            raise ValueError("Duplicate package member")
        files[name] = data

    private_bytes = (phase / "course-02/protocol-private.json").read_bytes()
    primary_private = json.loads(private_bytes)
    primary_public_bytes = Path(primary_private["public_protocol"]).read_bytes()
    if hashed(primary_public_bytes) != primary_private["public_protocol_sha256"]:
        raise ValueError("Primary public protocol changed")
    add("protocols/primary-public.json", primary_public_bytes)
    primary_run = phase / "trial-02-run-01"
    report = json.loads((primary_run / "report.json").read_bytes())
    if report["protocol_sha256"] != hashed(private_bytes):
        raise ValueError("Primary report protocol binding changed")
    for path in sorted(primary_run.iterdir()):
        if path.suffix == ".json":
            add("primary/" + path.name, path.read_bytes())
    for path in sorted((phase / "frozen-source-02").rglob("*.py")):
        relative = path.relative_to(phase / "frozen-source-02").as_posix()
        if hashed(path.read_bytes()) != primary_private["source_files"][relative]:
            raise ValueError("Frozen primary source differs")
        add("frozen-primary/" + relative, path.read_bytes())
    add(
        "frozen-primary/manifest.json",
        (phase / "frozen-source-02/manifest.json").read_bytes(),
    )
    source = Path(primary_private["dataset"]).read_bytes()
    if hashed(source) != primary_private["dataset_sha256"]:
        raise ValueError("Original primary dataset changed")
    attempted_tasks = {row["task_id"] for row in report["rows"] if row["attempted"]}
    selected_rows = []
    for split, rows in primary_private["selected"].items():
        for row in rows:
            if row["task_id"] not in attempted_tasks:
                continue
            fragment = source[row["start"] : row["end"]]
            if hashed(fragment) != row["row_sha256"]:
                raise ValueError("Selected primary row differs")
            # Decoding is limited to already attempted identities.
            if json.loads(fragment)["db_id"] != row["db_id"]:
                raise ValueError("Selected primary database differs")
            member = "gold/primary/" + row["task_id"] + ".json"
            add(member, fragment)
            selected_rows.append(
                {
                    "task_id": row["task_id"],
                    "db_id": row["db_id"],
                    "phase": split,
                    "member": member,
                    "sha256": row["row_sha256"],
                }
            )
    registry_bytes = Path(primary_private["registry"]).read_bytes()
    if hashed(registry_bytes) != primary_private["registry_sha256"]:
        raise ValueError("Registry identity changed")
    add("inputs/registry.json", registry_bytes)
    registry = json.loads(registry_bytes)
    for record in registry["databases"].values():
        for file in [record["database"], *record["documents"].values()]:
            data = (
                Path(primary_private["registry"]).parent / file["path"]
            ).read_bytes()
            if hashed(data) != file["sha256"]:
                raise ValueError("Registered source changed")
            add("inputs/" + file["path"], data)
    primary_inputs = {
        "original_private_protocol_sha256": hashed(private_bytes),
        "original_dataset_sha256": primary_private["dataset_sha256"],
        "selected_attempted_rows": selected_rows,
        "unexecuted_bodies_included": 0,
        "scope": "Operational paths are omitted. Selected raw rows were checked against the retained private freeze; that whole private file is referenced by hash, not reproduced.",
    }
    add("inputs/primary-projection.json", encoded(primary_inputs))
    for name in ("verdict-01.json", "receipt-01.json"):
        add(
            "independent-primary/" + name,
            (phase / "independent-primary-replay-01" / name).read_bytes(),
        )

    secondary_private_bytes = (
        phase / "model-substitution-01/protocol-private.json"
    ).read_bytes()
    secondary_private = json.loads(secondary_private_bytes)
    secondary_public_bytes = Path(secondary_private["public_protocol"]).read_bytes()
    if hashed(secondary_public_bytes) != secondary_private["public_protocol_sha256"]:
        raise ValueError("Secondary public protocol changed")
    secondary_public = json.loads(secondary_public_bytes)
    add("protocols/secondary-public.json", secondary_public_bytes)
    for path in sorted((phase / "model-substitution-run-01").iterdir()):
        if path.suffix == ".json":
            add("secondary/" + path.name, path.read_bytes())
    for relative, expected in secondary_public["source_files"].items():
        data = (repository / relative).read_bytes()
        if hashed(data) != expected:
            historical = phase / "frozen-source-02" / relative
            if not historical.is_file() or hashed(historical.read_bytes()) != expected:
                raise ValueError(
                    "Secondary source is not retained at its frozen identity"
                )
            data = historical.read_bytes()
        add("frozen-secondary/" + relative, data)
    for name, file in secondary_private["inputs"].items():
        if hashed(Path(file["path"]).read_bytes()) != file["sha256"]:
            raise ValueError("Secondary frozen input changed")
    reserved_bytes = Path(
        secondary_private["inputs"]["reserved_course"]["path"]
    ).read_bytes()
    reserved = json.loads(reserved_bytes)
    if {row["id"] for row in reserved["tasks"]} != set(
        secondary_public["selected_task_ids"]
    ):
        raise ValueError(
            "The copied secondary course is not exactly its twelve selected tasks"
        )
    add("inputs/secondary-reserved.json", reserved_bytes)
    oracle = Path(secondary_private["inputs"]["oracle"]["path"]).read_bytes()
    oracle_rows = []
    for task_id in secondary_public["selected_task_ids"]:
        data = selected_value(oracle, task_id)
        member = "gold/secondary/" + task_id + ".json"
        add(member, data)
        oracle_rows.append(
            {"task_id": task_id, "member": member, "sha256": hashed(data)}
        )
    add(
        "inputs/secondary-projection.json",
        encoded(
            {
                "original_private_protocol_sha256": hashed(secondary_private_bytes),
                "original_oracle_sha256": secondary_private["inputs"]["oracle"][
                    "sha256"
                ],
                "selected_oracle_rows": oracle_rows,
                "scope": "Only the twelve declared oracle entries are copied; the original larger oracle and operational protocol paths are not included.",
            }
        ),
    )
    add(
        "LICENSE-DATA.md",
        (
            "# Spider data attribution\n\n"
            "The included six SQLite databases and selected question/reference records are subsets of Spider 1.0 by Tao Yu, Rui Zhang, Kai Yang, Michihiro Yasunaga and the Spider authors (EMNLP 2018). The official project distributes this data under CC BY-SA 4.0.\n\n"
            "Project and attribution: https://yale-lily.github.io/spider\n"
            "Paper: https://aclanthology.org/D18-1425/\n"
            "License: https://creativecommons.org/licenses/by-sa/4.0/\n\n"
            "Database bytes and selected raw records are unmodified. Subsetting, generated schema documentation, execution traces and replay metadata are identified separately. Adaptations of the dataset are offered under the same CC BY-SA 4.0 terms. Code retains the repository license.\n"
        ).encode(),
    )
    for name, data in files.items():
        if any(
            marker in data
            for marker in (
                b"/Users/",
                b"/home/",
                b"/root/",
                b"wallet-auth:",
                b"privy_app_secret_",
                b"root@104.",
                b"root@185.",
            )
        ):
            raise ValueError("Private operational marker in package member: " + name)
        if re.search(rb"(?:sk-proj-|Bearer )[A-Za-z0-9_-]{20,}", data):
            raise ValueError("Credential marker in package member: " + name)
    inventory = [
        {"path": name, "bytes": len(data), "sha256": hashed(data)}
        for name, data in sorted(files.items())
    ]
    add(
        "manifest.json",
        encoded({"schema": "nuria.adaptive-reasoning-evidence.v1", "files": inventory}),
    )
    archive = io.BytesIO()
    with gzip.GzipFile(
        fileobj=archive, mode="wb", filename="", mtime=0, compresslevel=9
    ) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime, info.uid, info.gid = (
                    len(data),
                    0o644,
                    0,
                    0,
                    0,
                )
                tar.addfile(info, io.BytesIO(data))
    compressed = archive.getvalue()
    if (
        len(compressed) > 5 * 1024 * 1024
        or sum(map(len, files.values())) > 16 * 1024 * 1024
    ):
        raise ValueError("Evidence package exceeds its retained bounds")
    with (output / "evidence.tar.gz").open("xb") as stream:
        stream.write(compressed)
    package = {
        "schema": "nuria.adaptive-reasoning-package.v1",
        "archive": "evidence.tar.gz",
        "archive_bytes": len(compressed),
        "archive_sha256": hashed(compressed),
        "expanded_bytes": sum(map(len, files.values())),
        "member_count": len(files),
        "members": [
            {"path": name, "bytes": len(data), "sha256": hashed(data)}
            for name, data in sorted(files.items())
        ],
        "privacy_review": {
            "credential_and_private_path_markers_found": 0,
            "primary_unexecuted_bodies_included": 0,
            "raw_provider_responses_included": 0,
            "memory_sqlite_journals_included": 0,
        },
        "original_primary_private_protocol_sha256": hashed(private_bytes),
        "original_secondary_private_protocol_sha256": hashed(secondary_private_bytes),
        "extent": "Exact public protocol bytes, retained JSON job artifacts, declared metadata projections and public benchmark sources. Private operational protocols are referenced by original hash with paths omitted.",
    }
    with (output / "package.json").open("xb") as stream:
        stream.write(encoded(package))
    print(
        json.dumps(
            {
                key: package[key]
                for key in (
                    "archive_bytes",
                    "expanded_bytes",
                    "member_count",
                    "archive_sha256",
                )
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-root", required=True, type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.phase_root, args.repository, args.output)

"""Extract exact retained course bytes and invoke the frozen bounded verifier.

This runs no model, evaluator or skill executor. The package is a consistency
record, not an authenticated external execution witness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

PIN = "5130c4fd6aa3b992d55cc75038bcb18bf8d749e61d4652b553bb9454bab7c2e3"
SCHEMA = "nuria.analytical-skill-package.v1"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 128
MIN_FREE_BYTES = 16 * 1024**3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative_name(value):
    require(type(value) is str and 0 < len(value) <= 256, "Invalid member name")
    name = PurePosixPath(value)
    require(
        bool(name.parts)
        and not name.is_absolute()
        and name.as_posix() == value
        and all(part not in {"", ".", ".."} for part in name.parts)
        and "\\" not in value
        and ":" not in value
        and "\x00" not in value,
        "Unsafe member path",
    )
    return value


def read_package(package):
    package = Path(package).resolve()
    descriptor = package / "package.json"
    require(
        descriptor.is_file() and descriptor.stat().st_size <= 65536,
        "Package metadata exceeds its bound",
    )
    manifest = json.loads(descriptor.read_bytes())
    require(manifest.get("schema") == SCHEMA, "Unknown package schema")
    require(manifest.get("protocol_sha256") == PIN, "Package protocol pin changed")
    records = manifest.get("members")
    require(
        type(records) is list and 1 <= len(records) <= MAX_MEMBERS,
        "Member count exceeds its bound",
    )
    expected = {}
    total = 0
    for record in records:
        require(
            type(record) is dict and set(record) == {"path", "bytes", "sha256"},
            "Malformed member record",
        )
        name = relative_name(record["path"])
        require(name not in expected, "Duplicate manifest member")
        require(
            type(record["bytes"]) is int and 0 <= record["bytes"] <= MAX_EXPANDED_BYTES,
            "Invalid member size",
        )
        require(
            type(record["sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]),
            "Invalid member hash",
        )
        total += record["bytes"]
        expected[name] = record
    require(
        total <= MAX_EXPANDED_BYTES and manifest.get("expanded_bytes") == total,
        "Expanded evidence exceeds its bound",
    )
    archive = package / "evidence.zip"
    require(
        archive.is_file() and not archive.is_symlink(), "Archive is missing or linked"
    )
    require(archive.stat().st_size <= MAX_ARCHIVE_BYTES, "Archive exceeds its bound")
    data = archive.read_bytes()
    require(
        sha(data) == manifest.get("archive_sha256")
        and len(data) == manifest.get("archive_bytes"),
        "Archive identity changed",
    )
    contents = {}
    with zipfile.ZipFile(archive) as stream:
        members = stream.infolist()
        require(
            len(members) == len(expected) <= MAX_MEMBERS, "ZIP member coverage changed"
        )
        for info in members:
            name = relative_name(info.filename)
            require(
                name in expected and name not in contents,
                "Missing, extra or duplicate ZIP member",
            )
            mode = info.external_attr >> 16
            require(
                not info.is_dir() and stat.S_IFMT(mode) == stat.S_IFREG,
                "ZIP contains a linked or nonregular member",
            )
            require(
                not info.flag_bits & 1
                and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                "Unsupported encrypted or compressed member",
            )
            record = expected[name]
            require(info.file_size == record["bytes"], "ZIP member size changed")
            require(
                info.file_size <= max(1024, info.compress_size) * 1000,
                "ZIP compression ratio exceeds its bound",
            )
            with stream.open(info) as item:
                content = item.read(record["bytes"] + 1)
            require(
                len(content) == record["bytes"] and sha(content) == record["sha256"],
                "ZIP member bytes changed",
            )
            contents[name] = content
    require(set(contents) == set(expected), "ZIP omitted a declared member")
    require(
        sha(contents.get("prospective-protocol.json", b"")) == PIN,
        "Retained prospective protocol changed",
    )
    return manifest, contents


def extract_package(package, workspace):
    workspace = Path(workspace)
    require(
        not workspace.exists() and not workspace.is_symlink(),
        "Workspace already exists",
    )
    require(workspace.parent.is_dir(), "Workspace parent must exist")
    require(
        shutil.disk_usage(workspace.parent).free >= MIN_FREE_BYTES,
        "Free-disk floor is not met",
    )
    manifest, contents = read_package(package)
    workspace.mkdir(mode=0o700)
    for name, content in contents.items():
        file = workspace / name
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
    return manifest


def verify_package(package, repository, workspace):
    package, repository = Path(package).resolve(), Path(repository).resolve()
    manifest, contents = read_package(package)
    protocol = json.loads(contents["prospective-protocol.json"])
    identities = protocol["implementation_sha256"]
    require(
        type(identities) is dict and len(identities) == 7,
        "Frozen source inventory changed",
    )
    for name, identity in identities.items():
        name = relative_name(name)
        file = (repository / name).resolve()
        require(
            file.is_relative_to(repository)
            and file.is_file()
            and sha(file.read_bytes()) == identity,
            "Frozen repository source differs",
        )
    require(
        sha((repository / "research/analytical-skills/protocol-03.json").read_bytes())
        == PIN,
        "Repository protocol differs",
    )
    extract_package(package, workspace)
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.verify_analytical_skills",
            str(Path(workspace).resolve()),
            "--protocol-sha256",
            PIN,
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    require(
        result.returncode == 0,
        "Frozen course replay failed; extracted evidence is retained",
    )
    require(
        len(result.stdout.encode()) <= 65536 and len(result.stderr.encode()) <= 65536,
        "Verifier output exceeds its bound",
    )
    verdict = json.loads(result.stdout)
    require(
        verdict.get("passed") is True and verdict.get("phase") == "challenge",
        "Course replay did not pass",
    )
    expected = json.loads((package / "replay-result.json").read_bytes())
    require(
        verdict == expected,
        "Replayed verdict differs from the retained independent check",
    )
    require(
        manifest["expanded_bytes"] == sum(len(value) for value in contents.values()),
        "Evidence coverage differs",
    )
    return verdict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[3]
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is not None:
        require(
            not args.output.exists() and not args.output.is_symlink(),
            "Output already exists",
        )
    verdict = verify_package(args.package, args.repository, args.workspace)
    text = json.dumps(verdict, indent=2) + "\n"
    if args.output is not None:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(text)
    print(json.dumps(verdict))


if __name__ == "__main__":
    main()

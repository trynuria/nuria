"""Expand the retained workflow evidence into a new bounded workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath


def extract(evidence_dir, workspace):
    evidence_dir, workspace = Path(evidence_dir), Path(workspace)
    descriptor = json.loads((evidence_dir / "archive-manifest.json").read_bytes())
    if descriptor["archive"] != "workflow-evidence.tar.gz":
        raise ValueError("Unknown workflow archive")
    archive = evidence_dir / descriptor["archive"]
    if archive.stat().st_size > 4 * 1024**2:
        raise ValueError("Compressed workflow evidence exceeds its bound")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != descriptor["sha256"]:
        raise ValueError("Workflow archive identity differs")
    expected = descriptor["members"]
    if type(expected) is not dict or not 1 <= len(expected) <= 64:
        raise ValueError("Workflow evidence member count is invalid")
    workspace.mkdir(parents=True, exist_ok=False)
    seen, total = set(), 0
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream:
            name = PurePosixPath(member.name)
            if (
                not member.isfile()
                or member.name not in expected
                or member.name in seen
                or name.is_absolute()
                or any(part in ("", ".", "..") for part in name.parts)
                or "\\" in member.name
                or not 0 <= member.size <= 3 * 1024**2
            ):
                raise ValueError("Unsupported workflow evidence member")
            total += member.size
            if total > 8 * 1024**2:
                raise ValueError("Expanded workflow evidence exceeds its bound")
            reader = stream.extractfile(member)
            if reader is None:
                raise ValueError("Workflow evidence member cannot be read")
            with reader:
                content = reader.read(member.size + 1)
            if (
                len(content) != member.size
                or hashlib.sha256(content).hexdigest() != expected[member.name]
            ):
                raise ValueError("Workflow evidence member identity differs")
            target = workspace.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(content)
            seen.add(member.name)
    if seen != set(expected):
        raise ValueError("Workflow archive is incomplete")
    return workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    result = extract(args.evidence, args.workspace)
    print(json.dumps({"expanded": True, "workspace": str(result)}))


if __name__ == "__main__":
    main()

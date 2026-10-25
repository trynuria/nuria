"""Verify and reconstruct retained analytical evidence without executing it.

The destination must be new. ZIP members are copied through an explicit manifest;
no archive extraction routine, model, network request or pickle loader is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

SCHEMA = "nuria.grounded-comparison-publication.v1"
MAX_ARCHIVE = 20 * 1024 * 1024
MAX_ORIGINAL = 32 * 1024 * 1024
MAX_TOTAL = 500 * 1024 * 1024
MAX_CHUNK = 8 * 1024 * 1024
MAX_FILES = 10000


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_name(name):
    require(
        type(name) is str
        and name
        and "\\" not in name
        and "\0" not in name
        and ":" not in name
        and not any(ord(character) < 32 for character in name),
        "Invalid evidence path",
    )
    path = PurePosixPath(name)
    require(
        not path.is_absolute() and all(p not in ("", ".", "..") for p in path.parts),
        "Unsafe evidence path",
    )
    require(
        str(path) == name and len(name.encode()) <= 1024,
        "Noncanonical or oversized path",
    )
    return name


def direct_read(path, maximum):
    require(
        not path.is_symlink() and path.is_file() and path.stat().st_size <= maximum,
        "Missing, indirect or oversized evidence",
    )
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, "Evidence grew beyond its bound")
    return raw


def reconstruct(directory, output):
    directory, output = Path(directory), Path(output)
    require(
        not directory.is_symlink() and directory.is_dir(),
        "Evidence directory must be direct",
    )
    manifest = json.loads(direct_read(directory / "manifest.json", 16 * 1024 * 1024))
    require(manifest.get("schema") == SCHEMA, "Unsupported publication manifest")
    originals, archives = manifest["originals"], manifest["archives"]
    require(
        type(originals) is list and 1 <= len(originals) <= MAX_FILES,
        "Original file count exceeds bound",
    )
    require(
        type(archives) is list and 1 <= len(archives) <= MAX_FILES,
        "Archive count exceeds bound",
    )
    expected, total, names = {}, 0, set()
    for original in originals:
        name = safe_name(original["path"])
        require(
            name not in names and name.split("/")[0] in ("run", "evidence"),
            "Duplicate or unsupported original root",
        )
        names.add(name)
        require(
            type(original["bytes"]) is int and 0 <= original["bytes"] <= MAX_ORIGINAL,
            "Original size exceeds bound",
        )
        total += original["bytes"]
        require(total <= MAX_TOTAL, "Cumulative original size exceeds bound")
        require(
            type(original["chunks"]) is list and 1 <= len(original["chunks"]) <= 4,
            "Original chunk count exceeds bound",
        )
        offset = 0
        for chunk in original["chunks"]:
            archive, member = safe_name(chunk["archive"]), safe_name(chunk["member"])
            require(
                "/" not in archive
                and type(chunk["bytes"]) is int
                and 0 <= chunk["bytes"] <= MAX_CHUNK,
                "Archive or chunk size is invalid",
            )
            require(
                chunk["offset"] == offset and (archive, member) not in expected,
                "Chunk offsets or uniqueness differ",
            )
            offset += chunk["bytes"]
            expected[archive, member] = chunk
        require(offset == original["bytes"], "Chunk sizes do not cover original")
    loaded, archive_names, expanded = {}, set(), 0
    for record in archives:
        filename = safe_name(record["path"])
        require(
            "/" not in filename and filename not in archive_names,
            "Archive path is duplicate or indirect",
        )
        archive_names.add(filename)
        raw = direct_read(directory / filename, MAX_ARCHIVE)
        require(
            len(raw) == record["bytes"] and sha(raw) == record["sha256"],
            "Archive identity differs",
        )
        with zipfile.ZipFile(directory / filename) as archive:
            require(
                archive.namelist() == record["members"]
                and len(set(archive.namelist())) == len(archive.namelist()),
                "Archive members differ",
            )
            for info in archive.infolist():
                key = filename, safe_name(info.filename)
                require(
                    key in expected
                    and info.file_size == expected[key]["bytes"] <= MAX_CHUNK,
                    "Archive member size differs",
                )
                require(
                    not info.flag_bits & 1 and stat.S_ISREG(info.external_attr >> 16),
                    "Unsupported archive member type",
                )
                payload = archive.read(info)
                expanded += len(payload)
                require(
                    expanded <= MAX_TOTAL and sha(payload) == expected[key]["sha256"],
                    "Archive expansion or chunk identity differs",
                )
                loaded[key] = payload
    require(set(loaded) == set(expected), "Original archive coverage is incomplete")
    # Verify reconstructed bytes before creating the destination.
    for original in originals:
        digest = hashlib.sha256()
        for chunk in original["chunks"]:
            digest.update(loaded[chunk["archive"], chunk["member"]])
        require(digest.hexdigest() == original["sha256"], "Original checksum differs")
    require(not output.exists() and not output.is_symlink(), "Destination must be new")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for original in originals:
        path = output / safe_name(original["path"])
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            for chunk in original["chunks"]:
                stream.write(loaded[chunk["archive"], chunk["member"]])
        require(
            sha(direct_read(path, MAX_ORIGINAL)) == original["sha256"],
            "Written original differs",
        )
    return {
        "schema": SCHEMA,
        "original_files": len(originals),
        "archive_files": len(archives),
        "original_bytes": total,
        "all_originals_verified": True,
        "pickle_loaded": False,
        "scope": "Byte reconstruction only. To replay SQL and check recorded results, separately run the source repository's frozen comparison verifier.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, default=Path(__file__).resolve().parent
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(reconstruct(args.directory, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

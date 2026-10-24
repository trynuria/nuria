"""Retrieve pinned benchmark metadata into a new private staging directory.

No reserved specification is fetched. Optional development specifications are
restricted to the declared development families. Benchmark bodies are not copied
into the public research directory.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

REVISION = "8e3349ae8575c2615926b76170b287b6dddb5a73"
RAW = f"https://raw.githubusercontent.com/microsoft/prose-benchmarks/{REVISION}/"
DEVELOPMENT_FAMILIES = {"Currency", "Column", "Email"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
HASH = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")


def retrieve(url, maximum):
    request = urllib.request.Request(
        url, headers={"User-Agent": "Nuria-public-benchmark-inventory"}
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Pinned source exceeds its retrieval bound")
    return raw


def checked_blob(raw, entry):
    identity = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if len(raw) != entry["size"] or identity != entry["sha"]:
        raise ValueError("Pinned Git blob identity differs from source bytes")
    return raw


def development_path(name):
    if (
        type(name) is not str
        or not re.fullmatch(r"[A-Za-z]+\.\d{6}", name)
        or name.split(".")[0] not in DEVELOPMENT_FAMILIES
    ):
        raise ValueError(
            "Only declared development-family specifications may be fetched"
        )
    return f"Transformation.Text/{name}/spec.json"


def frozen_terms(protocol_raw, inventory_raw, expected_protocol_hash, record):
    """Validate source-access authority without opening a benchmark body."""
    if (
        not HASH.fullmatch(expected_protocol_hash or "")
        or hashlib.sha256(protocol_raw).hexdigest() != expected_protocol_hash
    ):
        raise ValueError("Frozen protocol commitment changed")
    protocol, inventory = json.loads(protocol_raw), json.loads(inventory_raw)
    inventory_hash = hashlib.sha256(inventory_raw).hexdigest()
    if (
        type(record) is not dict
        or type(record.get("commit")) is not str
        or not COMMIT.fullmatch(record["commit"])
        or record.get("protocol_sha256") != expected_protocol_hash
        or record.get("inventory_sha256") != inventory_hash
        or protocol.get("status") != "frozen_before_reserved_access"
        or protocol.get("execution_terms", {}).get("domain") != "external"
        or protocol["execution_terms"].get("phase") != "reserved"
        or protocol["execution_terms"].get("inventory_sha256") != inventory_hash
        or inventory.get("source", {}).get("commit") != REVISION
    ):
        raise ValueError(
            "Published freeze record, inventory or reserved terms disagree"
        )
    eligible = [
        entry
        for entry in inventory["tasks"]
        if entry["structurally_eligible"] and entry["split"] == "reserved"
    ]
    if (
        protocol["execution_terms"].get("task_ids")
        != [entry["task_id"] for entry in eligible]
        or not 1 <= len(eligible) <= 256
    ):
        raise ValueError(
            "Reserved task identities differ from the frozen metadata rule"
        )
    if (
        any(not 0 < entry["specification"]["bytes"] <= 100000 for entry in eligible)
        or sum(entry["specification"]["bytes"] for entry in eligible) > 512000
    ):
        raise ValueError("Reserved source retrieval exceeds its declared byte bounds")
    return protocol, inventory, eligible


def stage_reserved(
    destination, protocol_path, protocol_hash, freeze_record_path, inventory_path
):
    """Fetch only after matching the protocol and inventory at a public commit.

    The explicit record contains ``commit``, ``protocol_sha256`` and
    ``inventory_sha256``. Public raw-file reads independently check that record;
    a local declaration alone cannot unlock reserved source access.
    """
    protocol_raw, inventory_raw = (
        Path(protocol_path).read_bytes(),
        Path(inventory_path).read_bytes(),
    )
    if len(protocol_raw) > 256000 or len(inventory_raw) > 4_000_000:
        raise ValueError("Source access authority exceeds its input bound")
    record_raw = Path(freeze_record_path).read_bytes()
    if len(record_raw) > 16000:
        raise ValueError("Freeze record exceeds its input bound")
    record = json.loads(record_raw)
    protocol, inventory, entries = frozen_terms(
        protocol_raw, inventory_raw, protocol_hash, record
    )
    source_hashes = protocol.get("source_hashes", {})
    if (
        not source_hashes
        or "scripts/fetch_active_skill_sources.py" not in source_hashes
    ):
        raise ValueError("Freeze omits its source-access implementation")
    for filename, expected in source_hashes.items():
        relative = Path(filename)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not HASH.fullmatch(expected or "")
            or hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest()
            != expected
        ):
            raise ValueError("Local evaluated source differs from the published freeze")
    public = f"https://raw.githubusercontent.com/trynuria/nuria/{record['commit']}/research/active-skills/"
    if (
        retrieve(public + "protocol.json", 256000) != protocol_raw
        or retrieve(public + "source-inventory.json", 4_000_000) != inventory_raw
    ):
        raise ValueError(
            "Protocol and inventory are not published at the recorded commit"
        )
    destination = Path(destination)
    destination.mkdir(mode=0o700)

    def fetch(entry):
        specification = entry["specification"]
        path = specification["path"]
        if path != f"Transformation.Text/{entry['task_id']}/spec.json":
            raise ValueError("Reserved specification path changed")
        raw = checked_blob(
            retrieve(RAW + path, 100000),
            {"size": specification["bytes"], "sha": specification["git_blob_sha1"]},
        )
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
        return {
            "task_id": entry["task_id"],
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    receipt = {
        "revision": REVISION,
        "freeze_commit": record["commit"],
        "protocol_sha256": protocol_hash,
        "inventory_sha256": hashlib.sha256(inventory_raw).hexdigest(),
        "specifications": [],
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for entry in pool.map(fetch, entries):
            receipt["specifications"].append(entry)
    with (destination / "reserved-source-receipt.json").open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    return receipt


def stage(destination, development_specs=()):
    destination = Path(destination)
    destination.mkdir(mode=0o700)

    def write(name, raw):
        path = destination / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
        return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    tree_raw = retrieve(
        f"https://api.github.com/repos/microsoft/prose-benchmarks/git/trees/{REVISION}?recursive=1",
        4_000_000,
    )
    tree = json.loads(tree_raw)
    if tree.get("truncated") is not False:
        raise ValueError("Pinned repository tree is incomplete")
    entries = {entry["path"]: entry for entry in tree["tree"]}
    metadata = sorted(
        path
        for path in entries
        if path.startswith("Transformation.Text/") and path.endswith("/meta.json")
    )
    if len(metadata) != 354 or sum(entries[path]["size"] for path in metadata) != 26557:
        raise ValueError("Pinned metadata universe changed")
    receipt = {
        "revision": REVISION,
        "files": {"tree.json": write("tree.json", tree_raw)},
        "reserved_specifications_fetched": 0,
    }
    for path, maximum in (("README.md", 80_000), ("LICENSE", 300_000)):
        raw = checked_blob(retrieve(RAW + path, maximum), entries[path])
        receipt["files"][path] = write(path, raw)

    def fetch_metadata(path):
        raw = checked_blob(retrieve(RAW + path, 4096), entries[path])
        json.loads(raw)
        return path, write(path, raw)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for path, identity in pool.map(fetch_metadata, metadata):
            receipt["files"][path] = identity
    for name in sorted(set(development_specs)):
        path = development_path(name)
        if path not in entries:
            raise ValueError("Development task is absent from the pinned tree")
        raw = checked_blob(retrieve(RAW + path, 100_000), entries[path])
        receipt["files"][path] = write(path, raw)
    write("source-receipt.json", (json.dumps(receipt, indent=2) + "\n").encode())
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New private staging directory")
    parser.add_argument("--development-spec", action="append", default=[])
    parser.add_argument("--reserved-protocol", type=Path)
    parser.add_argument("--protocol-sha256")
    parser.add_argument("--freeze-record", type=Path)
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()
    for name in args.development_spec:
        development_path(name)
    if args.reserved_protocol:
        if args.development_spec or not all(
            (args.protocol_sha256, args.freeze_record, args.inventory)
        ):
            parser.error(
                "Reserved access needs a published freeze and cannot mix development fetches"
            )
        receipt = stage_reserved(
            args.destination,
            args.reserved_protocol,
            args.protocol_sha256,
            args.freeze_record,
            args.inventory,
        )
        print(
            json.dumps(
                {
                    "revision": REVISION,
                    "reserved_specifications_fetched": len(receipt["specifications"]),
                    "freeze_commit": receipt["freeze_commit"],
                }
            )
        )
        return
    if any((args.protocol_sha256, args.freeze_record, args.inventory)):
        parser.error("A reserved protocol is required for source-access authority")
    receipt = stage(args.destination, args.development_spec)
    print(
        json.dumps(
            {
                "revision": receipt["revision"],
                "files": len(receipt["files"]),
                "reserved_specifications_fetched": 0,
            }
        )
    )


if __name__ == "__main__":
    main()

"""Bounded snapshot cache and lossless updates for a public JSON resource.

The wire format is JSON Patch's add/remove/replace subset packed into tuples:
0=add, 1=remove, 2=replace. Both research encoders get the same packing.
Unknown or evicted bases and uneconomical patches receive the complete snapshot.
"""

import gzip
import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path


def compact_patch(operations):
    codes = {"add": 0, "remove": 1, "replace": 2}
    return [
        [codes[op["op"]], op["path"]] + ([op["value"]] if op["op"] != "remove" else [])
        for op in operations
    ]


def encode(value):
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


@dataclass
class Snapshot:
    version: str
    raw: bytes
    document: object
    compressed: bytes
    patches: dict = field(default_factory=dict)


class SnapshotCache:
    def __init__(self, path, make_patch, *, max_versions=3, max_bytes=8000000):
        self.path = Path(path)
        self.make_patch = make_patch
        self.max_versions = max(1, max_versions)
        self.max_bytes = max_bytes
        self.versions = OrderedDict()
        self.signature = None
        self.current = None
        self.lock = threading.Lock()

    def _load(self):
        stat = self.path.stat()
        signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        if self.current is not None and signature == self.signature:
            return self.current
        with self.path.open("rb") as stream:
            raw = stream.read(16000001)
        if len(raw) > 16000000:
            raise ValueError("Public snapshot exceeds the existing response bound")
        version = hashlib.sha256(raw).hexdigest()
        if self.current is not None and version == self.current.version:
            self.signature = signature
            return self.current
        document = json.loads(raw)
        snapshot = Snapshot(
            version, raw, document, gzip.compress(raw, compresslevel=3, mtime=0)
        )
        # Delta blobs belong only to the current target, not every cached base.
        for previous in self.versions.values():
            previous.patches.clear()
        self.versions[version] = snapshot
        self.versions.move_to_end(version)
        while len(self.versions) > 1 and (
            len(self.versions) > self.max_versions
            or sum(len(s.raw) for s in self.versions.values()) > self.max_bytes
        ):
            self.versions.popitem(last=False)
        self.current, self.signature = snapshot, signature
        return snapshot

    def response(self, *, base=None, compressed=False, if_none_match=None):
        """Return (status, bytes, metadata); never expose mutable cached objects."""
        with self.lock:
            current = self._load()
            etag = f'W/"{current.version}"'
            headers = {
                "X-Studio-Version": current.version,
                "X-Studio-Update": "full",
                "Content-Type": "application/json",
                "ETag": etag,
            }
            if (
                base is None
                and if_none_match
                and any(
                    token.strip() in ("*", etag, f'"{current.version}"')
                    for token in if_none_match.split(",")
                )
            ):
                return 304, b"", headers
            if base == current.version:
                headers.pop("ETag")
                headers["X-Studio-Update"] = "unchanged"
                return 204, b"", headers
            raw, zipped = current.raw, current.compressed
            if base in self.versions:
                if base not in current.patches:
                    operations = self.make_patch(
                        self.versions[base].document, current.document
                    )
                    patch = encode(compact_patch(operations))
                    zipped_patch = gzip.compress(patch, compresslevel=3, mtime=0)
                    # A delta must save bytes for both negotiated encodings.
                    current.patches[base] = (
                        (patch, zipped_patch)
                        if len(patch) < len(raw) and len(zipped_patch) < len(zipped)
                        else None
                    )
                if current.patches[base] is not None:
                    raw, zipped = current.patches[base]
                    headers.update(
                        {
                            "X-Studio-Update": "patch-v1",
                            "X-Studio-Base": base,
                            "Content-Type": "application/vnd.nuria.patch+json",
                        }
                    )
                    # This ETag identifies this delta representation, not the
                    # complete state. Version/base headers identify its meaning.
                    headers["ETag"] = f'W/"{hashlib.sha256(raw).hexdigest()}"'
            if compressed:
                headers["Content-Encoding"] = "gzip"
            return 200, zipped if compressed else raw, headers

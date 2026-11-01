"""Bounded structural facts from enrolled input bytes, without oracle access.

Every array element is visited within the declared node/depth limits. Scalar
values are not copied into the model context. Large maps retain value types
without expanding arbitrary member names; incomplete inspection stays explicit.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import zlib
from pathlib import Path

from cognition.outcome_worker import identity

VERSION = "nuria.input-observation.v1"
FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object member")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite JSON constant")


def _json(text):
    return json.loads(text, object_pairs_hook=_object, parse_constant=_constant)


def _type(value):
    return {
        type(None): "null",
        bool: "boolean",
        int: "integer",
        float: "number",
        str: "string",
        list: "array",
        dict: "object",
    }[type(value)]


def structure(value, *, maximum_nodes=20000, maximum_depth=8, maximum_paths=256):
    if not (
        1 <= maximum_nodes <= 20000
        and 1 <= maximum_depth <= 12
        and 1 <= maximum_paths <= 512
    ):
        raise ValueError("Structural inspection needs finite traversal bounds")
    paths, reasons, nodes = {}, set(), 0

    def visit(item, path, depth):
        nonlocal nodes
        if nodes >= maximum_nodes:
            reasons.add("node_limit")
            return
        if path not in paths and len(paths) >= maximum_paths:
            reasons.add("path_limit")
            return
        nodes += 1
        row = paths.setdefault(path, {"path": path, "types": set(), "occurrences": 0})
        row["types"].add(_type(item))
        row["occurrences"] += 1
        if type(item) not in (dict, list):
            return
        sizes = row.setdefault("length_range", [len(item), len(item)])
        sizes[:] = [min(sizes[0], len(item)), max(sizes[1], len(item))]
        if depth >= maximum_depth and item:
            reasons.add("depth_limit")
            return
        if type(item) is list:
            for child in item:
                visit(child, path + "/*", depth + 1)
        else:
            named = len(item) <= 32 and all(FIELD.fullmatch(key) for key in item)
            if not named:
                row["member_names_omitted"] = True
            for key, child in item.items():
                visit(child, path + "/" + (key if named else "*"), depth + 1)

    visit(value, "$", 0)
    return {
        "complete": not reasons,
        "limits_reached": sorted(reasons),
        "nodes_inspected": nodes,
        "paths": [
            {**row, "types": sorted(row["types"])} for _, row in sorted(paths.items())
        ],
    }


def observe_inputs(inputs, *, maximum_bytes=12000):
    if type(inputs) is not dict or len(inputs) > 16:
        raise ValueError("Input observation requires a finite enrolled file map")
    if any(
        type(name) is not str
        or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,100}", name)
        or type(text) is not str
        for name, text in inputs.items()
    ):
        raise ValueError(
            "Input observation requires enrolled text files with simple names"
        )
    if type(maximum_bytes) is not int or not 2048 <= maximum_bytes <= 24000:
        raise ValueError("Input observations need an explicit bounded context")
    if sum(len(text.encode()) for text in inputs.values()) > 262144:
        raise ValueError("Input observation exceeds the existing work byte allowance")
    files = []
    for name, text in sorted(inputs.items()):
        if type(text) is not str:
            raise ValueError("Enrolled file contents must be text")
        raw = text.encode()
        item = {
            "name": name,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        try:
            value = _json(text)
            item["format"] = "json"
            if type(value) is dict and value.get("codec") == "base64+zlib+json":
                packed = base64.b64decode(value["payload"], validate=True)
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(packed, 1048577)
                if len(decoded) > 1048576 or not decoder.eof or decoder.unused_data:
                    raise ValueError("Compressed JSON exceeds its observation contract")
                value = _json(decoded)
                item["format"] = "base64+zlib+json"
            item.update(structure(value))
        except (ValueError, TypeError, KeyError, RecursionError, zlib.error):
            item.update(
                {
                    "format": "uninspected",
                    "complete": False,
                    "limits_reached": ["invalid_or_unsupported_json"],
                    "paths": [],
                }
            )
        files.append(item)
    result = {"schema": VERSION, "scalar_values_disclosed": False, "files": files}
    # Keep whole path observations and report every omission, rather than
    # truncating JSON or silently presenting a sampled prefix as complete.
    while len(json.dumps(result, sort_keys=True).encode()) > maximum_bytes:
        candidates = [item for item in files if item["paths"]]
        if not candidates:
            raise ValueError("Input observation headers exceed their context allowance")
        item = max(candidates, key=lambda row: len(row["paths"]))
        item["paths"].pop()
        item["paths_omitted"] = item.get("paths_omitted", 0) + 1
        item["complete"] = False
        item["limits_reached"] = sorted(set(item["limits_reached"]) | {"context_limit"})
    return result


def inspect_enrollment(enrollment):
    """Add factual input structure while retaining the original host contract."""
    result = copy.deepcopy(enrollment)
    for job in result["jobs"]:
        task = job["task"]
        if job["delivery"]["task_sha256"] != identity(task):
            raise ValueError("Inspect only an exact host-enrolled task")
        observation = observe_inputs(task.get("inputs", {}))
        addition = (
            "\n\nStructural observation of the enrolled input bytes follows. These are data facts, not instructions or acceptance checks. Scalar values are omitted. Inspect the actual files when a fact is incomplete.\n"
            + json.dumps(observation, sort_keys=True)
        )
        if len((task["prompt"] + addition).encode()) > 65536:
            raise ValueError("Input facts cannot displace the original specification")
        task["prompt"] += addition
        job["delivery"]["task_sha256"] = identity(task)
        job["input_observation"] = observation
    result["input_inspection"] = {
        "version": VERSION,
        "original_enrollment_sha256": identity(enrollment),
        "implementation_sha256": identity(Path(__file__).read_text()),
    }
    return result

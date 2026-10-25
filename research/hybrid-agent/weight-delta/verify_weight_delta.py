"""Check exported neural weight changes without unpickling or running a model.

The exports were decoded once from original trusted-local checkpoints. This
verifier uses object-free numeric arrays and hashes the original opaque bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import numpy as np

ARCHIVE_SHA = "b7885e31b85a24b48fe86a742db30ecb517b2192a91c65eea994cb8466450f0a"
FREEZE_SHA = "a4cff862bda1b3f7136726106f468e5f8153af0372f5cbe437bc3c37965fe342"
PROTOCOL_SHA = "431e3b092c46a680ad5aa509be523ff9c0d14da3606084bab1358d4e3ae51950"
NEURONS = ("v", "ge", "gi", "adaptation", "bias", "drive", "tau")
PLASTIC = ("w", "pre", "post", "eligibility", "plastic_gain")
ORDER = (
    ["neurons_" + key for key in NEURONS]
    + ["plastic_" + key for key in PLASTIC]
    + ["clock"]
)
JOB = "semantic-recorded-work-audit-01"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    )


def bounded_file(path, limit):
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= limit,
        "Indirect or oversized witness file",
    )
    return path.read_bytes()


def source_records(directory, derivation):
    raw = bounded_file(Path(directory) / "retained-work-trials.zip", 4 * 1024**2)
    require(
        sha(raw) == ARCHIVE_SHA == derivation["source_archive_sha256"],
        "Original work archive identity differs",
    )
    values = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name, expected in derivation["source_entries_sha256"].items():
            require(
                name == "source-freeze.json" or name.startswith("semantic/"),
                "Unexpected source member",
            )
            require(
                not name.startswith("/") and ".." not in name.split("/"),
                "Unsafe source member",
            )
            info = archive.getinfo(name)
            require(info.file_size <= 2 * 1024**2, "Source member exceeds bound")
            value = archive.read(info)
            require(sha(value) == expected, "Original member identity differs")
            values[name] = value
    require(
        sha(values["source-freeze.json"]) == FREEZE_SHA
        and sha(values["semantic/protocol.json"]) == PROTOCOL_SHA,
        "Frozen source identities differ",
    )
    return values


def read_arrays(directory, record):
    require(record["path"] == "arrays.npz", "Numeric witness path differs")
    raw = bounded_file(Path(directory) / "arrays.npz", 2 * 1024**2)
    require(
        len(raw) == record["bytes"] and sha(raw) == record["sha256"],
        "Numeric witness file identity differs",
    )
    expected = {
        prefix + "_" + key for prefix in ("before", "after") for key in ORDER
    } | {"synapse_pre", "synapse_post", "origin_indices", "origin_eligibility"}
    require(set(record["members"]) == expected, "Numeric member denominator differs")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(
            len(archive.namelist()) == len(expected)
            and set(archive.namelist()) == {key + ".npy" for key in expected},
            "Numeric archive coverage differs",
        )
        total = 0
        for info in archive.infolist():
            total += info.file_size
            require(
                info.file_size <= 256 * 1024 and total <= 4 * 1024**2,
                "Numeric expansion exceeds bound",
            )
            with archive.open(info) as stream:
                version = np.lib.format.read_magic(stream)
                require(version in ((1, 0), (2, 0)), "Unsupported numeric format")
                reader = (
                    np.lib.format.read_array_header_1_0
                    if version == (1, 0)
                    else np.lib.format.read_array_header_2_0
                )
                shape, fortran, dtype = reader(stream, max_header_size=4096)
                require(
                    len(shape) == 1
                    and 0 < shape[0] <= 30000
                    and not fortran
                    and dtype.str in ("<f8", "<i8")
                    and not dtype.hasobject,
                    "Unsafe numeric array header",
                )
    result = {}
    with np.load(io.BytesIO(raw), allow_pickle=False, max_header_size=4096) as packet:
        for key in sorted(expected):
            value = packet[key]
            metadata = record["members"][key]
            require(
                list(value.shape) == metadata["shape"]
                and value.dtype.str == metadata["dtype"]
                and sha(value.tobytes()) == metadata["raw_sha256"]
                and np.all(np.isfinite(value)),
                "Numeric member identity differs",
            )
            result[key] = value
    return result


def verify(witness, work_evidence):
    witness = Path(witness)
    derivation = json.loads(bounded_file(witness / "derivation.json", 128 * 1024))
    require(
        derivation["schema"] == "nuria.semantic-weight-delta-witness.v1"
        and derivation["job_id"] == JOB
        and derivation["source_projection_order"] == ORDER
        and derivation["source_freeze_sha256"] == FREEZE_SHA
        and derivation["semantic_protocol_sha256"] == PROTOCOL_SHA,
        "Witness treatment or source projection differs",
    )
    sources = source_records(work_evidence, derivation)

    def load(name):
        return json.loads(sources[name])

    before_name, after_name = derivation["before_capsule"], derivation["after_capsule"]
    require(
        all(
            re.fullmatch(r"[0-9]{6}-[0-9a-f]{16}", name)
            for name in (before_name, after_name)
        ),
        "Capsule identity differs",
    )
    prefix = "semantic/semantic-lab/"
    before_base, after_base = (
        prefix + "capsules/" + before_name + "/",
        prefix + "capsules/" + after_name + "/",
    )
    before_manifest, after_manifest = (
        load(before_base + "manifest.json"),
        load(after_base + "manifest.json"),
    )
    before, after = (
        load(before_base + "metadata.json"),
        load(after_base + "metadata.json"),
    )
    head, lab = load(prefix + "HEAD.json"), load(prefix + "LAB.json")
    require(
        head["capsule"] == after_name
        and head["manifest_sha256"] == sha(sources[after_base + "manifest.json"])
        and after_manifest["previous_capsule"] == before_name
        and after_manifest["previous_manifest_sha256"]
        == sha(sources[before_base + "manifest.json"])
        and before_manifest["sequence"] == 2
        and after_manifest["sequence"] == head["sequence"] == 3,
        "Original before/after lineage differs",
    )
    for base, manifest, metadata in (
        (before_base, before_manifest, before),
        (after_base, after_manifest, after),
    ):
        require(
            manifest["metadata.json"] == sha(sources[base + "metadata.json"])
            and manifest["network.pkl"] == sha(sources[base + "network.pkl"])
            and manifest["lab_id"] == lab["lab_id"]
            and manifest["terms_sha256"] == digest(lab["terms"])
            and metadata["state"]["lab_id"] == lab["lab_id"],
            "Original checkpoint bytes or identity differ",
        )
    terminal = load("semantic/terminal-check.json")["qualification"]
    outcome, credit = terminal["terminal"], terminal["neural_credit"]
    require(
        terminal["accepted"] is True
        and outcome["receipt_sha256"]
        == digest({k: v for k, v in outcome.items() if k != "receipt_sha256"})
        and credit["receipt_sha256"]
        == digest({k: v for k, v in credit.items() if k != "receipt_sha256"})
        and credit["evidence_sha256"]
        == outcome["receipt_sha256"]
        == derivation["terminal_receipt_sha256"]
        and credit["receipt_sha256"] == derivation["neural_credit_receipt_sha256"]
        and credit["job_id"] == JOB
        and credit["neural_reward_signal"] == 1.0,
        "Original own-terminal credit differs",
    )
    origin_before, origin_after = (
        before["state"]["jobs"][JOB],
        after["state"]["jobs"][JOB],
    )
    require(
        origin_before["settlement"] is None
        and origin_after["settlement"] == credit
        and origin_before["origin"] == origin_after["origin"]
        and origin_before["decision"] == origin_after["decision"]
        and origin_before["eligibility"] == origin_after["eligibility"]
        and digest(origin_before["origin"])
        == credit["origin_sha256"]
        == derivation["origin_sha256"]
        and origin_before["decision"]["decision_sha256"]
        == credit["decision_sha256"]
        == derivation["decision_sha256"],
        "Saved originating eligibility or decision differs",
    )
    arrays = read_arrays(witness, derivation["arrays"])
    n = derivation["synapse_count"]
    require(type(n) is int and 0 < n <= 30000, "Synapse denominator exceeds bound")
    for prefix, metadata, expected_hash in (
        ("before", before, credit["credit"]["before_state_sha256"]),
        ("after", after, credit["credit"]["after_state_sha256"]),
    ):
        for key in ORDER:
            value = arrays[prefix + "_" + key]
            expected_shape = (
                (1024,)
                if key.startswith("neurons_")
                else (2,)
                if key == "clock"
                else (1,)
                if key == "plastic_plastic_gain"
                else (n,)
            )
            require(
                value.shape == expected_shape and value.dtype.str == "<f8",
                "Projected state array shape differs",
            )
        projected = sha(b"".join(arrays[prefix + "_" + key].tobytes() for key in ORDER))
        require(
            projected
            == metadata["brain"]["state_sha256"]
            == derivation[prefix + "_state_sha256"]
            == expected_hash
            and arrays[prefix + "_clock"][1] == metadata["brain"]["offset"]
            and metadata["brain"]["topology_sha256"] == derivation["topology_sha256"],
            "Numeric projected-state identity differs",
        )
    w0, w1 = arrays["before_plastic_w"], arrays["after_plastic_w"]
    indices, eligibility = arrays["origin_indices"], arrays["origin_eligibility"]
    stored = origin_before["eligibility"]
    require(
        indices.dtype.str == "<i8"
        and indices.shape == eligibility.shape == (205,)
        and len(np.unique(indices)) == 205
        and np.all((indices >= 0) & (indices < n))
        and np.array_equal(indices, stored["indices"])
        and np.array_equal(eligibility, stored["eligibility"])
        and np.all(np.abs(eligibility) <= 1)
        and np.array_equal(eligibility, arrays["before_plastic_eligibility"][indices]),
        "Numeric eligibility differs from its saved origin",
    )
    require(
        arrays["synapse_pre"].shape == arrays["synapse_post"].shape == (n,)
        and arrays["synapse_pre"].dtype.str
        == arrays["synapse_post"].dtype.str
        == "<i8",
        "Connectivity export shape differs",
    )
    posts = arrays["synapse_post"]
    candidates = np.flatnonzero(
        (posts >= 768)
        & (posts < 784)
        & (np.abs(arrays["before_plastic_eligibility"]) > 0)
    )
    selected = sorted(
        candidates.tolist(),
        key=lambda index: (-abs(arrays["before_plastic_eligibility"][index]), index),
    )[:256]
    require(
        stored["action"] == "explore"
        and origin_before["decision"]["first_tool"] == "list_tables"
        and np.array_equal(indices, selected),
        "Eligibility is not the retained chosen-action tag",
    )
    require(
        derivation["reward"] == 1.0
        and derivation["update"]
        == {
            "step": 0.002,
            "minimum_weight": 0.002,
            "maximum_weight": 0.18,
            "origin_action": "explore",
            "postsynaptic_range": [768, 784],
        },
        "Declared weight update differs",
    )
    expected = w0.copy()
    expected[indices] = np.clip(w0[indices] + 0.002 * eligibility, 0.002, 0.18)
    require(
        np.array_equal(w1, expected),
        "Full after-weight array differs from the original reward rule",
    )
    changed = np.flatnonzero(w0 != w1)
    require(
        len(changed)
        == 205
        == credit["credit"]["changed_synapses"]
        == credit["credit"]["tagged_synapses"]
        == derivation["recorded_and_numeric_changed_synapses"]
        and np.array_equal(changed, np.sort(indices))
        and np.all((w0 >= 0.002) & (w0 <= 0.18))
        and np.all((w1 >= 0.002) & (w1 <= 0.18)),
        "Numeric changed indices or weight bounds differ",
    )
    for key in ORDER:
        if key != "plastic_w":
            require(
                np.array_equal(arrays["before_" + key], arrays["after_" + key]),
                "Another projected neural variable changed",
            )
    difference = np.abs(w1[changed] - w0[changed])
    return {
        "schema": "nuria.semantic-weight-delta-check.v1",
        "passed": True,
        "synapses": n,
        "tagged_synapses": len(indices),
        "changed_synapses": len(changed),
        "all_other_projected_variables_unchanged": True,
        "full_weight_update_rule_exact": True,
        "before_projected_state_sha256": derivation["before_state_sha256"],
        "after_projected_state_sha256": derivation["after_state_sha256"],
        "minimum_absolute_weight_change": float(difference.min()),
        "maximum_absolute_weight_change": float(difference.max()),
        "pickle_loaded_by_this_verifier": False,
        "model_calls": 0,
        "encoder_calls": 0,
        "dynamics_reexecuted": False,
        "scope": "Numeric exports match the original recorded projected-state identities, saved eligibility and exact bounded reward update. Export provenance includes a prior restricted decode of original trusted-local checkpoints. Connectivity mapping is a derived export from those opaque checkpoint bytes. This is local numerical consistency, not independent attestation, full-state reconstruction, useful learning gain or a rerun of neural dynamics.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-evidence", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            verify(Path(__file__).resolve().parent, args.work_evidence),
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()

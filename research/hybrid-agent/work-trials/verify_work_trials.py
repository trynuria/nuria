"""Replay two recorded work audits without model calls or checkpoint restoration.

A new retained workspace receives the bounded original files. No files are
removed, no network is used, and no Python serialization is loaded.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
import re
import sqlite3
import sys
import zipfile
from pathlib import Path, PurePosixPath

FREEZE_SHA = "a4cff862bda1b3f7136726106f468e5f8153af0372f5cbe437bc3c37965fe342"
PROTOCOL_SHA = "431e3b092c46a680ad5aa509be523ff9c0d14da3606084bab1358d4e3ae51950"
MAX_FILE = 2 * 1024**2
MAX_TOTAL = 16 * 1024**2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def read_archive(directory):
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    require(
        manifest_path.is_file()
        and not manifest_path.is_symlink()
        and manifest_path.stat().st_size <= 256 * 1024,
        "Indirect or oversized manifest",
    )
    manifest = load(manifest_path)
    require(
        manifest["schema"] == "nuria.work-trial-evidence.v1", "Unknown evidence schema"
    )
    record = manifest["archive"]
    require(record["path"] == "retained-work-trials.zip", "Archive path differs")
    path = directory / record["path"]
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= 4 * 1024**2,
        "Indirect or oversized archive",
    )
    raw = path.read_bytes()
    require(
        len(raw) == record["bytes"] <= 4 * 1024**2 and sha(raw) == record["sha256"],
        "Archive identity differs",
    )
    records = manifest["files"]
    require(len(records) <= 64, "Archive file count exceeds bound")
    result = {}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(
            len(names) == len(set(names)) and set(names) == set(records),
            "Archive coverage differs",
        )
        for info in archive.infolist():
            name = info.filename
            require(
                not PurePosixPath(name).is_absolute()
                and all(
                    re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in (".", "..")
                    for part in name.split("/")
                ),
                "Unsafe archive path",
            )
            require(
                (info.external_attr >> 16) & 0o170000 == 0o100000
                and info.file_size <= MAX_FILE,
                "Indirect or oversized archive member",
            )
            require(
                sum(len(b) for b in result.values()) + info.file_size <= MAX_TOTAL,
                "Expanded archive exceeds bound",
            )
            value = archive.read(info)
            require(
                len(value) == records[name]["bytes"]
                and sha(value) == records[name]["sha256"],
                "Member identity differs",
            )
            result[name] = value
    require(
        sha(result["source-freeze.json"]) == FREEZE_SHA
        and sha(result["semantic/protocol.json"]) == PROTOCOL_SHA,
        "Original freeze or semantic protocol differs",
    )
    return manifest, result


def materialize(records, workspace):
    workspace = Path(workspace)
    require(
        not workspace.exists() and not workspace.is_symlink(), "Workspace must be new"
    )
    workspace.mkdir(mode=0o700)
    for name, value in records.items():
        target = workspace / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
    return workspace


def module_at(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def memory_records(path, expected_binding, verify):
    """Use immutable reads; independently check both DB rows and exported history."""
    before = sha(path.read_bytes())
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        identity = connection.execute(
            "SELECT payload FROM memory_identity WHERE id=1"
        ).fetchone()
        require(
            identity == (verify.serial(expected_binding),), "Memory binding differs"
        )
        raw_rows = connection.execute(
            "SELECT seq,previous_hash,hash,kind,episode_id,item_id,payload FROM memory_events ORDER BY seq"
        ).fetchall()
    finally:
        connection.close()
    require(
        sha(path.read_bytes()) == before and len(raw_rows) == 9,
        "Memory source or denominator differs",
    )
    parent = "0" * 64
    records = []
    for count, (seq, previous, head, kind, episode, item, raw) in enumerate(
        raw_rows, 1
    ):
        row = json.loads(raw)
        require(
            seq == count
            and previous == parent
            and sha((parent + raw).encode()) == head
            and row["binding_sha256"] == verify.digest(expected_binding)
            and [row[k] for k in ("kind", "episode_id", "item_id")]
            == [kind, episode, item],
            "Memory chain differs",
        )
        records.append({"seq": seq, "previous_hash": previous, "hash": head, **row})
        parent = head
    exported = load(path.parent / "memory-export.json")
    require(
        exported["binding"] == expected_binding
        and exported["records"] == records
        and exported["head"] == parent
        and exported["events"] == len(records)
        and exported["binding_sha256"] == verify.digest(expected_binding)
        and exported["truncated"] is False,
        "Memory export differs from original database",
    )
    return records


def verify(directory, repository, workspace):
    sys.dont_write_bytecode = True
    directory, repository = Path(directory).resolve(), Path(repository).resolve()
    manifest, entries = read_archive(directory)
    freeze = json.loads(entries["source-freeze.json"])
    protocol = json.loads(entries["semantic/protocol.json"])
    for relative, expected in {**freeze["files"], **protocol["new_source"]}.items():
        path = (repository / relative).resolve()
        require(
            path.is_relative_to(repository) and sha(path.read_bytes()) == expected,
            "Runtime source identity differs: " + relative,
        )
    sys.path.insert(0, str(repository))
    from scripts import verify_grounded_comparison as replay

    snapshot = repository / "research/hybrid-agent/work-snapshot"
    for relative, expected_hash in manifest["snapshot_files_sha256"].items():
        path = (snapshot / relative).resolve()
        require(
            path.is_relative_to(snapshot.resolve())
            and sha(path.read_bytes()) == expected_hash,
            "Snapshot source identity differs",
        )
    checker = module_at(snapshot / "check_recent_work.py", "work_snapshot_checker")
    source_check = checker.check(snapshot)
    domain = load(snapshot / "question-and-check.json")
    registry = load(snapshot / "registered/registry.json")
    registry_sha = replay.digest(registry, ascii=False)
    database = snapshot / "registered/work.sqlite"
    expected = checker.group_rows(
        load(snapshot / "action-task-projection.json")["action_tasks"]
    )
    require(
        source_check["task_rows"] == 40
        and source_check["semantic_acceptance_unknown_rows"] == 40
        and expected == domain["expected_rows"],
        "Source coverage or independent JSON result differs",
    )
    workspace = materialize(entries, workspace)
    system = next(
        ast.literal_eval(node.value)
        for node in ast.parse(
            (repository / "cognition/grounded_agent.py").read_text()
        ).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SYSTEM" for t in node.targets)
    )
    result = {}
    for label, arm in (("ordinary", "procedures"), ("semantic", "hybrid_neural")):
        root = workspace / label
        artifact, projection, terminal = (
            load(root / name)
            for name in (
                "artifact.json",
                "result-projection.json",
                "terminal-check.json",
            )
        )
        report = projection["record"]
        require(
            projection["schema"] == "nuria.work-trial-result-projection.v1"
            and re.fullmatch(r"[0-9a-f]{64}", projection["original_result_sha256"]),
            "Result projection identity differs",
        )
        require(
            report["artifact_sha256"] == sha((root / "artifact.json").read_bytes())
            and report["model_usage"] == artifact["model_usage"]
            and report["final_sql"] == artifact["final_sql"]
            and report["episode_id"] == artifact["episode_id"]
            and report["proposal_sha256"] == artifact["proposal_sha256"]
            and report["tool_calls"] == artifact["tool_calls"]
            and report["semantic_accepted"] is None,
            "Projected result differs from its original artifact",
        )
        decision = load(root / "decision.json") if label == "semantic" else None
        history = memory_records(
            root / "memory.sqlite", artifact["configuration"]["model_binding"], replay
        )
        records = [
            row for row in history if row["episode_id"] == artifact["episode_id"]
        ]
        require(len(records) == len(history), "Unexpected episode or omitted record")
        task = {"id": report["job_id"], "db_id": "work", "question": domain["question"]}
        usage = replay.verify_agent(
            artifact,
            task,
            arm,
            registry,
            registry_sha,
            records,
            freeze["files"],
            system_sha=replay.digest(system),
            decision=decision,
            history=history,
        )
        observed, columns = replay.sql_result(database, artifact["final_sql"])
        require(
            columns == domain["expected_columns"] == len(report["columns"])
            and observed == report["rows"]
            and replay.rows_equal(observed, artifact["selected_receipt"]["rows"], True)
            and replay.rows_equal(observed, expected, True),
            "SQL, retained result and independent JSON grouping differ",
        )
        qualification = terminal["qualification"] if label == "semantic" else terminal
        receipt = qualification["terminal"]
        replay.hashed_record(receipt, "receipt_sha256")
        require(
            qualification["accepted"] is True
            and receipt["accepted"] is True
            and receipt["terminal_reward"] == 1
            and receipt["episode_id"]
            == artifact["episode_id"]
            == receipt["origin_sha256"]
            and receipt["proposal_sha256"] == artifact["proposal_sha256"]
            and records[-1]["kind"] == "settlement"
            and records[-1]["record"] == receipt,
            "Terminal is not bound to its own verified proposal",
        )
        require(
            qualification["memory"]["events"] == 9
            and qualification["memory"]["head"] == records[-1]["hash"]
            and qualification["qualified_procedures"] == 0,
            "Terminal memory claim differs",
        )
        require(
            usage["completion_tokens"] == report["model_usage"]["completion_tokens"]
            and report["unresolved_completion_ceiling"] == 0
            and terminal["allowance"]["current_unknown_usage"] is False
            and terminal["allowance"]["pending_completion_reservation"] == 0,
            "Usage closure differs",
        )
        allowance = terminal["allowance"]
        require(
            allowance["new_known_completion_tokens"] == usage["completion_tokens"]
            and allowance["known_completion_tokens"]
            + allowance["prior_unknown_completion_ceiling"]
            + allowance["pending_completion_reservation"]
            == allowance["conservative_completion_charge"]
            <= allowance["global_completion_cap"]
            == 200000
            and allowance["prior_unknown_completion_ceiling"] == 12214,
            "Terminal allowance does not conserve own usage and retained liability",
        )
        if label == "semantic":
            ordinary_allowance = load(workspace / "ordinary/terminal-check.json")[
                "allowance"
            ]
            require(
                allowance["known_completion_tokens"]
                - ordinary_allowance["known_completion_tokens"]
                == usage["completion_tokens"],
                "Semantic allowance does not extend the closed prior usage",
            )
        row = {
            "accepted": True,
            "tool_calls": report["tool_calls"],
            "completion_tokens": usage["completion_tokens"],
            "prompt_tokens": usage["prompt_tokens"],
            "memory_events": 9,
            "qualified_procedures": 0,
            "final_sql_replayed": True,
        }
        if label == "semantic":
            replay.hashed_record(decision, "decision_sha256")
            semantic = load(root / "semantic-lab/SEMANTIC.json")
            lab = load(root / "semantic-lab/LAB.json")
            terms = semantic["terms"]
            require(
                semantic["terms_sha256"]
                == replay.digest(terms)
                == report["semantic_terms_sha256"]
                and lab["terms"]["source_id"] == "semantic:" + semantic["terms_sha256"]
                and terms["encoder_binding"] == protocol["encoder_binding"]
                and terms["encoder_binding_sha256"]
                == protocol["encoder_binding_sha256"]
                == replay.digest(terms["encoder_binding"])
                and terms["source_id"]
                == artifact["configuration"]["model_binding"]["source_id"]
                and terms["model_id"] == report["model_identity"]
                and lab["terms"]["mode"] == "neural"
                and lab["terms"]["strategy_actions"]
                == {"list_tables": "explore", "read_knowledge": "experiment"},
                "Semantic lab or encoder source binding differs",
            )
            observation = decision["semantic_observation"]
            import numpy as np

            vector = np.asarray(decision["semantic_vector"], dtype="<f4")
            underlying = observation["underlying_observation"]
            require(
                vector.shape == (384,)
                and np.all(np.isfinite(vector))
                and abs(float(np.linalg.norm(vector)) - 1) <= 1e-5
                and sha(vector.tobytes())
                == observation["vector_float32_sha256"]
                == underlying["vector_float32_sha256"]
                and underlying["text_sha256"] == observation["text_sha256"]
                and underlying["binding_sha256"]
                == terms["encoder_binding"]["encoder_binding_sha256"]
                and underlying["dimensions"] == observation["dimensions"] == 384
                and underlying["call"] == observation["call"] == 1
                and underlying["truncated"] is False
                and underlying["wordpieces_used"]
                == observation["wordpieces_used"]
                <= 256,
                "Recorded semantic vector or CPU receipt differs",
            )
            replay.hashed_record(observation, "receipt_sha256")
            replay.hashed_record(
                observation["underlying_observation"], "receipt_sha256"
            )
            require(
                report["decision_file_sha256"]
                == sha((root / "decision.json").read_bytes())
                and report["semantic_decision_sha256"] == decision["decision_sha256"]
                and report["semantic_observation"] == observation
                and observation["binding_sha256"] == protocol["encoder_binding_sha256"]
                and observation["text_sha256"] == sha(domain["question"].encode())
                and observation["truncated"] is False
                and report["completed_cpu_encodes"] == 1
                and report["unresolved_cpu_encode_ceiling"] == 0,
                "Semantic observation or first-tool provenance differs",
            )
            require(
                replay.digest(decision["spikes"]) == decision["spikes_sha256"]
                and decision["window_ms"] == 100,
                "Recorded spike window differs",
            )
            spikes = decision["spikes"]
            require(
                all(
                    type(t) in (int, float)
                    and math.isfinite(t)
                    and 0 <= t <= 0.1
                    and type(neuron) is int
                    and 0 <= neuron < 1024
                    for t, neuron in spikes
                ),
                "Invalid recorded spike",
            )
            counts = {
                "list_tables": sum(768 <= neuron < 784 for _, neuron in spikes),
                "read_knowledge": sum(832 <= neuron < 848 for _, neuron in spikes),
            }
            require(
                counts == decision["action_spikes"], "Recorded action spikes differ"
            )
            scores = {
                tool: 0.65 * decision["utilities"][tool]
                + 0.35 * count / max(1, sum(counts.values()))
                for tool, count in counts.items()
            }
            require(
                decision["neural_selection_weight"] == 0.35
                and decision["scores"] == scores == decision["recorded_blend_scores"]
                and decision["first_tool"]
                == max(scores, key=scores.get)
                == report["first_tool"],
                "Recorded blend did not select the enforced inspection",
            )
            credit = qualification["neural_credit"]
            replay.hashed_record(credit, "receipt_sha256")
            require(
                credit["accepted"] is True
                and credit["evidence_sha256"] == receipt["receipt_sha256"]
                and credit["decision_sha256"] == decision["decision_sha256"]
                and credit["origin_sha256"] == decision["origin_sha256"]
                and credit["job_id"] == report["job_id"]
                and credit["neural_reward_signal"] == 1.0,
                "Neural credit is not linked to this own terminal",
            )
            neural = replay.verify_capsules(
                root / "semantic-lab",
                [
                    {
                        "task_id": report["job_id"],
                        "neural_decision": decision,
                        "neural_credit": credit,
                        "settlement": receipt,
                    }
                ],
            )
            head = load(root / "semantic-lab/HEAD.json")
            state = load(
                root / "semantic-lab/capsules" / head["capsule"] / "metadata.json"
            )["state"]
            eligibility = state["jobs"][report["job_id"]]["eligibility"]
            require(
                len(eligibility["indices"])
                == len(eligibility["eligibility"])
                == len(set(eligibility["indices"]))
                == credit["credit"]["tagged_synapses"]
                == 205
                and credit["credit"]["changed_synapses"] == 205
                and credit["credit"]["before_state_sha256"]
                == decision["after_state_sha256"]
                and credit["credit"]["before_state_sha256"]
                != credit["credit"]["after_state_sha256"],
                "Stored origin eligibility or credit summary differs",
            )
            row.update(
                {
                    "completed_cpu_encodes": 1,
                    "first_tool": decision["first_tool"],
                    "action_spikes": counts,
                    "recorded_tagged_synapses": 205,
                    "recorded_changed_synapses": 205,
                    "neural_capsule_check": neural,
                }
            )
        result[label] = row
    return {
        "schema": "nuria.work-trial-replay.v1",
        "passed": True,
        "task_rows": 40,
        "semantic_acceptance_unknown_source_rows": 40,
        "expected_rows": expected,
        "trials": result,
        "model_calls": 0,
        "encoder_calls": 0,
        "pickle_loaded": False,
        "scope": "Finite SQL agreement and recorded local consistency for two same-question integration runs. Original result paths are omitted in explicit derived projections, so their original checksums are identity references rather than independently recomputable originals. Neural capsule bytes, lineage and origin-linked recorded credit are checked without restoring or reexecuting neural dynamics. No neural advantage, independent external attestation, general transfer, purchases, or semantic acceptance of the40source tasks is established.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(Path(__file__).resolve().parent, args.repository, args.workspace)
    raw = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(raw)
    print(raw)


if __name__ == "__main__":
    main()

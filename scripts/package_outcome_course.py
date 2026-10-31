"""Export public execution evidence without credentials or private host state."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from cognition.outcome_worker import OutcomeStore, identity
from scripts.evaluate_outcome_loop import load_tasks, save


def write_rows(path, rows):
    # A fixed gzip timestamp makes identical public exports byte-reproducible.
    with Path(path).open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            for row in rows:
                stream.write((json.dumps(row, sort_keys=True) + "\n").encode())


def package(root, output):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks(root)
    selected = {task["task_id"] for values in tasks.values() for task in values}
    original = [
        item["row"]
        for file in sorted((root / "corpus").glob("rows-*.json"))
        for item in json.loads(file.read_text())["rows"]
        if item["row"]["task_id"] in selected
    ]
    write_rows(output / "upstream-tasks.jsonl.gz", original)
    write_rows(
        output / "tasks.jsonl.gz",
        [
            {"split": split, **task}
            for split, values in tasks.items()
            for task in values
        ],
    )
    traces, journals = [], {}
    result_path = root / "held-out-results.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else None
    for name in [
        "training",
        "optimization",
        "simple_retrieval",
        "simple_reflection",
        "nuria_gepa",
        "outcome_playbook",
        "playbook-validation",
        "reflection-validation",
    ]:
        alias = (
            result["methods"].get(name, {}).get("execution_alias_of")
            if result
            else None
        )
        path = root / ((alias or name) + ".sqlite")
        if not path.exists():
            continue
        store = OutcomeStore(path)
        journals[name] = store.verify()
        for row in store.experiences():
            original_hash = identity(row)
            for attempt in row["attempts"]:
                diagnostic = attempt.pop("diagnostic", None)
                if diagnostic:
                    attempt["diagnostic_sha256"] = identity(diagnostic)
            traces.append(
                {
                    "method": name,
                    "execution_alias_of": alias,
                    "private_record_sha256": original_hash,
                    **row,
                }
            )
    write_rows(output / "executions.jsonl.gz", traces)
    for name in [
        "common-experience.json",
        "simple-reflection.json",
        "learned-policy.json",
        "gepa-result.json",
        "held-out-results.json",
        "test-execution-freeze.json",
        "execution-source-before-models.json",
        "public-protocol-ref-before-models.json",
        "expanded-oracle-preflight.json",
        "novelty-before-test.json",
        "novelty-112-before-test.json",
        "runtime-policy-before-test.json",
        "outcome-playbook.json",
        "structured-memory-before-test.json",
        "runtime-selection-before-inference.json",
    ]:
        source = root / name
        if source.exists():
            (output / name).write_bytes(source.read_bytes())
    reference = root.parent / "oracle-preflight-02.json"
    if reference.exists():
        (output / "initial-oracle-preflight.json").write_bytes(reference.read_bytes())
    # Export visible model text and inputs, without private reservation or
    # provider response identifiers. Hidden reasoning is never retained.
    transcript_root = root.parent / "provider-transcripts"
    if transcript_root.is_dir():
        public = []
        for file in sorted(transcript_root.glob("*.json"), key=lambda p: int(p.stem)):
            item = json.loads(file.read_text())
            response = item["response"]
            public.append(
                {
                    "request": item["request"],
                    "response": {
                        k: response[k]
                        for k in ("content", "usage", "model")
                        if k in response
                    },
                }
            )
        write_rows(output / "provider-text.jsonl.gz", public)
    save(output / "journals.json", journals)
    save(
        output / "export.json",
        {
            "schema": "nuria.outcome-export.v1",
            "traces": len(traces),
            "tasks": sum(map(len, tasks.values())),
            "task_records_sha256": identity(tasks),
            "raw_model_content": "retained programs, smoke checks, candidate policies and reported token usage",
            "payment_or_token_activity": False,
        },
    )
    return {"tasks": sum(map(len, tasks.values())), "traces": len(traces)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.root, args.output), sort_keys=True))


if __name__ == "__main__":
    main()

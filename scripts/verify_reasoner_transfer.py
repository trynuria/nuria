"""Package and independently replay a prospectively committed model comparison.

No provider credential or inference is used. Every program, rejected outcome and
configured token cost is retained. Only terminal operator paths may be redacted;
the complete pre-grading records and exact program commitments remain unchanged.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from cognition.experience_navigation import VERSION
from cognition.outcome_worker import IsolatedPython, identity
from scripts.evaluate_experience_navigation import read_rows
from scripts.evaluate_outcome_loop import exact_paired
from scripts.package_experience_navigation import (
    check_public,
    compressed_rows,
    json_bytes,
    read_store,
    redact_terminal_paths,
)


def verify_commitments(protocol, tasks, history, seal, records):
    names = set(protocol["conditions"])
    expected = {task["task_id"] for task in tasks}
    if (
        seal["protocol_sha256"] != identity(protocol)
        or set(seal["records"]) != names
        or seal["terminal_checks_revealed"] is not False
        or seal["tasks"] != len(tasks)
        or {row["condition"] for row in records} != names
    ):
        raise ValueError(
            "Complete prospective conditions and private seal are required"
        )
    history_hash, history_ids = identity(history), {row["job_id"] for row in history}
    implementation = identity(
        (
            Path(__file__).resolve().parents[1] / "cognition/experience_navigation.py"
        ).read_text()
    )
    evidence_by_task = {}
    for name in sorted(names):
        current = [row["record"] for row in records if row["condition"] == name]
        previous = seal["records"][name]
        if (
            len(current) != len(tasks)
            or len(previous) != len(tasks)
            or {row["task_id"] for row in current} != expected
            or {row["task_id"] for row in previous} != expected
        ):
            raise ValueError("An arm omits or duplicates an enrolled proposal")
        committed = {row["task_id"]: row for row in previous}
        for row in current:
            prior = committed[row["task_id"]]
            ungraded = {**row, "accepted": None, "checker": None}
            if (
                prior["accepted"] is not None
                or prior["checker"] is not None
                or identity(ungraded) != identity(prior)
            ):
                raise ValueError("Program or pre-feedback evidence changed")
            if (
                row["experience_sha256"] != history_hash
                or row["implementation_sha256"] != implementation
                or not set(row["experience_ids"]) <= history_ids
                or row["method"] != "retrieval"
                or row["proposed_code_sha256"] != identity(row["code"])
            ):
                raise ValueError("A proposal used different information or code")
            if (
                row["task_id"] in evidence_by_task
                and evidence_by_task[row["task_id"]] != row["experience_ids"]
            ):
                raise ValueError("Conditions received different retrieved evidence")
            evidence_by_task[row["task_id"]] = row["experience_ids"]
            if len(row["attempts"]) != 3:
                raise ValueError("Retain all three prospective provider attempts")
            for attempt in row["attempts"]:
                if attempt["usage"]["completion_tokens"] > 2400 or (
                    "content" in attempt
                    and identity(attempt["content"]) != attempt["response_sha256"]
                ):
                    raise ValueError("Provider output or completion allowance changed")


def summarize(protocol, tasks, records):
    vectors, families, costs = {}, {}, {}
    for name, configuration in protocol["conditions"].items():
        rows = sorted(
            (row["record"] for row in records if row["condition"] == name),
            key=lambda row: row["task_id"],
        )
        vectors[name] = [row["accepted"] for row in rows]
        costs[name] = {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "configured_micro_usd": 0,
        }
        for row in rows:
            for attempt in row["attempts"]:
                usage = attempt["usage"]
                prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
                costs[name]["requests"] += 1
                costs[name]["prompt_tokens"] += prompt
                costs[name]["completion_tokens"] += completion
                numerator = (
                    prompt * configuration["input_micro_usd_per_million_tokens"]
                    + completion * configuration["output_micro_usd_per_million_tokens"]
                )
                costs[name]["configured_micro_usd"] += (numerator + 999999) // 1000000
        for family in {task["family"] for task in tasks}:
            families.setdefault(family, {})[name] = sum(
                row["accepted"] for row in rows if row["family"] == family
            )
    comparison = exact_paired(vectors["astra-medium"], vectors["sol-low"])
    gate = protocol["gate"]
    return {
        "tasks": len(tasks),
        "accepted": {name: sum(v) for name, v in vectors.items()},
        "comparison": comparison,
        "families": families,
        "superiority_supported": len(tasks) >= gate["minimum_valid_tasks"]
        and comparison["accuracy_delta"] >= gate["minimum_accuracy_delta"]
        and comparison["one_sided_exact_p"] <= gate["maximum_one_sided_exact_p"]
        and all(
            row["astra-medium"] >= row["sol-low"] - gate["maximum_family_loss"]
            for row in families.values()
        ),
        "runtime_promoted": False,
        "neural_advantage_established": False,
    }, costs


def verify_journals(protocol, tasks, history, seal, records, events, redactions):
    """Bind each public condition to its original exact execution contract."""
    task_map = {task["task_id"]: task for task in tasks}
    for name, configuration in protocol["conditions"].items():
        previous = "0" * 64
        original = {row["job_id"]: row for row in seal["records"][name]}
        final = {
            row["record"]["job_id"]: row["record"]
            for row in records
            if row["condition"] == name
        }
        changed = {row["job_id"]: row for row in redactions if row["condition"] == name}
        begins, outcomes, acceptances = set(), set(), set()
        sequence = sorted(
            (row for row in events if row["condition"] == name),
            key=lambda row: row["seq"],
        )
        for seq, row in enumerate(sequence, 1):
            event = row["event"]
            if (
                row["seq"] != seq
                or row["previous"] != previous
                or row["sha256"] != identity({"previous": previous, "event": event})
            ):
                raise ValueError("The original condition journal changed")
            previous = row["sha256"]
            job = event["job"]
            record = original[job]
            if event["event"] == "begin":
                contract = {
                    "version": VERSION,
                    "implementation_sha256": record["implementation_sha256"],
                    "task": task_map[record["task_id"]],
                    "method": "retrieval",
                    "model": configuration,
                    "experience": identity(history),
                    "grade": False,
                    "max_tokens": 2400,
                    "max_calls": 3,
                    "correction_catalog": "automatic-checked-prior-receipts",
                }
                if job in begins or event["contract_sha256"] != identity(contract):
                    raise ValueError("A condition used a different execution contract")
                begins.add(job)
            elif event["event"] == "outcome":
                if (
                    job not in begins
                    or job in outcomes
                    or event["result_sha256"] != identity(record)
                ):
                    raise ValueError(
                        "Original proposal differs from the public commitment"
                    )
                outcomes.add(job)
            elif event["event"] == "acceptance":
                expected = (
                    changed[job]["original_result_sha256"]
                    if job in changed
                    else identity(final[job])
                )
                if (
                    outcomes != set(original)
                    or job in acceptances
                    or event["result_sha256"] != expected
                ):
                    raise ValueError(
                        "Terminal acceptance precedes or differs from commitment"
                    )
                acceptances.add(job)
            else:
                raise ValueError("Unknown condition journal event")
        if (
            begins != set(original)
            or outcomes != set(original)
            or acceptances != set(original)
        ):
            raise ValueError("The complete proposal and acceptance journal is required")


def material(package):
    package = Path(package)
    protocol = json.loads((package / "protocol.json").read_text())
    repository = Path(__file__).resolve().parents[1]
    for name, expected in protocol["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Prospective evidence paths must stay relative")
        path = (
            repository / relative
            if name.startswith(("cognition/", "scripts/", "tests/"))
            else package / relative
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Prospective source or evidence changed")
    tasks = read_rows(package / "tasks.jsonl.gz")
    upstream = {
        row["task_id"]: row for row in read_rows(package / "upstream-tasks.jsonl.gz")
    }
    if len(upstream) != len(tasks):
        raise ValueError("Retain every selected upstream task")
    for task in tasks:
        original = upstream[task["task_id"]]
        if (
            task["source_sha256"] != identity(original)
            or task["prompt"] != original["complete_prompt"]
            or not task["checker"].startswith(original["test"])
        ):
            raise ValueError("An upstream specification or independent checker changed")
    preflight = json.loads((package / "preflight.json").read_text())
    valid = {row["task_id"] for row in preflight if row["checker"]["passed"]}
    tasks = [task for task in tasks if task["task_id"] in valid]
    if [task["task_id"] for task in tasks] != protocol["valid_task_ids"]:
        raise ValueError("Reference-valid task enrollment changed")
    history = json.loads((package / "experience.json").read_text())
    if valid & {row["task_id"] for row in history}:
        raise ValueError("Fresh work appears in the enrolled history")
    return protocol, tasks, history


def audit_requests(protocol, records, transcripts):
    """Check actual request boundaries and equal initial/challenge information."""
    allowed = {
        "proposal": {"task", "experience", "runtime"},
        "blind_challenge": {"task", "experience"},
        "revision": {
            "task",
            "experience",
            "initial_proposal",
            "initial_error",
            "smoke",
            "blind_challenge",
            "challenge_execution",
        },
    }
    configurations = {
        (row["model"], row["reasoning_effort"]): name
        for name, row in protocol["conditions"].items()
    }
    indexed, shared = {}, {}
    for item in transcripts:
        request, response = item["request"], item["response"]
        name = configurations[(request["model"], request["reasoning"]["effort"])]
        body = json.loads(request["input"][1]["content"])
        stage = (
            "revision"
            if "initial_proposal" in body
            else "proposal"
            if "runtime" in body
            else "blind_challenge"
        )
        key = (name, body["task"], stage)
        if (
            set(body) != allowed[stage]
            or key in indexed
            or request["max_output_tokens"] != 2400
            or response["configured_model"] != request["model"]
        ):
            raise ValueError("An actual request differs from the enrolled interface")
        indexed[key] = response
        if stage != "revision":
            shared.setdefault((body["task"], stage), {})[name] = identity(
                request["input"]
            )
    matched, unavailable = 0, []
    models = {}
    for item in records:
        name, row = item["condition"], item["record"]
        for attempt in row["attempts"]:
            key = (name, row["prompt"], attempt["stage"])
            response = indexed.pop(key, None)
            if "model_error" in attempt and response is None:
                unavailable.append(
                    {
                        "condition": name,
                        "task_id": row["task_id"],
                        "stage": attempt["stage"],
                        "model_error": attempt["model_error"],
                        "usage": attempt["usage"],
                    }
                )
                continue
            if (
                response is None
                or response["usage"] != attempt["usage"]
                or identity(response["content"]) != attempt["response_sha256"]
            ):
                raise ValueError(
                    "Retained provider output differs from actual request evidence"
                )
            reported = response.get("reported_model")
            models.setdefault(name, set()).add(
                reported if isinstance(reported, str) and reported else "unknown"
            )
            matched += 1
    if indexed or any(len(set(values.values())) != 1 for values in shared.values()):
        raise ValueError("Extra requests or unequal initial/challenge information")
    verified_pairs = sum(
        set(values) == set(protocol["conditions"]) for values in shared.values()
    )
    expected_pairs = len({item["record"]["task_id"] for item in records}) * 2
    return {
        "verified_response_transcripts": matched,
        "known_error_responses_unavailable": unavailable,
        "reported_models": {name: sorted(values) for name, values in models.items()},
        "initial_and_blind_information_equal": True
        if verified_pairs == expected_pairs
        else None,
        "verified_information_pairs": verified_pairs,
        "unknown_information_pairs": expected_pairs - verified_pairs,
        "scope": "Host-retained provider responses; not independent provider billing or execution attestation",
    }


def verify(package, *, workspace=None, dependencies=()):
    package = Path(package)
    if workspace is not None and Path(workspace).exists():
        raise ValueError("Native replay requires a new retained workspace")
    protocol, tasks, history = material(package)
    seal = json.loads((package / "proposals-before-grade.json").read_text())
    records = read_rows(package / "executions.jsonl.gz")
    verify_commitments(protocol, tasks, history, seal, records)
    verify_journals(
        protocol,
        tasks,
        history,
        seal,
        records,
        read_rows(package / "journal-events.jsonl.gz"),
        json.loads((package / "path-redactions.json").read_text()),
    )
    result, costs = summarize(protocol, tasks, records)
    requests = audit_requests(
        protocol, records, read_rows(package / "provider-transcripts.jsonl.gz")
    )
    if result != json.loads((package / "results.json").read_text()):
        raise ValueError("Published comparison differs from complete paired outcomes")
    by_task = {task["task_id"]: task for task in tasks}
    executor = (
        IsolatedPython(workspace, dependency_roots=dependencies)
        if workspace is not None
        else None
    )
    rows = []
    for item in records:
        row, condition = item["record"], item["condition"]
        task = by_task[row["task_id"]]
        checker = row["checker"]
        if (
            type(row["accepted"]) is not bool
            or checker["passed"] is not row["accepted"]
            or row["source_sha256"] != task["source_sha256"]
            or row["prompt"] != task["prompt"]
            or checker["code_sha256"]
            != hashlib.sha256(row["code"].encode()).hexdigest()
            or checker["checks_sha256"]
            != hashlib.sha256(task["checker"].encode()).hexdigest()
        ):
            raise ValueError("Terminal acceptance is not bound to the enrolled program")
        if executor:
            actual = executor.run(
                row["code"],
                task["checker"],
                label=condition + "-" + task["task_id"].split("/")[1],
            )
            rows.append(
                {
                    "condition": condition,
                    "task_id": row["task_id"],
                    "recorded": row["accepted"],
                    "actual": actual["passed"],
                    "checker": actual,
                }
            )
    report = {
        "result": result,
        "configured_usage": costs,
        "replayed_programs": len(rows),
        "mismatches": [
            {k: row[k] for k in ("condition", "task_id", "recorded", "actual")}
            for row in rows
            if row["recorded"] != row["actual"]
        ],
        "model_calls": 0,
        "request_audit": requests,
    }
    if workspace is not None:
        path = Path(workspace) / "replay.json"
        with path.open("x") as stream:
            json.dump(
                {"summary": report, "programs": rows}, stream, sort_keys=True, indent=2
            )
            stream.write("\n")
    return report


def package(root, output):
    root, output = Path(root), Path(output)
    protocol, tasks, history = material(root)
    seal = json.loads((root / "proposals-before-grade.json").read_text())
    proof = json.loads((root / "public-proposals-proof.json").read_text())
    if (
        proof["sha256"]
        != hashlib.sha256(
            (root / "proposals-before-grade.json").read_bytes()
        ).hexdigest()
    ):
        raise ValueError("The public commitment identifies another original seal")
    records, journals, redactions = [], [], []
    for name in protocol["conditions"]:
        original, events = read_store(root / (name + ".sqlite"))
        public, changes = redact_terminal_paths(original)
        records.extend({"condition": name, "record": row} for row in public)
        journals.extend({"condition": name, **row} for row in events)
        redactions.extend({"condition": name, **row} for row in changes)
    verify_commitments(protocol, tasks, history, seal, records)
    verify_journals(protocol, tasks, history, seal, records, journals, redactions)
    result, costs = summarize(protocol, tasks, records)
    if result != json.loads((root / "results.json").read_text()):
        raise ValueError("Retained results changed")
    baseline = json.loads((root / "generation-budget.json").read_text())["initial"][
        "requests"
    ]
    calls = sum(len(row["record"]["attempts"]) for row in records)
    transcripts = []
    for number in range(baseline + 1, baseline + calls + 1):
        path = root.parent / "provider-transcripts" / (str(number) + ".json")
        if path.exists():
            item = json.loads(path.read_text())
            fields = (
                "content",
                "usage",
                "reported_model",
                "configured_model",
                "accounted_cost_micro_usd",
                "wall_seconds",
            )
            transcripts.append(
                {
                    "request": item["request"],
                    "response": {
                        key: item["response"][key]
                        for key in fields
                        if key in item["response"]
                    },
                }
            )
    requests = audit_requests(protocol, records, transcripts)
    files = {
        **{
            name: (root / name).read_bytes()
            for name in protocol["files"]
            if not name.startswith(("cognition/", "scripts/", "tests/"))
        },
        "protocol.json": (root / "protocol.json").read_bytes(),
        "proposals-before-grade.json": (
            root / "proposals-before-grade.json"
        ).read_bytes(),
        "public-proposals-proof.json": json_bytes(proof),
        "executions.jsonl.gz": compressed_rows(records),
        "journal-events.jsonl.gz": compressed_rows(journals),
        "results.json": json_bytes(result),
        "configured-usage.json": json_bytes(costs),
        "path-redactions.json": json_bytes(redactions),
        "provider-transcripts.jsonl.gz": compressed_rows(transcripts),
        "request-audit.json": json_bytes(requests),
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        if name.endswith(".gz"):
            check_public(gzip.decompress(raw).decode())
        elif name.endswith(".zip"):
            from scripts.package_experience_navigation import check_archive

            check_archive(raw)
        else:
            check_public(raw.decode())
    for name, raw in files.items():
        path = output / name
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("An evidence export cannot overwrite another artifact")
        else:
            with path.open("xb") as stream:
                stream.write(raw)
    return {
        "programs": len(records),
        "files": len(files),
        "configured_usage": costs,
        "result": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("package", "verify"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    if args.stage == "package":
        if args.output is None:
            parser.error("An explicit public evidence destination is required")
        result = package(args.root, args.output)
    else:
        result = verify(
            args.root, workspace=args.workspace, dependencies=args.dependency_root
        )
    print(json.dumps(result, sort_keys=True))
    if result.get("mismatches"):
        raise SystemExit("Independent replay differs from recorded acceptance")


if __name__ == "__main__":
    main()

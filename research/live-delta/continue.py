"""One resumable improvement trial; a Cherry timer continues within the old cap.

The initial blind qualification stays immutable. Later trials use those retired
cases as development data and a newly captured real transition as a fresh check.
Only validated library artifacts are published; generated code never executes
in the public web process or changes its reviewed deployment.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from evaluate import CHECKS, audit

from cognition.outcome_worker import IsolatedPython
from studio.store import Store, atomic, now
from studio.worker import Client


def write_source(path, source):
    temp = path.with_suffix(".tmp")
    temp.write_text(source)
    os.replace(temp, path)


def evaluate(root, code, group, name, seed):
    report_path = root / f"{name}.json"
    if report_path.exists():
        return json.loads(report_path.read_text())
    try:
        audit(code)
    except (ValueError, SyntaxError) as error:
        result = {"correct": False, "error": str(error)}
        atomic(report_path, result)
        return result
    corpus = root / group
    checks = CHECKS.replace("CASE_PATH", repr(str(corpus / "cases.json"))).replace(
        "SEED", str(seed)
    )
    checks = checks.replace("'jsonpatch_compact','jsondiff'", "'jsonpatch_compact'")
    executor = IsolatedPython(
        root / "executions",
        dependency_roots=("/opt/nuria/research-deps", str(corpus)),
        wall_seconds=60,
        cpu_seconds=55,
    )
    label = name
    recovery = 0
    while (root / "executions" / label).exists():
        recovery += 1
        label = f"{name}-recovery-{recovery}"
    execution = executor.run(code, checks, label=label)
    result_file = root / "executions" / label / "report.json"
    result = (
        json.loads(result_file.read_text())
        if result_file.exists()
        else {"correct": False, "execution": execution}
    )
    result["code_sha256"] = hashlib.sha256(code.encode()).hexdigest()
    atomic(report_path, result)
    return result


def trial(root, token_file):
    root = Path(root)
    continuous = root / "continuous"
    continuous.mkdir(exist_ok=True)
    store = Store("/var/lib/nuria-studio")
    client = Client("http://127.0.0.1:4041", token_file)
    public = Path("/var/lib/nuria-studio/public")
    state_path = continuous / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        state = {
            "attempt": 5,
            "accepted": 0,
            "phase": "research_waiting",
            "started_at": now(),
        }
        write_source(continuous / "best.py", (root / "selected.py").read_text())
        (continuous / "previous.json").write_bytes(
            (public / "status.json").read_bytes()
        )
        atomic(state_path, state)
    initial = json.loads((root / "qualification.json").read_text())
    assert initial["gate_passed"], "Initial library must pass its frozen qualification"

    def publish(phase, **fields):
        state.update(fields, phase=phase, updated_at=now(), budget=client.budget())
        atomic(state_path, state)
        store.put(
            "meta",
            "research",
            {
                "name": "LiveDelta",
                "phase": phase,
                "updated_at": state["updated_at"],
                "summary": "LiveDelta is running here: 56% less encoding time than python-jsonpatch on nine held-out real cases. Cherry continues testing improvements to the downloadable encoder.",
                "attempt": state["attempt"],
                "accepted_improvements": state["accepted"],
            },
        )
        atomic(
            public / "live-delta-results.json",
            {
                "initial_qualification": initial,
                "continuation": state,
                "scope": "The reviewed initial encoder serves the website. Later validated library versions are downloadable artifacts, executed only in an isolated test process. Initial holdout cases become development data only after initial qualification; each later candidate must also pass a newly captured real state transition. These are domain measurements, not universal superiority or scientific novelty.",
                "source": "https://github.com/trynuria/nuria/tree/main/research/live-delta",
            },
        )
        write_source(public / "live-delta.py", (continuous / "best.py").read_text())
        for name in ("live-delta.py", "live-delta-results.json"):
            (public / name).chmod(0o644)
        print(
            json.dumps({k: v for k, v in state.items() if k != "last_result"}),
            flush=True,
        )

    budget = client.budget()
    if (
        budget["blocked"]
        or budget["remaining_micro_usd"] < 30000
        or state["phase"] == "budget_exhausted"
    ):
        publish("budget_exhausted")
        return
    i = state["attempt"]
    work = continuous / f"trial-{i:04d}"
    if not (work / "experiment.json").exists():
        work.mkdir(exist_ok=True)
        for group in ("development", "holdout"):
            (work / group).mkdir(exist_ok=True)
        cases = []
        for group in ("development", "holdout"):
            cases.extend(json.loads((root / group / "cases.json").read_text()))
        atomic(work / "development/cases.json", cases)
        previous = json.loads((continuous / "previous.json").read_text())
        current = json.loads((public / "status.json").read_text())
        atomic(
            work / "holdout/cases.json",
            [{"id": f"future-observed-{i}", "old": previous, "new": current}],
        )
        atomic(continuous / "previous.json", current)
        write_source(work / "selected.py", (continuous / "best.py").read_text())
        if state.get("last_feedback"):
            atomic(work / "feedback.json", state["last_feedback"])
        atomic(
            work / "experiment.json",
            {
                "objective": "Improve the incumbent generic encoder by at least 10% encoding CPU, no more than 2% added compressed bytes, and no more than 5% client+server CPU regression. Same tuple transport for candidate and jsonpatch baseline. Preserve all JSON values.",
                "comparison": "The initial independent qualification passed. Continuation uses native and identically packed jsonpatch baselines; jsondiff's expensive nested list alignment was fully measured in the initial study and is not rerun each trial.",
                "data": "Retired original cases are development; a newly captured real transition and a fresh deterministic adversarial seed are withheld for acceptance. Do not specialize on field names or recorded values.",
            },
        )
    publish("researching", stage="proposal")
    source = work / f"candidate-{i:02d}.py"
    if not source.exists():
        response = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("propose.py")),
                "--root",
                str(work),
                "--token-file",
                token_file,
                "--attempt",
                str(i),
            ],
            timeout=300,
            check=False,
        )
        if response.returncode == 42:
            publish("budget_exhausted", stage="original_cap")
            return
        if response.returncode:
            if (work / f"proposal-{i:02d}.json").exists():
                publish(
                    "research_waiting",
                    attempt=i + 1,
                    stage="settled_invalid_proposal",
                    last_feedback={
                        "error": "The previous settled response did not contain a complete valid implementation. Keep the next complete code concise."
                    },
                )
                return
            publish("researching", stage="same_request_retry_required")
            raise RuntimeError("Unsettled request must keep its durable ID")
    candidate = source.read_text()
    incumbent = (work / "selected.py").read_text()
    publish("researching", stage="development")
    result = evaluate(work, candidate, "development", "candidate-development", 7100 + i)
    old = evaluate(work, incumbent, "development", "incumbent-development", 7100 + i)
    qualifies = False
    if result.get("gate_passed") and old.get("correct"):
        new_total = result["totals"]["candidate"]
        old_total = old["totals"]["candidate"]
        qualifies = (
            new_total["encode_ms"] <= 0.90 * old_total["encode_ms"]
            and new_total["wire_bytes"] <= 1.02 * old_total["wire_bytes"]
            and new_total["end_to_end_cpu_ms"] <= 1.05 * old_total["end_to_end_cpu_ms"]
        )
    accepted = False
    if qualifies:
        # The source and corpus are frozen before the separate future check.
        atomic(
            work / "selection.json",
            {
                "code_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
                "holdout_sha256": hashlib.sha256(
                    (work / "holdout/cases.json").read_bytes()
                ).hexdigest(),
                "selected_at": now(),
            },
        )
        publish("researching", stage="future_check")
        future = evaluate(work, candidate, "holdout", "candidate-future", 19000 + i)
        accepted = bool(future.get("gate_passed"))
    if accepted:
        write_source(continuous / "best.py", candidate)
        state["accepted"] += 1
        state["best_attempt"] = i
    # Future-check results are never used as optimization feedback.
    feedback = {k: v for k, v in result.items() if k != "rows"}
    feedback["incumbent_development"] = old.get("totals", {}).get("candidate")
    feedback["previous_candidate_code"] = candidate
    publish(
        "research_waiting",
        attempt=i + 1,
        stage="scheduled",
        last_feedback=feedback,
        last_result={
            "attempt": i,
            "accepted": accepted,
            "development": {k: v for k, v in result.items() if k != "rows"},
            "source_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        },
    )
    store.db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args()
    trial(args.root, args.token_file)

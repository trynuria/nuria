"""Resume a budget-bound research job on Cherry, without a desktop controller.

Only development feedback informs proposals. One selected program gets one final
holdout evaluation. No generated program can publish or edit the running server.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from evaluate import evaluate

from studio.store import atomic, now
from studio.worker import Client


def run(root, token_file):
    root = Path(root)
    client = Client("http://127.0.0.1:4041", token_file)
    state_path = root / "run.json"
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {"attempt": 0, "started_at": now(), "phase": "development"}
    )

    def save(**updates):
        state.update(updates, updated_at=now(), budget=client.budget())
        atomic(state_path, state)
        print(json.dumps(state), flush=True)

    if state["phase"] in ("complete", "holdout_failed", "budget_exhausted"):
        print(json.dumps(state), flush=True)
        return
    while True:
        i = state["attempt"]
        source = root / f"candidate-{i:02d}.py"
        if state["phase"] == "development":
            if not source.exists():
                budget = client.budget()
                if budget["blocked"] or budget["remaining_micro_usd"] < 30000:
                    save(phase="budget_exhausted")
                    return
                save(stage="propose")
                response = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("propose.py")),
                        "--root",
                        str(root),
                        "--token-file",
                        token_file,
                        "--attempt",
                        str(i),
                    ],
                    timeout=300,
                    check=False,
                )
                if response.returncode:
                    # A durable unsettled call must be retried with its same ID,
                    # never abandoned for a fresh charge after a transport error.
                    save(stage="proposal_needs_retry")
                    raise RuntimeError("Proposal did not settle; preserve request ID")
            label = f"development-v2-{i:02d}"
            report_path = root / f"{label}.json"
            save(stage="evaluate")
            if report_path.exists():
                report = json.loads(report_path.read_text())
            else:
                # A crash midway through a free evaluation gets a new execution
                # directory, retaining the incomplete one for inspection.
                suffix = 0
                while (root / "executions" / label).exists():
                    suffix += 1
                    label = f"development-v2-{i:02d}-recovery-{suffix}"
                try:
                    report = evaluate(root, source, "development", label)
                except (ValueError, SyntaxError) as exc:
                    report = {"correct": False, "error": str(exc)}
                    atomic(report_path, report)
            if report.get("gate_passed"):
                selected = root / "selected.py"
                selected.write_bytes(source.read_bytes())
                save(
                    phase="holdout",
                    stage="selected",
                    selected_sha256=hashlib.sha256(selected.read_bytes()).hexdigest(),
                    development_report=label,
                )
            else:
                # These are development results only, including exact failures.
                atomic(root / "feedback.json", report)
                (root / "selected.py").write_bytes(source.read_bytes())
                save(attempt=i + 1, stage="revise")
                continue
        if state["phase"] == "holdout":
            selected = root / "selected.py"
            if (
                hashlib.sha256(selected.read_bytes()).hexdigest()
                != state["selected_sha256"]
            ):
                raise RuntimeError("Selected source changed after freezing")
            final_path = root / "holdout-final.json"
            if final_path.exists():
                report = json.loads(final_path.read_text())
            elif (root / "executions/holdout-final").exists():
                save(stage="holdout_interrupted")
                raise RuntimeError("Do not silently rerun a partly observed holdout")
            else:
                report = evaluate(root, selected, "holdout", "holdout-final")
            save(
                phase="complete" if report.get("gate_passed") else "holdout_failed",
                stage="finished",
                result=report,
            )
            return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args()
    run(args.root, args.token_file)

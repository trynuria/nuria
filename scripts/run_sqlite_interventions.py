"""Run an explicit isolated workload file through actual SQL and Brian2 state."""

import argparse
import json
import os
from pathlib import Path

from cognition.intervention_runner import MAX_SCHEDULE_BYTES, InterventionRunner
from cognition.sqlite_lab import MAX_CSV_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("schedule", type=Path)
    parser.add_argument("state_directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-id")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output path; prior evidence is preserved")
    if (
        args.csv.stat().st_size > MAX_CSV_BYTES
        or args.schedule.stat().st_size > MAX_SCHEDULE_BYTES
    ):
        parser.error("CSV or schedule exceeds the bounded lab size")
    schedule = [
        json.loads(line)
        for line in args.schedule.read_text().splitlines()
        if line.strip()
    ]
    if not schedule or len(schedule) > 4096:
        parser.error("Supply one to 4,096 explicit workload records")
    source_id = args.source_id or schedule[0].get("source_id")
    with InterventionRunner(
        args.state_directory, args.csv.read_bytes(), source_id
    ) as runner:
        records = [runner.observe(value) for value in schedule]
        summary = runner.finish()
        report = {"summary": summary, "records": records}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            os.chmod(args.output, 0o600)
            json.dump(report, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
        print(
            json.dumps(
                {
                    key: summary[key]
                    for key in (
                        "observations",
                        "completed_receipts",
                        "all_results_verified",
                        "accounting_uncertain",
                        "financial_transactions",
                    )
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()

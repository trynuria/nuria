"""Execute one host-enrolled checked report without starting a model service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cognition.analytical_report_worker import AnalyticalReportWorker
from cognition.analytical_skills import AnalyticalSkills


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registry", "state", "skill-bank"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("source-id", "checker-id", "report-id", "db-id", "job-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--skill-id")
    args = parser.parse_args(argv)
    if not args.skill_bank.is_file():
        parser.error(
            "The skill bank must already exist; promotion is a separate host operation"
        )
    bank = AnalyticalSkills(
        args.skill_bank, source_id=args.source_id, checker_id=args.checker_id
    )
    worker = None
    try:
        worker = AnalyticalReportWorker(
            args.state,
            bank,
            source_id=args.source_id,
            checker_id=args.checker_id,
            report_id=args.report_id,
            db_id=args.db_id,
            skill_id=args.skill_id,
        )
        result = worker.run(args.job_id, args.registry)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return result
    finally:
        if worker is not None:
            worker.close()
        bank.close()


if __name__ == "__main__":
    main()

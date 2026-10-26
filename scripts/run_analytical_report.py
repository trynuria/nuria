"""Execute one host-enrolled checked report without starting a model service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cognition.analytical_report_worker import AnalyticalReportWorker
from cognition.analytical_skills import AnalyticalSkills


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--enrollment", type=Path)
    for name in ("state", "skill-bank"):
        parser.add_argument("--" + name, type=Path)
    for name in ("source-id", "checker-id", "report-id", "db-id"):
        parser.add_argument("--" + name)
    parser.add_argument("--skill-id")
    args = parser.parse_args(argv)
    manual = ("state", "skill_bank", "source_id", "checker_id", "report_id", "db_id")
    if args.enrollment is not None:
        if any(getattr(args, name) is not None for name in (*manual, "skill_id")):
            parser.error(
                "Enrollment mode cannot override manual source or state identities"
            )
        from cognition.analytical_enrollment import load_enrollment

        enrolled = load_enrollment(args.enrollment)
        descriptor = enrolled["descriptor"]
        args.state, args.skill_bank = enrolled["worker_state"], enrolled["skill_bank"]
        for name in ("source_id", "checker_id", "report_id", "db_id", "skill_id"):
            setattr(args, name, descriptor[name])
    elif any(getattr(args, name) is None for name in manual):
        parser.error(
            "Manual mode requires state, skill-bank, source-id, checker-id, report-id and db-id"
        )
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

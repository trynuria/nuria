"""Enroll one completed delivered-work program as a finite reusable tool."""

import argparse
import json
from pathlib import Path

from scripts.run_retained_work import _write, acquire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin_enrollment", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--origin-store", type=Path, required=True)
    parser.add_argument("--origin-delivery", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    enrollment = json.loads(args.origin_enrollment.read_text())
    jobs = [row for row in enrollment["jobs"] if row["job_id"] == args.job_id]
    if len(jobs) != 1:
        raise ValueError("The exact compiled originating job is required")
    result = acquire(
        args.origin_store,
        jobs[0]["task"],
        job_id=args.job_id,
        profile=json.loads(args.profile.read_text()),
        origin_delivery=json.loads(args.origin_delivery.read_text()),
    )
    _write(args.output, result)
    print(
        json.dumps(
            {
                "skill_sha256": result["skill_sha256"],
                "program_sha256": result["origin"]["program_sha256"],
                "model_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()

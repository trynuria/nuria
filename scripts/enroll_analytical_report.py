"""Enroll one accepted retained analytical episode in the reviewed report profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cognition.analytical_enrollment import MAX_JSON_BYTES, enroll_report, file_hash


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "memory-snapshot", "execution-receipt", "registry"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("episode-id", "db-id", "report-id"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    file_hash(args.execution_receipt, MAX_JSON_BYTES)
    value = json.loads(args.execution_receipt.read_bytes())
    receipt = value.get("selected_receipt", value) if type(value) is dict else value
    descriptor = enroll_report(
        args.output,
        memory_snapshot=args.memory_snapshot,
        episode_id=args.episode_id,
        execution_receipt=receipt,
        registry=args.registry,
        db_id=args.db_id,
        report_id=args.report_id,
    )
    print(json.dumps(descriptor, sort_keys=True, allow_nan=False))
    return descriptor


if __name__ == "__main__":
    main()

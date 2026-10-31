"""Run or verify the recorded-data acquisition experiment."""

import argparse
import json
from pathlib import Path

from cognition.active_observation import run, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("run")
    execute.add_argument("nwb", type=Path)
    execute.add_argument("development", type=Path)
    execute.add_argument("output", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("package", type=Path)
    args = parser.parse_args()
    result = (
        run(args.nwb, args.development, args.output)
        if args.command == "run"
        else verify(args.package)
    )
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()

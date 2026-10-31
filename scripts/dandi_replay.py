"""Acquire, import and compare one pinned recorded human-organoid session."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    acquire = commands.add_parser("download")
    acquire.add_argument("output", type=Path)
    imported = commands.add_parser("import")
    imported.add_argument("nwb", type=Path)
    imported.add_argument("output", type=Path)
    replay = commands.add_parser("run")
    replay.add_argument("binned", type=Path)
    replay.add_argument("output", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("binned", type=Path)
    check.add_argument("result", type=Path)
    args = parser.parse_args()
    if args.command == "download":
        from cognition.nwb_spikes import download

        result = download(args.output)
    elif args.command == "import":
        from cognition.nwb_spikes import import_nwb

        result = import_nwb(args.nwb, args.output)
    elif args.command == "run":
        from cognition.organoid_replay import run

        result = run(args.binned, args.output)
    else:
        from cognition.organoid_replay import verify

        result = verify(args.binned, args.result)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()

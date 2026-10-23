"""Validate a private task and add an unpredictable evaluation commitment nonce."""

import argparse
import json
import os
import secrets
from pathlib import Path

from commerce.tasks import digest, validate_task


def prepare(source, destination):
    task = json.loads(source.read_text())
    task.setdefault("evaluation_nonce", secrets.token_hex(32))
    validate_task(task)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(task, out, indent=2)
        out.write("\n")
    return {"goal_id": task["id"], "contract_sha256": digest(task)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination)))

"""Load the existing private reasoning configuration without unrelated runtimes."""

import json
import os
import sqlite3
from pathlib import Path

from cognition.hosted_reasoner import (
    OpenAIReasoner,
    SharedReasoningBudget,
    _private_file,
)


def load_reasoner(path, *, require_existing_budget=False):
    _, fd = _private_file(path)
    try:
        raw = os.read(fd, 8193)
    finally:
        os.close(fd)
    if len(raw) > 8192:
        raise ValueError("Provider configuration exceeds its bound")
    config = json.loads(raw)
    if set(config) != {
        "key_file",
        "budget_file",
        "max_cost_micro_usd",
        "max_requests",
        "model",
        "reasoning_effort",
        "input_micro_usd_per_million_tokens",
        "output_micro_usd_per_million_tokens",
    }:
        raise ValueError("Provider configuration differs from the existing contract")
    if any(not Path(config[k]).is_absolute() for k in ("key_file", "budget_file")):
        raise ValueError("Explicit provider paths are required")
    if require_existing_budget:
        # Production must fail closed if its accounting volume is missing.
        # SharedReasoningBudget can create a fresh ledger for initial setup;
        # that behavior must never silently renew a deployed allowance.
        budget_path, fd = _private_file(config["budget_file"])
        os.close(fd)
        with sqlite3.connect(budget_path.as_uri() + "?mode=rw", uri=True) as db:
            if db.execute("SELECT cap,maximum FROM policy WHERE id=1").fetchall() != [
                (config["max_cost_micro_usd"], config["max_requests"])
            ]:
                raise ValueError("The existing budget policy must be preserved")
            db.execute("SELECT reserved,charged,state FROM requests").fetchall()
    budget = SharedReasoningBudget(
        config["budget_file"],
        max_cost_micro_usd=config["max_cost_micro_usd"],
        max_requests=config["max_requests"],
    )
    return OpenAIReasoner(
        config["key_file"],
        config["model"],
        reasoning_effort=config["reasoning_effort"],
        budget=budget,
        input_micro_usd_per_million_tokens=config["input_micro_usd_per_million_tokens"],
        output_micro_usd_per_million_tokens=config[
            "output_micro_usd_per_million_tokens"
        ],
    )

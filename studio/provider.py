"""Load the existing private reasoning configuration without unrelated runtimes."""

import json
import os
from pathlib import Path

from cognition.hosted_reasoner import (
    OpenAIReasoner,
    SharedReasoningBudget,
    _private_file,
)


def load_reasoner(path):
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

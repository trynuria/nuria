"""Run a registered-source analyst with explicit private hosted configuration.

No provider is discovered from the environment and no fallback is selected.
The optional policy is an explicitly supplied frozen artifact. SQL execution
evidence does not establish semantic correctness without a separate checker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cognition.hosted_reasoner import (
    OpenAIReasoner,
    SharedReasoningBudget,
    _private_file,
)
from cognition.reasoner_client import digest
from cognition.reasoning_policy import PolicyReasoner
from scripts.ask_grounded_analyst import ask

REPOSITORY = Path(__file__).resolve().parents[1]
FIELDS = {
    "key_file",
    "budget_file",
    "max_cost_micro_usd",
    "max_requests",
    "model",
    "reasoning_effort",
    "input_micro_usd_per_million_tokens",
    "output_micro_usd_per_million_tokens",
}


def _outside(path):
    path = Path(path).expanduser().absolute()
    if path.resolve().is_relative_to(REPOSITORY):
        raise ValueError("Hosted credentials and state must stay outside public source")
    return path


def _private_bytes(path, maximum):
    path = _outside(path)
    _, descriptor = _private_file(path)
    try:
        raw = os.read(descriptor, maximum + 1)
    finally:
        os.close(descriptor)
    if len(raw) > maximum:
        raise ValueError("Private configuration exceeds its byte allowance")
    return raw


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate private configuration field")
        result[key] = value
    return result


def load_reasoner(config_path, policy_path=None):
    """Construct an explicit client and durable budget without making a request.

    The returned ``configuration`` contains no credential or private path.
    ``digest(reasoner.configuration)`` binds ordinary analysis state to this
    exact provider configuration and, when supplied, the frozen policy.
    """
    config = json.loads(
        _private_bytes(config_path, 8192), object_pairs_hook=_unique_object
    )
    if type(config) is not dict or set(config) != FIELDS:
        raise ValueError("Private provider configuration fields differ from contract")
    for key in ("key_file", "budget_file"):
        if type(config[key]) is not str or not Path(config[key]).is_absolute():
            raise ValueError(
                "Credential and budget paths must be explicit absolute paths"
            )
    key_file, budget_file = (
        _outside(config["key_file"]),
        _outside(config["budget_file"]),
    )
    key = _private_bytes(key_file, 4096)
    if not key.strip():
        raise ValueError("The private provider credential file is empty")
    del key
    policy = None
    if policy_path is not None:
        policy = json.loads(_private_bytes(policy_path, 256 * 1024))
        if type(policy) is not dict or not {
            "selected_policy",
            "artifact_sha256",
            "version",
        } <= set(policy):
            raise ValueError("An explicit frozen policy artifact is required")
        # Check the artifact before creating any spending ledger.
        PolicyReasoner(None, policy)
    budget = SharedReasoningBudget(
        budget_file,
        max_cost_micro_usd=config["max_cost_micro_usd"],
        max_requests=config["max_requests"],
    )
    reasoner = OpenAIReasoner(
        key_file,
        config["model"],
        reasoning_effort=config["reasoning_effort"],
        budget=budget,
        input_micro_usd_per_million_tokens=config["input_micro_usd_per_million_tokens"],
        output_micro_usd_per_million_tokens=config[
            "output_micro_usd_per_million_tokens"
        ],
    )
    # Validate the client's full credential contract before reserving a job.
    credential = reasoner._key()
    del credential
    if policy is not None:
        wrapped = PolicyReasoner(reasoner, policy)
        wrapped.configuration = {
            "provider": reasoner.configuration,
            "frozen_policy_artifact_sha256": policy["artifact_sha256"],
            "selected_policy_id": wrapped.policy["policy_id"],
        }
        return wrapped
    return reasoner


def ask_hosted(*, provider_config, policy=None, state, **arguments):
    """Use the existing persistent analyst with an explicitly configured client."""
    state = _outside(state)
    reasoner = load_reasoner(provider_config, policy)
    return ask(
        **arguments,
        state=state,
        model_identity=digest(reasoner.configuration),
        enable_thinking=reasoner.enable_thinking,
        reasoner_factory=lambda *args, **kwargs: reasoner,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-config", required=True, type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--db-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument(
        "--memory-mode", choices=("none", "episode", "procedure"), default="procedure"
    )
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--max-tokens-per-turn", type=int, default=1536)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261026)
    arguments = vars(parser.parse_args(argv))
    try:
        report = ask_hosted(**arguments)
    except KeyboardInterrupt:
        print(
            "Interrupted; inspect the retained private attempt and budget.",
            file=sys.stderr,
        )
        return 130
    except Exception as error:
        print(
            json.dumps({"status": "error", "error_type": type(error).__name__}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, allow_nan=False, indent=2))
    return 0 if report["delivery_complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

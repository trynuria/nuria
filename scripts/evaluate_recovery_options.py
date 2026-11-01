"""Qualify reasoning options on revealed failed work under one shared allowance.

This is development recovery, not a fresh superiority evaluation. Independent
acceptance stays outside model context; prior rejected code is common evidence.
"""

import argparse
import json
import math
import re
import time
from pathlib import Path

from cognition.input_observations import inspect_enrollment
from cognition.outcome_worker import IsolatedPython, OutcomeStore, identity, run_work
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_outcome_loop import BoundedClient
from scripts.run_delivered_course import run_delivered


class QualificationEnvelope:
    """A finite sublimit; never replaces or renews the provider's shared budget."""

    def __init__(self, provider, client, initial, limits, started):
        self.provider, self.client = provider, client
        self.initial, self.limits, self.started = initial, limits, started

    @property
    def configuration(self):
        return self.provider.configuration

    def complete(self, messages, *, max_tokens, seed=0):
        current = self.provider.budget.snapshot()
        body = {
            "model": self.provider.model,
            "reasoning": {"effort": self.provider.effort},
            "input": messages,
            "max_output_tokens": max_tokens,
            "store": False,
            "stream": False,
            "service_tier": "default",
            "truncation": "disabled",
        }
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        bound = len(raw) + 1024 + 128 * len(messages)
        ceiling = math.ceil(
            (bound * self.provider.input_rate + max_tokens * self.provider.output_rate)
            / 1_000_000
        )
        spent = (
            current["accounted_cost_and_liability_micro_usd"]
            - self.initial["accounted_cost_and_liability_micro_usd"]
        )
        if (
            current["blocked"]
            or current["requests"] - self.initial["requests"] >= self.limits["calls"]
            or spent + ceiling > self.limits["additional_micro_usd"]
            or time.time() - self.started > self.limits["wall_seconds"]
        ):
            raise ValueError("Qualification sublimit or unsettled shared liability")
        return self.client.complete(messages, max_tokens=max_tokens, seed=seed)


def qualify(enrollment, workspace, configurations, resource_root, *, dependencies=()):
    if not 1 <= len(enrollment["jobs"]) <= 2 or len(configurations) != 3:
        raise ValueError(
            "This qualification requires two failed jobs and three options"
        )
    observed = inspect_enrollment(enrollment)
    limits = {"calls": 12, "additional_micro_usd": 4_000_000, "wall_seconds": 1200}
    providers, results = [], {}
    for name, path in configurations:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in {
            row[0] for row in providers
        }:
            raise ValueError("Qualification option identities must be unique")
        provider = load_reasoner(path)
        provider.timeout = 90
        provider.max_input_bytes = 32768
        provider.max_output_tokens = 2400
        providers.append((name, path, provider))
    if len({provider.budget.path.resolve() for _, _, provider in providers}) != 1:
        raise ValueError("Every option must use the same durable allowance")
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    binding = identity(
        {
            "enrollment": observed,
            "limits": limits,
            "providers": {
                name: provider.configuration for name, _, provider in providers
            },
        }
    )
    guard = workspace / "qualification-budget.json"
    if guard.exists():
        saved = json.loads(guard.read_text())
        if saved["binding_sha256"] != binding:
            raise ValueError("Qualification identity changed")
    else:
        saved = {
            "binding_sha256": binding,
            "initial": providers[0][2].budget.snapshot(),
            "started_epoch": time.time(),
        }
        with guard.open("x") as stream:
            json.dump(saved, stream, sort_keys=True, indent=2)
            stream.write("\n")
    initial, started = saved["initial"], saved["started_epoch"]
    for name, path, provider in providers:
        before = provider.budget.snapshot()
        client = QualificationEnvelope(
            provider, BoundedClient(provider, resource_root), initial, limits, started
        )

        def runner(
            prepared, state, config, *, dependencies=(), resource_root=None, method=None
        ):
            executor = IsolatedPython(
                state / "executions", dependency_roots=dependencies
            )
            store = OutcomeStore(state / "outcomes.sqlite")
            rows = [
                run_work(
                    client,
                    executor,
                    store,
                    job["task"],
                    job_id=job["job_id"],
                    experience=[],
                    max_calls=2,
                    max_tokens=2400,
                )
                for job in prepared["jobs"]
            ]
            return {
                "version": "nuria.recovery-qualification.v1",
                "jobs": rows,
                "journal": store.verify(),
                "accepted": sum(row["accepted"] is True for row in rows),
                "replayed": sum(row["replayed"] for row in rows),
            }

        state = Path(workspace) / name
        course = run_delivered(
            observed,
            state,
            path,
            runner=runner,
            dependencies=dependencies,
            resource_root=resource_root,
        )
        after = provider.budget.snapshot()
        result = {
            "provider": provider.configuration,
            "course": course,
            "requests_added": after["requests"] - before["requests"],
            "configured_micro_usd_added": (
                after["accounted_cost_and_liability_micro_usd"]
                - before["accounted_cost_and_liability_micro_usd"]
            ),
        }
        with (state / "qualification.json").open("x") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write("\n")
        results[name] = result
        print(
            json.dumps(
                {
                    "option": name,
                    "accepted": course["work"]["accepted"],
                    "delivered": len(course["delivery"]["deliveries"]),
                    "requests": result["requests_added"],
                    "configured_micro_usd": result["configured_micro_usd_added"],
                }
            ),
            flush=True,
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enrollment", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--configuration", action="append", required=True)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    configurations = [item.split("=", 1) for item in args.configuration]
    if any(len(item) != 2 for item in configurations):
        raise ValueError("Use an option name and an explicit private configuration")
    qualify(
        json.loads(args.enrollment.read_text()),
        args.workspace,
        configurations,
        args.resource_root,
        dependencies=args.dependency_root,
    )


if __name__ == "__main__":
    main()

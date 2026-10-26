"""Goal-directed planning with bounded repair over a host-enrolled tool catalog.

The model proposes data. Host validation, registered handlers and a separate
artifact checker retain execution authority. This module has no provider, key,
network, file enrollment or spending discovery of its own.
"""

from __future__ import annotations

import heapq
import json

from cognition.workflow_planner import (
    VERSION,
    catalog_view,
    execute_plan,
    source_identity,
    validate_plan,
)

SYSTEM = """Return one JSON workflow plan, without Markdown or commentary.
Choose only registered operations and bind every required input to a named
source or an earlier node's named output. Every node must contribute to output.
Honor the goal type, work and serial deadline units, source identity and case ID.
Do not invent paths, tools, facts, payments, arbitrary code or extra fields.
Plan shape: {"version":VERSION,"case_id":CASE,"source_sha256":SHA,
"nodes":[{"id":"n1","op":"OP","inputs":{"PORT":{"source":"SOURCE"}},
"arguments":{}},{"id":"n2","op":"OP","inputs":{"PORT":{"node":"n1",
"port":"OUTPUT"}},"arguments":{}}],"output":{"node":"n2","port":"OUTPUT"}}.
Operation arguments are specified by their registered schema. Empty schema
means exactly {}. Use the cheapest valid dependency chain. Tool failure feedback
is actual execution evidence; a repair is a new plan, not an invented success.
"""


def _unique_fields(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("Duplicate workflow JSON field")
        fields[key] = value
    return fields


def classical_plan(
    *, catalog, case_id, sources, goal_type, method="exact", disabled=()
):
    """Same-catalog controls: finite exact state search or cheapest-ready greedy.

    These controls solve this small typed planning problem without a model. They
    receive identical observed availability failures, not a future failure oracle.
    Empty-argument operators only; arbitrary handlers are never called in search.
    """
    if method not in ("exact", "greedy"):
        raise ValueError("Unknown registered planning control")
    metadata = catalog_view(catalog)
    needed = {goal_type}
    while True:
        expanded = needed | {
            kind
            for op in metadata.values()
            if set(op["outputs"].values()) & needed
            for kind in op["inputs"].values()
        }
        if expanded == needed:
            break
        needed = expanded
    bindings = {value["type"]: {"source": key} for key, value in sources.items()}
    initial = (0, 0, [], bindings)
    queue, serial, seen = [initial], 0, {}
    while queue:
        cost, _, nodes, bindings = heapq.heappop(queue)
        if goal_type in bindings:
            return {
                "version": VERSION,
                "case_id": case_id,
                "source_sha256": source_identity(case_id, sources),
                "nodes": nodes,
                "output": bindings[goal_type],
            }
        state = tuple(sorted(bindings))
        if state in seen and seen[state] <= cost:
            continue
        seen[state] = cost
        ready = [
            (op["work_units"], name, op)
            for name, op in metadata.items()
            if name not in disabled
            and not op["arguments"]
            and set(op["inputs"].values()) <= bindings.keys()
            and set(op["outputs"].values()) & needed
            and not set(op["outputs"].values()) <= bindings.keys()
        ]
        for work, name, op in sorted(ready)[: 1 if method == "greedy" else None]:
            node_id = "n" + str(len(nodes) + 1)
            node = {
                "id": node_id,
                "op": name,
                "inputs": {port: bindings[kind] for port, kind in op["inputs"].items()},
                "arguments": {},
            }
            updated = {
                **bindings,
                **{
                    kind: {"node": node_id, "port": port}
                    for port, kind in op["outputs"].items()
                },
            }
            serial += 1
            heapq.heappush(queue, (cost + work, serial, nodes + [node], updated))
    raise ValueError("No typed plan reaches the enrolled goal")


def run_goal(
    *,
    goal,
    catalog,
    case_id,
    sources,
    goal_type,
    maximum_work,
    deadline_units,
    checker,
    execution_log,
    reasoner=None,
    method="model",
    maximum_calls=2,
    maximum_completion=2500,
):
    """Execute one finite case, retaining validation failures and repair attempts.

    A model case has at most two calls and a shared completion ceiling, including
    reasoning tokens returned by its client. No provider retry or acceptance
    feedback is requested. A failed reader may be excluded only after a real
    execution failure. Successful outputs are independently checked, never
    accepted because the planner says they are correct.
    """
    if method == "model" and reasoner is None:
        raise ValueError("A model planner needs an explicit reasoner")
    request = {
        "version": VERSION,
        "case_id": case_id,
        "goal": goal,
        "source_sha256": source_identity(case_id, sources),
        "sources": sources,
        "goal_type": goal_type,
        "maximum_work": maximum_work,
        "deadline_units": deadline_units,
        "catalog": catalog_view(catalog),
    }
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(request, sort_keys=True)},
    ]
    attempts, disabled, completions, calls = [], [], 0, 0
    for index in range(maximum_calls):
        if method == "model":
            allowance = min(1250, maximum_completion - completions)
            if allowance < 128:
                break
            response = reasoner.complete(messages, max_tokens=allowance, seed=0)
            calls += 1
            completions += response["usage"]["completion_tokens"]
            raw = response["content"]
            attempt = {
                "model": {
                    key: value for key, value in response.items() if key != "content"
                },
                "proposal": raw,
                "execution": None,
            }
        else:
            raw = json.dumps(
                classical_plan(
                    catalog=catalog,
                    case_id=case_id,
                    sources=sources,
                    goal_type=goal_type,
                    method=method,
                    disabled=disabled,
                )
            )
            attempt = {"proposal": raw, "execution": None}
        attempts.append(attempt)
        try:
            plan = json.loads(raw, object_pairs_hook=_unique_fields)
            validate_plan(
                plan,
                catalog=catalog,
                case_id=case_id,
                sources=sources,
                goal_type=goal_type,
                maximum_work=maximum_work,
                deadline_units=deadline_units,
            )
            record = execute_plan(
                plan,
                execution_id="attempt_" + str(index + 1),
                execution_log=execution_log,
                catalog=catalog,
                case_id=case_id,
                sources=sources,
                goal_type=goal_type,
                maximum_work=maximum_work,
                deadline_units=deadline_units,
                checker=checker,
            )
        except (ValueError, TypeError, KeyError) as error:
            attempt["validation_error"] = str(error)
            feedback = {"validation_error": str(error)}
        else:
            attempt["execution"] = record
            if record["accepted"] is not None:
                break
            if record["status"] != "tool_unavailable":
                break
            failed = record["trace"][-1]["op"]
            disabled.append(failed)
            spent = sum(value["charged_work_units"] for value in execution_log.values())
            elapsed = sum(
                value["charged_deadline_units"] for value in execution_log.values()
            )
            feedback = {
                "failed_operation": failed,
                "error": "tool_unavailable",
                "charged_work_units": spent,
                "remaining_work_units": maximum_work - spent,
                "charged_deadline_units": elapsed,
                "remaining_deadline_units": deadline_units - elapsed,
                "required_action": "Produce a new plan using another enrolled reader; prior failure stays charged.",
            }
        messages.extend(
            [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": json.dumps(feedback, sort_keys=True)},
            ]
        )
    return {
        "case_id": case_id,
        "method": method,
        "attempts": attempts,
        "accepted": any(
            (row.get("execution") or {}).get("accepted") is True for row in attempts
        ),
        "model_calls": calls,
        "completion_tokens": completions,
        "charged_work_units": sum(
            row["charged_work_units"] for row in execution_log.values()
        ),
        "execution_log": execution_log,
    }

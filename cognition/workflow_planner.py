"""Typed local workflows over trusted handlers, with separate acceptance.

Plans are bounded data, never Python, shell commands or network tools. Registered
handlers and optional type checks are trusted host code, not sandboxed arbitrary
callables. Static work/deadline units describe a serial local planning contract;
they are neither USD, machine instructions nor a wall-clock guarantee.
"""

from __future__ import annotations

import copy
import hashlib
import json
import marshal
import math
import re
import time

VERSION = "nuria.workflow-plan.v1"
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")


class ToolUnavailable(RuntimeError):
    """A real registered handler cannot currently fulfill its operation."""


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _json(value, maximum=65536, *, no_urls=False):
    def check(item, depth):
        if depth > 12:
            raise ValueError("Workflow JSON nesting exceeds its bound")
        if item is None or type(item) in (bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is str and len(item.encode()) <= 4096 and "\x00" not in item:
            if no_urls and re.search(r"(?i)https?://", item):
                raise ValueError("A workflow plan cannot supply HTTP URLs")
            return
        if type(item) is list and len(item) <= 256:
            for child in item:
                check(child, depth + 1)
            return
        if (
            type(item) is dict
            and len(item) <= 256
            and all(type(key) is str for key in item)
        ):
            for key, child in item.items():
                check(key, depth + 1)
                check(child, depth + 1)
            return
        raise ValueError("Workflow data must be finite bounded JSON")

    check(value, 0)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > maximum:
        raise ValueError("Workflow JSON byte allowance exceeded")


def _name(value, *, typed=False):
    if type(value) is not str or not (TYPE if typed else IDENT).fullmatch(value):
        raise ValueError("Workflow identifier is invalid")
    return value


def _integer(value):
    if type(value) is not int or not 1 <= value <= 1_000_000:
        raise ValueError("Local workflow units need positive finite integers")
    return value


def _typed(value, name, checks):
    _json(value)
    primitives = {
        "json": lambda v: True,
        "string": lambda v: type(v) is str,
        "integer": lambda v: type(v) is int,
        "number": lambda v: type(v) in (int, float),
        "boolean": lambda v: type(v) is bool,
        "array": lambda v: type(v) is list,
        "object": lambda v: type(v) is dict,
        "null": lambda v: v is None,
    }
    validator = checks.get(name, primitives.get(name))
    if validator is not None and (
        not callable(validator) or validator(copy.deepcopy(value)) is not True
    ):
        raise ValueError("A workflow payload does not match its declared type")


def _catalog(catalog):
    if type(catalog) is not dict or not 1 <= len(catalog) <= 32:
        raise ValueError("A finite enrolled operation catalog is required")
    metadata = {}
    for name, op in catalog.items():
        _name(name)
        required = {
            "inputs",
            "outputs",
            "arguments",
            "work_units",
            "deadline_units",
            "handler",
        }
        if (
            type(op) is not dict
            or not required <= set(op)
            or set(op) - required not in (set(), {"description"})
        ):
            raise ValueError("Registered operation fields differ from contract")
        if not callable(op["handler"]):
            raise ValueError("A handler must be supplied by the trusted host")
        for side in ("inputs", "outputs"):
            if type(op[side]) is not dict or not 1 <= len(op[side]) <= 4:
                raise ValueError("Registered typed ports exceed their bound")
            for port, kind in op[side].items():
                _name(port)
                _name(kind, typed=True)
        _integer(op["work_units"])
        _integer(op["deadline_units"])
        _json(op["arguments"], 4096, no_urls=True)
        if type(op["arguments"]) is not dict or len(op["arguments"]) > 8:
            raise ValueError("Registered argument contract is malformed")
        for name_arg, rule in op["arguments"].items():
            _name(name_arg)
            rule = {"type": rule} if type(rule) is str else rule
            if (
                type(rule) is not dict
                or "type" not in rule
                or set(rule) - {"type", "choices", "minimum", "maximum"}
            ):
                raise ValueError("Unknown registered argument rule")
            _name(rule["type"], typed=True)
        handler = op["handler"]
        code = getattr(handler, "__code__", None)
        identity = (
            hashlib.sha256(marshal.dumps(code)).hexdigest()
            if code
            else digest(
                {"module": type(handler).__module__, "name": type(handler).__qualname__}
            )
        )
        metadata[name] = {key: copy.deepcopy(op[key]) for key in required - {"handler"}}
        metadata[name]["handler_sha256"] = identity
        if "description" in op:
            if type(op["description"]) is not str:
                raise ValueError("Operation descriptions must be text")
            _json(op["description"], 2048, no_urls=True)
            metadata[name]["description"] = op["description"]
    return metadata


def catalog_view(catalog):
    """Expose descriptions/types/costs only, not callable code or authority."""
    return _catalog(catalog)


def source_identity(case_id, sources):
    _name(case_id)
    if type(sources) is not dict or not 1 <= len(sources) <= 12:
        raise ValueError("A finite case source set is required")
    for name, artifact in sources.items():
        _name(name)
        if type(artifact) is not dict or set(artifact) != {"type", "value"}:
            raise ValueError("A source needs exactly its type and value")
        _name(artifact["type"], typed=True)
        _json(artifact["value"])
    _json(sources, 131072)
    return digest({"case_id": case_id, "sources": sources})


def validate_plan(
    plan,
    *,
    catalog,
    case_id,
    sources,
    goal_type,
    maximum_work,
    deadline_units,
    type_checks=None,
):
    """Validate the whole typed DAG and serial declared costs before execution."""
    checks, metadata = type_checks or {}, _catalog(catalog)
    if type(checks) is not dict or not all(callable(v) for v in checks.values()):
        raise ValueError("Host type validators must be a callable mapping")
    source_sha = source_identity(case_id, sources)
    _name(goal_type, typed=True)
    _integer(maximum_work)
    _integer(deadline_units)
    _json(plan, 16384, no_urls=True)
    if type(plan) is not dict or set(plan) != {
        "version",
        "case_id",
        "source_sha256",
        "nodes",
        "output",
    }:
        raise ValueError("Workflow plan fields differ from contract")
    if (
        plan["version"] != VERSION
        or plan["case_id"] != case_id
        or plan["source_sha256"] != source_sha
    ):
        raise ValueError("Workflow plan is not bound to this case/source")
    if type(plan["nodes"]) is not list or not 1 <= len(plan["nodes"]) <= 12:
        raise ValueError("A workflow needs one to twelve nodes")
    nodes = {}
    for node in plan["nodes"]:
        if type(node) is not dict or set(node) != {"id", "op", "inputs", "arguments"}:
            raise ValueError("Workflow node fields differ from contract")
        name = _name(node["id"])
        operation = _name(node["op"])
        if name in nodes or operation not in metadata:
            raise ValueError("Workflow node is duplicate or operation is unavailable")
        op = metadata[node["op"]]
        if type(node["inputs"]) is not dict or set(node["inputs"]) != set(op["inputs"]):
            raise ValueError(
                "Workflow input ports differ from the registered operation"
            )
        if type(node["arguments"]) is not dict or set(node["arguments"]) != set(
            op["arguments"]
        ):
            raise ValueError("Workflow arguments differ from the registered operation")
        for key, value in node["arguments"].items():
            rule = op["arguments"][key]
            rule = {"type": rule} if type(rule) is str else rule
            _typed(value, rule["type"], checks)
            if "choices" in rule and value not in rule["choices"]:
                raise ValueError("Workflow argument is outside its registered choices")
            if (
                "minimum" in rule
                and value < rule["minimum"]
                or "maximum" in rule
                and value > rule["maximum"]
            ):
                raise ValueError("Workflow argument is outside its registered range")
        nodes[name] = node
    dependencies, edges = {name: [] for name in nodes}, 0
    for name, node in nodes.items():
        for port, ref in node["inputs"].items():
            if type(ref) is not dict:
                raise ValueError("Workflow input binding must be explicit")
            if set(ref) == {"source"} and _name(ref["source"]) in sources:
                kind = sources[ref["source"]]["type"]
                _typed(sources[ref["source"]]["value"], kind, checks)
            elif set(ref) == {"node", "port"} and _name(ref["node"]) in nodes:
                outputs = metadata[nodes[ref["node"]]["op"]]["outputs"]
                if _name(ref["port"]) not in outputs:
                    raise ValueError("Workflow output port does not exist")
                kind = outputs[ref["port"]]
                dependencies[name].append(ref["node"])
                edges += 1
            else:
                raise ValueError(
                    "Workflow binding references an unknown source or node"
                )
            if kind != metadata[node["op"]]["inputs"][port]:
                raise ValueError("Workflow port types do not match")
    if edges > 12:
        raise ValueError("Workflow dependency edges exceed twelve")
    output = plan["output"]
    if (
        type(output) is not dict
        or set(output) != {"node", "port"}
        or _name(output["node"]) not in nodes
    ):
        raise ValueError("Workflow final output binding is invalid")
    if (
        metadata[nodes[output["node"]]["op"]]["outputs"].get(_name(output["port"]))
        != goal_type
    ):
        raise ValueError("Workflow final output does not match the goal type")
    order, visiting = [], set()

    def visit(name):
        if name in visiting:
            raise ValueError("Workflow dependencies contain a cycle")
        if name in order:
            return
        visiting.add(name)
        for parent in dependencies[name]:
            visit(parent)
        visiting.remove(name)
        order.append(name)

    visit(output["node"])
    if len(order) != len(nodes):
        raise ValueError("Every workflow node must contribute to the final artifact")
    work = sum(metadata[node["op"]]["work_units"] for node in nodes.values())
    duration = sum(metadata[node["op"]]["deadline_units"] for node in nodes.values())
    if work > maximum_work or duration > deadline_units:
        raise ValueError("Workflow exceeds its local work/deadline allowance")
    return {
        "case_id": case_id,
        "source_sha256": source_sha,
        "catalog_sha256": digest(metadata),
        "plan_sha256": digest(plan),
        "order": order,
        "work_units": work,
        "deadline_units": duration,
        "goal_type": goal_type,
    }


def execute_plan(
    plan,
    *,
    execution_id,
    execution_log,
    catalog,
    case_id,
    sources,
    goal_type,
    maximum_work,
    deadline_units,
    checker=None,
    type_checks=None,
):
    """Run one explicit attempt; retain failures and never automatically retry.

    The caller owns and durably retains execution_log. One explicit repair after
    tool unavailability needs a fresh plan and execution identity. Prior attempted
    static work for this case stays charged. Comparison methods need separate logs.
    Handler fingerprints identify local code objects, not all mutable globals;
    independent final acceptance remains necessary.
    """
    _name(execution_id)
    if type(execution_log) is not dict or execution_id in execution_log:
        raise ValueError(
            "Workflow execution identity was already used or log is invalid"
        )
    validated = validate_plan(
        plan,
        catalog=catalog,
        case_id=case_id,
        sources=sources,
        goal_type=goal_type,
        maximum_work=maximum_work,
        deadline_units=deadline_units,
        type_checks=type_checks,
    )
    prior = [row for row in execution_log.values() if row.get("case_id") == case_id]
    if prior and (
        len(prior) != 1
        or prior[0]["status"] != "tool_unavailable"
        or prior[0]["source_sha256"] != validated["source_sha256"]
        or prior[0]["catalog_sha256"] != validated["catalog_sha256"]
        or prior[0]["plan_sha256"] == validated["plan_sha256"]
    ):
        raise ValueError("Only one fresh-plan repair of unavailable tooling is allowed")
    prior_work = sum(row["charged_work_units"] for row in prior)
    prior_time = sum(row["charged_deadline_units"] for row in prior)
    if (
        prior_work + validated["work_units"] > maximum_work
        or prior_time + validated["deadline_units"] > deadline_units
    ):
        raise ValueError(
            "Prior failed work and the new workflow exceed the case allowance"
        )
    record = {
        "version": VERSION,
        "execution_id": execution_id,
        **validated,
        "status": "running",
        "charged_work_units": 0,
        "charged_deadline_units": 0,
        "trace": [],
        "artifacts": {},
        "final_artifact": None,
        "accepted": None,
        "verification": None,
    }
    execution_log[execution_id] = record
    nodes = {node["id"]: node for node in copy.deepcopy(plan["nodes"])}
    operations = {
        name: {
            **copy.deepcopy({k: v for k, v in op.items() if k != "handler"}),
            "handler": op["handler"],
        }
        for name, op in catalog.items()
    }
    bound_sources = copy.deepcopy(sources)
    started = time.perf_counter()
    try:
        for name in validated["order"]:
            node, inputs = nodes[name], {}
            op = operations[node["op"]]
            for port, ref in node["inputs"].items():
                inputs[port] = copy.deepcopy(
                    bound_sources[ref["source"]]["value"]
                    if "source" in ref
                    else record["artifacts"][ref["node"] + "." + ref["port"]]["value"]
                )
            event = {
                "node": name,
                "op": node["op"],
                "input_sha256": digest(inputs),
                "arguments_sha256": digest(node["arguments"]),
                "status": "started",
            }
            record["charged_work_units"] += op["work_units"]
            record["charged_deadline_units"] += op["deadline_units"]
            tick = time.perf_counter()
            try:
                result = op["handler"](inputs, copy.deepcopy(node["arguments"]))
                if type(result) is not dict or set(result) != set(op["outputs"]):
                    raise ValueError(
                        "Registered handler returned unsupported output ports"
                    )
                for port, value in result.items():
                    _typed(value, op["outputs"][port], type_checks or {})
                    artifact = {
                        "case_id": case_id,
                        "source_sha256": validated["source_sha256"],
                        "execution_id": execution_id,
                        "node": name,
                        "port": port,
                        "type": op["outputs"][port],
                        "value": copy.deepcopy(value),
                    }
                    artifact["artifact_sha256"] = digest(artifact)
                    record["artifacts"][name + "." + port] = artifact
                event.update(status="completed", output_sha256=digest(result))
            except Exception as error:
                event.update(
                    status="tool_unavailable"
                    if isinstance(error, ToolUnavailable)
                    else "handler_error",
                    error_type=type(error).__name__,
                )
                record["status"] = event["status"]
            finally:
                event["wall_ms"] = (time.perf_counter() - tick) * 1000
                record["trace"].append(event)
            if event["status"] != "completed":
                break
        else:
            output = plan["output"]
            record["final_artifact"] = record["artifacts"][
                output["node"] + "." + output["port"]
            ]
            record["status"] = "delivered_unverified"
            if checker is not None:
                try:
                    verdict = checker(copy.deepcopy(record))
                    if (
                        type(verdict) is not dict
                        or set(verdict) != {"accepted", "evidence_sha256", "checker_id"}
                        or type(verdict["accepted"]) is not bool
                    ):
                        raise ValueError("Independent workflow verdict is malformed")
                    if not SHA.fullmatch(verdict["evidence_sha256"]):
                        raise ValueError(
                            "Independent workflow evidence identity is invalid"
                        )
                    _name(verdict["checker_id"], typed=True)
                    record.update(
                        accepted=verdict["accepted"],
                        verification=copy.deepcopy(verdict),
                        status="accepted" if verdict["accepted"] else "rejected",
                    )
                except Exception as error:
                    record.update(
                        status="verification_error",
                        verification={"error_type": type(error).__name__},
                    )
    finally:
        if record["status"] == "running":
            record["status"] = "interrupted"
        record["wall_ms"] = (time.perf_counter() - started) * 1000
        record["execution_sha256"] = digest(record)
    return copy.deepcopy(record)

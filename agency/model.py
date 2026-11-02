"""mini-swe-agent adapters to Nuria's existing durable inference gateway."""

import json
from pathlib import Path

from minisweagent.agents.default import DefaultAgent

from agency.learning import VERSION as LEARNING_VERSION
from agency.learning import task_frame
from studio.store import atomic, digest
from studio.worker import BudgetExhausted, parse


def parse_action(content):
    """Recover harmless serialization duplication, never choose between actions."""
    try:
        return parse(content)
    except ValueError:
        remaining, actions = content.strip(), []
        decoder = json.JSONDecoder()
        while remaining:
            if remaining.startswith("\\n"):
                remaining = remaining[2:].lstrip()
                continue
            try:
                value, end = decoder.raw_decode(remaining)
            except json.JSONDecodeError as error:
                raise ValueError("Incomplete or ambiguous action output") from error
            if type(value) is not dict or len(actions) >= 4:
                raise ValueError("Action output must contain one unambiguous decision")
            actions.append(value)
            remaining = remaining[end:].lstrip()
        if (
            not actions
            or len(
                {digest({k: v for k, v in a.items() if k != "reason"}) for a in actions}
            )
            != 1
        ):
            raise ValueError("Multiple different decisions cannot be executed")
        return actions[-1]


def working_context(messages, maximum=36000):
    """Bound active context; the complete trajectory remains on disk."""
    normalized = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in messages
        if m["role"] in ("system", "user", "assistant")
    ]
    if len(json.dumps(normalized)) <= maximum:
        return normalized
    head, tail = normalized[:2], []
    size = len(json.dumps(head)) + 250
    for message in reversed(normalized[2:]):
        encoded = len(json.dumps(message))
        if size + encoded > maximum:
            break
        tail.insert(0, message)
        size += encoded
    return (
        head
        + [
            {
                "role": "user",
                "content": "Earlier steps are excerpted. Read /observations/trajectory.json and /observations/goal.json for the complete record and accepted goal.",
            }
        ]
        + tail
    )


class Gateway:
    def __init__(self, store, client, root):
        self.store, self.client, self.root = store, client, Path(root)

    def ask(self, name, messages, tokens=4096):
        path = self.root / "requests" / f"{name}.json"
        if path.exists():
            request = json.loads(path.read_text())
        else:
            request = {"messages": messages, "tokens": tokens}
            atomic(path, request)
        denied = path.with_name(path.stem + "-not-issued.json")
        if denied.exists() and request["tokens"] > 1024:
            return self.ask(name + "-compact", request["messages"], 1024)
        try:
            result = self.store.call(
                self.client,
                f"agency-{self.root.name}-{name}",
                request["messages"],
                request["tokens"],
            )
        except BudgetExhausted:
            # The enrolled gateway returns HTTP 402 BEFORE inserting a pending
            # call or contacting the provider. Retain that known-unissued request
            # and try the identical evidence with a smaller response allowance.
            # Unknown settlement/transport errors never enter this branch.
            atomic(
                denied,
                {
                    "status": 402,
                    "provider_dispatched": False,
                    "original_request_sha256": digest(request),
                    "retry_max_tokens": 1024,
                },
            )
            if request["tokens"] <= 1024:
                raise
            return self.ask(name + "-compact", request["messages"], 1024)
        except OSError as error:
            # A transport failure is not a tool failure. Keep this exact request
            # pending; never advance to a fresh paid decision after uncertainty.
            raise RuntimeError(
                "Inference transport interrupted; replay the frozen request"
            ) from error
        if result.get("error") and result.get("usage") is None:
            raise RuntimeError("Inference settlement needs reconciliation")
        return result

    def decision(self, name, system, evidence, tokens=4096):
        result = self.ask(
            name,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
            ],
            tokens,
        )
        try:
            return parse(result.get("content", ""))
        except (ValueError, KeyError):
            return {
                "approved": False,
                "reason": "Settled verifier response was not complete JSON",
            }


class BridgeModel:
    def __init__(self, gateway, mission=None, memory=None):
        self.gateway = gateway
        self.mission, self.memory = mission, memory

    def query(self, messages, **kwargs):
        number = sum(m["role"] == "assistant" for m in messages)
        name = f"act-{number:03d}"
        if self.memory is None:
            result = self.gateway.ask(name, working_context(messages))
        else:
            # An older outstanding request must settle or be explicitly denied
            # before a new context version can acquire another request identity.
            legacy = self.gateway.root / "requests" / (name + ".json")
            result = None
            if legacy.exists():
                try:
                    result = self.gateway.ask(name, working_context(messages))
                except BudgetExhausted:
                    pass  # HTTP 402 is a known pre-dispatch rejection only.
            if result is None:
                from agency.policy import SYSTEM

                frame = task_frame(self.mission, self.memory, messages)
                atomic(self.gateway.root / "observations" / "task-frame.json", frame)
                result = self.gateway.ask(
                    name + "-" + LEARNING_VERSION,
                    [
                        {"role": "system", "content": SYSTEM},
                        {
                            "role": "user",
                            "content": json.dumps(frame, ensure_ascii=False),
                        },
                    ],
                    4096,
                )
        if result.get("error"):
            raise RuntimeError(
                "Settled inference did not produce a complete action: "
                + str(result["error"])
            )
        content = result.get("content", "")
        try:
            action = parse_action(content)
        except ValueError:
            action = {"kind": "invalid", "reason": "Return one complete JSON action"}
        action["action_id"] = f"act-{number:03d}-{digest(action)[:12]}"
        return self.format_message(
            role="assistant", content=content, extra={"actions": [action]}
        )

    def format_message(self, **kwargs):
        return kwargs

    def format_observation_messages(self, message, outputs, template_vars=None):
        terminal = next((o for o in outputs if o.get("terminal")), None)
        return [
            {
                "role": "exit" if terminal else "user",
                "content": json.dumps(outputs, ensure_ascii=False),
                "extra": {
                    "exit_status": terminal.get("status", "") if terminal else ""
                },
            }
        ]

    def get_template_vars(self, **kwargs):
        return kwargs

    def serialize(self):
        return {
            "accounting": "Only the original inference gateway ledger is authoritative; SDK cost is not used."
        }


class PersistentAgent(DefaultAgent):
    """Checkpoint the assistant action before executing it, then its receipt."""

    def add_messages(self, *messages):
        result = super().add_messages(*messages)
        self.save(self.config.output_path)
        return result

    def save(self, path, *extra_dicts):
        data = self.serialize(*extra_dicts)
        if path:
            atomic(path, data)
            atomic(
                Path(path).parent / "observations" / "trajectory.json", data["messages"]
            )
        return data

    def restore(self):
        path = self.config.output_path
        if path and path.exists():
            data = json.loads(path.read_text())
            self.messages = data["messages"]
            self.n_calls = data["info"]["model_stats"]["api_calls"]
            return True
        return False

    def resume_step(self):
        if self.messages[-1]["role"] == "assistant":
            return self.execute_actions(self.messages[-1])
        if self.messages[-1]["role"] != "exit":
            return self.step()
        return []

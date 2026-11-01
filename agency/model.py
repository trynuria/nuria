"""mini-swe-agent adapters to Nuria's existing durable inference gateway."""

import json
from pathlib import Path

from minisweagent.agents.default import DefaultAgent

from studio.store import atomic, digest
from studio.worker import parse


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
        try:
            result = self.store.call(
                self.client,
                f"agency-{self.root.name}-{name}",
                request["messages"],
                request["tokens"],
            )
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
    def __init__(self, gateway):
        self.gateway = gateway

    def query(self, messages, **kwargs):
        number = sum(m["role"] == "assistant" for m in messages)
        result = self.gateway.ask(f"act-{number:03d}", working_context(messages))
        content = result.get("content", "")
        try:
            action = parse(content)
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

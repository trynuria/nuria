"""Interpret live Responses message phases without changing frozen study code.

The inherited client still validates, hashes and accounts for the exact response
bytes. A bounded in-memory tap lets the live gateway select the completed answer
after settlement, rather than concatenate intermediate commentary into actions.
"""

import json
from contextlib import contextmanager

from cognition.hosted_reasoner import OpenAIReasoner
from cognition.reasoner_client import ReasonerError


class _ResponseTap:
    def __init__(self, opener):
        self.opener = opener
        self.raw = None

    @contextmanager
    def open(self, request, timeout):
        with self.opener.open(request, timeout=timeout) as response:
            tap = self

            class Reader:
                def read(self, limit):
                    tap.raw = response.read(limit)
                    return tap.raw

            yield Reader()


class StudioReasoner(OpenAIReasoner):
    """The serialized live gateway accepts one final assistant message.

    Unlabelled single-message replies remain compatible. Mixed/unknown phases,
    multiple finals, and commentary without a final are protocol errors, never
    permission to choose one of several competing actions. Hidden reasoning is
    neither copied into the result nor used to choose an action.
    """

    @property
    def configuration(self):
        return {
            **super().configuration,
            "output_contract": "nuria.responses-final-phase.v1",
        }

    def complete(self, messages, *, max_tokens=4096, seed=0):
        original = self._opener
        tap = _ResponseTap(original)
        self._opener = tap
        try:
            result = super().complete(messages, max_tokens=max_tokens, seed=seed)
        finally:
            self._opener = original
        # The inherited parser has already validated the shape, completion and
        # usage of these exact bytes. Do not alter them before hashing/settlement.
        output = json.loads(tap.raw)["output"]
        visible = [
            {
                "phase": item.get("phase"),
                "content": "".join(part["text"] for part in item["content"]),
            }
            for item in output
            if item.get("type") == "message"
        ]
        phases = [item["phase"] for item in visible]
        if phases == [None]:
            final = visible[0]["content"]
        elif (
            phases
            and phases[-1] == "final_answer"
            and all(phase == "commentary" for phase in phases[:-1])
        ):
            final = visible[-1]["content"]
        else:
            final = ""
        if not final.strip():
            raise ReasonerError(
                "model_protocol_error",
                usage=result["usage"],
                diagnostic={
                    key: result[key]
                    for key in (
                        "request_sha256",
                        "response_sha256",
                        "contract_sha256",
                        "reservation_id",
                    )
                },
            )
        return {**result, "content": final, "output_messages": visible}

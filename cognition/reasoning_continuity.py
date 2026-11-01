"""Stateless Responses transport and opaque reasoning continuity.

The original text-only transport stays unchanged. This optional adapter replays
complete provider output items rather than dropping their opaque state. Nothing
here decodes, publishes or treats that state as evidence of consciousness.
"""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import sqlite3
import time
import urllib.error
import urllib.request

from cognition.hosted_reasoner import ENDPOINT, _cost, _integer, digest
from cognition.reasoner_client import ReasonerError

VERSION = "nuria.reasoning-continuity.v1"
MAX_INPUT_TOKENS = 922000
LONG_CONTEXT_THRESHOLD = 272000


class ContinuityTransport:
    """Budgeted text-only Responses calls accepting prior provider items.

    A reservation covers the model's documented maximum input at the higher
    long-context/cache-write rates. Actual accounting uses a conservative
    cache-write input rate for every observed input token. No hosted tools,
    tiers, subscriptions or hidden server-managed agent loop are enabled.
    """

    def __init__(self, base):
        if (
            base.model != "gpt-6.1-sol"
            or base.input_rate != 2_000_000
            or base.output_rate != 10_000_000
        ):
            raise ValueError("This pricing envelope is enrolled only for GPT-6.1 Sol")
        self.base = base
        self.input_rate = 2_500_000
        self.output_rate = 10_000_000

    def __getattr__(self, name):
        return getattr(self.base, name)

    @property
    def configuration(self):
        return {
            **self.base.configuration,
            "version": VERSION,
            "input_micro_usd_per_million_tokens": self.input_rate,
            "input_envelope": "Documented model maximum, including opaque input; no hosted tools",
            "maximum_priced_input_tokens": MAX_INPUT_TOKENS,
            "long_context_threshold": LONG_CONTEXT_THRESHOLD,
            "long_context_input_multiplier": 2,
            "long_context_output_multiplier": 1.5,
            "accounting": "Conservative cache-write input rate for all inputs; not provider invoice",
            "reasoning_context_request": "model_default; effective context checked separately",
        }

    def reservation_ceiling(self, max_tokens):
        return _cost(
            MAX_INPUT_TOKENS,
            max_tokens,
            self.input_rate * 2,
            self.output_rate * 3 // 2,
        )

    def complete(self, messages, *, max_tokens=2400, seed=0):
        _integer(max_tokens, 1, min(2400, self.max_output_tokens))
        _integer(seed, 0, 2**32 - 1)
        if type(messages) is not list or not 1 <= len(messages) <= 64:
            raise ValueError("Continuity input exceeds its item allowance")
        for item in messages:
            if type(item) is not dict or item.get("type", "message") not in (
                "message",
                "reasoning",
            ):
                raise ValueError(
                    "Only text messages and opaque reasoning items are allowed"
                )
        body = {
            "model": self.model,
            "reasoning": {"effort": self.effort},
            "input": messages,
            "max_output_tokens": max_tokens,
            "store": False,
            "stream": False,
            "service_tier": "default",
            "truncation": "disabled",
        }
        raw_request = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        if len(raw_request) > self.max_input_bytes:
            raise ValueError("Continuity request byte allowance exhausted")
        key = self.base._key()
        request = urllib.request.Request(
            ENDPOINT,
            data=raw_request,
            method="POST",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "User-Agent": "Nuria/reasoning-continuity",
            },
        )
        del key
        contract_hash = digest(self.configuration)
        diagnostic = {"request_sha256": digest(body), "contract_sha256": contract_hash}
        reservation = self.budget.reserve(
            diagnostic["request_sha256"],
            contract_hash,
            self.reservation_ceiling(max_tokens),
            contract=self.configuration,
        )
        diagnostic["reservation_id"] = reservation
        started = time.monotonic()
        try:
            with self.base._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            self.budget.ambiguous(reservation)
            error.close()
            raise ReasonerError(
                f"model_http_{error.code}", diagnostic=diagnostic
            ) from None
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            http.client.HTTPException,
        ):
            self.budget.ambiguous(reservation)
            raise ReasonerError(
                "model_transport_error", diagnostic=diagnostic
            ) from None
        if len(raw) > self.max_response_bytes:
            self.budget.ambiguous(reservation)
            raise ReasonerError("model_response_limit", diagnostic=diagnostic)
        diagnostic["response_sha256"] = hashlib.sha256(raw).hexdigest()
        try:
            value = json.loads(raw)
            tokens = value["usage"]
            prompt = _integer(tokens["input_tokens"], 0, MAX_INPUT_TOKENS)
            completion = _integer(tokens["output_tokens"], 0, max_tokens)
            total = _integer(tokens["total_tokens"], 0, MAX_INPUT_TOKENS + max_tokens)
            reasoning = _integer(
                tokens["output_tokens_details"]["reasoning_tokens"], 0, completion
            )
            if total != prompt + completion or value.get("service_tier") not in (
                None,
                "default",
            ):
                raise ValueError("Unpriced or inconsistent usage")
            if not str(value.get("model", "")).startswith("gpt-6.1-sol"):
                raise ValueError("Unpriced model identity")
            usage = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "reasoning_tokens": reasoning,
            }
        except (ValueError, KeyError, TypeError, IndexError, RecursionError):
            self.budget.ambiguous(reservation)
            raise ReasonerError("model_usage_unknown", diagnostic=diagnostic) from None
        multiplier = 2 if prompt > LONG_CONTEXT_THRESHOLD else 1
        cost = _cost(
            prompt,
            completion,
            self.input_rate * multiplier,
            self.output_rate * (3 if multiplier == 2 else 2) // 2,
        )
        try:
            self.budget.settle(reservation, cost_micro_usd=cost, usage=usage)
        except (ValueError, OSError, sqlite3.Error):
            raise ReasonerError(
                "model_cost_accounting_error", usage=usage, diagnostic=diagnostic
            ) from None
        try:
            if value["status"] == "incomplete":
                raise ReasonerError(
                    "model_output_incomplete", usage=usage, diagnostic=diagnostic
                )
            if value["status"] != "completed" or value.get("error") is not None:
                raise ValueError("The response did not complete")
            output = value["output"]
            if type(output) is not list or not 1 <= len(output) <= 32:
                raise ValueError("Invalid response items")
            parts, opaque = [], []
            for item in output:
                if type(item) is not dict:
                    raise ValueError("Invalid response item")
                if item.get("type") == "reasoning":
                    encrypted = item.get("encrypted_content")
                    if type(encrypted) is not str or not encrypted:
                        raise ValueError("Opaque reasoning state is unavailable")
                    opaque.append(digest(item))
                elif item.get("type") == "message" and item.get("role") == "assistant":
                    if type(item.get("content")) is not list:
                        raise ValueError("Unexpected response content")
                    for part in item["content"]:
                        if (
                            type(part) is not dict
                            or part.get("type") != "output_text"
                            or type(part.get("text")) is not str
                        ):
                            raise ValueError("Unexpected response content")
                        parts.append(part["text"])
                else:
                    raise ValueError("Unenrolled response item")
            content = "".join(parts)
            if not content.strip():
                raise ValueError("No complete program response")
            reasoning_meta = value.get("reasoning") or {}
            if type(reasoning_meta) is not dict:
                raise ValueError("Malformed reasoning metadata")
        except (ValueError, KeyError, TypeError, IndexError, RecursionError):
            raise ReasonerError(
                "continuity_output_unavailable", usage=usage, diagnostic=diagnostic
            ) from None
        return {
            "content": content,
            "usage": usage,
            "output_items": output,
            "reasoning_context": reasoning_meta.get("context"),
            "opaque_item_sha256": opaque,
            "configured_model": self.model,
            "reported_model": value.get("model"),
            **diagnostic,
            "accounted_cost_micro_usd": cost,
            "finish_reason": "stop",
            "wall_seconds": time.monotonic() - started,
        }


class ContinuitySession:
    """Bridge the existing visible work protocol to complete native input items.

    A new two-message job resets the transient session. Cross-job experience
    continues to use the existing explicit outcome history. Returned work
    records contain opaque hashes only, never the encrypted provider material.
    """

    def __init__(self, client, *, keep_reasoning):
        if type(keep_reasoning) is not bool:
            raise ValueError("Choose an explicit continuity condition")
        self.client, self.keep = client, keep_reasoning
        self.visible, self.native, self.last_output, self.last_text = [], [], [], None
        self.calls = self.completions = 0

    @property
    def configuration(self):
        return {**self.client.configuration, "keep_opaque_reasoning": self.keep}

    def complete(self, messages, *, max_tokens, seed=0):
        if type(messages) is not list or any(
            type(m) is not dict
            or set(m) != {"role", "content"}
            or m["role"] not in ("system", "user", "assistant")
            or type(m["content"]) is not str
            for m in messages
        ):
            raise ValueError("The work bridge accepts explicit visible messages only")
        if len(messages) == 2:
            self.calls = self.completions = 0
            native = copy.deepcopy(messages)
        else:
            complete_step = (
                len(messages) == len(self.visible) + 2
                and messages[:-2] == self.visible
                and messages[-2] == {"role": "assistant", "content": self.last_text}
                and self.last_text is not None
            )
            truncated_step = (
                len(messages) == len(self.visible) + 1
                and messages[:-1] == self.visible
                and self.last_text is None
            )
            if (
                not self.visible
                or not (complete_step or truncated_step)
                or messages[-1]["role"] != "user"
            ):
                raise ValueError("Visible history changed or omitted a work step")
            native = (
                self.native + self.last_output + [messages[-1]]
                if self.keep
                else copy.deepcopy(messages)
            )
        if self.calls >= 5 or self.completions + max_tokens > 7200:
            raise ValueError("The shared per-job reasoning envelope is exhausted")
        self.calls += 1
        try:
            result = self.client.complete(native, max_tokens=max_tokens, seed=seed)
        except ReasonerError as error:
            if error.code == "model_output_incomplete" and error.usage is not None:
                self.completions += error.usage["completion_tokens"]
                self.visible, self.native = (
                    copy.deepcopy(messages),
                    copy.deepcopy(native),
                )
                # Neither condition gets a partial proposal or incomplete opaque
                # item. Earlier complete state and the full charge are retained.
                self.last_output, self.last_text = [], None
            raise
        self.completions += result["usage"]["completion_tokens"]
        self.visible, self.native = copy.deepcopy(messages), copy.deepcopy(native)
        self.last_output, self.last_text = (
            copy.deepcopy(result["output_items"]),
            result["content"],
        )
        if self.keep and result.get("reasoning_context") != "all_turns":
            raise ReasonerError(
                "continuity_context_unconfirmed",
                usage=result["usage"],
                diagnostic={"effective_context": result.get("reasoning_context")},
            )
        return {key: val for key, val in result.items() if key != "output_items"}

"""Bounded access to a separately operated, loopback reasoning model.

The public application and historical neural workers do not import this client.
The configured model process owns inference; a response model name is not proof
of its weights. Runtime artifact identities belong in the experiment manifest.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class ReasonerError(RuntimeError):
    """A failed reply, preserving independently parsed usage when available."""

    def __init__(self, code, *, usage=None, diagnostic=None):
        super().__init__(code)
        self.code = code
        self.usage = None if usage is None else dict(usage)
        self.usage_unknown = usage is None
        self.diagnostic = {} if diagnostic is None else dict(diagnostic)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Reasoner bound must be an exact integer")
    return value


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _diagnostic_shape(value):
    """Retain bounded protocol shape, never generated content or reasoning."""
    shape = {}
    if type(value) is not dict:
        return {"response_type": type(value).__name__}
    choices = value.get("choices")
    if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
        return {"choices_type": type(choices).__name__}
    choice = choices[0]
    finish = choice.get("finish_reason")
    shape["finish_reason"] = (
        finish.encode("utf8", errors="replace")[:64].decode("utf8", errors="ignore")
        if type(finish) is str
        else None
        if finish is None
        else {"type": type(finish).__name__}
    )
    message = choice.get("message")
    if type(message) is not dict:
        shape["message_type"] = type(message).__name__
        return shape
    shape["content_type"] = type(message.get("content")).__name__
    calls = message.get("tool_calls")
    names = []
    if type(calls) is list:
        for call in calls[:8]:
            function = call.get("function") if type(call) is dict else None
            name = function.get("name") if type(function) is dict else None
            if type(name) is str:
                names.append(
                    name.encode("utf8", errors="replace")[:64].decode(
                        "utf8", errors="ignore"
                    )
                )
    shape["tool_call_names"] = names
    return shape


class LocalReasoner:
    """One OpenAI-compatible request, with no implicit retries or remote fallback.

    This development adapter accepts a literal loopback address, disables proxy
    inheritance and refuses redirects. It reads no provider/account credentials.
    A future hosted adapter needs its own explicit project configuration.
    """

    def __init__(
        self,
        endpoint,
        model,
        *,
        timeout_seconds=120,
        max_input_bytes=120000,
        max_response_bytes=524288,
        enable_thinking=True,
    ):
        parsed = urllib.parse.urlsplit(endpoint)
        try:
            local = ipaddress.ip_address(parsed.hostname or "").is_loopback
            port = parsed.port
        except ValueError:
            local, port = False, None
        if (
            parsed.scheme != "http"
            or not local
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/v1/chat/completions"
        ):
            raise ValueError("Reasoner endpoint must be an explicit loopback chat URL")
        if type(model) is not str or not 1 <= len(model.encode()) <= 256:
            raise ValueError("Reasoner needs a bounded model identity")
        self.endpoint, self.model = endpoint, model
        self.timeout = _integer(timeout_seconds, 1, 600)
        self.max_input_bytes = _integer(max_input_bytes, 1024, 262144)
        self.max_response_bytes = _integer(max_response_bytes, 1024, 1048576)
        if type(enable_thinking) is not bool:
            raise ValueError("Reasoning mode must be explicit")
        self.enable_thinking = enable_thinking
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def complete(self, messages, *, max_tokens=768, seed=0):
        """Return content and observed token usage; malformed usage stops the job."""
        _integer(max_tokens, 1, 4096)
        _integer(seed, 0, 2**32 - 1)
        if type(messages) is not list or not 1 <= len(messages) <= 64:
            raise ValueError("Reasoner messages exceed their bound")
        clean = []
        for message in messages:
            if (
                type(message) is not dict
                or set(message) != {"role", "content"}
                or message["role"] not in ("system", "user", "assistant")
                or type(message["content"]) is not str
            ):
                raise ValueError("Malformed reasoner message")
            clean.append(dict(message))
        request_body = {
            "model": self.model,
            "messages": clean,
            "max_tokens": max_tokens,
            "temperature": 0,
            "seed": seed,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
        }
        raw_request = json.dumps(request_body, ensure_ascii=False).encode()
        if len(raw_request) > self.max_input_bytes:
            raise ValueError("Reasoner input byte allowance is exhausted")
        request = urllib.request.Request(
            self.endpoint,
            data=raw_request,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Nuria/grounded-agent",
            },
        )
        diagnostic = {"request_sha256": digest(request_body)}
        start = time.monotonic()
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            raise ReasonerError(
                f"model_http_{error.code}", diagnostic=diagnostic
            ) from None
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            http.client.HTTPException,
        ):
            raise ReasonerError(
                "model_transport_error", diagnostic=diagnostic
            ) from None
        if len(raw) > self.max_response_bytes:
            raise ReasonerError("model_response_limit", diagnostic=diagnostic)
        diagnostic["response_sha256"] = hashlib.sha256(raw).hexdigest()
        observed_usage = None
        try:
            value = json.loads(raw)
            diagnostic.update(_diagnostic_shape(value))
            usage = value["usage"]
            prompt = _integer(usage["prompt_tokens"], 0, 1000000)
            completion = _integer(usage["completion_tokens"], 0, max_tokens)
            if "total_tokens" in usage:
                total = _integer(usage["total_tokens"], 0, 1000000 + max_tokens)
                if total != prompt + completion:
                    raise ValueError("Token total differs")
            observed_usage = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            }
            choices = value["choices"]
            if type(choices) is not list or len(choices) != 1:
                raise ValueError("Expected one model choice")
            message = choices[0]["message"]
            content = message["content"]
            finish_reason = choices[0].get("finish_reason")
            if finish_reason == "length" and (
                content is None or type(content) is str and not content.strip()
            ):
                raise ReasonerError(
                    "model_output_incomplete",
                    usage=observed_usage,
                    diagnostic=diagnostic,
                )
            if (
                type(content) is not str
                or not content.strip()
                or message.get("tool_calls")
                or len(content.encode()) > self.max_response_bytes
            ):
                raise ValueError("Expected one content-only model reply")
            if finish_reason not in (None, "stop", "length"):
                raise ValueError("Unsupported model finish reason")
        except (ValueError, KeyError, TypeError, IndexError, RecursionError):
            raise ReasonerError(
                "model_protocol_error", usage=observed_usage, diagnostic=diagnostic
            ) from None
        return {
            "content": content,
            "usage": observed_usage,
            "configured_model": self.model,
            "reported_model": value.get("model"),
            "request_sha256": diagnostic["request_sha256"],
            "response_sha256": diagnostic["response_sha256"],
            "finish_reason": finish_reason,
            "wall_seconds": time.monotonic() - start,
        }

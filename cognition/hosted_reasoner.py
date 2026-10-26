"""Explicit hosted reasoning with a durable, shared spending allowance.

This adapter is optional and owns no environment-based credentials or fallback.
Its ledger accounts at host-supplied token rates, without cache discounts; it is
not a provider invoice. Prices and the text-input token envelope must be reviewed
before live use. Unknown settlement blocks later calls until host reconciliation.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import sqlite3
import stat
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

from cognition.reasoner_client import ReasonerError, _integer, _NoRedirect, digest

VERSION = "nuria.hosted-reasoner.v1"
ENDPOINT = "https://api.openai.com/v1/responses"


def _private_file(path, *, create=False):
    path = Path(path).absolute()
    parent = path.parent.stat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
    ):
        raise ValueError("Hosted state needs a private owner-controlled directory")
    flags = os.O_RDWR if create else os.O_RDONLY
    flags |= os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    descriptor = os.open(path, flags, 0o600)
    current = os.fstat(descriptor)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_uid != os.getuid()
        or stat.S_IMODE(current.st_mode) != 0o600
        or current.st_nlink != 1
    ):
        os.close(descriptor)
        raise ValueError("Hosted state must be an owner-only regular file")
    return path, descriptor


def _cost(input_tokens, output_tokens, input_rate, output_rate):
    """Ceil micro-USD using explicit micro-USD per million token rates."""
    return (input_tokens * input_rate + output_tokens * output_rate + 999999) // 1000000


class SharedReasoningBudget:
    """A finite allowance shared by all clients opening this private ledger.

    One unresolved request blocks further reservations, including after process
    loss. The allowance never renews on reopening. No automatic reconciliation
    can release an ambiguous liability.
    """

    def __init__(self, path, *, max_cost_micro_usd, max_requests=128):
        self.limit = _integer(max_cost_micro_usd, 1, 10_000_000_000)
        self.maximum = _integer(max_requests, 1, 10000)
        self.path, descriptor = _private_file(path, create=True)
        os.close(descriptor)
        with self._connection() as database:
            database.executescript(
                "CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1),"
                " cap INTEGER NOT NULL, maximum INTEGER NOT NULL);"
                "CREATE TABLE IF NOT EXISTS contracts (sha256 TEXT PRIMARY KEY,"
                " value TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY,"
                " request_sha256 TEXT NOT NULL, contract_sha256 TEXT NOT NULL,"
                " reserved INTEGER NOT NULL, charged INTEGER, state TEXT NOT NULL,"
                " usage TEXT);"
            )
            database.execute(
                "INSERT OR IGNORE INTO policy VALUES (1,?,?)",
                (self.limit, self.maximum),
            )
            if database.execute("SELECT cap,maximum FROM policy").fetchall() != [
                (self.limit, self.maximum)
            ]:
                raise ValueError("An existing allowance cannot be silently changed")

    @contextmanager
    def _connection(self):
        _, descriptor = _private_file(self.path)
        os.close(descriptor)
        database = sqlite3.connect(self.path, timeout=5, isolation_level="IMMEDIATE")
        try:
            database.execute("PRAGMA synchronous=FULL")
            database.execute("PRAGMA busy_timeout=5000")
            with database:
                yield database
        finally:
            database.close()

    def reserve(
        self, request_sha256, contract_sha256, cost_micro_usd, *, contract=None
    ):
        amount = _integer(cost_micro_usd, 1, self.limit)
        for identity in (request_sha256, contract_sha256):
            if type(identity) is not str or not re.fullmatch("[0-9a-f]{64}", identity):
                raise ValueError(
                    "A reservation needs exact request and contract hashes"
                )
        if contract is not None and digest(contract) != contract_sha256:
            raise ValueError("The configuration does not match its reservation hash")
        with self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            rows = database.execute(
                "SELECT reserved,charged,state FROM requests"
            ).fetchall()
            if any(state != "settled" for _, _, state in rows):
                raise ValueError("A prior request has unresolved spending liability")
            spent = sum(charged for _, charged, _ in rows)
            if len(rows) >= self.maximum or spent + amount > self.limit:
                raise ValueError("The shared reasoning allowance is exhausted")
            reservation = uuid.uuid4().hex
            if contract is not None:
                database.execute(
                    "INSERT OR IGNORE INTO contracts VALUES (?,?)",
                    (contract_sha256, json.dumps(contract, sort_keys=True)),
                )
            database.execute(
                "INSERT INTO requests VALUES (?,?,?,?,NULL,'reserved',NULL)",
                (reservation, request_sha256, contract_sha256, amount),
            )
        return reservation

    def ambiguous(self, reservation):
        with self._connection() as database:
            database.execute(
                "UPDATE requests SET state='ambiguous' WHERE id=? AND state='reserved'",
                (reservation,),
            )

    def settle(self, reservation, *, cost_micro_usd, usage):
        amount = _integer(cost_micro_usd, 0, 10_000_000_000)
        with self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT reserved,state FROM requests WHERE id=?", (reservation,)
            ).fetchone()
            if row is None or row[1] != "reserved":
                raise ValueError("Only the original pending reservation can settle")
            if amount > row[0]:
                database.execute(
                    "UPDATE requests SET state='ambiguous',charged=?,usage=? WHERE id=?",
                    (amount, json.dumps(usage, sort_keys=True), reservation),
                )
                overrun = True
            else:
                database.execute(
                    "UPDATE requests SET state='settled',charged=?,usage=? WHERE id=?",
                    (amount, json.dumps(usage, sort_keys=True), reservation),
                )
                overrun = False
        if overrun:
            raise ValueError("Observed usage exceeded the reserved cost envelope")

    def snapshot(self):
        with self._connection() as database:
            rows = database.execute(
                "SELECT reserved,charged,state FROM requests"
            ).fetchall()
        return {
            "maximum_cost_micro_usd": self.limit,
            "maximum_requests": self.maximum,
            "requests": len(rows),
            "settled_cost_micro_usd": sum(
                charged for _, charged, state in rows if state == "settled"
            ),
            "unresolved_liability_micro_usd": sum(
                max(reserved, charged or 0)
                for reserved, charged, state in rows
                if state != "settled"
            ),
            "blocked": any(state != "settled" for _, _, state in rows),
        }


class OpenAIReasoner:
    """Text-only Responses API client implementing LocalReasoner's interface.

    ``seed`` is accepted for caller compatibility but is not sent: Responses
    does not provide the local runtime's seeded sampling contract. No hidden
    reasoning items, server-side conversation IDs, tools or model fallbacks are
    requested or retained. ``store=False`` is not a claim of zero data retention.
    """

    def __init__(
        self,
        key_file,
        model,
        *,
        reasoning_effort,
        budget,
        input_micro_usd_per_million_tokens,
        output_micro_usd_per_million_tokens,
        timeout_seconds=180,
        max_input_bytes=120000,
        max_response_bytes=1048576,
        max_output_tokens=32768,
    ):
        if type(model) is not str or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", model):
            raise ValueError("An explicit bounded model ID is required")
        if reasoning_effort not in (
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            raise ValueError("An explicit supported reasoning effort is required")
        if not isinstance(budget, SharedReasoningBudget):
            raise ValueError("A shared durable reasoning allowance is required")
        self.key_file = Path(key_file)
        self.model, self.effort, self.budget = model, reasoning_effort, budget
        self.timeout = _integer(timeout_seconds, 1, 600)
        self.max_input_bytes = _integer(max_input_bytes, 1024, 262144)
        self.max_response_bytes = _integer(max_response_bytes, 1024, 4194304)
        self.max_output_tokens = _integer(max_output_tokens, 1, 65536)
        self.input_rate = _integer(input_micro_usd_per_million_tokens, 1, 10**12)
        self.output_rate = _integer(output_micro_usd_per_million_tokens, 1, 10**12)
        self.enable_thinking = reasoning_effort != "none"
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    @property
    def configuration(self):
        return {
            "version": VERSION,
            "endpoint": ENDPOINT,
            "model": self.model,
            "reasoning_effort": self.effort,
            "service_tier": "default",
            "store": False,
            "seed_supported": False,
            "timeout_seconds": self.timeout,
            "max_input_bytes": self.max_input_bytes,
            "max_response_bytes": self.max_response_bytes,
            "max_output_tokens": self.max_output_tokens,
            "input_micro_usd_per_million_tokens": self.input_rate,
            "output_micro_usd_per_million_tokens": self.output_rate,
            "input_envelope": "request_utf8_bytes+1024+128_per_message;text_only",
            "accounting": "configured_uncached_token_rates;not_provider_invoice",
            "budget_cap_micro_usd": self.budget.limit,
            "budget_maximum_requests": self.budget.maximum,
        }

    def _key(self):
        _, descriptor = _private_file(self.key_file)
        try:
            raw = os.read(descriptor, 4097)
        finally:
            os.close(descriptor)
        try:
            key = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            raise ValueError("The private credential file is invalid") from None
        if len(raw) > 4096 or not re.fullmatch(r"[A-Za-z0-9_-]{16,4096}", key):
            raise ValueError("The private credential file is invalid")
        return key

    def complete(self, messages, *, max_tokens=4096, seed=0):
        _integer(max_tokens, 1, self.max_output_tokens)
        _integer(seed, 0, 2**32 - 1)
        if type(messages) is not list or not 1 <= len(messages) <= 64:
            raise ValueError("Reasoner messages exceed their bound")
        clean = []
        for item in messages:
            if (
                type(item) is not dict
                or set(item) != {"role", "content"}
                or item["role"] not in ("system", "user", "assistant")
                or type(item["content"]) is not str
            ):
                raise ValueError("Malformed reasoner message")
            clean.append(dict(item))
        body = {
            "model": self.model,
            "reasoning": {"effort": self.effort},
            "input": clean,
            "max_output_tokens": max_tokens,
            "store": False,
            "stream": False,
            "service_tier": "default",
            "truncation": "disabled",
        }
        raw_request = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        if len(raw_request) > self.max_input_bytes:
            raise ValueError("Reasoner input byte allowance is exhausted")
        key = self._key()
        request = urllib.request.Request(
            ENDPOINT,
            data=raw_request,
            method="POST",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "User-Agent": "Nuria/hosted-reasoner",
            },
        )
        del key
        input_bound = len(raw_request) + 1024 + 128 * len(messages)
        contract_hash = digest(self.configuration)
        diagnostic = {"request_sha256": digest(body), "contract_sha256": contract_hash}
        reservation = self.budget.reserve(
            diagnostic["request_sha256"],
            contract_hash,
            _cost(input_bound, max_tokens, self.input_rate, self.output_rate),
            contract=self.configuration,
        )
        diagnostic["reservation_id"] = reservation
        started = time.monotonic()
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
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
        usage = None
        try:
            value = json.loads(raw)
            tokens = value["usage"]
            prompt = _integer(tokens["input_tokens"], 0, input_bound)
            completion = _integer(tokens["output_tokens"], 0, max_tokens)
            total = _integer(tokens["total_tokens"], 0, input_bound + max_tokens)
            reasoning = _integer(
                tokens["output_tokens_details"]["reasoning_tokens"], 0, completion
            )
            if total != prompt + completion:
                raise ValueError("Token total differs")
            if value.get("service_tier") not in (None, "default"):
                raise ValueError("The response used an unpriced service tier")
            usage = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "reasoning_tokens": reasoning,
            }
        except (ValueError, KeyError, TypeError, IndexError, RecursionError):
            self.budget.ambiguous(reservation)
            raise ReasonerError("model_usage_unknown", diagnostic=diagnostic) from None
        try:
            self.budget.settle(
                reservation,
                cost_micro_usd=_cost(
                    prompt, completion, self.input_rate, self.output_rate
                ),
                usage=usage,
            )
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
                raise ValueError("The model did not complete")
            parts = []
            output = value["output"]
            if type(output) is not list or len(output) > 32:
                raise ValueError("Unexpected output items")
            for item in output:
                if type(item) is not dict:
                    raise ValueError("Unexpected output item")
                if item.get("type") == "reasoning":
                    continue
                if item.get("type") != "message" or item.get("role") != "assistant":
                    raise ValueError("Only assistant text is accepted")
                if type(item.get("content")) is not list:
                    raise ValueError("Expected assistant text parts")
                for part in item["content"]:
                    if (
                        type(part) is not dict
                        or part.get("type") != "output_text"
                        or type(part.get("text")) is not str
                    ):
                        raise ValueError("Only assistant text is accepted")
                    parts.append(part["text"])
            content = "".join(parts)
            if not content.strip():
                raise ValueError("Expected a nonempty final reply")
        except (ValueError, KeyError, TypeError, IndexError, RecursionError):
            raise ReasonerError(
                "model_protocol_error", usage=usage, diagnostic=diagnostic
            ) from None
        return {
            "content": content,
            "usage": usage,
            "configured_model": self.model,
            "reported_model": value.get("model"),
            "request_sha256": diagnostic["request_sha256"],
            "response_sha256": diagnostic["response_sha256"],
            "contract_sha256": contract_hash,
            "reservation_id": reservation,
            "accounted_cost_micro_usd": _cost(
                prompt, completion, self.input_rate, self.output_rate
            ),
            "finish_reason": "stop",
            "wall_seconds": time.monotonic() - started,
        }

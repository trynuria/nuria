"""Exact receipt checks with bounded SQL pages and decompression.

The only verification cursor is process-local. A restart begins at genesis.
Every page checks its fixed target and previously verified boundary against the
database; a restored/replaced boundary invalidates the scan. This is local hash
integrity, not an independent witness against a malicious database writer.
"""

from __future__ import annotations

import hashlib
import json
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

ZERO = "0" * 64
SCOPE = (
    "Local input, spike and receipt hash integrity. "
    "No independent witness or Solana anchoring."
)


def canonical(value):
    # Byte-for-byte compatible with the original life.canonical helper.
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


class VerificationError(RuntimeError):
    def __init__(self, message, sequence=None):
        super().__init__(message)
        self.sequence = sequence


class ChainChanged(VerificationError):
    pass


class VerificationUnavailable(VerificationError):
    pass


class ExportLimitReached(VerificationUnavailable):
    pass


@dataclass(frozen=True)
class Limits:
    receipt_bytes: int = 256 * 1024
    input_bytes: int = 256 * 1024
    compressed_spike_bytes: int = 4 * 1024 * 1024
    spike_bytes: int = 4 * 1024 * 1024
    inputs_per_receipt: int = 4096
    export_ticks: int = 300
    export_bytes: int = 8 * 1024 * 1024


def head(db):
    row = db.execute(
        "SELECT seq,hash FROM receipts ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    return tuple(row) if row else (0, ZERO)


def bounded_text(value, maximum, label):
    if not isinstance(value, str) or len(value.encode()) > maximum:
        raise VerificationUnavailable(label + " exceeds verification size limit")
    return value


def inflate(raw, limits):
    if raw is None or len(raw) > limits.compressed_spike_bytes:
        raise VerificationUnavailable("Spike evidence missing or exceeds size limit")
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(raw, limits.spike_bytes + 1)
    except zlib.error as exc:
        raise VerificationError("Spike evidence decompression failed") from exc
    if len(decoded) > limits.spike_bytes or decoder.unconsumed_tail:
        raise VerificationUnavailable("Spike evidence exceeds verification size limit")
    if not decoder.eof or decoder.unused_data:
        raise VerificationError("Spike evidence decompression failed")
    return decoded


def check_evidence(db, row, limits, *, retain=False):
    number, parent, current, payload = row
    payload = bounded_text(payload, limits.receipt_bytes, "Receipt")
    if digest(parent.encode() + payload.encode()) != current:
        raise VerificationError("Receipt chain mismatch")
    try:
        value = json.loads(payload)
        identifiers = value["input_ids"]
        expected_hashes = value["input_evidence_sha256"]
        spike_hash = value["spikes_sha256"]
    except (ValueError, KeyError, TypeError) as exc:
        raise VerificationError("Receipt evidence schema mismatch") from exc
    if (
        not isinstance(identifiers, list)
        or not isinstance(expected_hashes, list)
        or len(identifiers) != len(expected_hashes)
    ):
        raise VerificationError("Input evidence length mismatch")
    if len(identifiers) > limits.inputs_per_receipt:
        raise VerificationUnavailable("Input count exceeds verification size limit")
    raw = db.execute(
        "SELECT CASE WHEN length(data)<=? THEN data ELSE NULL END "
        "FROM spike_windows WHERE seq=?",
        (limits.compressed_spike_bytes, number),
    ).fetchone()
    if not raw:
        raise VerificationError("Spike evidence missing")
    decoded = inflate(raw[0], limits)
    if digest(decoded) != spike_hash:
        raise VerificationError("Spike evidence mismatch")
    retained_inputs = {}
    retained_bytes = len(decoded) + len(payload.encode()) if retain else 0
    for ident, expected in zip(identifiers, expected_hashes):
        if not isinstance(ident, str):
            raise VerificationError("Input evidence identifier mismatch")
        event = db.execute(
            "SELECT CASE WHEN length(payload)<=? THEN payload ELSE NULL END,receipt "
            "FROM inputs WHERE id=?",
            (limits.input_bytes, ident),
        ).fetchone()
        if not event or event[1] != number:
            raise VerificationError("Input evidence mismatch")
        raw_event = bounded_text(event[0], limits.input_bytes, "Input evidence")
        try:
            parsed = json.loads(raw_event)
            if digest(canonical(parsed)) != expected:
                raise VerificationError("Input evidence mismatch")
        except (ValueError, TypeError) as exc:
            raise VerificationError("Input evidence mismatch") from exc
        if retain:
            if not isinstance(parsed, dict):
                raise VerificationError("Input evidence schema mismatch")
            retained_bytes += len(raw_event.encode())
            if retained_bytes > limits.export_bytes:
                raise ExportLimitReached("One receipt exceeds export byte limit")
            retained_inputs[ident] = dict(parsed, receipt=number)
    if retain:
        try:
            spikes = json.loads(decoded)
        except ValueError as exc:
            raise VerificationError("Spike evidence JSON mismatch") from exc
        return (
            dict(value, seq=number, previous_hash=parent, hash=current),
            list(retained_inputs.values()),
            spikes,
        )
    return None


class ReceiptVerifier:
    def __init__(self, *, batch_size=256, limits=None):
        if type(batch_size) is not int or not 1 <= batch_size <= 256:
            raise ValueError("Receipt batch must contain 1 through 256 rows")
        self.batch_size = batch_size
        self.limits = limits or Limits()
        default = Limits()
        for name in default.__dataclass_fields__:
            value = getattr(self.limits, name)
            if type(value) is not int or not 1 <= value <= getattr(default, name):
                raise ValueError("Unsupported verifier limit: " + name)
        if self.limits.export_bytes < 1024:
            raise ValueError("Export byte limit is too small for status metadata")
        self.reset()

    def reset(self):
        self.seq, self.previous = 0, ZERO
        self.target = None
        self.observed = (0, ZERO)

    def check_boundaries(self, db):
        current = head(db)
        if current[0] < self.seq or (
            self.target is not None and current[0] < self.target[0]
        ):
            raise ChainChanged("Receipt head regressed; full scan required")
        for number, expected in ((self.seq, self.previous), self.target or (0, ZERO)):
            if number:
                row = db.execute(
                    "SELECT hash FROM receipts WHERE seq=?", (number,)
                ).fetchone()
                if not row or row[0] != expected:
                    raise ChainChanged("Receipt boundary changed; full scan required")
        self.observed = current
        return current

    def advance(self, db):
        current = self.check_boundaries(db)
        if self.target is None or self.seq == self.target[0]:
            self.target = current
        # LIMIT is part of SQL: a normal PostgreSQL client cursor cannot buffer
        # the complete history before Python consumes the first row.
        rows = db.execute(
            "SELECT seq,previous_hash,hash,"
            "CASE WHEN length(payload)<=? THEN payload ELSE NULL END "
            "FROM receipts WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?",
            (self.limits.receipt_bytes, self.seq, self.target[0], self.batch_size),
        ).fetchall()
        for row in rows:
            number, parent, current_hash, _ = row
            if number != self.seq + 1 or parent != self.previous:
                raise VerificationError("Receipt chain mismatch", self.seq + 1)
            try:
                check_evidence(db, row, self.limits)
            except VerificationError as exc:
                exc.sequence = number
                raise
            self.seq, self.previous = number, current_hash
        if not rows and self.seq < self.target[0]:
            raise VerificationError("Receipt gap before scan target", self.seq + 1)
        self.check_boundaries(db)
        if self.seq == self.target[0] and self.previous != self.target[1]:
            raise ChainChanged("Receipt target differs; full scan required")
        complete = (self.seq, self.previous) == self.observed
        return {
            "valid": complete,
            "phase": "verified" if complete else "catching_up",
            "checked": self.seq,
            "head": self.previous,
            "target_seq": self.target[0],
            "target_head": self.target[1],
            "target_complete": self.seq == self.target[0],
            "observed_head_seq": self.observed[0],
            "observed_head": self.observed[1],
            "remaining_to_target": self.target[0] - self.seq,
            "batch_rows_checked": len(rows),
            "verified_utc": utc(),
            "scope": SCOPE,
        }

    def failure(self, exc):
        mismatch = isinstance(exc, VerificationError) and not isinstance(
            exc, (ChainChanged, VerificationUnavailable)
        )
        return {
            "valid": False,
            "phase": "error" if mismatch else "unavailable",
            "checked": self.seq,
            "head": self.previous,
            "first_failure": exc.sequence if mismatch else None,
            "reason": str(exc)
            if isinstance(exc, VerificationError)
            else "Verification unavailable",
            "verified_utc": utc(),
            "scope": SCOPE,
        }

    def export(self, db, integrity):
        """Export only a checked prefix, bounded by ticks AND serialized bytes."""
        self.check_boundaries(db)
        candidates = db.execute(
            "SELECT seq,previous_hash,hash,"
            "CASE WHEN length(payload)<=? THEN payload ELSE NULL END "
            "FROM receipts WHERE seq<=? ORDER BY seq DESC LIMIT ?",
            (self.limits.receipt_bytes, self.seq, self.limits.export_ticks),
        ).fetchall()
        result = {
            "receipts": [],
            "inputs": [],
            "spike_windows": {},
            "scope": "Latest verified durable ticks within export limits; "
            "complete history retained privately",
            "integrity": integrity,
            "export": {
                "max_ticks": self.limits.export_ticks,
                "max_bytes": self.limits.export_bytes,
                "truncated": False,
                "checked_end_seq": self.seq,
                "exported_ticks": 0,
            },
        }
        expected_seq, expected_hash = self.seq, self.previous
        for row in candidates:
            if row[0] != expected_seq or row[2] != expected_hash:
                raise ChainChanged(
                    "Receipt export boundary changed; full scan required"
                )
            try:
                receipt, inputs, spikes = check_evidence(
                    db, row, self.limits, retain=True
                )
            except ExportLimitReached:
                result["export"].update(
                    truncated=True, reason="One receipt exceeds export byte limit"
                )
                break
            except VerificationError as exc:
                exc.sequence = row[0]
                raise
            candidate = {
                **result,
                "receipts": [receipt] + result["receipts"],
                "inputs": inputs + result["inputs"],
                "spike_windows": {**result["spike_windows"], row[0]: spikes},
                "export": {
                    **result["export"],
                    "exported_ticks": len(result["receipts"]) + 1,
                },
            }
            # Reserve room for an explicit truncation reason. An individually
            # oversized record is not represented as a complete empty ledger.
            if len(json.dumps(candidate, separators=(",", ":")).encode()) > (
                self.limits.export_bytes - 256
            ):
                result["export"].update(
                    truncated=True, reason="Serialized evidence exceeds byte limit"
                )
                break
            result = candidate
            expected_seq, expected_hash = row[0] - 1, row[1]
        if expected_seq > 0:
            result["export"]["truncated"] = True
        self.check_boundaries(db)
        if len(json.dumps(result, separators=(",", ":")).encode()) > (
            self.limits.export_bytes
        ):
            raise VerificationUnavailable("Evidence export metadata exceeds byte limit")
        return result

"""Atomic cognitive checkpoints and append-only local integrity records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from pathlib import Path


def canonical(value: dict | list) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


class Journal:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "cognition.sqlite3", timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS checkpoint(id INTEGER PRIMARY KEY CHECK(id=1),metadata TEXT NOT NULL,network BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS records(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,previous_hash TEXT NOT NULL,hash TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,spikes BLOB);
        CREATE INDEX IF NOT EXISTS record_kind ON records(kind,seq);
        CREATE TABLE IF NOT EXISTS effects(input_id TEXT PRIMARY KEY,event_order INTEGER UNIQUE NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_reservations(id TEXT PRIMARY KEY,intent_hash TEXT NOT NULL,day TEXT NOT NULL,lamports INTEGER NOT NULL,status TEXT NOT NULL);
        """)
        head = self.db.execute(
            "SELECT seq,hash FROM records ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        self.seq, self.head = head if head else (0, "0" * 64)
        self.verified_seq = 0
        self.verified_head = "0" * 64

    def append(self, kind: str, payload: dict, spikes: list | None = None) -> dict:
        payload = {**payload, "kind": kind}
        encoded = canonical(payload)
        digest = hashlib.sha256(self.head.encode() + encoded).hexdigest()
        self.seq += 1
        self.db.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?)",
            (
                self.seq,
                kind,
                self.head,
                digest,
                encoded.decode(),
                zlib.compress(canonical(spikes)) if spikes is not None else None,
            ),
        )
        self.head = digest
        return {"seq": self.seq, "hash": digest, **payload}

    def save(self, metadata: dict, network: bytes) -> None:
        self.db.execute(
            "INSERT INTO checkpoint VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata,network=excluded.network",
            (canonical(metadata).decode(), network),
        )
        self.db.commit()

    def restore(self) -> tuple[dict, bytes] | None:
        row = self.db.execute(
            "SELECT metadata,network FROM checkpoint WHERE id=1"
        ).fetchone()
        if not row:
            if (
                self.seq
                or self.db.execute("SELECT count(*) FROM effects").fetchone()[0]
            ):
                raise RuntimeError(
                    "Cognitive history exists without checkpoint; reset refused"
                )
            return None
        metadata = json.loads(row[0])
        if metadata["record_seq"] != self.seq or metadata["record_head"] != self.head:
            raise RuntimeError(
                "Cognitive checkpoint and history disagree; reset refused"
            )
        return metadata, row[1]

    def recent(self, kind: str | None = None, limit: int = 30) -> list:
        if kind:
            rows = self.db.execute(
                "SELECT seq,previous_hash,hash,payload FROM records WHERE kind=? ORDER BY seq DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT seq,previous_hash,hash,payload FROM records ORDER BY seq DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "seq": seq,
                "previous_hash": previous,
                "hash": digest,
                **json.loads(payload),
            }
            for seq, previous, digest, payload in rows
        ]

    def verify(self, full: bool = False) -> dict:
        previous = "0" * 64 if full else self.verified_head
        checked = 0 if full else self.verified_seq
        for seq, kind, parent, digest, payload, spikes in self.db.execute(
            "SELECT seq,kind,previous_hash,hash,payload,spikes FROM records WHERE seq>? ORDER BY seq",
            (checked,),
        ):
            checked += 1
            if (
                seq != checked
                or parent != previous
                or json.loads(payload).get("kind") != kind
                or hashlib.sha256(parent.encode() + payload.encode()).hexdigest()
                != digest
            ):
                return {"valid": False, "checked": checked, "first_failure": seq}
            if spikes is not None:
                expected = json.loads(payload).get("spikes_sha256")
                raw = zlib.decompress(spikes)
                if hashlib.sha256(raw).hexdigest() != expected:
                    return {
                        "valid": False,
                        "checked": checked,
                        "first_failure": seq,
                        "reason": "Spike evidence differs",
                    }
            previous = digest
        self.verified_seq, self.verified_head = checked, previous
        return {
            "valid": True,
            "checked": checked,
            "head": previous,
            "mode": "full" if full else "incremental",
            "verified_through_seq": checked,
            "scope": "Local cognitive record and spike integrity; no independent witness or onchain attestation",
        }

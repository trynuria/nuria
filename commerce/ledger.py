"""Durable reservations and append-only payment lifecycle evidence."""

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, decision_hash TEXT UNIQUE NOT NULL, provider TEXT NOT NULL,
          action TEXT NOT NULL, amount INTEGER NOT NULL, day TEXT NOT NULL, created REAL NOT NULL,
          status TEXT NOT NULL, terms TEXT NOT NULL, delivery TEXT, settlement TEXT, reward REAL);
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY, previous_hash TEXT NOT NULL, hash TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS authorizations(
          job_id TEXT PRIMARY KEY, payload TEXT NOT NULL, response TEXT);
        """)
        self.verify()

    def verify(self, full=True):
        previous = "0" * 64 if full else self.verified_head
        count = 0 if full else self.verified_seq
        for seq, parent, digest, raw in self.db.execute(
            "SELECT * FROM events WHERE seq>? ORDER BY seq", (count,)
        ):
            count += 1
            if (
                seq != count
                or parent != previous
                or hashlib.sha256((parent + raw).encode()).hexdigest() != digest
            ):
                raise RuntimeError("Commerce evidence integrity failed")
            previous = digest
        self.verified_seq, self.verified_head = count, previous
        return {
            "valid": True,
            "events": count,
            "head": previous,
            "scope": "Startup full audit, then incremental local hash verification; not an independent attestation",
        }

    def event(self, payload):
        row = self.db.execute(
            "SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq, previous = row if row else (0, "0" * 64)
        raw = canonical(payload)
        digest = hashlib.sha256((previous + raw).encode()).hexdigest()
        self.db.execute(
            "INSERT INTO events VALUES(?,?,?,?)", (seq + 1, previous, digest, raw)
        )

    def reserve(self, job, policy, balance, now):
        if not policy.enabled:
            raise ValueError("Payments are disabled")
        amount = job["amount"]
        if type(amount) is not int or not 0 < amount <= policy.per_job_micro_usdc:
            raise ValueError("Job exceeds payment cap")
        checked = balance.get("checked_at")
        if (
            type(checked) not in (int, float)
            or not math.isfinite(checked)
            or not -2 <= now - checked <= 30
        ):
            raise ValueError("Finalized USDC balance is stale or missing")
        units = balance.get("micro_usdc")
        if type(units) is not int or units < 0:
            raise ValueError("Finalized USDC balance is unknown")
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            pending = self.db.execute(
                "SELECT coalesce(sum(amount),0) FROM jobs WHERE status IN ('reserved','authorized','uncertain')"
            ).fetchone()[0]
            daily = self.db.execute(
                "SELECT coalesce(sum(amount),0) FROM jobs WHERE day=?", (day,)
            ).fetchone()[0]
            last = self.db.execute("SELECT max(created) FROM jobs").fetchone()[0]
            if last is not None and now - last < policy.cooldown_seconds:
                raise ValueError("Paid-job cooldown has not elapsed")
            if (
                daily + amount > policy.per_day_micro_usdc
                or units - pending - amount < policy.reserve_micro_usdc
            ):
                raise ValueError("Payment exceeds daily cap or reserve floor")
            self.db.execute(
                "INSERT INTO jobs(id,decision_hash,provider,action,amount,day,created,status,terms) VALUES(?,?,?,?,?,?,?,'reserved',?)",
                (
                    job["id"],
                    job["decision_hash"],
                    job["provider"],
                    job["action"],
                    amount,
                    day,
                    now,
                    canonical(job),
                ),
            )
            self.event(
                {
                    "job_id": job["id"],
                    "state": "reserved",
                    "amount_micro_usdc": amount,
                    "at": now,
                    "terms_sha256": hashlib.sha256(canonical(job).encode()).hexdigest(),
                }
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def transition(self, ident, state, detail, now):
        allowed = {
            "reserved": {"authorized", "failed"},
            "authorized": {"uncertain", "settled"},
            "uncertain": {"settled"},
            "settled": {"delivered"},
            "delivered": {"evaluated"},
        }
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.db.execute(
                "SELECT status FROM jobs WHERE id=?", (ident,)
            ).fetchone()
            if not current or state not in allowed.get(current[0], set()):
                raise ValueError("Invalid payment lifecycle transition")
            self.db.execute("UPDATE jobs SET status=? WHERE id=?", (state, ident))
            self.event({"job_id": ident, "state": state, "at": now, **detail})
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def recent(self):
        return [
            {"seq": seq, "previous_hash": parent, "hash": digest, **json.loads(raw)}
            for seq, parent, digest, raw in self.db.execute(
                "SELECT * FROM events ORDER BY seq DESC LIMIT 40"
            )
        ]

    def minimum_balance_slot(self):
        return max(
            (
                json.loads(raw)["slot"]
                for (raw,) in self.db.execute(
                    "SELECT settlement FROM jobs WHERE settlement IS NOT NULL"
                )
            ),
            default=0,
        )

    def summary(self):
        return {
            "counts": dict(
                self.db.execute("SELECT status,count(*) FROM jobs GROUP BY status")
            ),
            "reserved_micro_usdc": self.db.execute(
                "SELECT coalesce(sum(amount),0) FROM jobs WHERE status IN ('reserved','authorized','uncertain')"
            ).fetchone()[0],
            "settled_micro_usdc": self.db.execute(
                "SELECT coalesce(sum(amount),0) FROM jobs WHERE status IN ('settled','delivered','evaluated')"
            ).fetchone()[0],
            "integrity": self.verify(full=False),
            "records": self.recent(),
        }

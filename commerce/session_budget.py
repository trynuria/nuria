"""Durable total-cost ceilings for a bounded testing or operational session."""

import json
import math
import sqlite3


class SessionBudget:
    def __init__(self, path, limit, per_job, deadline):
        if (
            type(limit) is not int
            or type(per_job) is not int
            or not 0 < per_job <= limit
            or type(deadline) not in (int, float)
            or not math.isfinite(deadline)
        ):
            raise ValueError("Invalid session budget")
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS session(limit_units INTEGER, job_units INTEGER, deadline REAL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS charges(id TEXT PRIMARY KEY, maximum INTEGER NOT NULL, actual INTEGER, state TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS resolutions(id TEXT PRIMARY KEY, ledger_head TEXT NOT NULL)"
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            terms = self.db.execute("SELECT * FROM session").fetchall()
            if not terms:
                self.db.execute(
                    "INSERT INTO session VALUES(?,?,?)", (limit, per_job, deadline)
                )
            elif terms != [(limit, per_job, deadline)]:
                raise ValueError("Existing session ceiling or deadline differs")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.limit, self.per_job, self.deadline = limit, per_job, deadline

    def reserve(self, ident, maximum, now):
        if (
            not isinstance(ident, str)
            or not ident
            or len(ident) > 128
            or type(maximum) is not int
            or not 0 < maximum <= self.per_job
            or type(now) not in (int, float)
            or not math.isfinite(now)
            or not 0 <= now < self.deadline
        ):
            raise ValueError("Session request exceeds limits or deadline")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                "SELECT maximum,state FROM charges WHERE id=?", (ident,)
            ).fetchone()
            if existing:
                if existing[0] != maximum or existing[1] in (
                    "released",
                    "expired_unsettled",
                ):
                    raise ValueError("Existing reservation differs or was released")
                created = False
            else:
                used = self.db.execute(
                    "SELECT coalesce(sum(CASE "
                    "WHEN state='spent' THEN actual "
                    "WHEN state='expired_unsettled' AND id IN (SELECT id FROM resolutions) THEN 0 "
                    "ELSE maximum END),0) FROM charges WHERE state!='released'"
                ).fetchone()[0]
                if used + maximum > self.limit:
                    raise ValueError("Total session budget reached")
                self.db.execute(
                    "INSERT INTO charges VALUES(?,?,NULL,'reserved')", (ident, maximum)
                )
                created = True
            self.db.execute("COMMIT")
            return created
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def disclose(self, ident):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT state FROM charges WHERE id=?", (ident,)
            ).fetchone()
            if not row or row[0] not in ("reserved", "disclosed"):
                raise ValueError("No undisclosed or pending reservation")
            self.db.execute("UPDATE charges SET state='disclosed' WHERE id=?", (ident,))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def settle(self, ident, actual):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT maximum,actual,state FROM charges WHERE id=?", (ident,)
            ).fetchone()
            if (
                not row
                or row[2] not in ("disclosed", "spent")
                or type(actual) is not int
                or not 0 <= actual <= row[0]
                or (row[2] == "spent" and row[1] != actual)
            ):
                raise ValueError("Settlement differs from the reserved expense")
            self.db.execute(
                "UPDATE charges SET state='spent',actual=? WHERE id=?", (actual, ident)
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def release_undisclosed(self, ident):
        changed = self.db.execute(
            "UPDATE charges SET state='released' WHERE id=? AND state='reserved'",
            (ident,),
        ).rowcount
        if changed != 1:
            raise ValueError("Disclosed or missing expense cannot be released")

    def release_expired(self, ident, ledger, now):
        """Release only after the payment ledger has recorded anchored expiration."""
        head = ledger.verify()["head"]
        status = ledger.db.execute(
            "SELECT status FROM jobs WHERE id=?", (ident,)
        ).fetchone()
        recorded = ledger.db.execute(
            "SELECT payload FROM events WHERE json_extract(payload,'$.job_id')=? AND json_extract(payload,'$.state')='expired_unsettled' ORDER BY seq DESC LIMIT 1",
            (ident,),
        ).fetchone()
        event = json.loads(recorded[0]) if recorded else None
        self.db.execute("BEGIN IMMEDIATE")
        try:
            charge = self.db.execute(
                "SELECT state FROM charges WHERE id=?", (ident,)
            ).fetchone()
            if charge and charge[0] == "expired_unsettled":
                self.db.execute("COMMIT")
                return False
            checked = event.get("checked_at") if event else None
            if (
                not charge
                or charge[0] != "disclosed"
                or not status
                or status[0] != "expired_unsettled"
                or type(checked) not in (int, float)
                or not math.isfinite(checked)
                or type(now) not in (int, float)
                or not math.isfinite(now)
                or not 0 <= now - checked
            ):
                raise ValueError(
                    "Verified terminal payment-ledger expiration is required"
                )
            self.db.execute(
                "UPDATE charges SET state='expired_unsettled',actual=0 WHERE id=?",
                (ident,),
            )
            self.db.execute("INSERT INTO resolutions VALUES(?,?)", (ident, head))
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def summary(self):
        spent = self.db.execute(
            "SELECT coalesce(sum(actual),0) FROM charges WHERE state='spent'"
        ).fetchone()[0]
        pending = self.db.execute(
            "SELECT coalesce(sum(maximum),0) FROM charges WHERE state IN ('reserved','disclosed')"
        ).fetchone()[0]
        return {
            "limit": self.limit,
            "spent": spent,
            "pending": pending,
            "remaining": self.limit - spent - pending,
        }

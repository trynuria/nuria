"""Private PostgreSQL store, with SQLite retained only for offline legacy checks."""

import os
import sqlite3
from contextlib import contextmanager

import psycopg


class Connection:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s").replace("rowid", "event_order")
        if sql.startswith("INSERT OR IGNORE INTO inputs VALUES"):
            sql = (
                sql.replace(
                    "INSERT OR IGNORE INTO inputs VALUES",
                    "INSERT INTO inputs(id,kind,payload,created_utc,receipt) VALUES",
                )
                + " ON CONFLICT DO NOTHING"
            )
        elif sql.startswith("INSERT OR IGNORE"):
            sql = sql.replace("INSERT OR IGNORE", "INSERT") + " ON CONFLICT DO NOTHING"
        elif sql.startswith("INSERT OR REPLACE INTO source_cursors"):
            sql = (
                sql.replace("INSERT OR REPLACE", "INSERT")
                + " ON CONFLICT(address) DO UPDATE SET signature=excluded.signature"
            )
        elif sql.startswith("INSERT OR REPLACE INTO scan_progress"):
            sql = (
                sql.replace("INSERT OR REPLACE", "INSERT")
                + " ON CONFLICT(address) DO UPDATE SET newest=excluded.newest,before_signature=excluded.before_signature"
            )
        return self.raw.execute(sql, params)


@contextmanager
def connect(path):
    dsn = os.environ.get("NURIA_DATABASE")
    if dsn:
        with psycopg.connect(dsn, connect_timeout=5) as raw:
            raw.execute("SET statement_timeout='30s'")
            yield Connection(raw)
    else:
        raw = sqlite3.connect(path, timeout=10)
        try:
            raw.execute("PRAGMA journal_mode=WAL")
            raw.execute("PRAGMA busy_timeout=10000")
            with raw:
                yield raw
        finally:
            raw.close()

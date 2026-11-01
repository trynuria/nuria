"""Small durable project store; completed stages never regenerate on replay."""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    os.replace(temp, path)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "studio.sqlite", timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, data TEXT NOT NULL);
        """)

    def put(self, table, key, value):
        if table not in ("projects", "skills", "meta"):
            raise ValueError("Unknown record type")
        with self.db:
            self.db.execute(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?)", (key, json.dumps(value))
            )

    def get(self, table, key):
        if table not in ("projects", "skills", "meta"):
            raise ValueError("Unknown record type")
        field = "key" if table == "meta" else "id"
        row = self.db.execute(
            f"SELECT data FROM {table} WHERE {field}=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, table):
        if table not in ("projects", "skills"):
            raise ValueError("Unknown record type")
        values = [
            json.loads(r[0])
            for r in self.db.execute(f"SELECT data FROM {table} ORDER BY rowid")
        ]
        # Replacing an edited record changes its SQLite rowid. Feedback must
        # not reorder projects or change which completed brief comes next.
        return sorted(
            values,
            key=lambda value: (
                value["id"] if table == "projects" else value.get("project_id", ""),
                value["id"],
            ),
        )

    def save(self, project, kind=None, summary=None):
        project["updated_at"] = now()
        if kind:
            project.setdefault("events", []).append(
                {"at": now(), "kind": kind, "summary": summary}
            )
        self.put("projects", project["id"], project)
        atomic(self.root / "projects" / project["id"] / "project.json", project)

    def call(self, client, call_id, messages, tokens=8192):
        request = {"messages": messages, "max_tokens": tokens}
        encoded = json.dumps(request, sort_keys=True)
        row = self.db.execute(
            "SELECT request,response FROM calls WHERE id=?", (call_id,)
        ).fetchone()
        if row:
            if row[0] != encoded:
                raise ValueError("A recorded call cannot change its input")
            if row[1]:
                return json.loads(row[1])
        else:
            with self.db:
                self.db.execute(
                    "INSERT INTO calls VALUES (?,?,NULL)", (call_id, encoded)
                )
        # The private gateway has a second durable idempotency boundary. A
        # transport retry can retrieve a settled response, never buy it again.
        result = client.complete(call_id, **request)
        with self.db:
            self.db.execute(
                "UPDATE calls SET response=? WHERE id=?", (json.dumps(result), call_id)
            )
        return result

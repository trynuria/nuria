"""Persistent episodic recall and bounded workspace competition."""

from __future__ import annotations

import json
import math
import sqlite3

import numpy as np


class EpisodicMemory:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.execute(
            "CREATE TABLE IF NOT EXISTS episodes(id TEXT PRIMARY KEY,event_order INTEGER UNIQUE,source TEXT NOT NULL,payload TEXT NOT NULL,features BLOB NOT NULL,importance REAL NOT NULL,context TEXT NOT NULL)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS episode_context ON episodes(context,event_order)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS memory_stats(context TEXT PRIMARY KEY,episodes INTEGER NOT NULL,importance_total REAL NOT NULL)"
        )
        if not db.execute("SELECT 1 FROM memory_stats LIMIT 1").fetchone():
            db.execute(
                "INSERT INTO memory_stats SELECT context,count(*),sum(importance) FROM episodes GROUP BY context"
            )
        self.recent = []

    @staticmethod
    def context(event: dict) -> str:
        amount = float(event["quote_amount"])
        size = "small" if amount < 0.5 else "medium" if amount < 5 else "large"
        return event["side"] + ":" + size

    def add(self, order: int, event: dict, features: list, importance: float) -> None:
        packed = np.clip(np.asarray(features) * 255, 0, 255).astype(np.uint8).tobytes()
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO episodes VALUES(?,?,?,?,?,?,?)",
            (
                event["id"],
                order,
                event.get("source", "unknown"),
                json.dumps(event, separators=(",", ":")),
                packed,
                float(importance),
                self.context(event),
            ),
        )

        if inserted.rowcount:
            self.db.execute(
                "INSERT INTO memory_stats VALUES(?,1,?) ON CONFLICT(context) DO UPDATE SET episodes=episodes+1,importance_total=importance_total+excluded.importance_total",
                (self.context(event), float(importance)),
            )

    def recall(
        self,
        features: list,
        context: str | None = None,
        limit: int = 4,
        source: str | None = None,
    ) -> list:
        if context and source:
            rows = self.db.execute(
                "SELECT id,payload,features,importance,event_order FROM episodes WHERE context=? AND source=? ORDER BY event_order DESC LIMIT 128",
                (context, source),
            ).fetchall()
        elif context:
            rows = self.db.execute(
                "SELECT id,payload,features,importance,event_order FROM episodes WHERE context=? ORDER BY event_order DESC LIMIT 128",
                (context,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id,payload,features,importance,event_order FROM episodes ORDER BY event_order DESC LIMIT 128"
            ).fetchall()
        query = np.asarray(features, dtype=float)
        norm = float(np.linalg.norm(query))
        scored = []
        for ident, payload, raw, importance, order in rows:
            candidate = np.frombuffer(raw, dtype=np.uint8).astype(float) / 255
            similarity = float(
                query @ candidate / (norm * float(np.linalg.norm(candidate)) + 1e-9)
            )
            scored.append(
                {
                    "id": ident,
                    "event": json.loads(payload),
                    "similarity": round(similarity, 5),
                    "importance": round(importance, 5),
                    "order": order,
                    "features": candidate.tolist(),
                }
            )
        scored.sort(
            key=lambda item: item["similarity"] * 0.7 + item["importance"] * 0.3,
            reverse=True,
        )
        self.recent = scored[:limit]
        return self.recent

    def summary(self) -> dict:
        contexts = self.db.execute(
            "SELECT context,episodes,importance_total/episodes FROM memory_stats ORDER BY episodes DESC"
        ).fetchall()
        count = sum(row[1] for row in contexts)
        return {
            "episodes": count,
            "contexts": [
                {"context": c, "episodes": n, "mean_importance": round(i, 5)}
                for c, n, i in contexts
            ],
            "recall": [
                {k: v for k, v in item.items() if k != "features"}
                for item in self.recent
            ],
            "scope": "Recorded event memory and similarity retrieval; recurrent neural state is stored separately",
        }


class Workspace:
    """Compete for a bounded set of broadcast slots; scores affect action drives."""

    def __init__(self, state: dict | None = None):
        state = state or {}
        self.visits = state.get("visits", {})
        self.selected = state.get("selected", [])
        self.uncertainty = float(state.get("uncertainty", 1))
        self.last_surprise = float(state.get("last_surprise", 0))

    def novelty(self, context: str) -> float:
        n = self.visits.get(context, 0)
        self.visits[context] = n + 1
        return 1 / math.sqrt(n + 1)

    def broadcast(self, candidates: list[dict]) -> list:
        self.selected = sorted(candidates, key=lambda c: (-c["salience"], c["kind"]))[
            :3
        ]
        return self.selected

    def state(self) -> dict:
        return {
            "visits": self.visits,
            "selected": self.selected,
            "uncertainty": self.uncertainty,
            "last_surprise": self.last_surprise,
        }

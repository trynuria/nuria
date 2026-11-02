"""Evidence-linked procedural memory for real missions, using SQLite FTS5.

Retrieval is not execution authority. A remembered method is a hypothesis about
what may help; only independent task outcomes can support or contradict its use.
"""

import json
import re
import sqlite3
from pathlib import Path

from studio.store import atomic, digest, now

VERSION = "real-task-learning-v1"


def bounded(value, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise ValueError("Learning text is empty or exceeds its bound")
    return value.strip()


def procedure(value):
    if not isinstance(value, dict):
        raise ValueError("A procedure must be structured")
    result = {
        key: bounded(value.get(key), limit)
        for key, limit in (
            ("title", 160),
            ("when", 700),
            ("method", 1800),
            ("pitfalls", 700),
        )
    }
    if value.get("revises") is not None:
        result["revises"] = bounded(value["revises"], 80)
    return result


class ExperienceMemory:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "experience.sqlite", timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS lessons(
            id TEXT PRIMARY KEY, mission TEXT NOT NULL, kind TEXT NOT NULL,
            payload TEXT NOT NULL, updated_at TEXT NOT NULL);
          CREATE VIRTUAL TABLE IF NOT EXISTS lesson_search USING fts5(id UNINDEXED, text);
          CREATE TABLE IF NOT EXISTS uses(
            mission TEXT NOT NULL, action TEXT NOT NULL, lesson TEXT NOT NULL,
            application TEXT NOT NULL, PRIMARY KEY(mission,action,lesson));
          CREATE TABLE IF NOT EXISTS feedback(
            mission TEXT NOT NULL, lesson TEXT NOT NULL, review TEXT NOT NULL,
            verdict TEXT NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(mission,lesson,review));
        """)

    def close(self):
        self.db.close()

    def put(self, key, mission, kind, value):
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if len(payload.encode()) > 16000:
            raise ValueError("Learning record exceeds its bound")
        previous = self.db.execute(
            "SELECT payload FROM lessons WHERE id=?", (key,)
        ).fetchone()
        if previous and previous[0] == payload:
            return
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO lessons VALUES (?,?,?,?,?)",
                (key, mission, kind, payload, now()),
            )
            self.db.execute("DELETE FROM lesson_search WHERE id=?", (key,))
            self.db.execute(
                "INSERT INTO lesson_search(id,text) VALUES (?,?)", (key, payload)
            )
        atomic(self.root / "records" / (key + ".json"), value)

    def get(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9-]{1,80}", key):
            raise ValueError("Invalid lesson identity")
        row = self.db.execute(
            "SELECT payload FROM lessons WHERE id=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def search(self, query, *, maximum=3, byte_limit=5000, exclude_mission=None):
        bounded(query, 5000)
        words = list(dict.fromkeys(re.findall(r"[a-zA-Z0-9_]{3,40}", query.lower())))[
            :32
        ]
        if not words:
            return []
        match = " OR ".join('"' + word + '"' for word in words)
        rows = self.db.execute(
            "SELECT l.payload FROM lesson_search s JOIN lessons l ON l.id=s.id "
            "WHERE lesson_search MATCH ? AND (? IS NULL OR l.mission != ?) "
            "ORDER BY bm25(lesson_search),l.id LIMIT ?",
            (match, exclude_mission, exclude_mission, min(maximum, 8) * 3),
        ).fetchall()
        found, size = [], 0
        for (raw,) in rows:
            value = json.loads(raw)
            feedback = self.db.execute(
                "SELECT mission,verdict,evidence FROM feedback WHERE lesson=? ORDER BY mission",
                (value["id"],),
            ).fetchall()
            value["transfer_feedback"] = [
                {"mission": m, "verdict": v, "evidence": e} for m, v, e in feedback[-8:]
            ]
            value["feedback_count"] = len(feedback)
            encoded = len(json.dumps(value, ensure_ascii=False).encode())
            if size + encoded > byte_limit:
                continue
            found.append(value)
            size += encoded
            if len(found) >= maximum:
                break
        return found

    def declare_use(self, mission, action, declarations):
        if not isinstance(declarations, list) or len(declarations) > 3:
            raise ValueError("Declare at most three specific lesson applications")
        with self.db:
            prior = {
                row[0]
                for row in self.db.execute(
                    "SELECT DISTINCT lesson FROM uses WHERE mission=?", (mission,)
                )
            }
            if len(prior | {d.get("id") for d in declarations}) > 3:
                raise ValueError(
                    "A mission can review at most three distinct method applications"
                )
            for declaration in declarations:
                lesson = self.get(declaration.get("id"))
                if lesson is None:
                    raise ValueError("Cannot use an unknown lesson")
                application = bounded(declaration.get("application"), 600)
                self.db.execute(
                    "INSERT OR IGNORE INTO uses VALUES (?,?,?,?)",
                    (mission, action, lesson["id"], application),
                )

    def applications(self, mission):
        return [
            {"action": a, "id": i, "application": p}
            for a, i, p in self.db.execute(
                "SELECT action,lesson,application FROM uses WHERE mission=? ORDER BY action,lesson",
                (mission,),
            )
        ]

    def ingest(self, mission_root):
        """Import recorded reviews; never infer acceptance from the agent's claim."""
        mission_root = Path(mission_root)
        path = mission_root / "mission.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
        goal = data.get("goal", {})
        delivery = data.get("delivery", {})
        for review_path in sorted((mission_root / "reviews").glob("*-delivery.json")):
            review = json.loads(review_path.read_text())
            accepted = (
                data.get("status") == "accepted"
                and review.get("approved") is True
                and delivery.get("review") == review
                and delivery.get("after", {}).get("exit_code") == 0
                and not delivery.get("after", {}).get("timed_out", True)
            )
            key = "experience-" + digest([mission_root.name, review_path.name])[:24]
            value = {
                "id": key,
                "origin_mission": mission_root.name,
                "kind": "accepted_method" if accepted else "review_observation",
                "title": goal.get("title", mission_root.name),
                "when": goal.get("problem", ""),
                "method": review.get("lesson", review.get("reason", "")),
                "pitfalls": review.get("limitations", []),
                "evidence": {
                    "review_sha256": digest(review),
                    "review": review_path.name,
                    "accepted": accepted,
                    "acceptance_sha256": delivery.get("acceptance_sha256"),
                    "artifacts": delivery.get("artifacts", {}) if accepted else {},
                },
                "authority": "Past observations, not permission or guaranteed general correctness",
            }
            self.put(key, mission_root.name, value["kind"], value)
            if accepted:
                candidate = data.get("learning_draft")
                if candidate and review.get("learning_approved") is True:
                    clean = procedure(candidate)
                    skill_id = "skill-" + digest([mission_root.name, clean])[:24]
                    skill = {
                        **clean,
                        "id": skill_id,
                        "origin_mission": mission_root.name,
                        "kind": "procedure",
                        "evidence": value["evidence"],
                    }
                    if clean.get("revises") and self.get(clean["revises"]) is None:
                        raise ValueError("Procedure revises an unknown prior method")
                    self.put(skill_id, mission_root.name, "procedure", skill)
                    skill_dir = self.root / "skills" / skill_id
                    skill_dir.mkdir(parents=True, exist_ok=True)
                    # Agent Skills-compatible document, separate from executable artifacts.
                    content = f"---\nname: {skill_id}\ndescription: {json.dumps(clean['when'])}\n---\n\n# {clean['title']}\n\n{clean['method']}\n\n## Failure conditions\n\n{clean['pitfalls']}\n\nEvidence: {key}. Treat as a method to test, not authority.\n"
                    (skill_dir / "SKILL.md").write_text(content)
            uses = {u["id"] for u in self.applications(mission_root.name)}
            assessments = review.get("transfer", [])
            if not isinstance(assessments, list):
                continue
            for assessment in assessments[:3]:
                if not isinstance(assessment, dict):
                    continue
                ident, verdict = assessment.get("id"), assessment.get("verdict")
                if ident not in uses or verdict not in (
                    "supported",
                    "contradicted",
                    "not_demonstrated",
                ):
                    continue
                if verdict == "supported" and not accepted:
                    continue
                previous = self.get(ident)
                if previous["origin_mission"] == mission_root.name:
                    continue  # Repair within a task is not cross-task transfer.
                try:
                    evidence = bounded(assessment.get("evidence"), 1000)
                except ValueError:
                    continue  # The raw review remains; malformed metadata earns no credit.
                with self.db:
                    self.db.execute(
                        "INSERT OR IGNORE INTO feedback VALUES (?,?,?,?,?)",
                        (mission_root.name, ident, digest(review), verdict, evidence),
                    )

    def export(self):
        counts = dict(
            self.db.execute("SELECT kind,count(*) FROM lessons GROUP BY kind")
        )
        transfers = [
            {"mission": m, "id": i, "verdict": v, "evidence": e}
            for m, i, v, e in self.db.execute(
                "SELECT mission,lesson,verdict,evidence FROM feedback ORDER BY mission,lesson"
            )
        ]
        return {
            "version": VERSION,
            "records": counts,
            "applications": self.db.execute("SELECT count(*) FROM uses").fetchone()[0],
            "transfer_feedback": transfers,
        }


def outcome_index(library):
    """Exact current task status, separate from historical reasons and reviews."""
    path = Path(library) / "memory.json" if library else None
    if path is None or not path.exists():
        return {"available": False, "items": [], "history": "/library/memory.json"}
    history = json.loads(path.read_text())
    counts = {}
    for record in history:
        status = record["status"]
        counts[status] = counts.get(status, 0) + 1
    items, size = [], 0
    for record in reversed(history):
        item = {
            "id": record["id"],
            "status": record["status"],
            "title": (record.get("goal") or {}).get("title"),
            "can_reopen": record["status"] in ("unresolved", "abandoned"),
        }
        encoded = len(json.dumps(item).encode())
        if size + encoded > 8000:
            break
        items.append(item)
        size += encoded
    return {
        "available": True,
        "total": len(history),
        "status_counts": counts,
        "items": items,
        "truncated": len(items) != len(history),
        "history": "/library/memory.json",
        "scope": "Current terminal status. Historical resumption reasons do not override a later accepted result. Accepted work cannot be reopened.",
    }


def task_frame(mission, memory, messages):
    """Reconstruct working state from durable facts; leave full records accessible.

    This is explicit working memory, not provider reasoning compaction. No
    acceptance input is changed. Long observations have exact on-disk references.
    """
    data = mission.data
    goal = data.get("goal") or {}
    query = (
        " ".join(str(goal.get(k, "")) for k in ("title", "problem")).strip()
        or "useful method failure reusable task"
    )
    lessons = memory.search(
        query[:5000], maximum=2, byte_limit=3000, exclude_mission=data["id"]
    )
    # Keep several complete exchanges: retaining just the last one made the
    # live worker alternate forever between reading its code and its producer.
    # This is an exact suffix, not an inferred summary. The latest exchange is
    # always complete; older messages remain in the durable trajectory.
    recent, size = [], 0
    for message in reversed(messages[-8:]):
        if message.get("role") not in ("user", "assistant"):
            continue
        item = {"role": message["role"], "content": message.get("content", "")}
        encoded = len(json.dumps(item, ensure_ascii=False).encode())
        if len(recent) >= 2 and size + encoded > 40000:
            break
        recent.insert(0, item)
        size += encoded
    # Do not begin an older excerpt with an observation whose action was omitted.
    if len(recent) > 2 and recent[0]["role"] == "user":
        recent.pop(0)
    reviews = sorted((mission.root / "reviews").glob("*-delivery.json"))
    last_review = json.loads(reviews[-1].read_text()) if reviews else None
    return {
        "version": VERSION,
        "mission": data["id"],
        "status": data["status"],
        "goal": goal,
        "outcome_index": outcome_index(getattr(mission, "library", None))
        if not goal
        else None,
        "criteria": data.get("criteria", []),
        "plan": data.get("working_state"),
        "learning_draft": data.get("learning_draft"),
        "last_delivery_review": last_review,
        "relevant_experience": lessons,
        "recent": recent,
        "complete_records": [
            "/observations/goal.json",
            "/observations/trajectory.json",
            "/observations/working-state.json",
        ],
    }

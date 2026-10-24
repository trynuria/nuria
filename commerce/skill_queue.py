"""Finite, source-bound jobs for the existing neural task catalogue.

Queue entries are actual compute contracts, not token events or merchant orders.
Completed contracts stay completed across receipt-window eviction and recovery.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from commerce.action_loop import ActionLoop
from commerce.ledger import canonical
from commerce.tasks import HASH, IDENT, digest, validate_task
from publish import publish

VERSION = "nuria.skill-queue.v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_TASKS = 256


def private_destination(path):
    destination = Path(path)
    if not destination.is_absolute() or not destination.parent.is_dir():
        raise ValueError("Private catalogue directory must already exist")
    destination = destination.resolve()
    roots = [Path("/var/lib/nuria/public"), Path("/opt/nuria/www")]
    roots += [
        Path(os.environ[name]).resolve()
        for name in ("NURIA_PUBLIC", "NURIA_COMMERCE_PUBLIC", "NURIA_WEB_ROOT")
        if os.environ.get(name)
    ]
    if any(destination == root or destination.is_relative_to(root) for root in roots):
        raise ValueError("Buyer-held catalogue cannot be inside a public cache")
    if destination.parent.stat().st_mode & 0o007:
        raise ValueError("Buyer-held catalogue needs a private parent directory")
    return destination


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Skill queue exceeds its bounded input size")
    return raw, json.loads(raw)


def disable(destination):
    destination = private_destination(destination)
    publish(
        destination.parent,
        destination.name,
        {"planning_enabled": False, "tasks": [], "phase": "queue_evidence_required"},
    )


def materialize(ledger, configuration, destination):
    """Publish at most one pending concrete contract per stable goal lane.

    The destination is a private shared file, not the public cache. It contains
    buyer-held validation rows and must not be served by the HTTP application.
    Only the returned progress projection is public.
    """
    destination = private_destination(destination)
    _, controls = read_json(configuration)
    if (
        type(controls) is not dict
        or set(controls)
        != {"schema", "manifest", "manifest_sha256", "provider", "enabled"}
        or controls["schema"] != VERSION
        or type(controls["enabled"]) is not bool
        or type(controls["manifest"]) is not str
        or not Path(controls["manifest"]).is_absolute()
        or not isinstance(controls["manifest_sha256"], str)
        or not HASH.fullmatch(controls["manifest_sha256"])
        or not isinstance(controls["provider"], str)
        or not IDENT.fullmatch(controls["provider"])
    ):
        raise ValueError("Skill queue authority is malformed")
    raw, manifest = read_json(controls["manifest"])
    identity = hashlib.sha256(raw).hexdigest()
    if identity != controls["manifest_sha256"]:
        raise ValueError("Skill queue manifest commitment changed")
    if (
        type(manifest) is not dict
        or set(manifest) != {"schema", "source", "tasks"}
        or manifest["schema"] != VERSION
        or type(manifest["source"]) is not dict
        or type(manifest["tasks"]) is not list
        or not 1 <= len(manifest["tasks"]) <= MAX_TASKS
    ):
        raise ValueError("Skill queue manifest is malformed")
    tasks, identities = manifest["tasks"], set()
    for task in tasks:
        validate_task(task)
        if (
            task["kind"] != "compute"
            or task["provider"] != controls["provider"]
            or task["maximum_micro_usd"] != 0
            or task["acceptance"]["type"]
            not in ("transform_program", "transform_execution", "transform_active")
            or "outcome" in task
            or digest(task) in identities
        ):
            raise ValueError("Queue permits only distinct zero-payment skill jobs")
        identities.add(digest(task))
    if len({task["id"] for task in tasks}) > 16:
        raise ValueError("Skill queue has too many simultaneous goal lanes")
    ActionLoop(ledger)
    ledger.db.executescript("""
      CREATE TABLE IF NOT EXISTS skill_queues(
        identity TEXT PRIMARY KEY, provider TEXT NOT NULL, source TEXT NOT NULL,
        total INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS action_tasks_contract ON action_tasks(contract);
    """)
    retained = ledger.db.execute(
        "SELECT provider,source,total FROM skill_queues WHERE identity=?", (identity,)
    ).fetchone()
    expected = (controls["provider"], canonical(manifest["source"]), len(tasks))
    if retained and retained != expected:
        raise ValueError("Retained skill queue identity disagrees with its source")
    if retained is None:
        ledger.db.execute("BEGIN IMMEDIATE")
        try:
            ledger.db.execute(
                "INSERT INTO skill_queues VALUES(?,?,?,?)", (identity, *expected)
            )
            ledger.event(
                {
                    "skill_queue_sha256": identity,
                    "provider": controls["provider"],
                    "tasks": len(tasks),
                    "source_sha256": digest(manifest["source"]),
                    "scope": "Finite included-capacity software jobs; not token activity or paid work",
                }
            )
            ledger.db.execute("COMMIT")
        except BaseException:
            ledger.db.execute("ROLLBACK")
            raise
    selected, lanes = [], set()
    evaluated = accepted = cancelled = 0
    for task in tasks:
        states = ledger.db.execute(
            "SELECT state,count(*),max(CASE WHEN json_extract(acceptance,'$.passed')=1 THEN 1 ELSE 0 END) FROM action_tasks WHERE contract=? GROUP BY state",
            (canonical(task),),
        ).fetchall()
        completed = [row for row in states if row[0] == "evaluated"]
        cancelled += sum(row[1] for row in states if row[0] == "cancelled")
        if completed:
            evaluated += 1
            accepted += int(any(row[2] == 1 for row in completed))
            continue
        if task["id"] not in lanes:
            selected.append(task)
            lanes.add(task["id"])
    catalogue = {
        "planning_enabled": controls["enabled"] and bool(selected),
        "tasks": selected if controls["enabled"] else [],
        "queue_sha256": identity,
    }
    prior = destination.read_bytes() if destination.is_file() else None
    encoded = json.dumps(catalogue, allow_nan=False, separators=(",", ":")).encode()
    if prior != encoded:
        publish(destination.parent, destination.name, catalogue)
    return catalogue, {
        "schema": VERSION,
        "manifest_sha256": identity,
        "source_sha256": digest(manifest["source"]),
        "enabled": controls["enabled"],
        "total": len(tasks),
        "evaluated": evaluated,
        "accepted": accepted,
        "remaining": len(tasks) - evaluated,
        "cancelled_unexecuted_attempts": cancelled,
        "active_goal_lanes": len(selected),
        "scope": "Real safe-program execution over a finite declared workload; payment is zero and source formatting may be generated",
    }

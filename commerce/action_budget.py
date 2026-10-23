"""Shared conservative USD commitments across merchant and task execution."""


def exists(db):
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_tasks'"
        ).fetchone()
        is not None
    )


def commitments(db, day, account):
    if not exists(db):
        return {"daily": 0, "pending": 0, "unresolved": 0, "last": None}
    daily = db.execute(
        "SELECT coalesce(sum(amount),0) FROM action_tasks WHERE day=?", (day,)
    ).fetchone()[0]
    pending = db.execute(
        "SELECT coalesce(sum(amount),0) FROM action_tasks WHERE account=? AND settlement IS NULL AND state!='cancelled'",
        (account,),
    ).fetchone()[0]
    unresolved = db.execute(
        "SELECT count(*) FROM action_tasks WHERE amount>0 AND settlement IS NULL AND state!='cancelled'"
    ).fetchone()[0]
    last = db.execute(
        "SELECT max(json_extract(quote,'$.checked_at')) FROM action_tasks WHERE amount>0"
    ).fetchone()[0]
    return {"daily": daily, "pending": pending, "unresolved": unresolved, "last": last}

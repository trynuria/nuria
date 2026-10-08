"""Bounded, cached public ledger pages and independently checkable hash links."""

import hashlib
import json
from datetime import datetime, timezone

from commerce.ledger import canonical
from publish import publish

PAGE_SIZE = 250


def export_pages(ledger, directory):
    # Only the tail changes. Complete pages survive restart without re-encoding.
    head = ledger.verify(full=False)
    count = head["events"]
    pages = (count + PAGE_SIZE - 1) // PAGE_SIZE
    prior_path = directory / "index.json"
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {"events": 0}
    if prior["events"] > count:
        raise ValueError("Public ledger would roll back")
    if prior["events"]:
        old = ledger.db.execute(
            "SELECT hash FROM events WHERE seq=?", (prior["events"],)
        ).fetchone()
        if not old or old[0] != prior["head"]:
            raise ValueError("Public ledger history differs from private ledger")
    for page in range(prior["events"] // PAGE_SIZE, pages):
        filename = f"ledger-{page}.json"
        rows = ledger.db.execute(
            "SELECT * FROM events WHERE seq>? AND seq<=? ORDER BY seq",
            (page * PAGE_SIZE, min(count, (page + 1) * PAGE_SIZE)),
        ).fetchall()
        records = [
            {
                "seq": seq,
                "previous_hash": parent,
                "hash": digest,
                "payload": json.loads(raw),
            }
            for seq, parent, digest, raw in rows
        ]
        value = {
            "schema": "nuria.commerce.ledger.v1",
            "page": page,
            "page_size": PAGE_SIZE,
            "records": records,
            "first_seq": records[0]["seq"],
            "last_seq": records[-1]["seq"],
            "page_sha256": hashlib.sha256(canonical(records).encode()).hexdigest(),
        }
        publish(directory, filename, value)
    result = {
        "schema": "nuria.commerce.index.v1",
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "events": count,
        "pages": pages,
        "page_size": PAGE_SIZE,
        "head": head["head"],
        "genesis_previous_hash": "0" * 64,
        "endpoint": "/api/commerce/ledger?page=<zero-based page>",
        "verification": "SHA256(previous_hash UTF-8 + canonical payload JSON UTF-8). Pages contain every recorded event. Local continuity is not an independent witness or proof of completeness.",
    }
    publish(directory, "index.json", result)
    return result


def verify_pages(index, pages):
    previous, seq = "0" * 64, 0
    for page, body in enumerate(pages):
        if (
            body["page"] != page
            or hashlib.sha256(canonical(body["records"]).encode()).hexdigest()
            != body["page_sha256"]
        ):
            raise ValueError("Public ledger page hash mismatch")
        for record in body["records"]:
            if record["seq"] > index["events"]:
                break
            seq += 1
            digest = hashlib.sha256(
                (previous + canonical(record["payload"])).encode()
            ).hexdigest()
            if (
                record["seq"] != seq
                or record["previous_hash"] != previous
                or record["hash"] != digest
            ):
                raise ValueError("Public ledger continuity failed")
            previous = digest
    if seq != index["events"] or previous != index["head"]:
        raise ValueError("Public ledger does not reach advertised head")
    return {"valid": True, "events": seq, "head": previous}

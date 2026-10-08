"""Bounded public work records projected from the existing payment journal.

This module neither creates jobs nor grants financial authority. A settled
payment, validated delivery and measured outcome remain distinct observations.
Unconfigured hiring and compute integrations are reported as planned.
"""

import json
import math

CATALOG = (
    {
        "id": "data",
        "name": "Data and APIs",
        "status": "gated",
        "detail": "Exact Solana USDC buyer implemented; production providers and token are not connected.",
    },
    {
        "id": "agents",
        "name": "Specialist agents",
        "status": "planned",
        "detail": "Contracted tasks with a defined artifact and acceptance check. Hiring integration is not installed.",
    },
    {
        "id": "humans",
        "name": "Human contributors",
        "status": "planned",
        "detail": "Bounties, escrow and worker payouts require a separate marketplace integration.",
    },
    {
        "id": "compute",
        "name": "Compute",
        "status": "planned",
        "detail": "Finite jobs with spending commitments, delivery receipts and measured utility. Provider activation is pending.",
    },
)


def snapshot(ledger, commerce, limit=80):
    """Project whitelisted fields; never expose a signed payment authorization."""
    if type(limit) is not int or not 1 <= limit <= 80:
        raise ValueError("Work snapshot limit must be between one and eighty")
    jobs = []
    for row in ledger.db.execute(
        "SELECT id,provider,action,amount,created,status,terms,delivery,settlement,reward "
        "FROM jobs ORDER BY created DESC,id LIMIT ?",
        (limit,),
    ):
        ident, provider, action, amount, created, state, raw, artifact, paid, reward = (
            row
        )
        terms = json.loads(raw)
        delivery = json.loads(artifact) if artifact else None
        settlement = json.loads(paid) if paid else None
        if (
            state in ("settled", "delivered", "evaluated")
            and not settlement
            or delivery
            and not settlement
            or state in ("delivered", "evaluated")
            and not delivery
            or state == "evaluated"
            and (type(reward) not in (int, float) or not math.isfinite(reward))
        ):
            raise ValueError("Job lifecycle evidence is incomplete")
        schema = terms.get("schema")
        forecast = schema == "nuria.forecast.v1"
        jobs.append(
            {
                "id": ident,
                "provider": provider,
                "kind": "data",
                "title": "Structured input forecast" if forecast else "Market context",
                "purpose": (
                    "Compare a provider forecast with the local forecast on a later finalized token input."
                    if forecast
                    else "Obtain time-limited Solana/USD context; no neural reward is attributed."
                ),
                "action": action,
                "created_at": created,
                "amount_micro_usdc": amount,
                "recipient": terms.get("accepted", {}).get("payTo"),
                "decision_hash": terms.get("decision_hash"),
                "job_state": (
                    "evaluated"
                    if state == "evaluated" and delivery and reward is not None
                    else "delivered"
                    if delivery
                    else "closed"
                    if state in ("failed", "expired_unsettled")
                    else "awaiting_delivery"
                    if settlement
                    else "pending"
                ),
                "payment_state": (
                    "settled"
                    if settlement
                    else "expired_unsettled"
                    if state == "expired_unsettled"
                    else "not_disclosed"
                    if state == "failed"
                    else "unresolved"
                    if state in ("authorized", "uncertain")
                    else "reserved"
                ),
                "outcome_state": (
                    "measured"
                    if state == "evaluated" and delivery and reward is not None
                    else "not_implemented"
                    if not forecast
                    else "pending"
                ),
                "acceptance": (
                    "Exact configured mint, finite probability and bounded future expiry; scored against a later finalized input."
                    if forecast
                    else "Exact Solana/USD resource, finite positive price and bounded observation age."
                ),
                "artifact_sha256": delivery.get("sha256") if delivery else None,
                "delivery_schema": delivery.get("schema") if delivery else schema,
                "transaction": settlement.get("transaction") if settlement else None,
                "settlement_slot": settlement.get("slot") if settlement else None,
                "reward": reward
                if state == "evaluated" and delivery and reward is not None
                else None,
            }
        )
    total = ledger.db.execute("SELECT count(*) FROM jobs").fetchone()[0]
    catalog = [dict(item) for item in CATALOG]
    if (commerce.get("policy") or {}).get("providers"):
        catalog[0]["status"] = (
            "connected" if commerce["financial_execution"] else "gated"
        )
        catalog[0]["detail"] = (
            "Configured exact Solana USDC provider catalog; each payment and delivery is verified separately."
        )
    return {
        "schema": "nuria.work.v1",
        "updated_utc": commerce["updated_utc"],
        "phase": commerce["phase"],
        "financial_execution": commerce["financial_execution"],
        "jobs": jobs,
        "total_jobs": total,
        "coverage": {
            "recent_limit": limit,
            "returned": len(jobs),
            "truncated": total > limit,
        },
        "catalog": catalog,
        "money": {
            "balance": commerce.get("usdc_balance"),
            "committed_micro_usdc": commerce["reserved_micro_usdc"],
            "settled_micro_usdc": commerce["settled_micro_usdc"],
            "policy": commerce.get("policy"),
            "missing": commerce.get("missing", []),
        },
        "integrity": commerce["integrity"],
        "ledger_index": commerce["ledger_index"],
        "scope": "Recent production payment jobs only. Local experiments, private funded tests and proposed external hires are not presented as commissioned work. Hash continuity is a local integrity check, not an independent witness.",
    }

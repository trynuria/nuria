"""Current marketplace commitments and unsigned request preparation.

No signing, deposits, orders or messages occur in this module. Human escrow and
agent settlement are different rails; one adapter must not impersonate another.
"""

import hashlib
import json
import re
from urllib.parse import quote as urlquote

from commerce.commissioning import BASE_USDC, HASH, IDENT, public_text
from commerce.transport import request

OFFER_FIELDS = (
    "version",
    "seller",
    "title",
    "terms",
    "amount_atomic",
    "chain_id",
    "token",
    "delivery_window_seconds",
    "expiry",
    "commit_nonce",
)


def agent_offer(raw, now):
    """Recompute the first-party offer commitment before quoting its price."""
    if (
        raw.get("version") != "1f916.offer.v1"
        or raw.get("chain_id") != 8453
        or raw.get("token", "").lower() != BASE_USDC
        or raw.get("asset") != "USDC"
    ):
        raise ValueError("Offer is not a supported Base-USDC contract")
    recipe = raw.get("payload_hash_recipe", {})
    if (
        recipe.get("algorithm") != "sha256"
        or tuple(recipe.get("fields", ())) != OFFER_FIELDS
    ):
        raise ValueError("Offer commitment recipe changed; adapter review required")
    payload = json.dumps(
        [raw[k] for k in OFFER_FIELDS],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    if not HASH.fullmatch(raw.get("payload_hash", "")) or digest != raw["payload_hash"]:
        raise ValueError("Marketplace offer commitment mismatch")
    amount = raw.get("amount_atomic")
    if not isinstance(amount, str) or not re.fullmatch(r"[1-9][0-9]{0,14}", amount):
        raise ValueError("Offer price is not integer micro-USDC")
    if type(raw.get("expiry")) is not int or not 0 < raw["expiry"] - now <= 86400:
        raise ValueError("Offer is expired or its quote horizon is excessive")
    if (
        type(raw.get("delivery_window_seconds")) is not int
        or not 60 <= raw["delivery_window_seconds"] <= 604800
    ):
        raise ValueError("Offer delivery window is invalid")
    if not IDENT.fullmatch(raw.get("id", "")):
        raise ValueError("Offer identity is invalid")
    return {
        "provider": "1f916",
        "offer_id": raw["id"],
        "offer_sha256": digest,
        "chain_id": 8453,
        "token": BASE_USDC,
        "amount_micro_usdc": int(amount),
        "fees_micro_usdc": 0,
        "verifier_micro_usdc": 0,
        "gas_micro_usdc": 0,
        "expires_at": raw["expiry"],
        "delivery_seconds": raw["delivery_window_seconds"],
        "recipient": None,
    }


def agent_order(row):
    quote, contract = row["quote"], row["contract"]
    if quote["provider"] != "1f916" or contract["kind"] != "agent":
        raise ValueError("This contract is not an agent-marketplace order")
    # Buyer targets and baseline vectors are never disclosed to the contractor.
    brief = public_text(contract["brief"], 8000)
    body = {
        "brief": brief
        + "\n\nNuria commission: "
        + row["id"]
        + "\nDataset: "
        + contract["evaluation"]["dataset_sha256"]
        + "\nDeliver nuria.predictions.v1 JSON containing commission_id, dataset_sha256 and predictions. No executable artifacts or production access are authorized.",
    }
    return {
        "endpoint": "https://1f916.ai/api/offers/"
        + urlquote(quote["offer_id"], safe="")
        + "/orders",
        "method": "POST",
        "body": body,
        "gates": [
            "project_marketplace_identity",
            "dedicated_Base_custody",
            "offer_bound_payment_authority",
        ],
        "scope": "Unsigned request only; submitting it creates a public purchase order",
    }


def discovery(now, http=None):
    """One bounded public discovery read; no enrollment or account creation."""
    if http is None:
        status, _, raw = request("https://1f916.ai/api/offers", max_body=1_048_576)
    else:
        status, _, raw = http("https://1f916.ai/api/offers")
    if len(raw) > 1_048_576:
        raise ValueError("Marketplace discovery exceeds its response bound")
    if status != 200:
        raise ValueError("Agent marketplace is unavailable")
    body = json.loads(raw)
    if type(body.get("has_more")) is not bool or not isinstance(
        body.get("offers"), list
    ):
        raise ValueError("Marketplace discovery envelope changed")
    result, refused = [], 0
    for offer in body["offers"][:200]:
        try:
            result.append(agent_offer(offer, now))
        except (ValueError, KeyError, TypeError):
            refused += 1
    return {
        "checked_at": now,
        "quotes": result,
        "refused": refused,
        "coverage_complete": not body["has_more"] and len(body["offers"]) <= 200,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "funded": False,
    }


def readiness():
    return [
        {
            "id": "1f916",
            "kind": "agent",
            "phase": "prepared",
            "chain_id": 8453,
            "installed": [
                "offer_commitment_verification",
                "unsigned_order_preparation",
            ],
            "missing": [
                "marketplace_identity",
                "Base_wallet_and_signer",
                "binding_verification",
                "order_acknowledgment_contract",
                "funded_delivery_test",
            ],
        },
        {
            "id": "rentahuman",
            "kind": "human",
            "phase": "blocked",
            "chain_id": 8453,
            "installed": ["structured_job_lifecycle"],
            "missing": [
                "project_account",
                "current_bounty_and_escrow_API_contract",
                "Base_funding",
                "authenticated_release_and_payout_evidence",
            ],
            "scope": "Public OpenAPI exposes legacy bookings; those are not substituted for current bounties or service escrow",
        },
        {
            "id": "compute",
            "kind": "compute",
            "phase": "blocked",
            "installed": ["structured_job_lifecycle"],
            "missing": [
                "selected_provider",
                "finite_job_quote",
                "restricted_account",
                "delivery_and_cost_evidence",
            ],
        },
    ]

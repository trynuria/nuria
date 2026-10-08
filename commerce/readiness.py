"""Read-only activation preflight. It never signs, funds or broadcasts."""

import argparse
import json
import os
from pathlib import Path

from cognition.fee_observer import rpc
from commerce.config import Policy
from commerce.fees import observe
from commerce.ledger import Ledger
from commerce.managed import ManagedSigner
from commerce.solana import usdc_balance
from commerce.transport import request
from commerce.x402 import quote


def inspect(policy, ledger):
    result = {
        "financial_execution": policy.enabled,
        "policy": policy.public(),
        "missing": policy.missing(),
        "checks": [],
        "broadcast": False,
    }
    checks = []
    if policy.mint and policy.creator_wallet:
        checks.append(
            (
                "fee_path",
                lambda: observe(policy.mint, policy.creator_wallet, rpc, policy.pool),
            )
        )
    if policy.spending_wallet:
        checks.append(
            (
                "inventory",
                lambda: usdc_balance(
                    policy.spending_wallet, rpc, ledger.minimum_balance_slot()
                ),
            )
        )
        if (
            os.environ.get("NURIA_PRIVY_CREDENTIALS")
            and Path(os.environ["NURIA_PRIVY_CREDENTIALS"]).exists()
        ):
            checks.append(
                ("custody", ManagedSigner(policy.spending_wallet, ledger, policy).check)
            )
        else:
            result["missing"].append("managed_custody_configuration")
    for provider in policy.providers:

        def invoice(provider=provider):
            status, headers, _ = request(provider.endpoint)
            if status != 402:
                raise ValueError("Merchant did not offer a payable invoice")
            return {
                "provider": provider.id,
                "accepted": quote(headers.get("payment-required"), provider),
                "delivery_verified": False,
            }

        checks.append(("provider_invoice", invoice))
    for name, check in checks:
        try:
            result["checks"].append(
                {"check": name, "verified": True, "evidence": check()}
            )
        except Exception:
            result["checks"].append(
                {
                    "check": name,
                    "verified": False,
                    "error": "Required evidence unavailable; no success inferred",
                }
            )
    result["scope"] = (
        "Unpaid readiness only. Real settlement, delivered usefulness, provider batching and complete fee-loop behavior require separate end-to-end evidence."
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    policy = Policy.load(json.loads(args.policy.read_text()))
    ledger = Ledger(args.ledger)
    try:
        print(json.dumps(inspect(policy, ledger), indent=2))
    finally:
        ledger.db.close()


if __name__ == "__main__":
    main()

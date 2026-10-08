"""Verify and replace the dedicated server's token profile without enabling funds."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cognition.fee_observer import rpc  # noqa: E402
from commerce.fees import observe  # noqa: E402
from token_profile import public, validate  # noqa: E402


def configure(args):
    for name in ("policy", "collection", "conversion", "sweep", "commissioning"):
        path = Path("/etc/nuria-commerce") / (name + ".json")
        if path.exists() and json.loads(path.read_text()).get("enabled"):
            raise ValueError(
                "Disable financial execution before changing token identity"
            )
    for line in Path(args.rpc_env).read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value.strip().strip('"').strip("'")
    value = validate(
        {
            "schema": "nuria.token-config.v1",
            "mode": args.mode,
            "mint": args.mint,
            "creator_wallet": args.creator_wallet,
            "pool": args.pool,
        }
    )
    mint = rpc(
        "getAccountInfo",
        [value["mint"], {"encoding": "jsonParsed", "commitment": "finalized"}],
    )
    account = mint.get("value") or {}
    if account.get("data", {}).get("parsed", {}).get("type") != "mint":
        raise ValueError("Configured address is not an initialized token mint")
    evidence = observe(value["mint"], value["creator_wallet"], rpc, value["pool"])
    if value["mode"] == "production" and evidence["quote_mint"] not in (
        "11111111111111111111111111111111",
        "So11111111111111111111111111111111111111112",
    ):
        raise ValueError("Nuria production requires a verified SOL-paired mint")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(args.config)
    if path.exists():
        backup = path.with_name(path.name + ".pre-" + stamp)
        backup.write_bytes(path.read_bytes())
        backup.chmod(0o400)
    candidate = path.with_name(path.name + ".candidate-" + stamp)
    candidate.write_text(json.dumps(value, indent=2) + "\n")
    os.chown(candidate, 0, path.parent.stat().st_gid)
    candidate.chmod(0o640)
    os.replace(candidate, path)
    return {
        "profile": public(value),
        "verified_slot": evidence["slot"],
        "quote_mint": evidence["quote_mint"],
        "native_claim_supported": evidence["standard_claim_supported"],
        "financial_execution": False,
        "signing_authority_changed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("test", "production"), required=True)
    parser.add_argument("--mint", required=True)
    parser.add_argument("--creator-wallet", required=True)
    parser.add_argument("--pool")
    parser.add_argument("--config", default="/etc/nuria-token/token.json")
    parser.add_argument("--rpc-env", default="/etc/nuria/helius.env")
    print(json.dumps(configure(parser.parse_args()), indent=2))

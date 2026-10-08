"""One public token identity; this configuration grants no wallet authority."""

import hashlib
import json
import os
from pathlib import Path

from solders.pubkey import Pubkey


def validate(value):
    if set(value) != {"schema", "mode", "mint", "creator_wallet", "pool"}:
        raise ValueError("Token profile fields differ from the supported contract")
    if value["schema"] != "nuria.token-config.v1" or value["mode"] not in (
        "test",
        "production",
    ):
        raise ValueError("Invalid token configuration mode")
    for name in ("mint", "creator_wallet"):
        Pubkey.from_string(value[name])
    if value["pool"] is not None:
        Pubkey.from_string(value["pool"])
    return dict(value)


def load(path=None):
    path = path or os.environ.get("NURIA_TOKEN_CONFIG")
    if not path:
        return None
    return validate(json.loads(Path(path).read_text()))


def public(value):
    value = validate(value)
    return {
        **value,
        "schema": "nuria.token.v1",
        "network": "solana-mainnet",
        "label": "Onchain test token" if value["mode"] == "test" else "Nuria token",
        "source": "solana_test_finalized:" + value["mint"]
        if value["mode"] == "test"
        else "solana_finalized",
        "configuration_sha256": hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "scope": "Public identity only. A wallet address does not grant signing or spending authority.",
    }


def apply(configuration, profile):
    if profile is None:
        return configuration
    profile = validate(profile)
    return {
        **configuration,
        "mint": profile["mint"],
        "creator_wallet": profile["creator_wallet"],
        "pool": profile["pool"],
    }

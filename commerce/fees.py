"""Read-only Pump fee-path checks and a standard-mode unsigned claim plan.

This deliberately rejects sharing, holder rewards, USDC curves and legacy layouts
until their deployed versions have their own reviewed adapters.
"""

import hashlib

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from commerce.solana import account_bytes

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_FEES = str(
    Pubkey.from_bytes(
        bytes(
            [
                12,
                53,
                255,
                169,
                5,
                90,
                142,
                86,
                141,
                168,
                247,
                188,
                7,
                86,
                21,
                39,
                76,
                241,
                201,
                44,
                164,
                31,
                64,
                0,
                156,
                81,
                106,
                164,
                20,
                194,
                124,
                112,
            ]
        )
    )
)
SYSTEM = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"
LOADER = "BPFLoaderUpgradeab1e11111111111111111111111"
CURVE_DISC = hashlib.sha256(b"account:BondingCurve").digest()[:8]
COLLECT_DISC = bytes([20, 22, 86, 123, 198, 28, 219, 132])


def pda(seed, address, program=PUMP):
    return str(
        Pubkey.find_program_address(
            [seed, bytes(Pubkey.from_string(address))], Pubkey.from_string(program)
        )[0]
    )


def observe(mint, creator, fetch):
    addresses = [
        pda(b"bonding-curve", mint),
        pda(b"creator-vault", creator),
        pda(b"sharing-config", mint, PUMP_FEES),
    ]
    result = fetch(
        "getMultipleAccounts",
        [addresses, {"encoding": "base64", "commitment": "finalized"}],
    )
    curve, vault, sharing = result["value"]
    if not curve or curve.get("owner") != PUMP:
        raise ValueError("Mint has no verified Pump bonding curve")
    raw = account_bytes(curve)
    if len(raw) < 131 or raw[:8] != CURVE_DISC:
        raise ValueError("Pump layout lacks explicit mode fields")
    actual_creator = str(Pubkey.from_bytes(raw[49:81]))
    quote_mint = str(Pubkey.from_bytes(raw[83:115]))
    if actual_creator != creator:
        raise ValueError("Configured fee beneficiary differs from live curve creator")
    if (
        raw[48] not in (0, 1)
        or raw[81] not in (0, 1)
        or raw[82] not in (0, 1)
        or raw[124] not in (0, 1)
    ):
        raise ValueError("Pump mode fields are malformed")
    supported = (
        not sharing
        and not raw[81]
        and not raw[82]
        and not raw[124]
        and quote_mint in (SYSTEM, WSOL)
    )
    if vault and (vault.get("owner") != SYSTEM or account_bytes(vault)):
        raise ValueError("Creator vault does not match the standard native SOL path")
    return {
        "mint": mint,
        "creator_wallet": creator,
        "curve": addresses[0],
        "vault": addresses[1],
        "slot": result["context"]["slot"],
        "graduated": bool(raw[48]),
        "sharing_config": addresses[2] if sharing else None,
        "holder_rewards": bool(raw[124]),
        "quote_mint": quote_mint,
        "standard_claim_supported": supported,
        "vault_balance_lamports": vault["lamports"] if vault else 0,
        "attributed_nuria_fees_lamports": None,
        "scope": "Creator vault can aggregate multiple coins. Balance is not token-specific fee income. PumpSwap claims require a separate verified adapter.",
    }


def program_identity(fetch):
    program = fetch(
        "getAccountInfo", [PUMP, {"encoding": "base64", "commitment": "finalized"}]
    )["value"]
    if not program or not program.get("executable") or program.get("owner") != LOADER:
        raise ValueError("Pump executable identity is unavailable")
    raw = account_bytes(program)
    if len(raw) != 36 or raw[:4] != (2).to_bytes(4, "little"):
        raise ValueError("Unexpected Pump upgradeable program layout")
    location = str(Pubkey.from_bytes(raw[4:36]))
    data = fetch(
        "getAccountInfo", [location, {"encoding": "base64", "commitment": "finalized"}]
    )["value"]
    if not data or data.get("owner") != LOADER:
        raise ValueError("Pump program data is unavailable")
    return {
        "program": PUMP,
        "program_data": location,
        "program_data_sha256": hashlib.sha256(account_bytes(data)).hexdigest(),
    }


def claim_plan(observation, payer, blockhash, deployment, approved_sha256):
    if (
        not observation.get("standard_claim_supported")
        or not approved_sha256
        or deployment.get("program_data_sha256") != approved_sha256
    ):
        raise ValueError(
            "Claim requires a verified standard mode and pinned deployed program"
        )
    creator = observation["creator_wallet"]
    if observation["vault"] != pda(b"creator-vault", creator):
        raise ValueError("Claim vault differs from beneficiary PDA")
    event = str(
        Pubkey.find_program_address([b"__event_authority"], Pubkey.from_string(PUMP))[0]
    )
    instruction = Instruction(
        Pubkey.from_string(PUMP),
        COLLECT_DISC,
        [
            AccountMeta(Pubkey.from_string(creator), False, True),
            AccountMeta(Pubkey.from_string(observation["vault"]), False, True),
            AccountMeta(Pubkey.from_string(SYSTEM), False, False),
            AccountMeta(Pubkey.from_string(event), False, False),
            AccountMeta(Pubkey.from_string(PUMP), False, False),
        ],
    )
    transaction = Transaction.new_unsigned(
        Message.new_with_blockhash(
            [instruction], Pubkey.from_string(payer), Hash.from_string(blockhash)
        )
    )
    return transaction

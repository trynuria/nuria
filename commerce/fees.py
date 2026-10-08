"""Read-only Pump fee-path checks and a swept standard-native claim plan.

This deliberately rejects sharing, holder rewards, USDC curves and legacy layouts
until their deployed versions have their own reviewed adapters.
"""

import hashlib

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from commerce.config import ATA, TOKEN
from commerce.solana import account_bytes, associated

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
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
COLLECT_DISC = bytes([207, 17, 138, 242, 4, 34, 19, 56])
SWEEP_DISC = bytes([32, 246, 191, 52, 8, 201, 73, 186])


def constant_pda(seed, program):
    return str(Pubkey.find_program_address([seed], Pubkey.from_string(program))[0])


def trailing_u64(raw, offset):
    """Absent append-only fields are zero; partial fields are not evidence."""
    if len(raw) <= offset:
        return 0
    if len(raw) < offset + 8:
        raise ValueError("Truncated Pump fee field")
    return int.from_bytes(raw[offset : offset + 8], "little")


def pda(seed, address, program=PUMP):
    return str(
        Pubkey.find_program_address(
            [seed, bytes(Pubkey.from_string(address))], Pubkey.from_string(program)
        )[0]
    )


def canonical_pool(mint):
    return str(
        Pubkey.find_program_address(
            [
                b"pool",
                bytes(2),
                bytes(Pubkey.from_string(pda(b"pool-authority", mint))),
                bytes(Pubkey.from_string(mint)),
                bytes(Pubkey.from_string(WSOL)),
            ],
            Pubkey.from_string(AMM),
        )[0]
    )


def observe(mint, creator, fetch, pool=None):
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
    if len(raw) < 125 or raw[:8] != CURVE_DISC:
        raise ValueError("Pump layout lacks explicit mode fields")
    actual_creator = str(Pubkey.from_bytes(raw[49:81]))
    quote_mint = str(Pubkey.from_bytes(raw[83:115]))
    if actual_creator != creator:
        raise ValueError("Configured fee beneficiary differs from live curve creator")
    if (
        raw[48] not in (0, 1)
        or raw[81] not in (0, 1)
        or raw[82] not in (0, 1)
        or raw[123] not in (0, 1)
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
    observation = {
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
        "curve_creator_fee_lamports": trailing_u64(raw, 125),
        "curve_protocol_fee_lamports": trailing_u64(raw, 133),
        "claim_interface": "native_sweep_v2",
        "attributed_nuria_fees_lamports": None,
        "scope": "Retained curve fees are mint-specific accrual, not wallet receipts. Creator vault balance may cover multiple coins; collection is not token-specific income.",
    }
    if observation["graduated"]:
        observation["standard_claim_supported"] = False
        if not pool:
            observation["scope"] = (
                "Graduated curve requires its exact verified PumpSwap pool. No collection is authorized."
            )
            return observation
        if pool != canonical_pool(mint):
            raise ValueError("Claim requires the canonical migrated PumpSwap pool")
        amm = fetch(
            "getAccountInfo", [pool, {"encoding": "base64", "commitment": "finalized"}]
        )["value"]
        if not amm or amm["owner"] != AMM:
            raise ValueError("Configured PumpSwap pool owner differs")
        data = account_bytes(amm)
        if (
            len(data) < 271
            or data[:8] != hashlib.sha256(b"account:Pool").digest()[:8]
            or str(Pubkey.from_bytes(data[43:75])) != mint
            or str(Pubkey.from_bytes(data[75:107])) != WSOL
            or str(Pubkey.from_bytes(data[211:243])) != creator
        ):
            raise ValueError("PumpSwap mint, quote or creator differs")
        if any(data[i] not in (0, 1) for i in (243, 244, 269, 270)):
            raise ValueError("PumpSwap mode fields differ")
        authority = pda(b"creator_vault", creator, AMM)
        token_vault = associated(authority, WSOL)
        info = fetch(
            "getAccountInfo",
            [token_vault, {"encoding": "jsonParsed", "commitment": "finalized"}],
        )
        amount = 0
        if info["value"]:
            parsed = info["value"]["data"]["parsed"]["info"]
            if (
                info["value"]["owner"] != TOKEN
                or parsed.get("mint") != WSOL
                or parsed.get("owner") != authority
                or parsed.get("delegate")
                or parsed.get("state") != "initialized"
                or parsed["tokenAmount"].get("decimals") != 9
            ):
                raise ValueError("PumpSwap creator token vault differs")
            amount = int(parsed["tokenAmount"]["amount"])
        observation.update(
            pool=pool,
            amm_program=AMM,
            amm_vault_authority=authority,
            amm_token_vault=token_vault,
            amm_vault_wsol_lamports=amount,
            pool_creator_fee_lamports=trailing_u64(data, 279),
            pool_protocol_fee_lamports=trailing_u64(data, 271),
            pool_quote_token_account=str(Pubkey.from_bytes(data[171:203])),
            standard_claim_supported=supported
            and int.from_bytes(data[245:261], "little", signed=True) <= 0
            and not data[243]
            and not data[244]
            and not data[270]
            and str(Pubkey.from_bytes(data[171:203])) == associated(pool, WSOL),
            scope="Verified standard WSOL PumpSwap pool. Retained pool and curve fee buckets are accrual; aggregate creator-vault collection is not inferred as mint-specific revenue.",
        )
    return observation


def program_identity(fetch, program_address=PUMP):
    program = fetch(
        "getAccountInfo",
        [program_address, {"encoding": "base64", "commitment": "finalized"}],
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
        "program": program_address,
        "program_data": location,
        "program_data_sha256": hashlib.sha256(account_bytes(data)).hexdigest(),
    }


def claim_plan(
    observation,
    payer,
    blockhash,
    deployment,
    approved_sha256,
    amm_deployment=None,
    approved_amm_sha256=None,
):
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
    if (
        observation.get("claim_interface") != "native_sweep_v2"
        or observation.get("curve") != pda(b"bonding-curve", observation["mint"])
        or observation.get("quote_mint") not in (SYSTEM, WSOL)
    ):
        raise ValueError("Claim requires the reviewed native sweep interface")
    event = constant_pda(b"__event_authority", PUMP)

    def instruction(program, discriminator, roles):
        return Instruction(
            Pubkey.from_string(program),
            discriminator,
            [
                AccountMeta(Pubkey.from_string(a), signer, writable)
                for a, signer, writable in roles
            ],
        )

    curve, vault = observation["curve"], observation["vault"]
    collect = instruction(
        PUMP,
        COLLECT_DISC,
        [
            (creator, False, True),
            (associated(creator, WSOL), False, True),
            (vault, False, True),
            (associated(vault, WSOL), False, True),
            (WSOL, False, False),
            (TOKEN, False, False),
            (ATA, False, False),
            (SYSTEM, False, False),
            (event, False, False),
            (PUMP, False, False),
        ],
    )
    instructions = []
    if observation.get("curve_creator_fee_lamports", 0):
        instructions.append(
            instruction(
                PUMP,
                SWEEP_DISC,
                [
                    (payer, True, True),
                    (constant_pda(b"global", PUMP), False, False),
                    (observation["mint"], False, False),
                    (WSOL, False, False),
                    (TOKEN, False, False),
                    (ATA, False, False),
                    (SYSTEM, False, False),
                    (curve, False, True),
                    (associated(curve, WSOL), False, True),
                    (vault, False, True),
                    (associated(vault, WSOL), False, True),
                    (event, False, False),
                    (PUMP, False, False),
                ],
            )
        )
    if observation.get("graduated"):
        if (
            not amm_deployment
            or not approved_amm_sha256
            or amm_deployment.get("program_data_sha256") != approved_amm_sha256
            or observation.get("amm_vault_authority")
            != pda(b"creator_vault", creator, AMM)
            or observation.get("amm_token_vault")
            != associated(observation["amm_vault_authority"], WSOL)
        ):
            raise ValueError(
                "PumpSwap bridge requires the verified vault and program pin"
            )
        amm_event = str(
            Pubkey.find_program_address(
                [b"__event_authority"], Pubkey.from_string(AMM)
            )[0]
        )
        if observation.get("pool_creator_fee_lamports", 0):
            pool = observation["pool"]
            if observation.get("pool_quote_token_account") != associated(pool, WSOL):
                raise ValueError("Pool fee source is not its verified quote account")
            instructions.append(
                instruction(
                    AMM,
                    SWEEP_DISC,
                    [
                        (payer, True, True),
                        (constant_pda(b"global_config", AMM), False, False),
                        (pool, False, True),
                        (WSOL, False, False),
                        (TOKEN, False, False),
                        (observation["pool_quote_token_account"], False, True),
                        (observation["amm_vault_authority"], False, False),
                        (observation["amm_token_vault"], False, True),
                        (SYSTEM, False, False),
                        (ATA, False, False),
                        (amm_event, False, False),
                        (AMM, False, False),
                    ],
                )
            )
        roles = [
            (WSOL, False),
            (TOKEN, False),
            (SYSTEM, False),
            (ATA, False),
            (creator, False),
            (observation["amm_vault_authority"], True),
            (observation["amm_token_vault"], True),
            (observation["vault"], True),
            (amm_event, False),
            (AMM, False),
        ]
        instructions.append(
            Instruction(
                Pubkey.from_string(AMM),
                bytes([139, 52, 134, 85, 228, 229, 108, 241]),
                [AccountMeta(Pubkey.from_string(a), False, w) for a, w in roles],
            )
        )
    instructions.append(collect)
    transaction = Transaction.new_unsigned(
        Message.new_with_blockhash(
            instructions, Pubkey.from_string(payer), Hash.from_string(blockhash)
        )
    )
    return transaction

"""Public policy terms and explicit activation requirements."""

from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from solders.pubkey import Pubkey

NETWORK = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOURCE = "solana_finalized"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ATA = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE = "ComputeBudget111111111111111111111111111111"
MEMO = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"


def address(value):
    if not isinstance(value, str):
        raise ValueError("Public address is missing")
    return str(Pubkey.from_string(value))


@dataclass(frozen=True)
class Provider:
    id: str
    endpoint: str
    recipient: str
    fee_payer: str
    maximum_micro_usdc: int
    action: str = "predict"
    schema: str = "nuria.forecast.v1"

    def validate(self):
        url = urlsplit(self.endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or (url.query and self.schema != "coingecko.sol-price.v1")
            or url.port not in (None, 443)
        ):
            raise ValueError(
                "Provider requires an exact credential-free HTTPS endpoint"
            )
        address(self.recipient)
        address(self.fee_payer)
        if (
            not self.id
            or len(self.id) > 64
            or self.action not in ("predict", "experiment", "compare")
        ):
            raise ValueError("Unsupported provider job")
        if self.schema == "coingecko.sol-price.v1" and (
            self.endpoint
            != "https://pro-api.coingecko.com/api/v3/x402/simple/price?ids=solana&vs_currencies=usd"
            or self.action != "compare"
        ):
            raise ValueError("Price adapter requires the exact Solana/USD resource")
        if self.schema not in ("nuria.forecast.v1", "coingecko.sol-price.v1"):
            raise ValueError("Delivery schema is not supported")
        if type(self.maximum_micro_usdc) is not int or self.maximum_micro_usdc <= 0:
            raise ValueError("Provider price ceiling is invalid")


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    mint: str | None = None
    creator_wallet: str | None = None
    spending_wallet: str | None = None
    per_job_micro_usdc: int = 0
    per_day_micro_usdc: int = 0
    reserve_micro_usdc: int = 0
    cooldown_seconds: int = 300
    providers: tuple[Provider, ...] = ()
    signer: str = "privy"
    monthly_signature_limit: int = 40_000
    maximum_unresolved_jobs: int = 3
    reserve_wallet: str | None = None
    pool: str | None = None

    @classmethod
    def load(cls, raw):
        allowed = set(cls.__dataclass_fields__)
        if set(raw) - allowed:
            raise ValueError("Unknown policy fields")
        raw = dict(raw)
        raw["providers"] = tuple(Provider(**p) for p in raw.get("providers", []))
        result = cls(**raw)
        if type(result.enabled) is not bool:
            raise ValueError("Enabled must be a boolean")
        for field in (
            "per_job_micro_usdc",
            "per_day_micro_usdc",
            "reserve_micro_usdc",
            "cooldown_seconds",
            "monthly_signature_limit",
            "maximum_unresolved_jobs",
        ):
            value = getattr(result, field)
            if type(value) is not int or value < 0:
                raise ValueError("Policy limits must be nonnegative integers")
        if result.cooldown_seconds < 60:
            raise ValueError("Paid jobs require at least a sixty-second cooldown")
        if result.signer not in ("privy", "local_test"):
            raise ValueError("Unsupported signer")
        if (
            not 1 <= result.monthly_signature_limit <= 40_000
            or not 1 <= result.maximum_unresolved_jobs <= 3
        ):
            raise ValueError("Signature or unresolved-job limit is invalid")
        for field in (
            "mint",
            "creator_wallet",
            "spending_wallet",
            "reserve_wallet",
            "pool",
        ):
            if getattr(result, field) is not None:
                address(getattr(result, field))
        for provider in result.providers:
            provider.validate()
            if (
                provider.fee_payer == result.spending_wallet
                or provider.recipient == result.spending_wallet
            ):
                raise ValueError(
                    "Provider fee payer and recipient must differ from Nuria"
                )
        if len({p.id for p in result.providers}) != len(result.providers):
            raise ValueError("Provider IDs must be unique")
        if result.enabled and result.missing():
            raise ValueError("Enabled policy lacks required configuration")
        return result

    def missing(self):
        missing = [
            name
            for name in ("mint", "creator_wallet", "spending_wallet")
            if not getattr(self, name)
        ]
        if not self.providers:
            missing.append("verified_provider")
        if not self.per_job_micro_usdc or not self.per_day_micro_usdc:
            missing.append("spending_limits")
        return missing

    def public(self):
        return {
            **asdict(self),
            "asset": USDC,
            "network": NETWORK,
            "unit": "micro-USDC; 1 USDC = 1,000,000 units",
        }

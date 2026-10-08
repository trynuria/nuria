"""Small bounded decoder for the published Squads V4 spending-limit ABI."""

import base64
import hashlib

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from commerce.config import TOKEN, USDC
from commerce.solana import associated

PROGRAM = "SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf"
SYSTEM = "11111111111111111111111111111111"


class Reader:
    def __init__(self, raw):
        if len(raw) > 16384:
            raise ValueError("Reserve account is oversized")
        self.raw, self.offset = raw, 0

    def take(self, count):
        result = self.raw[self.offset : self.offset + count]
        if len(result) != count:
            raise ValueError("Reserve account is truncated")
        self.offset += count
        return result

    def number(self, size, signed=False):
        return int.from_bytes(self.take(size), "little", signed=signed)

    def key(self):
        return str(Pubkey.from_bytes(self.take(32)))

    def keys(self):
        count = self.number(4)
        if count > 32:
            raise ValueError("Unexpected reserve membership size")
        return [self.key() for _ in range(count)]

    def discriminator(self, name):
        if self.take(8) != hashlib.sha256(("account:" + name).encode()).digest()[:8]:
            raise ValueError("Reserve account discriminator differs")


def pda(seeds):
    return str(Pubkey.find_program_address(seeds, Pubkey.from_string(PROGRAM))[0])


def check_multisig(raw, address, member):
    reader = Reader(raw)
    reader.discriminator("Multisig")
    create, authority = reader.key(), reader.key()
    threshold = reader.number(2)
    reader.take(4 + 8 + 8)
    option = reader.number(1)
    if option not in (0, 1):
        raise ValueError("Reserve rent option differs")
    if option:
        reader.take(32)
    reader.take(1)
    count = reader.number(4)
    if count > 32:
        raise ValueError("Reserve membership is oversized")
    members = [(reader.key(), reader.number(1)) for _ in range(count)]
    if (
        address != pda([b"multisig", b"multisig", bytes(Pubkey.from_string(create))])
        or authority != SYSTEM
        or threshold < 2
        or (member, 4) not in members
    ):
        raise ValueError(
            "Reserve requires independent quorum and an execution-only member"
        )
    return {
        "threshold": threshold,
        "config_authority": authority,
        "members": [{"wallet": w, "permissions": m} for w, m in members],
    }


def build(value):
    reader = Reader(base64.b64decode(value["account"], validate=True))
    reader.discriminator("SpendingLimit")
    multisig, create = reader.key(), reader.key()
    index, mint, maximum, period, remaining, reset = (
        reader.number(1),
        reader.key(),
        reader.number(8),
        reader.number(1),
        reader.number(8),
        reader.number(8, signed=True),
    )
    reader.take(1)
    members, destinations = reader.keys(), reader.keys()
    wallet, amount = value["wallet"], value["amount"]
    vault = pda(
        [b"multisig", bytes(Pubkey.from_string(multisig)), b"vault", bytes([index])]
    )
    limit = pda(
        [
            b"multisig",
            bytes(Pubkey.from_string(multisig)),
            b"spending_limit",
            bytes(Pubkey.from_string(create)),
        ]
    )
    if (
        value["owner"] != PROGRAM
        or multisig != value["multisig"]
        or index != value["vault_index"]
        or vault != value["vault"]
        or limit != value["spending_limit"]
        or mint not in (USDC, SYSTEM)
        or value["mint"] != mint
        or period != 1
        or maximum > value["maximum"]
        or members != [wallet]
        or destinations != [wallet]
        or type(amount) is not int
        or not 0 < amount <= value["maximum"]
    ):
        raise ValueError("Reserve allowance differs from reviewed terms")
    available = maximum if value["now"] >= reset + 86400 else remaining
    if amount > available:
        raise ValueError("Reserve allowance is exhausted")
    token = mint == USDC
    roles = [
        (multisig, False, False),
        (wallet, True, False),
        (limit, False, True),
        (vault, False, True),
        (wallet, False, True),
        (SYSTEM, False, False),
        (USDC if token else PROGRAM, False, False),
        (associated(vault) if token else PROGRAM, False, token),
        (associated(wallet) if token else PROGRAM, False, token),
        (TOKEN if token else PROGRAM, False, False),
    ]
    ix = Instruction(
        Pubkey.from_string(PROGRAM),
        bytes([16, 57, 130, 127, 193, 20, 155, 134])
        + amount.to_bytes(8, "little")
        + bytes([6 if token else 9, 0]),
        [AccountMeta(Pubkey.from_string(w), s, m) for w, s, m in roles],
    )
    message = MessageV0.try_compile(
        Pubkey.from_string(wallet), [ix], [], Hash.from_string(value["blockhash"])
    )
    tx = VersionedTransaction.populate(message, [Signature.default()])
    return {
        "transaction": base64.b64encode(bytes(tx)).decode(),
        "program": PROGRAM,
        "vault": vault,
        "allowance": maximum,
        "remaining": available,
    }

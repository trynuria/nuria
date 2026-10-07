"""Streaming archive compatibility and consistent cognitive snapshot recovery."""

import base64
import io
import json
import tarfile
import unittest
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backup_crypto import seal_archive, stream_digest
from cognition.backup import snapshot
from cognition.worker import Organism

ROOT = Path(__file__).resolve().parents[1]


class RecoveryArchiveTests(unittest.TestCase):
    def test_streamed_encryption_decrypts_in_original_format(self):
        directory = ROOT / ".test-state" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        source = directory / "fixture.txt"
        source.write_text("retained evidence\n" * 10000)
        # An ephemeral test key exists only in process memory.
        recovery = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        sealed = directory / "fixture.nuria.enc"
        metadata = seal_archive(
            [(source, "evidence.txt")], recovery.public_key(), sealed
        )
        with sealed.open("rb") as stream:
            self.assertEqual(stream_digest(stream), metadata["cipher_sha256"])
        magic, header, cipher = sealed.read_bytes().split(b"\n", 2)
        self.assertEqual(magic, b"NURIABACKUP1")
        parameters = json.loads(header)
        key = recovery.decrypt(
            base64.b64decode(parameters["wrapped_key"]),
            padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None
            ),
        )
        plain = AESGCM(key).decrypt(
            base64.b64decode(parameters["nonce"]), cipher, header
        )
        with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as archive:
            self.assertEqual(
                archive.extractfile("evidence.txt").read(), source.read_bytes()
            )

    def test_snapshot_contains_only_committed_neural_and_input_state(self):
        directory = ROOT / ".test-state" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        organism = Organism(directory / "source", "fixture")
        self.addCleanup(organism.journal.db.close)
        organism.cycle(
            [
                (
                    1,
                    {
                        "id": "snapshot-input",
                        "side": "buy",
                        "quote_amount": 1.0,
                        "source": "test",
                    },
                )
            ]
        )
        state = organism.brain.state_hash()
        organism.cycle(
            [
                (
                    2,
                    {
                        "id": "pending-input",
                        "side": "sell",
                        "quote_amount": 1.0,
                        "source": "test",
                    },
                )
            ],
            durable=False,
        )
        copied = directory / "restored"
        copied.mkdir()
        result = snapshot(
            directory / "source" / "cognition.sqlite3", copied / "cognition.sqlite3"
        )
        self.assertEqual(result["cursor"], 1)
        restored = Organism(copied, "fixture")
        self.addCleanup(restored.journal.db.close)
        self.assertEqual(restored.brain.state_hash(), state)
        self.assertEqual(restored.cursor, 1)

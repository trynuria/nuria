"""Streaming tar encryption compatible with the existing Nuria backup format."""

import base64
import hashlib
import json
import os
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class EncryptedWriter:
    def __init__(self, stream, encryptor):
        self.stream, self.encryptor = stream, encryptor

    def write(self, data):
        self.stream.write(self.encryptor.update(data))
        return len(data)

    def flush(self):
        self.stream.flush()


def seal_archive(paths: list[tuple[Path, str]], recipient, destination: Path) -> dict:
    key = os.urandom(32)
    nonce = os.urandom(12)
    wrapped = recipient.encrypt(
        key,
        padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None
        ),
    )
    header = json.dumps(
        {
            "schema": 1,
            "wrapped_key": base64.b64encode(wrapped).decode(),
            "nonce": base64.b64encode(nonce).decode(),
        },
        separators=(",", ":"),
    ).encode()
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    with destination.open("wb") as output:
        destination.chmod(0o600)
        output.write(b"NURIABACKUP1\n" + header + b"\n")
        sink = EncryptedWriter(output, encryptor)
        with tarfile.open(fileobj=sink, mode="w|gz") as archive:
            for path, name in paths:
                archive.add(path, arcname=name)
        output.write(encryptor.finalize())
        output.write(encryptor.tag)
        output.flush()
        os.fsync(output.fileno())
    with destination.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"encrypted_bytes": destination.stat().st_size, "cipher_sha256": digest}


def stream_digest(stream) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()

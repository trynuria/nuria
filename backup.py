"""Nuria-only encrypted Spaces backups. The private recovery key stays off this server."""

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
BASE = Path("/var/lib/nuria/backups")
BASE.mkdir(mode=0o700, exist_ok=True)
STAGE = Path("/var/lib/nuria/history/backup-staging/current")
if (
    sum(p.stat().st_size for p in BASE.glob("*") if p.is_file()) > 2 * 1024**3
    or shutil.disk_usage(BASE).free < 8 * 1024**3
):
    raise SystemExit(
        "Backup capacity approval required; retained files were not deleted"
    )
credentials = json.loads(Path("/etc/nuria/spaces-credentials.json").read_text())
client = boto3.client(
    "s3",
    region_name="ams3",
    endpoint_url="https://ams3.digitaloceanspaces.com",
    aws_access_key_id=credentials["access_key"],
    aws_secret_access_key=credentials["secret_key"],
)
bucket = os.environ["NURIA_BACKUP_BUCKET"]
existing = client.list_objects_v2(
    Bucket=bucket, Prefix="application/", MaxKeys=100
).get("Contents", [])
if len(existing) >= 31:
    raise SystemExit(
        "Backup retention approval required: 31 archives retained; no files deleted"
    )
subprocess.run(
    [
        "sudo",
        "-u",
        "nuria-engine",
        "/opt/nuria/venv/bin/python",
        "/opt/nuria/app/backup_snapshot.py",
        str(STAGE),
    ],
    check=True,
    timeout=360,
)
subprocess.run(
    [
        "sudo",
        "-u",
        "nuria-cognition",
        "/opt/nuria/venv/bin/python",
        "-m",
        "cognition.backup",
        "/var/lib/nuria/cognition/backup/current.sqlite3",
    ],
    cwd="/opt/nuria/app",
    check=True,
    timeout=60,
)
archive = io.BytesIO()
if (STAGE / "database.dump").stat().st_size > 512 * 1024**2:
    raise SystemExit(
        "Backup snapshot exceeds measured memory bound; streaming backup upgrade required"
    )
with tarfile.open(fileobj=archive, mode="w:gz") as tar:
    tar.add(STAGE, arcname="snapshot")
    tar.add("/opt/nuria/app", arcname="app")
    tar.add(
        "/var/lib/nuria/cognition/backup/current.sqlite3",
        arcname="cognition/cognition.sqlite3",
    )
plain = archive.getvalue()
recipient = serialization.load_pem_public_key(
    Path("/etc/nuria/backup-recipient.pem").read_bytes()
)
key = AESGCM.generate_key(bit_length=256)
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
cipher = AESGCM(key).encrypt(nonce, plain, header)
sealed = BASE / (STAMP + ".nuria.enc")
sealed.write_bytes(b"NURIABACKUP1\n" + header + b"\n" + cipher)
sealed.chmod(0o600)
object_key = "application/" + sealed.name
client.upload_file(str(sealed), bucket, object_key, ExtraArgs={"ACL": "private"})
returned = client.get_object(Bucket=bucket, Key=object_key)["Body"].read()
if hashlib.sha256(returned).digest() != hashlib.sha256(sealed.read_bytes()).digest():
    raise RuntimeError("Backup upload readback differs from encrypted archive")
result = {
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "object_key": object_key,
    "encrypted_bytes": len(returned),
    "cipher_sha256": hashlib.sha256(returned).hexdigest(),
    "upload_readback_verified": True,
    "head": json.loads((STAGE / "manifest.json").read_text())["head"],
    "restore_test": "Pending",
}
(BASE / "latest.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))

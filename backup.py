"""Nuria-only encrypted Spaces backups. The private recovery key stays off this server."""

import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import boto3
from cryptography.hazmat.primitives import serialization

from backup_crypto import seal_archive, stream_digest

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
    cwd=str(Path(__file__).resolve().parent),
    check=True,
    timeout=60,
)
discovery_backup = Path("/var/lib/nuria/discovery/backup/current.sqlite3")
if Path("/var/lib/nuria/discovery/cognition.sqlite3").exists():
    subprocess.run(
        [
            "sudo",
            "-u",
            "nuria-discovery",
            "env",
            "NURIA_COGNITION_DATA=/var/lib/nuria/discovery",
            "/opt/nuria/venv/bin/python",
            "-m",
            "cognition.backup",
            str(discovery_backup),
        ],
        cwd=str(Path(__file__).resolve().parent),
        check=True,
        timeout=60,
    )
recipient = serialization.load_pem_public_key(
    Path("/etc/nuria/backup-recipient.pem").read_bytes()
)
sealed = BASE / (STAMP + ".nuria.enc")
sealed_metadata = seal_archive(
    [
        (STAGE, "snapshot"),
        (Path("/opt/nuria/app"), "app"),
        (Path(__file__).resolve().parent, "cognitive-release"),
        (
            Path("/var/lib/nuria/cognition/backup/current.sqlite3"),
            "cognition/cognition.sqlite3",
        ),
    ]
    + (
        [(discovery_backup, "discovery/cognition.sqlite3")]
        if discovery_backup.exists()
        else []
    ),
    recipient,
    sealed,
)
object_key = "application/" + sealed.name
client.upload_file(str(sealed), bucket, object_key, ExtraArgs={"ACL": "private"})
body = client.get_object(Bucket=bucket, Key=object_key)["Body"]
try:
    returned_digest = stream_digest(body)
finally:
    body.close()
if returned_digest != sealed_metadata["cipher_sha256"]:
    raise RuntimeError("Backup upload readback differs from encrypted archive")
with closing(
    sqlite3.connect(
        "file:/var/lib/nuria/cognition/backup/current.sqlite3?mode=ro", uri=True
    )
) as snapshot:
    cognitive = json.loads(
        snapshot.execute("SELECT metadata FROM checkpoint WHERE id=1").fetchone()[0]
    )
result = {
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "object_key": object_key,
    **sealed_metadata,
    "upload_readback_verified": True,
    "head": json.loads((STAGE / "manifest.json").read_text())["head"],
    "cognitive_head": cognitive["record_head"],
    "cognitive_tick": cognitive["tick"],
    "restore_test": "Pending",
}
(BASE / "latest.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))

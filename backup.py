"""Nuria-only encrypted Spaces backups. The private recovery key stays off this server."""

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from backup_crypto import seal_archive, stream_digest


@dataclass(frozen=True)
class BackupPaths:
    run_id: str
    base: Path
    stage: Path
    cognition: Path
    discovery: Path
    commerce: Path

    @property
    def sealed(self) -> Path:
        return self.base / (self.run_id + ".nuria.enc")

    @property
    def receipt(self) -> Path:
        return self.base / (self.run_id + ".json")

    def require_unused(self) -> None:
        for path in (
            self.stage,
            self.cognition,
            self.discovery,
            self.commerce,
            self.sealed,
            self.receipt,
        ):
            if path.exists() or path.is_symlink():
                raise FileExistsError(f"Backup destination already exists: {path}")


def new_backup_paths(
    root: Path = Path("/var/lib/nuria"), now: datetime | None = None
) -> BackupPaths:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_id = timestamp.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
    return BackupPaths(
        run_id,
        root / "backups",
        root / "history" / "backup-staging" / run_id,
        root / "cognition" / "backup" / (run_id + ".sqlite3"),
        root / "discovery" / "backup" / (run_id + ".sqlite3"),
        root / "commerce" / "backup" / (run_id + ".sqlite3"),
    )


def main() -> None:
    import boto3

    paths = new_backup_paths()
    paths.base.mkdir(mode=0o700, exist_ok=True)
    paths.require_unused()
    if (
        sum(p.stat().st_size for p in paths.base.glob("*") if p.is_file()) > 2 * 1024**3
        or shutil.disk_usage(paths.base).free < 8 * 1024**3
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
            str(Path(__file__).resolve().with_name("backup_snapshot.py")),
            str(paths.stage),
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
            str(paths.cognition),
        ],
        cwd=str(Path(__file__).resolve().parent),
        check=True,
        timeout=60,
    )
    discovery_backup = paths.discovery
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
    commerce_backup = paths.commerce
    if Path("/var/lib/nuria/commerce/commerce.sqlite3").exists():
        subprocess.run(
            [
                "sudo",
                "-u",
                "nuria-commerce",
                "/opt/nuria/commerce-venv/bin/python",
                "-m",
                "commerce.backup",
                str(commerce_backup),
            ],
            cwd=str(Path(__file__).resolve().parent),
            check=True,
            timeout=60,
        )
    sealed = paths.sealed
    sealed_metadata = seal_archive(
        [
            (paths.stage, "snapshot"),
            (Path("/opt/nuria/app"), "app"),
            (Path(__file__).resolve().parent, "cognitive-release"),
            (
                paths.cognition,
                "cognition/cognition.sqlite3",
            ),
        ]
        + (
            [(discovery_backup, "discovery/cognition.sqlite3")]
            if discovery_backup.exists()
            else []
        )
        + (
            [(commerce_backup, "commerce/commerce.sqlite3")]
            if commerce_backup.exists()
            else []
        ),
        # The spending key is deliberately outside these source and snapshot roots.
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
        sqlite3.connect(f"file:{paths.cognition}?mode=ro", uri=True)
    ) as snapshot:
        cognitive = json.loads(
            snapshot.execute("SELECT metadata FROM checkpoint WHERE id=1").fetchone()[0]
        )
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "object_key": object_key,
        **sealed_metadata,
        "upload_readback_verified": True,
        "head": json.loads((paths.stage / "manifest.json").read_text())["head"],
        "cognitive_head": cognitive["record_head"],
        "cognitive_tick": cognitive["tick"],
        "restore_test": "Pending",
    }
    with paths.receipt.open("x") as receipt:
        receipt.write(json.dumps(result, indent=2))
    # The latest pointer is mutable; every successful run also retains its own receipt.
    (paths.base / "latest.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    main()

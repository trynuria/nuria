"""External source retrieval preserves identity and the reserved-target boundary."""

import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.fetch_active_skill_sources import (
    PROJECT_ROOT,
    REVISION,
    checked_blob,
    development_path,
    frozen_terms,
    retrieve,
    stage_reserved,
)


class ActiveSkillSourceTests(unittest.TestCase):
    def authority(self):
        inventory = {
            "source": {"commit": REVISION},
            "tasks": [
                {
                    "task_id": "Unit.000001",
                    "structurally_eligible": True,
                    "split": "reserved",
                    "specification": {"bytes": 10},
                }
            ],
        }
        inventory_raw = json.dumps(inventory).encode()
        inventory_hash = hashlib.sha256(inventory_raw).hexdigest()
        protocol = {
            "status": "frozen_before_reserved_access",
            "source_hashes": {
                "scripts/fetch_active_skill_sources.py": hashlib.sha256(
                    (
                        PROJECT_ROOT / "scripts/fetch_active_skill_sources.py"
                    ).read_bytes()
                ).hexdigest()
            },
            "execution_terms": {
                "domain": "external",
                "phase": "reserved",
                "inventory_sha256": inventory_hash,
                "task_ids": ["Unit.000001"],
            },
        }
        protocol_raw = json.dumps(protocol).encode()
        protocol_hash = hashlib.sha256(protocol_raw).hexdigest()
        record = {
            "commit": "b" * 40,
            "protocol_sha256": protocol_hash,
            "inventory_sha256": inventory_hash,
        }
        return protocol_raw, inventory_raw, protocol_hash, record

    def test_exact_git_blob_passes_and_tampering_or_length_change_fails(self):
        raw = b'{"Synthetic":true}'
        entry = {
            "size": len(raw),
            "sha": hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw
            ).hexdigest(),
        }
        self.assertEqual(checked_blob(raw, entry), raw)
        for changed in (raw + b" ", raw.replace(b"true", b"null")):
            with self.assertRaisesRegex(ValueError, "identity"):
                checked_blob(changed, entry)

    def test_reserved_family_or_path_injection_cannot_request_a_specification(self):
        self.assertEqual(
            development_path("Email.000008"),
            "Transformation.Text/Email.000008/spec.json",
        )
        for name in ("Name.000008", "Email.000008/../../LICENSE", "Email.1", None):
            with self.assertRaisesRegex(ValueError, "development-family"):
                development_path(name)

    def test_response_is_read_only_to_its_bound_and_oversize_is_rejected(self):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"12345")):
            with self.assertRaisesRegex(ValueError, "bound"):
                retrieve("https://example.invalid/fixture", 4)

    def test_reserved_authority_requires_frozen_hashes_task_ids_and_commit_record(self):
        protocol, inventory, identity, record = self.authority()
        self.assertEqual(len(frozen_terms(protocol, inventory, identity, record)[2]), 1)
        for altered in (
            {**record, "commit": "not-published"},
            {**record, "inventory_sha256": "0" * 64},
            {**record, "protocol_sha256": "0" * 64},
        ):
            with self.assertRaises(ValueError):
                frozen_terms(protocol, inventory, identity, altered)
        with self.assertRaises(ValueError):
            frozen_terms(protocol + b" ", inventory, identity, record)

    def test_local_freeze_declaration_cannot_unlock_unpublished_reserved_data(self):
        protocol, inventory, identity, record = self.authority()
        files = {
            "unit-protocol": protocol,
            "unit-inventory": inventory,
            "unit-record": json.dumps(record).encode(),
        }
        original = Path.read_bytes

        def read(path):
            return files[str(path)] if str(path) in files else original(path)

        with (
            patch.object(Path, "read_bytes", read),
            patch(
                "scripts.fetch_active_skill_sources.retrieve",
                return_value=b"unpublished",
            ) as network,
        ):
            with self.assertRaisesRegex(ValueError, "not published"):
                stage_reserved(
                    "unit-unused-destination",
                    "unit-protocol",
                    identity,
                    "unit-record",
                    "unit-inventory",
                )
        self.assertEqual(network.call_count, 1)
        self.assertIn("trynuria/nuria/" + record["commit"], network.call_args.args[0])

import gzip
import io
import json
import tarfile
import unittest
import uuid
from pathlib import Path

import replay
import verify as core

ROOT = Path(__file__).resolve().parent


class PortableReplayTests(unittest.TestCase):
    def directory(self):
        root = ROOT / ".test-state"
        root.mkdir(exist_ok=True)
        path = root / ("package-test-" + uuid.uuid4().hex)
        path.mkdir(mode=0o700)
        return path

    def test_complete_actual_replay_preserves_partial_and_negative_results(self):
        root = self.directory()
        actual = replay.replay(ROOT, root / "extracted")
        self.assertEqual(actual, core.read(ROOT / "replay-result.json"))
        self.assertEqual(actual["primary"]["unexecuted_ungraded"], 22)
        self.assertFalse(actual["primary"]["candidate_promoted"])
        self.assertEqual(actual["model_substitution"]["accepted"], 9)
        self.assertEqual(
            actual["model_substitution"]["unchanged_rejected_task_ids"],
            ["spider-dev-0267", "spider-dev-0687", "spider-dev-0698"],
        )
        self.assertEqual(actual["model_calls"], 0)
        self.assertFalse(actual["provider_billing_verified"])

    def test_archive_hash_rejects_corruption_before_creating_workspace(self):
        root = self.directory()
        package = (ROOT / "package.json").read_bytes()
        archive = bytearray((ROOT / "evidence.tar.gz").read_bytes())
        archive[len(archive) // 2] ^= 1
        (root / "package.json").write_bytes(package)
        (root / "evidence.tar.gz").write_bytes(archive)
        with self.assertRaisesRegex(core.ReplayError, "archive_identity"):
            replay.extract(root, root / "extracted")
        self.assertFalse((root / "extracted").exists())

    def test_inventory_count_cannot_hide_a_missing_or_duplicate_member(self):
        root = self.directory()
        package = core.read(ROOT / "package.json")
        package["member_count"] += 1
        (root / "package.json").write_text(json.dumps(package))
        (root / "evidence.tar.gz").write_bytes((ROOT / "evidence.tar.gz").read_bytes())
        with self.assertRaisesRegex(core.ReplayError, "archive_inventory_bound"):
            replay.extract(root, root / "extracted")

    def test_compressed_expansion_is_bounded_before_tar_header_walk(self):
        root = self.directory()
        compressed = gzip.compress(b"0" * (18 * 1024 * 1024 + 1), mtime=0)
        package = {
            "archive": "evidence.tar.gz",
            "archive_bytes": len(compressed),
            "archive_sha256": core.sha(compressed),
            "member_count": 0,
            "members": [],
            "expanded_bytes": 0,
        }
        (root / "package.json").write_text(json.dumps(package))
        (root / "evidence.tar.gz").write_bytes(compressed)
        with self.assertRaisesRegex(core.ReplayError, "archive_decompression_bound"):
            replay.extract(root, root / "extracted")

    def unsafe_package(self, member, *, symbolic=False):
        root = self.directory()
        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w") as archive:
            info = tarfile.TarInfo(member)
            if symbolic:
                info.type = tarfile.SYMTYPE
                info.linkname = "../outside"
                data = b""
            else:
                data = b"bad"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        compressed = gzip.compress(tar_bytes.getvalue(), mtime=0)
        (root / "evidence.tar.gz").write_bytes(compressed)
        (root / "package.json").write_text(
            json.dumps(
                {
                    "archive": "evidence.tar.gz",
                    "archive_bytes": len(compressed),
                    "archive_sha256": core.sha(compressed),
                    "expanded_bytes": len(data),
                    "member_count": 1,
                    "members": [
                        {"path": member, "bytes": len(data), "sha256": core.sha(data)}
                    ],
                }
            )
        )
        return root

    def test_self_consistent_traversal_member_is_still_rejected(self):
        root = self.unsafe_package("../outside")
        with self.assertRaisesRegex(core.ReplayError, "archive_regular_member"):
            replay.extract(root, root / "extracted")
        self.assertFalse((root / "outside").exists())

    def test_symbolic_archive_members_are_never_followed(self):
        root = self.unsafe_package("alias", symbolic=True)
        with self.assertRaisesRegex(core.ReplayError, "archive_regular_member"):
            replay.extract(root, root / "extracted")
        self.assertFalse((root / "extracted/alias").exists())

    def test_package_contains_only_already_attempted_primary_task_bodies(self):
        with tarfile.open(ROOT / "evidence.tar.gz", mode="r:gz") as archive:
            report = json.load(archive.extractfile("primary/report.json"))
            bodies = {
                name.removeprefix("gold/primary/").removesuffix(".json")
                for name in archive.getnames()
                if name.startswith("gold/primary/")
            }
        attempted = {row["task_id"] for row in report["rows"] if row["attempted"]}
        never_attempted = {row["task_id"] for row in report["rows"]} - attempted
        self.assertEqual(bodies, attempted)
        self.assertEqual(len(bodies), 15)
        self.assertEqual(len(never_attempted), 7)
        self.assertTrue(bodies.isdisjoint(never_attempted))


if __name__ == "__main__":
    unittest.main()

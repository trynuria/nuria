import gzip
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_neural_routing_results import check_archives, safe_path, verify


class NeuralRoutingEvidenceTests(unittest.TestCase):
    def fixture(self):
        # Retain finite diagnostic fixtures rather than deleting evidence.
        root = Path(tempfile.mkdtemp(prefix="nuria-routing-evidence-"))
        for number in (1, 2, 3):
            content = json.dumps({"attempt": number}).encode()
            name = f"attempt-{number}.json.gz"
            published = gzip.compress(content, mtime=0)
            (root / name).write_bytes(published)
            manifest = {
                "schema": "nuria.neural-routing-development-archive.v1",
                "artifacts": [
                    {
                        "path": name,
                        "published_sha256": hashlib.sha256(published).hexdigest(),
                        "normalized_sha256": hashlib.sha256(content).hexdigest(),
                        "original_sha256": hashlib.sha256(content).hexdigest(),
                    }
                ],
            }
            (root / f"archive-manifest-{number:02}.json").write_text(
                json.dumps(manifest)
            )
        return root

    def test_compressed_bytes_and_content_are_both_bound(self):
        root = self.fixture()
        artifacts, originals = check_archives(root)
        self.assertEqual(len(artifacts), 3)
        self.assertEqual(artifacts, originals)
        (root / "attempt-2.json.gz").write_bytes(gzip.compress(b"{}", mtime=0))
        with self.assertRaises(ValueError):
            check_archives(root)

    def test_expanded_private_annotations_fail_even_with_updated_byte_hash(self):
        root = self.fixture()
        content = json.dumps({"note": "conversation" + "_id"}).encode()
        published = gzip.compress(content, mtime=0)
        (root / "attempt-1.json.gz").write_bytes(published)
        path = root / "archive-manifest-01.json"
        data = json.loads(path.read_text())
        row = data["artifacts"][0]
        row["published_sha256"] = hashlib.sha256(published).hexdigest()
        row["normalized_sha256"] = hashlib.sha256(content).hexdigest()
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            check_archives(root)

    def test_paths_cannot_escape_evidence_root(self):
        root = self.fixture()
        for name in (
            "../attempt-1.json.gz",
            str(root / "attempt-1.json.gz"),
            "absent.json",
        ):
            with self.assertRaises(ValueError):
                safe_path(root, name)
        (root / "alias.json").symlink_to(root / "attempt-1.json.gz")
        with self.assertRaises(ValueError):
            safe_path(root, "alias.json")

    def test_compressed_payload_still_has_a_finite_expansion_bound(self):
        root = self.fixture()
        content = b" " * 1024
        published = gzip.compress(content, mtime=0)
        (root / "attempt-1.json.gz").write_bytes(published)
        path = root / "archive-manifest-01.json"
        data = json.loads(path.read_text())
        data["artifacts"][0].update(
            published_sha256=hashlib.sha256(published).hexdigest(),
            normalized_sha256=hashlib.sha256(content).hexdigest(),
        )
        path.write_text(json.dumps(data))
        with patch("scripts.check_neural_routing_results.MAX_ARTIFACT", 512):
            with self.assertRaisesRegex(ValueError, "Expanded artifact exceeds"):
                check_archives(root)

    def test_all_retained_authored_deliveries_reproduce(self):
        root = (
            Path(__file__).resolve().parents[1]
            / "research/neural-routing-loop/development"
        )
        receipt = verify(root)
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["independently_replayed_deliveries"], 14592)
        self.assertGreaterEqual(receipt["archive_files"], 179)
        self.assertFalse(receipt["superiority_established"])
        self.assertEqual(
            [a["accepted"]["spiking"] for a in receipt["attempts"]],
            [302, 313, 317, 266],
        )

    def test_every_published_archive_item_is_actually_in_git(self):
        repository = Path(__file__).resolve().parents[1]
        if not (repository / ".git").exists():
            self.skipTest("A source distribution need not include Git metadata")
        names = set(
            subprocess.check_output(["git", "ls-files", "-z"], cwd=repository)
            .decode()
            .split("\0")
        )
        root = repository / "research/neural-routing-loop/development"
        required = {
            str((root / row["path"]).relative_to(repository))
            for manifest in root.glob("archive-manifest-*.json")
            for row in json.loads(manifest.read_bytes())["artifacts"]
        }
        self.assertFalse(
            required - names,
            "A local-only artifact would be absent from a fresh checkout",
        )


if __name__ == "__main__":
    unittest.main()

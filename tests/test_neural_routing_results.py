import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

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

    def test_all_retained_authored_deliveries_reproduce(self):
        root = (
            Path(__file__).resolve().parents[1]
            / "research/neural-routing-loop/development"
        )
        receipt = verify(root)
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["independently_replayed_deliveries"], 14592)
        self.assertEqual(receipt["archive_files"], 179)
        self.assertFalse(receipt["superiority_established"])
        self.assertEqual(
            [a["accepted"]["spiking"] for a in receipt["attempts"]],
            [302, 313, 317, 266],
        )


if __name__ == "__main__":
    unittest.main()

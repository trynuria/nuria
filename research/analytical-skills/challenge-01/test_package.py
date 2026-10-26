"""Finite portable-evidence checks; created fixtures are retained."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import stat
import unittest
import uuid
import warnings
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]
SPEC = importlib.util.spec_from_file_location(
    "analytical_skill_package", HERE / "verify.py"
)
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.root = REPOSITORY / ".test-state" / ("report-package-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o700, parents=True)

    def variant(self, name, transform=None):
        target = self.root / name
        target.mkdir(mode=0o700)
        descriptor = json.loads((HERE / "package.json").read_bytes())
        with zipfile.ZipFile(HERE / "evidence.zip") as source:
            members = [
                (copy.copy(info), source.read(info)) for info in source.infolist()
            ]
        if transform is not None:
            members = transform(members)
        archive = target / "evidence.zip"
        with warnings.catch_warnings(), zipfile.ZipFile(archive, "w") as output:
            warnings.simplefilter("ignore", UserWarning)
            for info, data in members:
                output.writestr(info, data)
        descriptor["archive_bytes"] = archive.stat().st_size
        descriptor["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        (target / "package.json").write_text(json.dumps(descriptor))
        return target

    def test_actual_roundtrip_matches_retained_independent_verdict(self):
        workspace = self.root / "roundtrip"
        observed = PACKAGE.verify_package(HERE, REPOSITORY, workspace)
        expected = json.loads((HERE / "replay-result.json").read_bytes())
        self.assertEqual(observed, expected)
        self.assertEqual(observed["queries_replayed"], 36)
        self.assertEqual(observed["skill_events"], 37)
        self.assertEqual(len([x for x in workspace.rglob("*") if x.is_file()]), 98)

    def test_existing_workspace_is_never_overwritten(self):
        workspace = self.root / "occupied"
        workspace.mkdir()
        marker = workspace / "keep.txt"
        marker.write_text("retained")
        with self.assertRaises(ValueError):
            PACKAGE.extract_package(HERE, workspace)
        self.assertEqual(marker.read_text(), "retained")

    def test_outer_archive_identity_is_required(self):
        package = self.variant("outer-hash")
        descriptor = json.loads((package / "package.json").read_bytes())
        descriptor["archive_sha256"] = "0" * 64
        (package / "package.json").write_text(json.dumps(descriptor))
        with self.assertRaises(ValueError):
            PACKAGE.read_package(package)

    def test_extra_missing_duplicate_unsafe_and_linked_members_are_refused(self):
        def extra(items):
            info = zipfile.ZipInfo("extra.json")
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            return items + [(info, b"{}")]

        def unsafe(items):
            items[0][0].filename = "../outside.json"
            return items

        def linked(items):
            items[0][0].external_attr = (stat.S_IFLNK | 0o700) << 16
            return items

        transforms = {
            "extra": extra,
            "missing": lambda items: items[:-1],
            "duplicate": lambda items: items + [items[0]],
            "unsafe": unsafe,
            "symlink": linked,
        }
        for name, transform in transforms.items():
            with self.subTest(name=name):
                package = self.variant(name, transform)
                workspace = self.root / (name + "-workspace")
                with self.assertRaises(ValueError):
                    PACKAGE.extract_package(package, workspace)
                self.assertFalse(workspace.exists())

    def test_rehashed_archive_cannot_change_a_declared_member(self):
        def changed(items):
            info, data = items[0]
            items[0] = (info, data + b" ")
            return items

        package = self.variant("changed-member", changed)
        with self.assertRaises(ValueError):
            PACKAGE.read_package(package)

    def test_size_limits_and_frozen_repository_identity_are_enforced(self):
        package = self.variant("size-bound")
        descriptor = json.loads((package / "package.json").read_bytes())
        descriptor["expanded_bytes"] = PACKAGE.MAX_EXPANDED_BYTES + 1
        (package / "package.json").write_text(json.dumps(descriptor))
        with self.assertRaises(ValueError):
            PACKAGE.read_package(package)
        fake_repository = self.root / "other-repository"
        fake_repository.mkdir()
        with self.assertRaises(ValueError):
            PACKAGE.verify_package(HERE, fake_repository, self.root / "uncreated")
        self.assertFalse((self.root / "uncreated").exists())


if __name__ == "__main__":
    unittest.main()

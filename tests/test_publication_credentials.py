"""Reject credential-shaped material without repeating it in diagnostics."""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.check_publication import check


class PublicationCredentialTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("publication-credentials-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)

    def publication(self, text):
        candidate = self.root / "candidate.txt"
        candidate.write_text(text, encoding="utf-8")
        candidate.chmod(0o600)
        with patch(
            "scripts.check_publication.subprocess.check_output",
            return_value=b"candidate.txt\0",
        ):
            return check(self.root)

    def rejects_without_value(self, secret):
        with self.assertRaises(ValueError) as context:
            self.publication('OPENAI_API_KEY="' + secret + '"')
        diagnostic = str(context.exception)
        self.assertEqual(
            diagnostic,
            "[{'path': 'candidate.txt', 'rule': 'OpenAI credential'}]",
        )
        self.assertNotIn(secret, diagnostic)

    def test_project_credential_is_rejected_without_its_value(self):
        self.rejects_without_value("sk-" + "proj-" + "x" * 80)

    def test_service_account_credential_is_rejected_without_its_value(self):
        self.rejects_without_value("sk-" + "svcacct-" + "Y_2-" * 20)

    def test_legacy_credential_is_rejected_without_its_value(self):
        self.rejects_without_value("sk-" + "Z7" * 24)

    def test_documentation_placeholders_are_allowed(self):
        result = self.publication(
            "Use OPENAI_API_KEY=<YOUR_API_KEY>. Examples: "
            "sk-proj-<redacted>, sk-svcacct-<redacted>, sk-..."
        )
        self.assertFalse(result["private_material_detected"])
        self.assertEqual(result["checked_text_files"], 1)


if __name__ == "__main__":
    unittest.main()

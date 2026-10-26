import copy
import hashlib
import json
import unittest
import uuid
from pathlib import Path

from scripts.check_workflow_artifact import CHECKER_ID, SCOPE, verify_artifact


def source():
    return {
        "schema": "fixture-source-v1",
        "source_sha256": "a" * 64,
        "action_tasks": [
            {
                "id": "c",
                "provider": "alpha",
                "acceptance": {"passed": True},
                "outcome": {"utility": None},
                "settlement": {
                    "basis": "reported_external",
                    "total_micro_usd": 1200000,
                },
            },
            {
                "id": "a",
                "provider": "alpha",
                "acceptance": {"passed": False},
                "outcome": {"utility": 0},
                "settlement": {"basis": "included_capacity", "total_micro_usd": 0},
            },
            {
                "id": "b",
                "provider": "beta",
                "acceptance": {"passed": None},
                "outcome": {"utility": -1},
                "settlement": {"basis": "legacy", "total_micro_usd": 300000},
            },
        ],
    }


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def provider_expected():
    return {
        "row_count": 3,
        "semantic_unknown": 3,
        "providers": [
            {
                "provider": "alpha",
                "rows": 2,
                "delivered": 1,
                "delivery_failed": 1,
                "delivery_unknown": 0,
                "utility_positive": 0,
                "utility_nonpositive": 1,
                "utility_unknown": 1,
                "recorded_micro_usd": 1200000,
                "included_capacity": 1,
            },
            {
                "provider": "beta",
                "rows": 1,
                "delivered": 0,
                "delivery_failed": 0,
                "delivery_unknown": 1,
                "utility_positive": 0,
                "utility_nonpositive": 1,
                "utility_unknown": 0,
                "recorded_micro_usd": 300000,
                "included_capacity": 0,
            },
        ],
    }


def payment_expected():
    return {
        "row_count": 3,
        "semantic_unknown": 3,
        "delivery": {"passed": 1, "failed": 1, "unknown": 1},
        "utility": {"positive": 0, "nonpositive": 2, "unknown": 1},
        "settlement": {
            "included_capacity": 1,
            "reported_external": 1,
            "other": 1,
            "total_recorded_micro_usd": 1500000,
            "positive_recorded_amounts": 2,
        },
        "independently_verified_payments": 0,
    }


def audit_expected(maximum=4):
    eligible = ["a", "c"]
    return {
        "row_count": 3,
        "semantic_unknown": 3,
        "policy": {"maximum_tasks": maximum},
        "eligible_ids": eligible,
        "selected_ids": eligible[:maximum],
        "deferred_ids": eligible[maximum:],
        "work_units": len(eligible[:maximum]),
        "execution": "planned_only",
        "usd_spent": 0,
    }


class WorkflowArtifactCheckerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(".test-state") / ("workflow-checker-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o700, parents=True)
        self.projection = source()

    def artifact(self, kind, facts, *, text=None):
        expected_source = fingerprint(self.projection)
        if text is None:
            title = {
                "provider_brief": "Provider brief",
                "payment_brief": "Payment brief",
                "audit_brief": "Audit brief",
            }[kind]
            text = (
                "# "
                + title
                + "\n\n"
                + SCOPE
                + (
                    "\n\nCoverage: " + self.projection["coverage"]
                    if "coverage" in self.projection
                    else ""
                )
                + "\n\nSource: `"
                + expected_source
                + "`\n\n```json\n"
                + json.dumps(facts, sort_keys=True, indent=2)
                + "\n```\n"
            )
        path = self.root / (uuid.uuid4().hex + ".md")
        path.write_text(text)
        return {
            "kind": kind,
            "path": str(path),
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "source_sha256": expected_source,
        }

    def test_three_independently_counted_briefs_preserve_unknowns(self):
        for kind, facts in [
            ("provider_brief", provider_expected()),
            ("payment_brief", payment_expected()),
            ("audit_brief", audit_expected()),
        ]:
            with self.subTest(kind=kind):
                artifact = self.artifact(kind, facts)
                result = verify_artifact(self.projection, artifact, kind)
                self.assertTrue(result["accepted"])
                self.assertEqual(result["checker_id"], CHECKER_ID)
                self.assertEqual(len(result["evidence_sha256"]), 64)
                self.assertEqual(
                    verify_artifact(self.projection, artifact, kind), result
                )

    def test_incorrect_counts_and_false_payment_or_semantic_claims_are_rejected(self):
        variants = []
        wrong = provider_expected()
        wrong["providers"][0]["utility_unknown"] = 2
        variants.append(("provider_brief", wrong))
        wrong = payment_expected()
        wrong["independently_verified_payments"] = 2
        variants.append(("payment_brief", wrong))
        wrong = payment_expected()
        wrong["semantic_unknown"] = 0
        variants.append(("payment_brief", wrong))
        wrong = payment_expected()
        wrong["delivery"]["passed"] = True
        variants.append(("payment_brief", wrong))
        wrong = payment_expected()
        wrong["delivery"]["passed"] = 1.0
        variants.append(("payment_brief", wrong))
        for kind, facts in variants:
            with self.subTest(facts=facts):
                self.assertFalse(
                    verify_artifact(self.projection, self.artifact(kind, facts), kind)[
                        "accepted"
                    ]
                )

    def test_audit_policy_limits_priorities_without_claiming_execution(self):
        artifact = self.artifact("audit_brief", audit_expected(1))
        self.assertTrue(
            verify_artifact(
                self.projection, artifact, "audit_brief", {"maximum_tasks": 1}
            )["accepted"]
        )
        self.assertFalse(
            verify_artifact(self.projection, artifact, "audit_brief")["accepted"]
        )
        for update in (
            {"selected_ids": ["c"], "deferred_ids": ["a"]},
            {"execution": "completed"},
            {"usd_spent": 1},
        ):
            facts = audit_expected(1) | update
            self.assertFalse(
                verify_artifact(
                    self.projection,
                    self.artifact("audit_brief", facts),
                    "audit_brief",
                    {"maximum_tasks": 1},
                )["accepted"]
            )

    def test_failed_delivery_priority_uses_full_ids_not_utility_priority(self):
        self.projection["action_tasks"][0]["id"] = "z"
        self.projection["action_tasks"][0]["acceptance"]["passed"] = False
        self.projection["action_tasks"][2]["outcome"]["utility"] = None
        facts = audit_expected() | {
            "eligible_ids": ["a", "z", "b"],
            "selected_ids": ["a", "z", "b"],
            "work_units": 3,
        }
        self.assertTrue(
            verify_artifact(
                self.projection, self.artifact("audit_brief", facts), "audit_brief"
            )["accepted"]
        )

    def test_source_and_saved_content_hashes_are_both_bound(self):
        artifact = self.artifact("payment_brief", payment_expected())
        self.assertFalse(
            verify_artifact(
                self.projection,
                artifact | {"source_sha256": self.projection["source_sha256"]},
                "payment_brief",
            )["accepted"]
        )
        self.assertFalse(
            verify_artifact(
                self.projection,
                artifact | {"content_sha256": "f" * 64},
                "payment_brief",
            )["accepted"]
        )
        changed = copy.deepcopy(self.projection)
        changed["action_tasks"][0]["settlement"]["total_micro_usd"] += 1
        self.assertFalse(
            verify_artifact(changed, artifact, "payment_brief")["accepted"]
        )

    def test_synthetic_scope_cannot_be_removed_from_standalone_artifact(self):
        self.projection["coverage"] = "Declared synthetic boundary revision."
        artifact = self.artifact("payment_brief", payment_expected())
        self.assertTrue(
            verify_artifact(self.projection, artifact, "payment_brief")["accepted"]
        )
        text = Path(artifact["path"]).read_text()
        altered = self.artifact(
            "payment_brief",
            {},
            text=text.replace(
                "\n\nCoverage: Declared synthetic boundary revision.", ""
            ),
        )
        self.assertFalse(
            verify_artifact(self.projection, altered, "payment_brief")["accepted"]
        )

    def test_document_requires_scope_source_and_one_standalone_json_fact_block(self):
        artifact = self.artifact("payment_brief", payment_expected())
        original = Path(artifact["path"]).read_text()
        for text in (
            original.replace(SCOPE, "Verified payments and semantic acceptance."),
            original.replace("```json", "```"),
            original + "Payments independently verified.\n",
            original + "\n```json\n{}\n```\n",
            original.replace('"row_count": 3', '"row_count": 3, "row_count": 3'),
        ):
            with self.subTest(text=text):
                altered = self.artifact("payment_brief", {}, text=text)
                self.assertFalse(
                    verify_artifact(self.projection, altered, "payment_brief")[
                        "accepted"
                    ]
                )

    def test_relative_artifacts_replay_inside_explicit_root_and_refuse_escape_or_symlinks(
        self,
    ):
        artifact = self.artifact("payment_brief", payment_expected())
        relative = artifact | {"path": Path(artifact["path"]).name}
        self.assertTrue(
            verify_artifact(
                self.projection, relative, "payment_brief", artifact_root=self.root
            )["accepted"]
        )
        self.assertFalse(
            verify_artifact(
                self.projection,
                artifact,
                "payment_brief",
                artifact_root=self.root / "other",
            )["accepted"]
        )
        linked = self.root / "alias.md"
        linked.symlink_to(Path(artifact["path"]).name)
        self.assertFalse(
            verify_artifact(
                self.projection,
                artifact | {"path": linked.name},
                "payment_brief",
                artifact_root=self.root,
            )["accepted"]
        )

    def test_invalid_typed_source_missing_artifacts_and_invalid_policy_are_known_rejections(
        self,
    ):
        artifact = self.artifact("payment_brief", payment_expected())
        variants = []
        for utility in (True, float("nan"), float("inf")):
            altered = copy.deepcopy(self.projection)
            altered["action_tasks"][0]["outcome"]["utility"] = utility
            variants.append(altered)
        altered = copy.deepcopy(self.projection)
        altered["action_tasks"][1]["id"] = "c"
        variants.append(altered)
        for projection in variants:
            self.assertFalse(
                verify_artifact(projection, artifact, "payment_brief")["accepted"]
            )
        self.assertFalse(
            verify_artifact(
                self.projection,
                artifact | {"path": str(self.root / "missing.md")},
                "payment_brief",
            )["accepted"]
        )
        audit = self.artifact("audit_brief", audit_expected())
        for policy in (
            {"maximum_tasks": True},
            {"maximum_tasks": -1},
            {"maximum_tasks": 4, "unregistered": 1},
        ):
            self.assertFalse(
                verify_artifact(self.projection, audit, "audit_brief", policy)[
                    "accepted"
                ]
            )


if __name__ == "__main__":
    unittest.main()

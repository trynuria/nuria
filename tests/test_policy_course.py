"""Prospective task selection and delayed-reference boundaries."""

from __future__ import annotations

import hashlib
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.prepare_policy_course import (
    METHODS,
    load_selected_reference,
    load_selected_task,
    prepare_policy_course,
    spider_metadata,
    verify_freeze,
)


def dump(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def fixture(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    rows = [
        {
            "db_id": f"database_{index % 6}",
            "question": f'Question {index} with {{ braces }} and "quotes"',
            "query": f"SELECT {index}",
            "sql": {"opaque": ["do not decode", {"nested": [1, None, True]}]},
        }
        for index in range(72)
    ]
    dataset = dump(tmp_path / "spider-original.json", rows)
    registry = dump(
        tmp_path / "registry.json",
        {"databases": {f"database_{index}": {} for index in range(6)}},
    )
    prior = dump(
        tmp_path / "prior-selection.json",
        {"selected_task_ids": ["spider-dev-0000", "spider-dev-0001"]},
    )
    options = {
        "dataset": dataset,
        "registry": registry,
        "prior_records": [prior],
        "source_files": [repository / "tests/test_policy_course.py"],
        "output": tmp_path / "frozen",
        "repository": repository,
        "model_identity": "declared-test-model",
    }
    return options


class PolicyCourseTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("policy-course-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)

    def test_metadata_does_not_decode_question_query_or_ast(self):
        raw = b'[{"question":"SECRET QUESTION","query":"SECRET SQL","sql":{"body":"SECRET AST"},"db_id":"database_0"}]'
        loads = json.loads
        decoded = []

        def checked(value, *args, **kwargs):
            encoded = value.encode() if isinstance(value, str) else value
            assert b"SECRET" not in encoded
            decoded.append(encoded)
            return loads(value, *args, **kwargs)

        with patch("scripts.prepare_policy_course.json.loads", checked):
            rows = spider_metadata(raw)
        assert rows[0]["task_id"] == "spider-dev-0000"
        assert rows[0]["db_id"] == "database_0"
        assert len(decoded) == 5

    def test_disjoint_balanced_selection_and_complete_denominators(self):
        tmp_path = self.root
        result = prepare_policy_course(**fixture(tmp_path))
        protocol = json.loads(Path(result["public_protocol"]).read_bytes())
        selected = protocol["selected"]
        assert {phase: len(rows) for phase, rows in selected.items()} == {
            "train": 6,
            "validation": 4,
            "test": 12,
        }
        ids = [row["task_id"] for rows in selected.values() for row in rows]
        assert len(set(ids)) == 22
        assert not {"spider-dev-0000", "spider-dev-0001"} & set(ids)
        assert len({row["db_id"] for row in selected["train"]}) == 6
        assert all(
            sum(row["db_id"] == database for row in selected["test"]) == 2
            for database in protocol["database_order"]
        )
        assert {
            phase: len(jobs) for phase, jobs in protocol["expected_jobs"].items()
        } == {
            "train": 6,
            "validation": 12,
            "test": 36,
        }
        assert protocol["expected_job_count"] == 54
        assert protocol["inference_seed"] == 20261026
        assert protocol["phase_methods"]["validation"] == [
            "schema_first",
            "candidate_1",
            "candidate_2",
        ]
        assert protocol["maximum_planned_completion_tokens"] == 54 * 6000 + 2 * 2048
        assert protocol["limits_per_job"]["max_output_tokens"] == 6000

    def test_public_protocol_has_no_local_paths_or_task_bodies(self):
        tmp_path = self.root
        result = prepare_policy_course(**fixture(tmp_path))
        raw = Path(result["public_protocol"]).read_text()
        assert str(tmp_path) not in raw
        assert str(Path(__file__).resolve().parents[1]) not in raw
        assert "SELECT " not in raw
        assert "Question " not in raw
        assert "opaque" not in raw
        assert (
            hashlib.sha256(raw.encode()).hexdigest() == result["public_protocol_sha256"]
        )

    def test_same_seed_and_inputs_give_same_selection(self):
        tmp_path = self.root
        options = fixture(tmp_path)
        first = prepare_policy_course(**options)
        options["output"] = tmp_path / "second"
        second = prepare_policy_course(**options)
        assert first["selected"] == second["selected"]

    def test_selected_task_exposes_learner_fields_only(self):
        tmp_path = self.root
        result = prepare_policy_course(**fixture(tmp_path))
        task_id = result["selected"]["test"][0]["task_id"]
        task = load_selected_task(
            result["private_protocol"],
            result["private_protocol_sha256"],
            task_id,
            phase="test",
        )
        assert set(task) == {"task_id", "db_id", "question"}
        assert task["task_id"] == task_id
        with self.assertRaisesRegex(ValueError, "not selected"):
            load_selected_task(
                result["private_protocol"],
                result["private_protocol_sha256"],
                "spider-dev-0000",
                phase="test",
            )

    def test_reference_requires_every_method_and_preserves_training_feedback(self):
        tmp_path = self.root
        result = prepare_policy_course(**fixture(tmp_path))
        args = (result["private_protocol"], result["private_protocol_sha256"])
        task_id = result["selected"]["test"][0]["task_id"]
        with self.assertRaisesRegex(ValueError, "terminal record"):
            load_selected_reference(*args, task_id, phase="test")
        jobs = [
            (method, row["task_id"])
            for row in result["selected"]["test"]
            for method in METHODS
        ]
        with self.assertRaisesRegex(ValueError, "terminal record"):
            load_selected_reference(
                *args, task_id, phase="test", terminal_jobs=jobs[:-1]
            )
        reference = load_selected_reference(
            *args, task_id, phase="test", terminal_jobs=jobs
        )
        assert set(reference) == {"task_id", "db_id", "query", "ordered"}
        assert reference["query"].startswith("SELECT ")
        training = result["selected"]["train"][0]["task_id"]
        assert load_selected_reference(*args, training, phase="train")["query"]

    def _changed_input(self, changed):
        tmp_path = self.root
        options = fixture(tmp_path)
        result = prepare_policy_course(**options)
        path = options["prior_records"][0] if changed == "prior" else options[changed]
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            verify_freeze(result["private_protocol"], result["private_protocol_sha256"])

    def test_wrong_protocol_identity_refuses_loading(self):
        tmp_path = self.root
        result = prepare_policy_course(**fixture(tmp_path))
        with self.assertRaisesRegex(ValueError, "freeze identity"):
            verify_freeze(result["private_protocol"], "0" * 64)

    def test_duplicate_metadata_keys_and_trailing_content_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            spider_metadata(b'[{"db_id":"a","db_id":"b"}]')
        with self.assertRaisesRegex(ValueError, "Trailing"):
            spider_metadata(b'[{"db_id":"a"}][]')
        with self.assertRaisesRegex(ValueError, "Unterminated"):
            spider_metadata(b'[{"db_id":"a","question":"broken}]')

    def test_explicit_prior_records_and_six_databases_required(self):
        tmp_path = self.root
        options = fixture(tmp_path)
        options["prior_records"] = []
        with self.assertRaisesRegex(ValueError, "prior-selection"):
            prepare_policy_course(**options)
        options = fixture(tmp_path)
        dump(options["registry"], {"databases": {"database_0": {}}})
        with self.assertRaisesRegex(ValueError, "six reviewed"):
            prepare_policy_course(**options)

    def test_no_overwrite_and_no_duet_input(self):
        tmp_path = self.root
        options = fixture(tmp_path)
        prepare_policy_course(**options)
        with self.assertRaises(FileExistsError):
            prepare_policy_course(**options)
        duet = tmp_path / "reserved-duet"
        duet.mkdir()
        options["dataset"] = dump(duet / "tasks.json", [])
        options["output"] = tmp_path / "never"
        with self.assertRaisesRegex(ValueError, "Duet"):
            prepare_policy_course(**options)
        assert not options["output"].exists()

    def test_changed_dataset(self):
        self._changed_input("dataset")

    def test_changed_registry(self):
        self._changed_input("registry")

    def test_changed_prior(self):
        self._changed_input("prior")


if __name__ == "__main__":
    unittest.main()

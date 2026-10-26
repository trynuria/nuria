"""Protocol fixtures over real SQLite revisions; no inference or neural simulation."""

import copy
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from cognition.analyst_workspace import SCHEMA, grade_result
from cognition.procedural_memory import canonical, digest
from cognition.reasoner_client import ReasonerError

MODULE = Path(__file__).resolve().parents[1] / "cognition/source_series.py"
SPEC = importlib.util.spec_from_file_location("nuria_source_series_fixture", MODULE)
series = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = series
SPEC.loader.exec_module(series)

MODEL = "scripted-protocol-fixture-v1"
ENCODER = digest({"schema": "fixture-encoder-v1"})
CHECKER = "independent-fixture-sum"
CHECKER_SHA = digest({"schema": "fixture-checker-v1"})


class EncoderFixture:
    binding_sha256 = ENCODER


class ReasonerFixture:
    """Only scripted protocol packets; this class performs no model request."""

    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = 0

    def complete(self, messages, *, max_tokens, seed):
        self.calls += 1
        action = next(self.actions)
        if isinstance(action, Exception):
            raise action
        content = json.dumps(action)
        return {
            "content": content,
            "usage": {"prompt_tokens": 10, "completion_tokens": min(5, max_tokens)},
            "request_sha256": digest(messages),
            "response_sha256": digest(content),
            "finish_reason": "stop",
            "wall_seconds": 0.0,
        }


class BridgeFixture:
    """Persistent bridge protocol fixture; it never observes or advances a brain."""

    fail_after_credit = False

    def __init__(self, directory, *, source_id, model_id, tool_version, encoder):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.path = self.directory / "fixture.json"
        self.semantic_terms = {
            "source_id": source_id,
            "model_id": model_id,
            "tool_version": tool_version,
        }
        self.encoder_binding_sha256 = encoder.binding_sha256
        if self.path.exists():
            saved = json.loads(self.path.read_bytes())
            if saved["identity"] != self.semantic_terms:
                raise ValueError("Fixture bridge identity changed")
            self.state = saved["state"]
        else:
            self.state = {"jobs": {}, "advances": 0, "credits": 0}
            self.save()

    def save(self):
        self.path.write_text(
            canonical({"identity": self.semantic_terms, "state": self.state})
        )
        os.chmod(self.path, 0o600)

    def choose(self, job_id, context_sha256, question, features):
        del features
        origin = {"context_sha256": context_sha256, "question": question}
        if job_id in self.state["jobs"]:
            if self.state["jobs"][job_id]["origin"] != origin:
                raise ValueError("Fixture origin changed")
            return copy.deepcopy(self.state["jobs"][job_id]["decision"])
        self.state["advances"] += 1
        decision = {
            "first_tool": "list_tables",
            "utilities": {"list_tables": 0.5, "read_knowledge": 0.4},
            "action_spikes": {"list_tables": 2, "read_knowledge": 1},
            "window_ms": 100,
            "context_sha256": context_sha256,
            "fixture_sequence": self.state["advances"],
        }
        self.state["jobs"][job_id] = {
            "origin": origin,
            "decision": decision,
            "settlement": None,
        }
        self.save()
        return copy.deepcopy(decision)

    def settle(self, job_id, accepted, evidence_sha256):
        row = self.state["jobs"][job_id]
        result = {
            "job_id": job_id,
            "accepted": accepted,
            "evidence_sha256": evidence_sha256,
            "context_sha256": row["origin"]["context_sha256"],
        }
        if row["settlement"] is not None:
            if row["settlement"] != result:
                raise ValueError("Fixture credit changed")
            return copy.deepcopy(result)
        row["settlement"] = result
        self.state["credits"] += 1
        self.save()
        if self.fail_after_credit:
            raise RuntimeError("Fixture interruption after retained credit")
        return copy.deepcopy(result)

    def close(self):
        pass


class SourceSeriesTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1] / ".test-state" / uuid.uuid4().hex
        )
        self.root.mkdir(mode=0o700, parents=True)
        self.a = self.snapshot("a", [1])
        self.b = self.snapshot("b", [1, 2])
        self.contract = series.enrollment(
            self.a,
            source_name="fixture-evolving-ledger",
            model_id=MODEL,
            encoder_binding_sha256=ENCODER,
            checker_id=CHECKER,
            checker_implementation_sha256=CHECKER_SHA,
            maximum_jobs=8,
            maximum_pending=4,
            maximum_completion_tokens=800,
        )
        self.host = series.SourceSeriesHost(self.root / "series", self.contract)
        self.ra = self.host.admit_revision(
            self.a,
            parent_revision_id=None,
            watermark=1,
            evidence_sha256=digest("source-a"),
        )
        self.encoder = EncoderFixture()
        BridgeFixture.fail_after_credit = False

    def tearDown(self):
        if self.host is not None:
            self.host.close()
        BridgeFixture.fail_after_credit = False

    def snapshot(
        self,
        name,
        values,
        *,
        extra_schema=False,
        changed_document=False,
        provenance=None,
        lookalike_schema=False,
    ):
        root = self.root / name
        root.mkdir(mode=0o700)
        path = root / "rows.sqlite"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE entries(id INTEGER PRIMARY KEY,amount INTEGER)"
            )
            connection.executemany(
                "INSERT INTO entries VALUES(?,?)", enumerate(values, 1)
            )
            if extra_schema:
                connection.execute("CREATE INDEX entries_amount ON entries(amount)")
            if lookalike_schema:
                connection.execute("CREATE TABLE sqliteXledger(value INTEGER)")
        docs = {}
        for section in ("schema", "columns", "business"):
            file = root / (section + ".txt")
            file.write_text(
                "Entry amounts are additive integers; missing values are unknown."
                + (" Changed semantics." if changed_document else "")
            )
            docs[section] = {
                "path": file.name,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
        if provenance is not None:
            file = root / "source.txt"
            file.write_text(provenance)
            docs["source"] = {
                "path": file.name,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
        manifest = {
            "schema": SCHEMA,
            "revision": "sha256:"
            + digest(
                {
                    "fixture_values": values,
                    "index": extra_schema,
                    "document": changed_document,
                    "provenance": provenance,
                    "lookalike_schema": lookalike_schema,
                }
            ),
            "databases": {
                "ledger": {
                    "database": {
                        "path": path.name,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    },
                    "documents": docs,
                }
            },
        }
        registry = root / "registry.json"
        registry.write_text(json.dumps(manifest, indent=2) + "\n")
        return registry

    def admit_b(self):
        return self.host.admit_revision(
            self.b,
            parent_revision_id=self.ra["revision_id"],
            watermark=2,
            evidence_sha256=digest("source-b"),
        )

    def reserve(self, job, revision):
        return self.host.reserve(
            job,
            revision["revision_id"],
            "What is the total amount?",
            "ledger",
            completion_limit=100,
        )

    def reasoner(self):
        sql = "SELECT sum(amount) FROM entries"
        return ReasonerFixture(
            [
                {"tool": "query", "arguments": {"sql": sql}},
                {
                    "tool": "final",
                    "arguments": {
                        "sql": sql,
                        "procedure": "Aggregate the current additive amount field after checking its schema.",
                        "preconditions": ["Amounts are additive integers"],
                    },
                },
            ]
        )

    def run_job(self, job, reasoner=None, **kwargs):
        return self.host.run(
            job,
            reasoner=reasoner or self.reasoner(),
            encoder=self.encoder,
            model_id=MODEL,
            bridge_factory=BridgeFixture,
            **kwargs,
        )

    def settle(self, job, revision, expected, *, callback=None, **kwargs):
        def check(request, workspace):
            self.assertEqual(request["revision_id"], revision["revision_id"])
            self.assertEqual(
                workspace.registry_sha256,
                revision["snapshot"]["canonical_registry_sha256"],
            )
            receipt = request["report"]["selected_receipt"]
            return {
                "accepted": grade_result(receipt, [[expected]], expected_columns=1),
                "evidence_sha256": digest(
                    {"expected": expected, "source": receipt["source"]}
                ),
            }

        return self.host.settle(
            job,
            revision_id=revision["revision_id"],
            encoder=self.encoder,
            checker=callback or check,
            checker_id=CHECKER,
            checker_implementation_sha256=CHECKER_SHA,
            bridge_factory=BridgeFixture,
            **kwargs,
        )

    def test_two_real_revisions_keep_history_and_fresh_receipts(self):
        self.reserve("a-job", self.ra)
        a = self.run_job("a-job")
        rb = self.admit_b()
        self.reserve("b-job", rb)
        b = self.run_job("b-job")
        self.assertEqual(a["report"]["selected_receipt"]["rows"], [[1]])
        self.assertEqual(b["report"]["selected_receipt"]["rows"], [[3]])
        self.assertNotEqual(
            a["report"]["source"]["db_sha256"], b["report"]["source"]["db_sha256"]
        )
        self.assertNotEqual(
            a["report"]["context_sha256"], b["report"]["context_sha256"]
        )
        self.assertEqual(
            a["report"]["configuration"]["model_binding"],
            b["report"]["configuration"]["model_binding"],
        )
        self.assertEqual(b["decision"]["fixture_sequence"], 2)
        self.settle("a-job", self.ra, 1)
        self.settle("b-job", rb, 3)
        memory = self.host._memory()
        try:
            self.assertEqual(
                memory.retrieve("total amount", mode="procedure")["item_count"], 2
            )
        finally:
            memory.close()
        source_id = self.host.source_id
        with self.assertRaises(AttributeError):
            self.host.source_id = "a-different-source"
        self.host.close()
        self.host = series.SourceSeriesHost(self.root / "series", self.contract)
        self.assertEqual(self.host.source_id, source_id)
        self.assertEqual(len(self.host.state()["jobs"]), 2)
        self.assertEqual(self.host.allowance()["known_completion_tokens"], 20)

    def test_file_and_canonical_registry_hashes_are_distinct(self):
        snapshot = self.ra["snapshot"]
        self.assertNotEqual(
            snapshot["registry_file_sha256"], snapshot["canonical_registry_sha256"]
        )
        job = self.reserve("job", self.ra)
        self.assertEqual(job["registry_file_sha256"], snapshot["registry_file_sha256"])
        self.assertEqual(
            job["canonical_registry_sha256"], snapshot["canonical_registry_sha256"]
        )

    def test_schema_definition_or_knowledge_changes_are_rejected(self):
        for name, kwargs in (
            ("changed-schema", {"extra_schema": True}),
            ("changed-doc", {"changed_document": True}),
            ("lookalike-schema", {"lookalike_schema": True}),
        ):
            path = self.snapshot(name, [1, 2], **kwargs)
            with self.assertRaisesRegex(ValueError, "Schema or supplied knowledge"):
                self.host.admit_revision(
                    path,
                    parent_revision_id=self.ra["revision_id"],
                    watermark=2,
                    evidence_sha256=digest(name),
                )

    def test_changed_snapshot_bytes_fail_before_inference(self):
        self.reserve("job", self.ra)
        with (
            closing(sqlite3.connect(self.a.parent / "rows.sqlite")) as connection,
            connection,
        ):
            connection.execute("INSERT INTO entries VALUES(2,2)")
        reasoner = self.reasoner()
        with self.assertRaises(ValueError):
            self.run_job("job", reasoner)
        self.assertEqual(reasoner.calls, 0)
        self.assertEqual(self.host.state()["jobs"]["job"]["phase"], "reserved")

    def test_fork_old_revision_and_watermark_rollback_refused(self):
        rb = self.admit_b()
        with self.assertRaises(ValueError):
            self.reserve("stale", self.ra)
        with self.assertRaises(ValueError):
            self.host.admit_revision(
                self.a,
                parent_revision_id=self.ra["revision_id"],
                watermark=3,
                evidence_sha256=digest("fork"),
            )
        with self.assertRaises(ValueError):
            self.host.admit_revision(
                self.a,
                parent_revision_id=rb["revision_id"],
                watermark=1,
                evidence_sha256=digest("rollback"),
            )

    def test_duplicate_jobs_do_not_grant_more_authority(self):
        self.reserve("job", self.ra)
        allowance = self.host.allowance()
        with self.assertRaises(ValueError):
            self.reserve("job", self.ra)
        self.assertEqual(self.host.allowance(), allowance)
        self.run_job("job")
        with self.assertRaises(ValueError):
            self.run_job("job")

    def test_runtime_model_tool_and_encoder_are_checked_before_start(self):
        self.reserve("job", self.ra)
        for kwargs in ({"model_id": "another-model"}, {"tool_version": "another-tool"}):
            with self.assertRaises(ValueError):
                self.host.run(
                    "job",
                    reasoner=self.reasoner(),
                    encoder=self.encoder,
                    bridge_factory=BridgeFixture,
                    **{"model_id": MODEL, **kwargs},
                )
        wrong = EncoderFixture()
        wrong.binding_sha256 = digest("another-encoder")
        with self.assertRaises(ValueError):
            self.host.run(
                "job",
                reasoner=self.reasoner(),
                encoder=wrong,
                model_id=MODEL,
                bridge_factory=BridgeFixture,
            )
        self.assertEqual(self.host.state()["jobs"]["job"]["phase"], "reserved")

    def test_changed_enrollment_refuses_existing_history(self):
        self.host.close()
        self.host = None
        for field, changed in (
            ("model_id", "another-model"),
            ("encoder_binding_sha256", digest("another-encoder")),
            ("tool_version", "another-tool"),
            ("checker_id", "another-checker"),
        ):
            contract = copy.deepcopy(self.contract)
            contract[field] = changed
            with self.assertRaises(ValueError):
                series.SourceSeriesHost(self.root / "series", contract)

    def test_pending_and_generation_caps_survive_reopen(self):
        self.reserve("job", self.ra)
        self.host.close()
        self.host = series.SourceSeriesHost(self.root / "series", self.contract)
        self.assertEqual(self.host.allowance()["reserved_completion_tokens"], 100)
        self.assertEqual(self.host.allowance()["reserved_cpu_encodes"], 1)
        with self.assertRaisesRegex(ValueError, "Completion authority exhausted"):
            self.host.reserve(
                "overflow",
                self.ra["revision_id"],
                "What is total?",
                "ledger",
                completion_limit=701,
            )
        for job in ("pending-two", "pending-three", "pending-four"):
            self.reserve(job, self.ra)
        with self.assertRaisesRegex(ValueError, "Pending outcome bound"):
            self.reserve("pending-five", self.ra)
        contract = {
            **self.contract,
            "maximum_jobs": 1,
            "maximum_pending": 1,
            "maximum_cpu_encodes": 1,
        }
        limited = series.SourceSeriesHost(self.root / "limited", contract)
        try:
            revision = limited.admit_revision(
                self.a,
                parent_revision_id=None,
                watermark=1,
                evidence_sha256=digest("limit"),
            )
            limited.reserve(
                "first",
                revision["revision_id"],
                "What is total?",
                "ledger",
                completion_limit=100,
            )
            with self.assertRaisesRegex(ValueError, "Finite job bound"):
                limited.reserve(
                    "second",
                    revision["revision_id"],
                    "What is total?",
                    "ledger",
                    completion_limit=100,
                )
        finally:
            limited.close()

    def test_uncertain_attempt_blocks_retry_and_new_authority(self):
        self.reserve("job", self.ra)
        # A protocol fixture failure occurs after the durable run_started marker.
        reasoner = ReasonerFixture([ReasonerError("fixture unknown usage")])
        with self.assertRaisesRegex(ValueError, "Inference usage remains ambiguous"):
            self.run_job("job", reasoner)
        self.host.close()
        self.host = series.SourceSeriesHost(self.root / "series", self.contract)
        self.assertEqual(self.host.allowance()["reserved_completion_tokens"], 100)
        with self.assertRaises(ValueError):
            self.run_job("job")
        with self.assertRaises(ValueError):
            self.reserve("new-job", self.ra)

    def test_wrong_revision_or_checker_cannot_credit(self):
        self.reserve("job", self.ra)
        self.run_job("job")
        rb = self.admit_b()
        with self.assertRaises(ValueError):
            self.host.settle("job", revision_id=rb["revision_id"], encoder=self.encoder)
        with self.assertRaises(ValueError):
            self.host.settle(
                "job",
                revision_id=self.ra["revision_id"],
                encoder=self.encoder,
                checker=lambda *_: self.fail("Wrong checker ran"),
                checker_id="wrong",
                checker_implementation_sha256=CHECKER_SHA,
                bridge_factory=BridgeFixture,
            )
        self.assertEqual(self.host.state()["jobs"]["job"]["phase"], "closed")

    def test_recovery_reuses_verdict_and_origin_credit_once(self):
        self.reserve("job", self.ra)
        self.run_job("job")
        BridgeFixture.fail_after_credit = True
        with self.assertRaises(RuntimeError):
            self.settle("job", self.ra, 1)
        self.assertEqual(self.host.state()["jobs"]["job"]["phase"], "checked")
        self.host.close()
        self.host = series.SourceSeriesHost(self.root / "series", self.contract)
        BridgeFixture.fail_after_credit = False
        recovered = self.host.settle(
            "job",
            revision_id=self.ra["revision_id"],
            encoder=self.encoder,
            bridge_factory=BridgeFixture,
        )
        duplicate = self.host.settle(
            "job",
            revision_id=self.ra["revision_id"],
            encoder=self.encoder,
            bridge_factory=BridgeFixture,
        )
        self.assertEqual(recovered, duplicate)
        bridge = BridgeFixture(
            self.root / "series/semantic-lab",
            source_id=self.host.source_id,
            model_id=MODEL,
            tool_version=SCHEMA,
            encoder=self.encoder,
        )
        self.assertEqual(bridge.state["credits"], 1)

    def test_wrong_encoder_cannot_apply_procedural_credit(self):
        self.reserve("job", self.ra)
        packet = self.run_job("job")
        wrong = EncoderFixture()
        wrong.binding_sha256 = digest("wrong-encoder")
        with self.assertRaises(ValueError):
            self.host.settle(
                "job",
                revision_id=self.ra["revision_id"],
                encoder=wrong,
                checker=lambda *_: self.fail("Wrong encoder reached checker"),
                checker_id=CHECKER,
                checker_implementation_sha256=CHECKER_SHA,
                bridge_factory=BridgeFixture,
            )
        memory = self.host._memory()
        try:
            self.assertIsNone(memory.feedback(packet["report"]["episode_id"]))
        finally:
            memory.close()

    def test_abstention_closes_known_usage_but_cannot_be_accepted(self):
        self.reserve("job", self.ra)
        packet = self.run_job(
            "job",
            ReasonerFixture(
                [{"tool": "abstain", "arguments": {"reason": "Fixture abstention"}}]
            ),
        )
        self.assertFalse(packet["answer_complete"])
        self.assertEqual(self.host.allowance()["known_completion_tokens"], 5)
        with self.assertRaises(ValueError):
            self.host.settle(
                "job",
                revision_id=self.ra["revision_id"],
                encoder=self.encoder,
                checker=lambda *_: {
                    "accepted": True,
                    "evidence_sha256": digest("invalid"),
                },
                checker_id=CHECKER,
                checker_implementation_sha256=CHECKER_SHA,
                bridge_factory=BridgeFixture,
            )

    def test_exclusive_writer_and_journal_corruption_refusal(self):
        with self.assertRaises(BlockingIOError):
            series.SourceSeriesHost(self.root / "series", self.contract)
        self.reserve("job", self.ra)
        originals = self.host.db.execute(
            "SELECT seq,previous,hash,payload FROM events ORDER BY seq"
        ).fetchall()
        self.host.db.execute("UPDATE events SET payload='{}' WHERE seq=1")
        with self.assertRaises(ValueError):
            self.host.state()
        self.host.db.execute(
            "UPDATE events SET payload=? WHERE seq=1", (originals[0][3],)
        )
        sequence, previous, _, raw = originals[1]
        event = json.loads(raw)
        event["record"]["completion_limit"] = -1
        event["record"]["context_sha256"] = digest(
            {k: v for k, v in event["record"].items() if k != "context_sha256"}
        )
        raw = canonical(event)
        self.host.db.execute(
            "UPDATE events SET payload=?,hash=? WHERE seq=?",
            (raw, hashlib.sha256((previous + raw).encode()).hexdigest(), sequence),
        )
        with self.assertRaisesRegex(ValueError, "Integer exceeds"):
            self.host.state()

    def test_explicit_provenance_changes_preserve_static_contract(self):
        a = self.snapshot(
            "provenance-a", [1], provenance="Retained response A, recent rows only."
        )
        b = self.snapshot(
            "provenance-b", [1, 2], provenance="Retained response B, recent rows only."
        )
        kwargs = {
            "source_name": "fixture-provenance",
            "model_id": MODEL,
            "encoder_binding_sha256": ENCODER,
            "checker_id": CHECKER,
            "checker_implementation_sha256": CHECKER_SHA,
        }
        contract = series.enrollment(
            a, revision_varying_documents={"ledger": ["source"]}, **kwargs
        )
        with self.assertRaises(ValueError):
            series.enrollment(
                a, revision_varying_documents={"ledger": ["business"]}, **kwargs
            )
        host = series.SourceSeriesHost(self.root / "provenance-series", contract)
        try:
            first = host.admit_revision(
                a,
                parent_revision_id=None,
                watermark=1,
                evidence_sha256=digest("provenance-a"),
            )
            second = host.admit_revision(
                b,
                parent_revision_id=first["revision_id"],
                watermark=2,
                evidence_sha256=digest("provenance-b"),
            )
            self.assertEqual(
                first["snapshot"]["schema_contract_sha256"],
                second["snapshot"]["schema_contract_sha256"],
            )
            self.assertNotEqual(
                first["snapshot"]["databases"]["ledger"]["documents"]["source"][
                    "sha256"
                ],
                second["snapshot"]["databases"]["ledger"]["documents"]["source"][
                    "sha256"
                ],
            )
            receipt = host._workspace(second).call(
                "ledger", "read_knowledge", {"section": "source"}
            )
            self.assertEqual(
                receipt["source"]["registry_sha256"],
                second["snapshot"]["canonical_registry_sha256"],
            )
            self.assertIn("response B", receipt["content"])
            changed = json.loads(b.read_bytes())
            section = b.parent / "unexpected.txt"
            section.write_text("Undeclared new section.")
            changed["databases"]["ledger"]["documents"]["unexpected"] = {
                "path": section.name,
                "sha256": series.file_sha(section),
            }
            extra = b.parent / "extra-registry.json"
            extra.write_text(json.dumps(changed, indent=2) + "\n")
            with self.assertRaisesRegex(ValueError, "Schema or supplied knowledge"):
                host.admit_revision(
                    extra,
                    parent_revision_id=second["revision_id"],
                    watermark=3,
                    evidence_sha256=digest("unexpected"),
                )
        finally:
            host.close()


if __name__ == "__main__":
    unittest.main()

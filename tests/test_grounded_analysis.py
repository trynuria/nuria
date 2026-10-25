"""Offline registered-source analysis delivery and independently delayed credit."""

import copy
import hashlib
import json
import sqlite3
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from test_action_loop import decision

from cognition.agency import NeuralTaskPolicy
from cognition.analyst_workspace import SCHEMA, AnalystWorkspace, grade_result
from cognition.grounded_agent import GroundedAnalyst
from cognition.procedural_memory import ProceduralMemory
from cognition.reasoner_client import digest as model_digest
from commerce.action_loop import ActionLoop
from commerce.grounded_analysis import GroundedAnalysis
from commerce.ledger import Ledger, canonical
from commerce.task_runtime import run_actions
from commerce.tasks import contractor_brief, digest, validate_task


class ScriptedReasoner:
    """Finite replies only: this fixture cannot open a model endpoint."""

    enable_thinking = False

    def __init__(self, actions):
        self.actions = iter(actions)
        self.messages = []

    def complete(self, messages, *, max_tokens, seed):
        self.messages.append(copy.deepcopy(messages))
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        content = action if isinstance(action, str) else json.dumps(action)
        completion = min(5, max_tokens)
        return {
            "content": content,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": completion,
                "total_tokens": 10 + completion,
            },
            "request_sha256": model_digest(messages),
            "response_sha256": model_digest(content),
            "finish_reason": "stop",
            "wall_seconds": 0.0,
        }


class GroundedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("grounded-analysis-" + uuid.uuid4().hex)
        )
        self.root.mkdir(parents=True)
        self.database = self.root / "records.sqlite"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE events(id INTEGER,amount INTEGER)")
            connection.executemany(
                "INSERT INTO events VALUES(?,?)", [(1, None), (2, 1), (3, 1), (4, 1)]
            )
        documents = {}
        for section in ("business", "columns", "schema"):
            path = self.root / (section + ".txt")
            path.write_text("Events have an id and additive amount; NULL is missing.")
            documents[section] = {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        registry = {
            "schema": SCHEMA,
            "revision": "0" * 40,
            "databases": {
                "events": {
                    "database": {
                        "path": self.database.name,
                        "sha256": hashlib.sha256(
                            self.database.read_bytes()
                        ).hexdigest(),
                    },
                    "documents": documents,
                }
            },
        }
        self.manifest = self.root / "registry.json"
        self.manifest.write_text(json.dumps(registry))
        self.workspace = AnalystWorkspace(self.manifest)
        self.ledger = Ledger(self.root / "commerce.sqlite3")
        self.loop = ActionLoop(self.ledger)
        self.memories = []
        self.now = float(int(time.time()))
        self.question = "GA_PRIVATE_QUESTION_61f0: What is the total amount?"
        self.sql = "SELECT sum(amount) FROM events"
        self.money = SimpleNamespace(enabled=False)
        self.checker_calls = []

    def tearDown(self):
        for memory in self.memories:
            memory.close()
        self.ledger.db.close()

    def query_actions(self, sql=None):
        sql = sql or self.sql
        return [
            {"tool": "query", "arguments": {"sql": sql}},
            {
                "tool": "final",
                "arguments": {
                    "sql": sql,
                    "procedure": "Inspect the additive field and aggregate it.",
                    "preconditions": ["The quantity is additive"],
                },
            },
        ]

    def analyst(self, actions, **limits):
        memory = ProceduralMemory(
            self.root / ("memory-" + uuid.uuid4().hex + ".sqlite"),
            source_id="0" * 40,
            model_id="scripted-analysis-fixture",
            tool_version=SCHEMA,
        )
        self.memories.append(memory)
        reasoner = ScriptedReasoner(actions)
        agent = GroundedAnalyst(
            reasoner,
            self.workspace,
            memory,
            memory_mode="procedure",
            max_turns=limits.get("max_turns", 4),
            max_output_tokens=limits.get("max_output_tokens", 32),
            max_tokens_per_turn=limits.get("max_tokens_per_turn", 16),
            max_tool_calls=limits.get("max_tool_calls", 4),
        )
        return agent, reasoner, memory

    def config(self, agent, *, checker_id=None, **changes):
        configuration = agent.configuration
        value = {
            "type": "grounded_analysis",
            "id": "included-analysis-fixture",
            "enabled": True,
            "registry_sha256": self.workspace.registry_sha256,
            "source_id": "0" * 40,
            "model_id": "scripted-analysis-fixture",
            "memory_mode": configuration["memory_mode"],
            "max_turns": configuration["max_turns"],
            "max_output_tokens": configuration["max_output_tokens"],
            "max_tokens_per_turn": configuration["max_tokens_per_turn"],
            "max_tool_calls": configuration["max_tool_calls"],
            "maximum_jobs_per_day": 4,
            "maximum_completion_tokens_per_day": 128,
            "cooldown_seconds": 0,
            "questions": {
                goal: {
                    "question": self.question,
                    "db_id": "events",
                    "checker_id": checker_id,
                }
                for goal in ("sum-events", "sum-events-next")
            },
        }
        value.update(changes)
        return value

    def checker(self, request):
        self.checker_calls.append(copy.deepcopy(request))
        self.assertEqual(
            set(request),
            {
                "job_id",
                "question",
                "db_id",
                "artifact_sha256",
                "selected_receipt",
                "proposal_sha256",
                "source",
            },
        )
        return {
            "accepted": grade_result(
                request["selected_receipt"], [[3]], expected_columns=1
            ),
            "evidence_sha256": digest(
                {
                    "checked_job": request["job_id"],
                    "receipt": request["selected_receipt"],
                }
            ),
            "checker_id": "sum-v1",
        }

    def contract(self, provider, goal="sum-events"):
        contract = provider.contract(
            goal,
            title="Aggregate the registered fixture",
            purpose="Answer a registered-source question with executed evidence",
            action="experiment",
            evaluation_nonce=digest({"goal": goal, "fixture": str(self.root)}),
        )
        validate_task(contract)
        self.assertEqual(contract["kind"], "compute")
        self.assertEqual(contract["maximum_micro_usd"], 0)
        self.assertEqual(contract["acceptance"]["type"], "registered_analysis")
        return contract

    def propose(self, contract):
        policy = NeuralTaskPolicy()
        origin = decision(policy, [contract], self.now)
        ident = self.loop.propose(contract, origin, self.now)
        return ident, policy, origin

    def dispatch(self, provider, contract):
        ident, policy, origin = self.propose(contract)
        row = self.loop.row(ident)
        self.loop.reserve(
            ident,
            provider.quote(row, self.now),
            provider.balance(self.now),
            self.money,
            {"enabled": True, "providers": [provider.id]},
            self.now,
        )
        sent = self.loop.operate(
            ident,
            "dispatch",
            contractor_brief(contract, ident),
            lambda _, operation_id: provider.dispatch(
                self.loop.row(ident), self.now, operation_id
            ),
            self.now,
        )
        return ident, policy, origin, sent

    def receive(self, provider, ident):
        observed = provider.observe(self.loop.row(ident), self.now)
        self.assertIn("delivery", observed)
        self.assertEqual(
            set(json.loads(observed["delivery"])["result"]),
            {
                "artifact_sha256",
                "episode_id",
                "proposal_sha256",
                "execution_receipt_sha256",
            },
        )
        proof = self.loop.receive(
            ident,
            observed["delivery"],
            self.now,
            analysis_resolver=provider.resolve_delivery,
        )
        return observed, proof

    def run_runtime(self, contract, origin, config, agent=None, checkers=None):
        bindings = (
            {}
            if agent is None
            else {config["id"]: {"analyst": agent, "checkers": checkers or {}}}
        )
        return run_actions(
            self.ledger,
            SimpleNamespace(),
            self.money,
            None,
            "",
            {"planning_enabled": True, "tasks": [contract]},
            {"enabled": False, "adapters": [config]},
            {
                "phase": "running",
                "committed_tick": origin["tick"],
                "agency": {"feedback_cursor": 0},
            },
            [origin],
            self.now,
            analysis_bindings=bindings,
        )

    def test_actual_sql_delivery_stays_semantically_unknown_without_checker(self):
        agent, reasoner, memory = self.analyst(self.query_actions())
        provider = GroundedAnalysis(self.ledger, self.config(agent), analyst=agent)
        contract = self.contract(provider)
        ident, _, _, sent = self.dispatch(provider, contract)
        self.assertTrue(sent)
        observed, proof = self.receive(provider, ident)
        self.assertTrue(proof["passed"])
        self.assertIsNone(proof["semantic_accepted"])
        self.assertIsNone(proof["utility"])
        self.assertEqual(self.loop.row(ident)["state"], "delivered")
        self.assertEqual(len(reasoner.messages), 2)
        self.assertIsNone(provider.grade(self.loop.row(ident)))
        self.assertIsNone(self.loop.grade_analysis(ident, provider.grade, self.now))
        self.loop.settle(ident, observed["settlement"], self.now)
        self.assertIsNone(self.loop.evaluate(ident, self.now))
        self.assertEqual(self.loop.feedback(), [])
        self.assertEqual(memory.retrieve("total", mode="procedure")["item_count"], 0)
        self.assertEqual(self.loop.row(ident)["settlement"]["total_micro_usd"], 0)

    def test_authentic_correct_grade_credits_only_the_originating_neural_intent(self):
        agent, reasoner, memory = self.analyst(self.query_actions())
        provider = GroundedAnalysis(
            self.ledger,
            self.config(agent, checker_id="sum-v1"),
            analyst=agent,
            checkers={"sum-v1": self.checker},
        )
        contract = self.contract(provider)
        ident, policy, _, _ = self.dispatch(provider, contract)
        observed, _ = self.receive(provider, ident)
        self.assertEqual(self.checker_calls, [])
        self.assertEqual(self.loop.feedback(), [])
        self.loop.grade_analysis(ident, provider.grade, self.now)
        self.loop.settle(ident, observed["settlement"], self.now)
        outcome = self.loop.evaluate(ident, self.now)
        self.assertGreater(outcome["reward"], 0)
        self.assertTrue(self.loop.row(ident)["acceptance"]["semantic_accepted"])
        self.assertEqual(self.checker_calls[0]["selected_receipt"]["rows"], [[3]])
        self.assertEqual(len(self.checker_calls), 1)
        self.assertEqual(len(reasoner.messages), 2)
        self.assertEqual(memory.retrieve("total", mode="procedure")["item_count"], 1)
        records = self.loop.feedback()
        self.assertEqual(len(records), 1)
        forged = copy.deepcopy(records[0])
        forged["intent_id"] = "f" * 64
        with self.assertRaises(ValueError):
            policy.learn(forged)
        self.assertEqual(policy.completed, 0)
        self.assertTrue(policy.learn(records[0]))
        self.assertFalse(policy.learn(records[0]))
        self.assertEqual(policy.completed, 1)
        self.loop.grade_analysis(ident, provider.grade, self.now)
        self.loop.evaluate(ident, self.now)
        self.assertEqual(len(self.checker_calls), 1)
        self.assertEqual(len(self.loop.feedback()), 1)

    def test_successful_wrong_sql_is_rejected_only_by_the_independent_checker(self):
        wrong_sql = "SELECT max(amount) FROM events"
        agent, _, memory = self.analyst(self.query_actions(wrong_sql))
        provider = GroundedAnalysis(
            self.ledger,
            self.config(agent, checker_id="sum-v1"),
            analyst=agent,
            checkers={"sum-v1": self.checker},
        )
        ident, _, _, _ = self.dispatch(provider, self.contract(provider))
        observed, proof = self.receive(provider, ident)
        self.assertTrue(proof["passed"])
        self.assertIsNone(proof["semantic_accepted"])
        self.loop.grade_analysis(ident, provider.grade, self.now)
        self.loop.settle(ident, observed["settlement"], self.now)
        outcome = self.loop.evaluate(ident, self.now)
        self.assertFalse(self.loop.row(ident)["acceptance"]["semantic_accepted"])
        self.assertLessEqual(outcome["reward"], 0)
        self.assertEqual(self.checker_calls[0]["selected_receipt"]["rows"], [[1]])
        self.assertEqual(memory.retrieve("total", mode="procedure")["item_count"], 0)

    def test_missing_enrolled_checker_remains_pending_without_semantic_credit(self):
        agent, _, memory = self.analyst(self.query_actions())
        provider = GroundedAnalysis(
            self.ledger, self.config(agent, checker_id="sum-v1"), analyst=agent
        )
        ident, _, _, _ = self.dispatch(provider, self.contract(provider))
        observed, _ = self.receive(provider, ident)
        self.assertIsNone(self.loop.grade_analysis(ident, provider.grade, self.now))
        self.loop.settle(ident, observed["settlement"], self.now)
        self.assertIsNone(self.loop.evaluate(ident, self.now))
        self.assertEqual(self.loop.feedback(), [])
        self.assertEqual(memory.retrieve("total", mode="procedure")["item_count"], 0)

    def test_checker_cannot_return_a_different_enrolled_identity(self):
        agent, _, memory = self.analyst(self.query_actions())
        calls = []

        def wrong_identity(request):
            calls.append(request)
            return {
                "accepted": True,
                "checker_id": "another-checker",
                "evidence_sha256": "a" * 64,
            }

        provider = GroundedAnalysis(
            self.ledger,
            self.config(agent, checker_id="sum-v1"),
            analyst=agent,
            checkers={"sum-v1": wrong_identity},
        )
        ident, _, _, _ = self.dispatch(provider, self.contract(provider))
        observed, _ = self.receive(provider, ident)
        self.assertIsNone(self.loop.grade_analysis(ident, provider.grade, self.now))
        self.loop.settle(ident, observed["settlement"], self.now)
        self.assertIsNone(self.loop.evaluate(ident, self.now))
        self.assertIsNone(self.loop.row(ident)["acceptance"]["semantic_accepted"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.loop.feedback(), [])
        self.assertEqual(memory.retrieve("total", mode="procedure")["item_count"], 0)

    def corrupt_rejected_cached_grade(self, *, rehash_memory):
        agent, _, memory = self.analyst(
            self.query_actions("SELECT max(amount) FROM events")
        )
        provider = GroundedAnalysis(
            self.ledger,
            self.config(agent, checker_id="sum-v1"),
            analyst=agent,
            checkers={"sum-v1": self.checker},
        )
        ident, _, _, _ = self.dispatch(provider, self.contract(provider))
        self.receive(provider, ident)
        # Model the recovery window after the provider retained its host grade
        # but before ActionLoop committed that grade to the task acceptance.
        rejected = provider.grade(self.loop.row(ident))
        self.assertFalse(rejected["semantic_accepted"])
        self.assertIsNone(self.loop.row(ident)["acceptance"]["semantic_accepted"])
        seq, raw = memory.db.execute(
            "SELECT seq,payload FROM memory_events WHERE kind='settlement'"
        ).fetchone()
        payload = json.loads(raw)
        receipt = payload["record"]
        receipt.update(accepted=True, terminal_reward=1)
        if rehash_memory:
            receipt["receipt_sha256"] = digest(
                {
                    key: value
                    for key, value in receipt.items()
                    if key != "receipt_sha256"
                }
            )
        memory.db.execute(
            "UPDATE memory_events SET payload=? WHERE seq=?", (canonical(payload), seq)
        )
        if rehash_memory:
            # Even a mutually consistent rewritten memory/cache cannot replace
            # the original, unchanged commerce host-grade journal event.
            parent = "0" * 64
            for event_seq, event_raw in memory.db.execute(
                "SELECT seq,payload FROM memory_events ORDER BY seq"
            ).fetchall():
                head = hashlib.sha256((parent + event_raw).encode()).hexdigest()
                memory.db.execute(
                    "UPDATE memory_events SET previous_hash=?,hash=? WHERE seq=?",
                    (parent, head, event_seq),
                )
                parent = head
            memory.verify()
        forged = {
            **rejected,
            "semantic_accepted": True,
            "utility": 1,
            "checker_receipt_sha256": receipt["receipt_sha256"],
        }
        self.ledger.db.execute(
            "UPDATE grounded_analysis_jobs SET grade=? WHERE job_id=?",
            (canonical(forged), ident),
        )
        with self.assertRaises(ValueError):
            self.loop.grade_analysis(ident, provider.grade, self.now)
        self.assertIsNone(self.loop.row(ident)["acceptance"]["semantic_accepted"])
        self.assertEqual(self.loop.feedback(), [])
        self.assertEqual(len(self.checker_calls), 1)

    def test_corrupt_settlement_and_cached_grade_fail_memory_integrity(self):
        self.corrupt_rejected_cached_grade(rehash_memory=False)

    def test_rehashed_settlement_and_cached_grade_cannot_replace_original_grade(self):
        self.corrupt_rejected_cached_grade(rehash_memory=True)

    def test_cross_job_delivery_cannot_be_rebound_by_rewriting_the_outer_ids(self):
        agent, _, _ = self.analyst(self.query_actions() + self.query_actions())
        provider = GroundedAnalysis(self.ledger, self.config(agent), analyst=agent)
        first_contract = self.contract(provider)
        first, _, _, _ = self.dispatch(provider, first_contract)
        raw = provider.observe(self.loop.row(first), self.now)["delivery"]
        second_contract = self.contract(provider, "sum-events-next")
        second, _, _, _ = self.dispatch(provider, second_contract)
        forged = json.loads(raw)
        forged.update(job_id=second, contract_sha256=digest(second_contract))
        with self.assertRaises(ValueError):
            provider.resolve_delivery(
                second_contract, second, canonical(forged).encode()
            )
        self.assertEqual(self.loop.feedback(), [])

    def test_changed_registered_database_refuses_delivery_proof(self):
        agent, _, _ = self.analyst(self.query_actions())
        provider = GroundedAnalysis(self.ledger, self.config(agent), analyst=agent)
        contract = self.contract(provider)
        ident, _, _, _ = self.dispatch(provider, contract)
        raw = provider.observe(self.loop.row(ident), self.now)["delivery"]
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE events SET amount=99 WHERE id=2")
        with self.assertRaises(ValueError):
            provider.resolve_delivery(contract, ident, raw)
        self.assertEqual(self.loop.feedback(), [])

    def test_unexecuted_final_cannot_establish_analysis_delivery(self):
        actions = [self.query_actions()[1]]
        agent, _, _ = self.analyst(actions, max_turns=1)
        provider = GroundedAnalysis(self.ledger, self.config(agent), analyst=agent)
        contract = self.contract(provider)
        ident, _, _, _ = self.dispatch(provider, contract)
        observed = provider.observe(self.loop.row(ident), self.now)
        if observed.get("delivery"):
            with self.assertRaises(ValueError):
                provider.resolve_delivery(contract, ident, observed["delivery"])
        self.assertIsNone(self.loop.row(ident)["acceptance"])
        self.assertEqual(self.loop.feedback(), [])

    def test_interrupted_inference_keeps_capacity_reserved_and_is_not_retried(self):
        agent, reasoner, _ = self.analyst([RuntimeError("offline interrupted reply")])
        config = self.config(agent, maximum_jobs_per_day=1)
        provider = GroundedAnalysis(self.ledger, config, analyst=agent)
        contract = self.contract(provider)
        ident, _, origin, sent = self.dispatch(provider, contract)
        self.assertFalse(sent)
        self.assertEqual(self.loop.row(ident)["state"], "uncertain")
        self.assertEqual(len(reasoner.messages), 1)
        self.run_runtime(contract, origin, config, agent)
        self.assertEqual(len(reasoner.messages), 1)
        self.assertEqual(self.loop.feedback(), [])
        next_contract = self.contract(provider, "sum-events-next")
        next_ident, _, _ = self.propose(next_contract)
        with self.assertRaises(ValueError):
            provider.quote(self.loop.row(next_ident), self.now)

    def test_public_proofs_exclude_private_question_sql_rows_and_model_trace(self):
        agent, _, _ = self.analyst(self.query_actions())
        provider = GroundedAnalysis(self.ledger, self.config(agent), analyst=agent)
        ident, _, _, _ = self.dispatch(provider, self.contract(provider))
        self.receive(provider, ident)
        public = canonical({"provider": provider.public(), "tasks": self.loop.public()})
        for private in (
            self.question,
            self.sql,
            "selected_receipt",
            "final_sql",
            '"rows"',
            '"trace"',
            str(self.root),
        ):
            self.assertNotIn(private, public)

    def test_unbound_or_disabled_provider_never_runs_or_gains_financial_authority(self):
        agent, reasoner, _ = self.analyst(self.query_actions())
        config = self.config(agent)
        unbound = GroundedAnalysis(self.ledger, config)
        self.assertFalse(unbound.enabled)
        disabled = GroundedAnalysis(
            self.ledger, {**config, "enabled": False}, analyst=agent
        )
        self.assertFalse(disabled.enabled)
        provider = GroundedAnalysis(self.ledger, config, analyst=agent)
        contract = self.contract(provider)
        origin = decision(NeuralTaskPolicy(), [contract], self.now)
        result = self.run_runtime(contract, origin, config)
        self.assertFalse(result["included_compute_enabled"])
        self.assertFalse(result["execution_enabled"])
        self.assertEqual(reasoner.messages, [])
        self.assertEqual(result["feedback"], [])
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM authorizations").fetchone()[0],
            0,
        )

    def test_enrolled_caps_must_match_the_actual_analyst_configuration(self):
        agent, _, _ = self.analyst(self.query_actions())
        for field in (
            "max_turns",
            "max_output_tokens",
            "max_tokens_per_turn",
            "max_tool_calls",
        ):
            config = self.config(agent)
            config[field] += 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                GroundedAnalysis(self.ledger, config, analyst=agent)

    def test_daily_completion_reservation_and_cooldown_are_enforced(self):
        agent, _, _ = self.analyst(self.query_actions())
        config = self.config(
            agent, maximum_completion_tokens_per_day=32, cooldown_seconds=60
        )
        provider = GroundedAnalysis(self.ledger, config, analyst=agent)
        ident, _, _, _ = self.dispatch(provider, self.contract(provider))
        self.assertEqual(self.loop.row(ident)["amount"], 0)
        next_contract = self.contract(provider, "sum-events-next")
        next_ident, _, _ = self.propose(next_contract)
        with self.assertRaises(ValueError):
            provider.quote(self.loop.row(next_ident), self.now + 1)
        with self.assertRaises(ValueError):
            provider.quote(self.loop.row(next_ident), self.now + 61)

    def test_runtime_uses_only_explicit_bound_analysis_and_authentic_host_grade(self):
        agent, reasoner, _ = self.analyst(self.query_actions())
        config = self.config(agent, checker_id="sum-v1")
        provider = GroundedAnalysis(
            self.ledger, config, analyst=agent, checkers={"sum-v1": self.checker}
        )
        contract = self.contract(provider)
        policy = NeuralTaskPolicy()
        origin = decision(policy, [contract], self.now)
        result = self.run_runtime(
            contract, origin, config, agent, {"sum-v1": self.checker}
        )
        self.assertTrue(result["included_compute_enabled"])
        self.assertFalse(result["execution_enabled"])
        self.assertEqual(len(reasoner.messages), 2)
        self.assertEqual(len(self.checker_calls), 1)
        self.assertEqual(len(result["feedback"]), 1)
        self.assertTrue(policy.learn(result["feedback"][0]))
        self.assertEqual(policy.completed, 1)
        self.assertEqual(result["tasks"][0]["state"], "evaluated")


if __name__ == "__main__":
    unittest.main()

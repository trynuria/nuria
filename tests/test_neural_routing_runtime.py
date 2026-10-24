"""Real metered text profiles, exact buyer evidence and joint policy recovery.

The fixtures stand in for a newly enrolled ledger producer. They deliberately
do not claim that the historical validator or deployed worker accepts this new
provider. Search and held-out execution use the actual reviewed interpreters.
"""

import copy
import hashlib
import json
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cognition.routed_text_search import RoutedTextLibrary
from cognition.routing_features import encode
from cognition.text_skills import DEFAULT_TEXT_CONFIG, execute_checked
from commerce.active_skill_runtime import ActiveSkillRuntime
from commerce.ledger import Ledger, canonical
from commerce.neural_routing_runtime import POLICY, RoutingDecision
from commerce.tasks import (
    active_probe_receipt,
    active_probe_work,
    digest,
    transform_work,
)

SOURCE = "nuria:neural-routing-runtime-test"
PROVIDER = "local-neural-routing-test"
OBSERVED = []


def encoder(audits, library_state, config, neural_features64, profile_caps):
    OBSERVED.append(
        copy.deepcopy((audits, library_state, config, neural_features64, profile_caps))
    )
    values = [min(1, len(audits) / 4), min(1, len(audits[0]["output"]) / 128)]
    return values + [0.0] * 62 + list(neural_features64)


class NeuralRoutingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(".test-state") / ("neural-routing-" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.ledger = Ledger(self.directory / "commerce.sqlite3")
        self.addCleanup(self.ledger.db.close)
        ActiveSkillRuntime(SimpleNamespace(ledger=self.ledger))
        self.ledger.db.executescript("""
          CREATE TABLE local_skill_state(provider TEXT PRIMARY KEY,source_id TEXT NOT NULL,config_sha256 TEXT NOT NULL,state TEXT NOT NULL);
          CREATE TABLE local_skill_jobs(job_id TEXT PRIMARY KEY,provider TEXT NOT NULL,proposal TEXT,delivery TEXT);
        """)
        self.config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        self.config.update(max_rows=16, max_input_bytes=128, max_ops=12)
        self.library = RoutedTextLibrary(SOURCE, self.config)
        self.ledger.db.execute(
            "INSERT INTO local_skill_state VALUES(?,?,?,?)",
            (PROVIDER, SOURCE, digest(self.config), canonical(self.library.dump())),
        )
        self.options = {
            "source_id": SOURCE,
            "provider_id": PROVIDER,
            "feature_dimension": 128,
            "feature_encoder": encoder,
            "profiles": {"default": 50000, "extract_first": 50000},
            "solve_mode": "fresh",
            "policy_configuration": {
                "width": 8,
                "ticks": 4,
                "updates": 1,
                "warmup": 0,
                "exploration": 0,
            },
        }
        self.runtime = RoutingDecision(self.ledger, **self.options)

    def job(self, name="batch-one", *, audits=4, neural=0.2):
        inputs = [canonical(["  Item" + str(i) + "  ", "other"]) for i in range(audits)]
        outputs = ["ITEM" + str(i) for i in range(audits)]
        final = [canonical(["  Item" + str(i) + "  ", "other"]) for i in (11, 12)]
        task = {
            "id": name,
            "title": "Normalize records",
            "purpose": "Produce checked canonical records",
            "brief": "Use only disclosed diagnostic examples",
            "kind": "compute",
            "provider": PROVIDER,
            "action": "experiment",
            "maximum_micro_usd": 0,
            "delivery_seconds": 3600,
            "evaluation_nonce": digest({"nonce_fixture": name}),
            "acceptance": {
                "type": "transform_active",
                "policy": POLICY,
                "source_id": SOURCE,
                "config": copy.deepcopy(self.config),
                "diagnostic_inputs": inputs,
                "diagnostic_outputs": outputs,
                "heldout_inputs": final,
                "heldout_outputs": ["ITEM11", "ITEM12"],
                "maximum_work": 1_050_000,
                "maximum_probes": audits,
                "minimum_evidence": audits,
                "synthesis_work": 50000,
            },
        }
        intent = {
            "schema": "neural-task-policy-v1",
            "goal_id": name,
            "action": "experiment",
            "provider": PROVIDER,
            "contract_sha256": digest(task),
            "neural_state_sha256": "b" * 64,
            "neural_features64": [neural] * 64,
        }
        intent["id"] = digest(intent)
        ident = digest({"intent_id": intent["id"], "contract": digest(task)})
        self.ledger.db.execute(
            "INSERT INTO action_tasks(id,intent_id,goal_id,contract,intent,provider,state,created,updated) VALUES(?,?,?,?,?,?,'submitted',0,0)",
            (ident, intent["id"], name, canonical(task), canonical(intent), PROVIDER),
        )
        self.runtime.loop.event(
            ident,
            "proposed",
            {
                "intent_id": intent["id"],
                "contract_sha256": digest(task),
                "neural_state_sha256": intent["neural_state_sha256"],
                "origin_decision_hash": "e" * 64,
            },
            0,
        )
        session = digest({"session": ident})
        for index in range(audits):
            request = {
                "session_id": session,
                "sequence": index + 1,
                "kind": "probe",
                "input_index": index,
                "input": inputs[index],
                "input_sha256": digest(inputs[index]),
                "maximum_output_bytes": self.config["max_input_bytes"],
                "maximum_work": 1
                + (len(inputs[index].encode()) + self.config["max_input_bytes"] + 31)
                // 32,
            }
            request["id"] = digest(request)
            response = {
                **active_probe_receipt(task, ident, request),
                "source_id": SOURCE,
            }
            self.operation(ident, index, "probe", request, response)
        return ident

    def operation(self, job_id, ordinal, kind, request, response):
        work = response["work"] if kind == "probe" else transform_work(response["work"])
        self.ledger.event(
            {
                "active_action_id": request["id"],
                "active_phase": "operation_reserved",
                "request_sha256": digest(request),
                "maximum_work": request["maximum_work"],
            }
        )
        self.ledger.db.execute(
            "INSERT INTO active_skill_actions VALUES(?,?,?,?,?,?,'completed',?,?,?)",
            (
                request["id"],
                job_id,
                ordinal,
                kind,
                canonical(request),
                digest(request),
                request["maximum_work"],
                canonical(response),
                work,
            ),
        )
        self.ledger.event(
            {
                "active_action_id": request["id"],
                "active_phase": "operation_completed",
                "response_sha256": digest(response),
                "work": work,
            }
        )

    def buyer(self, job_id, decision, *, profile=None, execution=None):
        row = self.runtime.loop.row(job_id)
        task, rule = row["contract"], row["contract"]["acceptance"]
        profile = decision["choice"]["arm"] if profile is None else profile
        execution = copy.deepcopy(
            decision["execution"] if execution is None else execution
        )
        library = RoutedTextLibrary(
            SOURCE,
            execution["config"],
            state=execution["library_before"],
            profile=profile,
            search_mode=execution["search_mode"],
        )
        # Reserve first, execute the real selected profile, then commit its result.
        request = {
            "session_id": digest({"session": job_id}),
            "sequence": rule["maximum_probes"] + 1,
            "kind": "synthesize",
            "maximum_work": decision["profile_maximum_work"],
        }
        request["id"] = digest(request)
        self.ledger.event(
            {
                "active_action_id": request["id"],
                "active_phase": "operation_reserved",
                "request_sha256": digest(request),
                "maximum_work": request["maximum_work"],
            }
        )
        proposal = library.solve(
            execution["inputs"],
            execution["outputs"],
            mode=execution["mode"],
            budget=request["maximum_work"],
        )
        work = transform_work(proposal["work"])
        self.ledger.db.execute(
            "INSERT INTO active_skill_actions VALUES(?,?,?,?,?,?,'completed',?,?,?)",
            (
                request["id"],
                job_id,
                10,
                "synthesize",
                canonical(request),
                digest(request),
                request["maximum_work"],
                canonical(proposal),
                work,
            ),
        )
        self.ledger.event(
            {
                "active_action_id": request["id"],
                "active_phase": "operation_completed",
                "response_sha256": digest(proposal),
                "work": work,
            }
        )
        delivery = {
            "schema": "nuria.task-result.v1",
            "job_id": job_id,
            "contract_sha256": digest(task),
            "result": {
                "program": proposal["program"],
                "program_sha256": proposal["program_sha256"],
            },
        }
        raw = canonical(delivery)
        self.ledger.db.execute(
            "INSERT INTO local_skill_jobs VALUES(?,?,?,?)",
            (job_id, PROVIDER, canonical(proposal), raw),
        )
        predicted, verifier_work = [], 0
        if proposal["program"] is not None:
            for value in rule["heldout_inputs"]:
                checked = execute_checked(proposal["program"], value, self.config)
                predicted.append(checked["output"])
                verifier_work += transform_work(checked["work"])
        verifier_work += sum(
            active_probe_work(value, expected)
            for value, expected in zip(
                rule["heldout_inputs"], rule["heldout_outputs"], strict=True
            )
        )
        verifier_work += sum(
            active_probe_work(value or "", expected)
            for value, expected in zip(predicted, rule["heldout_outputs"], strict=False)
        )
        correct = sum(
            actual == expected
            for actual, expected in zip(
                predicted, rule["heldout_outputs"], strict=False
            )
        )
        audits = [
            json.loads(row[0])
            for row in self.ledger.db.execute(
                "SELECT response FROM active_skill_actions WHERE job_id=? AND kind='probe' ORDER BY ordinal",
                (job_id,),
            )
        ]
        probe_work = sum(receipt["work"] for receipt in audits)
        acceptance = {
            "passed": proposal["program"] is not None
            and correct == len(rule["heldout_inputs"]),
            "source_id": SOURCE,
            "program_sha256": proposal["program_sha256"],
            "artifact_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "maximum_work": rule["maximum_work"],
            "total_work": work + probe_work + verifier_work,
            "solver_work": work + probe_work,
            "probe_work": probe_work,
            "verifier_work": verifier_work,
            "probe_receipt_sha256s": [digest(receipt) for receipt in audits],
            "exact_rows": correct,
            "evaluation_rows": len(rule["heldout_inputs"]),
        }
        self.ledger.db.execute(
            "UPDATE action_tasks SET acceptance=?,state=? WHERE id=?",
            (
                canonical(acceptance),
                "accepted" if acceptance["passed"] else "rejected",
                job_id,
            ),
        )
        self.runtime.loop.event(
            job_id, "accepted" if acceptance["passed"] else "rejected", acceptance, 1
        )
        return acceptance

    def test_actual_profile_and_buyer_work_train_selected_synapses(self):
        job = self.job()
        before = self.runtime.policy.model_hash()
        decision = self.runtime.decide(job)
        self.assertEqual(self.runtime.policy.model_hash(), before)
        self.assertIn(decision["choice"]["arm"], self.options["profiles"])
        self.assertTrue(decision["choice"]["spike_counts"])
        acceptance = self.buyer(job, decision)
        feedback = self.runtime.complete(job)
        expected = (
            int(acceptance["passed"])
            - 0.1 * acceptance["total_work"] / acceptance["maximum_work"]
        )
        self.assertEqual(feedback["receipt"]["reward"], expected)
        self.assertNotEqual(self.runtime.policy.model_hash(), before)
        self.assertEqual(
            feedback["policy_result"]["origin_features_sha256"],
            decision["origin"]["feature_sha256"],
        )
        self.assertEqual(feedback["receipt"]["arm"], decision["choice"]["arm"])
        self.assertEqual(feedback["financial_transactions"], 0)

    def test_encoder_receives_disclosed_observations_not_final_rows_or_task_identity(
        self,
    ):
        job = self.job()
        self.runtime.decide(job)
        audits, library, config, neural, profiles = OBSERVED[-1]
        self.assertEqual(len(audits), 4)
        self.assertTrue(
            all(set(item) == {"input", "output", "receipt_sha256"} for item in audits)
        )
        self.assertEqual(neural, [0.2] * 64)
        self.assertEqual(profiles, self.options["profiles"])
        self.assertEqual(config, self.config)
        self.assertEqual(library["source_id"], SOURCE)
        self.assertNotIn(
            "ITEM11", canonical((audits, library, config, neural, profiles))
        )

    def test_declared_one_through_four_audits_are_real_and_never_fabricated(self):
        for count in range(1, 5):
            job = self.job(f"count-{count}", audits=count)
            decision = self.runtime.decide(job)
            self.assertEqual(len(OBSERVED[-1][0]), count)
            completed = self.ledger.db.execute(
                "SELECT max(e.seq),count(*) FROM events e JOIN active_skill_actions a "
                "ON a.id=json_extract(e.payload,'$.active_action_id') "
                "WHERE a.job_id=? AND a.kind='probe' "
                "AND json_extract(e.payload,'$.active_phase')='operation_completed'",
                (job,),
            ).fetchone()
            self.assertEqual(completed[1], count)
            self.assertEqual(
                decision["origin"]["last_audit_sequence"],
                completed[0],
            )

    def test_reviewed_encoder_consumes_actual_receipts_and_raw_neural_context(self):
        provider, source = self.options["provider_id"], self.options["source_id"]
        # A new encoder is a new authority; use a separate database rather than
        # change an existing policy's pinned implementation.
        other = Ledger(self.directory / "reviewed-encoder.sqlite3")
        self.addCleanup(other.db.close)
        other.db.executescript("""
          CREATE TABLE local_skill_state(provider TEXT PRIMARY KEY,source_id TEXT NOT NULL,config_sha256 TEXT NOT NULL,state TEXT NOT NULL);
          CREATE TABLE local_skill_jobs(job_id TEXT PRIMARY KEY,provider TEXT NOT NULL,proposal TEXT,delivery TEXT);
        """)
        ActiveSkillRuntime(SimpleNamespace(ledger=other))
        other.db.execute(
            "INSERT INTO local_skill_state VALUES(?,?,?,?)",
            (provider, source, digest(self.config), canonical(self.library.dump())),
        )
        self.ledger = other
        self.runtime = RoutingDecision(
            other, **{**self.options, "feature_encoder": encode}
        )
        job = self.job(audits=2, neural=0.4)
        decision = self.runtime.decide(job)
        pending = self.runtime.policy.pending[decision["choice"]["decision_id"]]
        self.assertEqual(len(pending["features"]), 128)
        self.assertEqual(pending["features"][64:], [0.4] * 64)
        self.assertEqual(pending["features"][21:64], [0.0] * 43)
        self.buyer(job, decision)
        self.assertIsNotNone(self.runtime.complete(job))

    def test_two_stale_instances_preserve_each_others_choices_and_feedback(self):
        stale = RoutingDecision(self.ledger, **self.options)
        first = self.job("interleaved-first")
        second = self.job("interleaved-second")
        first_choice = self.runtime.decide(first)
        second_choice = stale.decide(second)
        self.assertEqual(second_choice["choice"]["ordinal"], 2)
        self.assertIn(first_choice["choice"]["decision_id"], stale.policy.pending)
        self.buyer(first, first_choice)
        self.buyer(second, second_choice)
        first_feedback = self.runtime.complete(first)
        second_feedback = stale.complete(second)
        self.assertEqual(first_feedback["receipt"]["seq"], 1)
        self.assertEqual(second_feedback["receipt"]["seq"], 2)
        self.assertFalse(stale.policy.pending)
        self.assertEqual(self.runtime.complete(second), second_feedback)
        self.assertEqual(self.runtime.policy.state(), stale.policy.state())

    def test_legacy_pooled_context_is_not_silently_upsampled_to_raw_neurons(self):
        job = self.job()
        row = self.runtime.loop.row(job)
        intent = row["intent"]
        intent.pop("neural_features64")
        intent["neural_context"] = [0.2] * 17
        intent["id"] = digest(
            {key: value for key, value in intent.items() if key != "id"}
        )
        self.ledger.db.execute(
            "UPDATE action_tasks SET intent=?,intent_id=? WHERE id=?",
            (canonical(intent), intent["id"], job),
        )
        with self.assertRaises(ValueError):
            self.runtime.decide(job)

    def test_exact_decision_and_feedback_recovery_do_not_repeat_updates(self):
        job = self.job()
        decision = self.runtime.decide(job)
        restored = RoutingDecision(self.ledger, **self.options)
        self.assertEqual(restored.decide(job), decision)
        self.buyer(job, decision)
        first = restored.complete(job)
        state = restored.policy.state()
        again = RoutingDecision(self.ledger, **self.options)
        self.assertEqual(again.complete(job), first)
        self.assertEqual(again.decide(job), decision)
        self.assertEqual(again.policy.state(), state)

    def test_delayed_feedback_credits_the_first_job_not_newest_context(self):
        first = self.job("first", neural=0.1)
        origin = self.runtime.decide(first)
        second = self.runtime.decide(self.job("second", neural=0.9))
        self.assertNotEqual(
            origin["origin"]["feature_sha256"], second["origin"]["feature_sha256"]
        )
        self.buyer(first, origin)
        feedback = self.runtime.complete(first)
        self.assertEqual(
            feedback["policy_result"]["origin_features_sha256"],
            origin["origin"]["feature_sha256"],
        )
        self.assertIn(second["choice"]["decision_id"], self.runtime.policy.pending)

    def test_caller_success_flag_or_uncommitted_acceptance_cannot_train(self):
        job = self.job()
        self.runtime.decide(job)
        before = self.runtime.policy.state()
        self.assertIsNone(self.runtime.complete(job))
        with self.assertRaises(TypeError):
            self.runtime.complete(job, accepted=True)
        self.ledger.db.execute(
            "UPDATE action_tasks SET acceptance=? WHERE id=?",
            (canonical({"passed": True}), job),
        )
        with self.assertRaises(ValueError):
            self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), before)

    def test_actual_execution_of_another_profile_does_not_credit_the_chosen_one(self):
        job = self.job()
        decision = self.runtime.decide(job)
        wrong = next(
            name
            for name in self.options["profiles"]
            if name != decision["choice"]["arm"]
        )
        self.buyer(job, decision, profile=wrong)
        before = self.runtime.policy.state()
        with self.assertRaisesRegex(ValueError, "profile execution"):
            self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), before)

    def test_actual_execution_configuration_cannot_replace_the_committed_config(self):
        job = self.job()
        decision = self.runtime.decide(job)
        execution = copy.deepcopy(decision["execution"])
        execution["config"]["max_ops"] -= 1
        execution["library_before"] = RoutedTextLibrary(
            SOURCE, execution["config"]
        ).dump()
        self.buyer(job, decision, execution=execution)
        before = self.runtime.policy.state()
        with self.assertRaisesRegex(ValueError, "executed configuration"):
            self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), before)

    def test_actual_solve_mode_cannot_change_after_the_neural_choice(self):
        job = self.job()
        decision = self.runtime.decide(job)
        execution = copy.deepcopy(decision["execution"])
        execution["mode"] = "retained_factored"
        self.buyer(job, decision, execution=execution)
        before = self.runtime.policy.state()
        with self.assertRaisesRegex(ValueError, "executed solve mode"):
            self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), before)

    def test_actual_search_mode_cannot_change_after_the_neural_choice(self):
        job = self.job()
        decision = self.runtime.decide(job)
        execution = copy.deepcopy(decision["execution"])
        execution["search_mode"] = "enumerative"
        execution["library_before"] = RoutedTextLibrary(
            SOURCE, self.config, search_mode="enumerative"
        ).dump()
        self.buyer(job, decision, execution=execution)
        before = self.runtime.policy.state()
        with self.assertRaisesRegex(ValueError, "executed search mode"):
            self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), before)

    def test_tool_must_execute_the_actual_frozen_origin_library_snapshot(self):
        job = self.job()
        decision = self.runtime.decide(job)
        execution = copy.deepcopy(decision["execution"])
        different = RoutedTextLibrary(
            SOURCE, self.config, state=execution["library_before"]
        )
        different.solve([canonical(["other"])], ["OTHER"], mode="fresh", budget=50000)
        execution["library_before"] = different.dump()
        self.buyer(job, decision, execution=execution)
        before = self.runtime.policy.state()
        with self.assertRaisesRegex(ValueError, "executed library snapshot"):
            self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), before)

    def test_tool_cannot_substitute_unpaid_labels_for_the_recorded_audits(self):
        job = self.job()
        decision = self.runtime.decide(job)
        execution = copy.deepcopy(decision["execution"])
        execution["outputs"] = ["UNRELATED"] * len(execution["outputs"])
        self.buyer(job, decision, execution=execution)
        before = self.runtime.policy.state()
        with self.assertRaisesRegex(ValueError, "executed diagnostic examples"):
            self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), before)

    def test_feature_encoder_and_caps_are_pinned_with_the_execution_context(self):
        job = self.job()
        decision = self.runtime.decide(job)
        self.assertEqual(
            decision["origin"]["profile_caps_sha256"], digest(self.options["profiles"])
        )
        self.assertEqual(
            decision["origin"]["feature_encoder_sha256"],
            self.runtime.terms["feature_encoder_sha256"],
        )
        self.assertEqual(
            digest(decision["execution"]["library_before"]),
            decision["origin"]["library_before_sha256"],
        )
        with self.assertRaisesRegex(ValueError, "authority"):
            RoutingDecision(
                self.ledger,
                **{
                    **self.options,
                    "profiles": {"default": 40000, "extract_first": 50000},
                },
            )
        before = self.runtime.policy.state()
        self.runtime.feature_encoder = encode
        with self.assertRaisesRegex(ValueError, "implementation"):
            self.runtime.decide(job)
        self.assertEqual(self.runtime.policy.state(), before)

    def test_feature_encoder_cannot_capture_uncommitted_callable_state(self):
        offset = 0.1

        def captured(audits, library, config, neural, profiles):
            return [offset] * 128

        with self.assertRaisesRegex(ValueError, "reviewed source feature encoder"):
            RoutingDecision(
                self.ledger, **{**self.options, "feature_encoder": captured}
            )

    def test_choice_commit_failure_restores_pending_rng_and_next_actual_choice(self):
        job = self.job()
        before = self.runtime.policy.state()
        with patch.object(
            self.runtime, "_commit", side_effect=RuntimeError("injected")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.runtime.decide(job)
        self.assertEqual(self.runtime.policy.state(), before)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT COUNT(*) FROM neural_routing_choices"
            ).fetchone()[0],
            0,
        )
        decision = self.runtime.decide(job)
        self.assertEqual(decision["choice"]["ordinal"], 1)

    def test_feedback_commit_failure_restores_model_optimizer_and_uncredited_origin(
        self,
    ):
        job = self.job()
        decision = self.runtime.decide(job)
        self.buyer(job, decision)
        before = self.runtime.policy.state()
        with patch.object(
            self.runtime, "_commit", side_effect=RuntimeError("injected")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), before)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT phase FROM neural_routing_choices"
            ).fetchone()[0],
            "selected",
        )
        self.assertIsNotNone(self.runtime.complete(job))

    def test_postcommit_reply_failure_recovers_the_durable_single_update(self):
        job = self.job()
        decision = self.runtime.decide(job)
        self.buyer(job, decision)
        commit = self.runtime._commit

        def interrupted_reply():
            commit()
            raise RuntimeError("after commit")

        with patch.object(self.runtime, "_commit", side_effect=interrupted_reply):
            with self.assertRaisesRegex(RuntimeError, "after commit"):
                self.runtime.complete(job)
        first = self.runtime.policy.state()
        result = self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), first)
        self.assertEqual(result["receipt"]["seq"], 1)

    def test_source_configuration_or_buyer_mutation_is_refused(self):
        with self.assertRaises(ValueError):
            RoutingDecision(
                self.ledger, **{**self.options, "source_id": "nuria:another-source"}
            )
        with self.assertRaises(ValueError):
            RoutingDecision(self.ledger, **{**self.options, "profiles": {}})
        job = self.job()
        decision = self.runtime.decide(job)
        acceptance = self.buyer(job, decision)
        self.runtime.complete(job)
        acceptance["total_work"] += 1
        self.ledger.db.execute(
            "UPDATE action_tasks SET acceptance=? WHERE id=?",
            (canonical(acceptance), job),
        )
        with self.assertRaisesRegex(ValueError, "committed evidence"):
            self.runtime.complete(job)

    def test_feedback_table_cannot_change_the_journalled_training_receipt(self):
        job = self.job()
        decision = self.runtime.decide(job)
        self.buyer(job, decision)
        feedback = self.runtime.complete(job)
        before = self.runtime.policy.state()
        feedback["receipt"]["reward"] = 0.5
        self.ledger.db.execute(
            "UPDATE neural_routing_choices SET feedback=? WHERE job_id=?",
            (canonical(feedback), job),
        )
        with self.assertRaisesRegex(ValueError, "retained journal"):
            self.runtime.complete(job)
        self.assertEqual(self.runtime.policy.state(), before)

    def test_actual_delivery_program_must_be_the_selected_tool_result(self):
        job = self.job()
        decision = self.runtime.decide(job)
        acceptance = self.buyer(job, decision)
        delivery = json.loads(
            self.ledger.db.execute(
                "SELECT delivery FROM local_skill_jobs WHERE job_id=?", (job,)
            ).fetchone()[0]
        )
        delivery["result"]["program"] = {
            "version": "safe-text-v1",
            "expr": {"op": "constant", "value": "wrong"},
        }
        raw = canonical(delivery)
        self.ledger.db.execute(
            "UPDATE local_skill_jobs SET delivery=? WHERE job_id=?", (raw, job)
        )
        # A replacement body cannot retain the buyer's earlier artifact proof.
        self.assertNotEqual(
            hashlib.sha256(raw.encode()).hexdigest(), acceptance["artifact_sha256"]
        )
        with self.assertRaisesRegex(ValueError, "complete work"):
            self.runtime.complete(job)


if __name__ == "__main__":
    unittest.main()

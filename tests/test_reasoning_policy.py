import copy
import json
import unittest

from cognition.reasoner_client import ReasonerError, digest
from cognition.reasoning_policy import (
    BASELINE,
    PolicyReasoner,
    ReasoningPolicyStudy,
    policy_identity,
    text_sha256,
)


class RecordingReasoner:
    model = "test-reasoner"
    enable_thinking = True

    def __init__(self, suffix="Inspect join multiplicity before aggregation."):
        self.calls = []
        self.suffix = suffix

    def complete(self, messages, *, max_tokens=768, seed=0):
        self.calls.append((copy.deepcopy(messages), max_tokens, seed))
        content = json.dumps({"suffix": self.suffix})
        return {
            "content": content,
            "usage": {
                "prompt_tokens": 30,
                "completion_tokens": 20,
                "total_tokens": 50,
            },
            "request_sha256": digest(self.calls[-1]),
            "response_sha256": text_sha256(content),
        }


def training(accepted=False):
    trace = json.dumps(
        {
            "actions": ["Inspect schema", "Execute joined aggregate"],
            "observed_rows": [["provider-a", 6]],
        },
        sort_keys=True,
    )
    return {
        "task_id": "train-1",
        "source_id": "registered-source-v1",
        "source_sha256": "1" * 64,
        "question": "Count completed jobs once for each provider.",
        "trace": trace,
        "trace_sha256": text_sha256(trace),
        "outcome": {
            "task_id": "train-1",
            "source_id": "registered-source-v1",
            "source_sha256": "1" * 64,
            "trace_sha256": text_sha256(trace),
            "checker_id": "independent-count-checker-v1",
            "complete": True,
            "accepted": accepted,
            "evidence_sha256": "2" * 64,
            "feedback": "A one-to-many join multiplied the counted jobs.",
        },
    }


def study(**changes):
    args = {
        "source_id": "registered-source-v1",
        "checker_id": "independent-count-checker-v1",
        "train_ids": ["train-1"],
        "validation_ids": ["validation-1", "validation-2"],
        "test_ids": ["protected-1", "protected-2"],
    }
    args.update(changes)
    return ReasoningPolicyStudy(**args)


def verdict(course, policy_id, task_id, accepted, **changes):
    args = {
        "complete": True,
        "accepted": accepted,
        "evidence_sha256": digest([policy_id, task_id, accepted]),
        "source_id": course.source_id,
        "checker_id": course.checker_id,
        "source_sha256": digest(task_id),
    }
    args.update(changes)
    course.record_validation(policy_id, task_id, **args)


class ReasoningPolicyTests(unittest.TestCase):
    def test_split_overlap_and_unbounded_candidate_pool_are_refused(self):
        for changes in (
            {"validation_ids": ["train-1"]},
            {"test_ids": ["validation-1"]},
            {"train_ids": ["train-1", "train-1"]},
            {"max_candidates": 4},
            {"max_candidates": True},
            {"test_ids": []},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                study(**changes)

    def test_critic_receives_only_complete_bound_training_evidence(self):
        course = study()
        reasoner = RecordingReasoner()
        evidence = training()
        candidate = course.propose(reasoner, [evidence], max_tokens=400, seed=5)
        messages, allowance, seed = reasoner.calls[0]
        supplied = json.loads(messages[1]["content"])
        self.assertEqual(supplied["training_executions"], [evidence])
        self.assertEqual(
            supplied["proposal_search_context"],
            {
                "proposal_index": 1,
                "maximum_proposals": 3,
                "previous_candidates": [],
            },
        )
        self.assertNotIn("protected-1", messages[1]["content"])
        self.assertNotIn("validation-1", messages[1]["content"])
        self.assertEqual((allowance, seed), (400, 5))
        self.assertEqual(candidate["policy_id"], policy_identity(reasoner.suffix))
        attempt = course.proposals[0]
        self.assertEqual(attempt["training_evidence_sha256"], digest([evidence]))
        self.assertEqual(attempt["critic_messages_sha256"], digest(messages))
        self.assertEqual(attempt["status"], "candidate")

    def test_later_proposals_receive_declared_search_context_without_new_labels(self):
        course = study(max_candidates=2)
        first_reasoner = RecordingReasoner()
        first = course.propose(first_reasoner, [training()], seed=0)
        second_reasoner = RecordingReasoner("Check missing-value boundaries.")
        course.propose(second_reasoner, [training()], seed=0)
        first_messages = first_reasoner.calls[0][0]
        second_messages = second_reasoner.calls[0][0]
        first_payload = json.loads(first_messages[1]["content"])
        second_payload = json.loads(second_messages[1]["content"])
        self.assertNotEqual(first_messages, second_messages)
        self.assertEqual(
            first_payload["training_executions"],
            second_payload["training_executions"],
        )
        self.assertEqual(
            second_payload["proposal_search_context"],
            {
                "proposal_index": 2,
                "maximum_proposals": 2,
                "previous_candidates": [first],
            },
        )
        self.assertNotIn("validation-1", second_messages[1]["content"])
        self.assertNotIn("protected-1", second_messages[1]["content"])
        self.assertEqual(
            course.proposals[0]["training_evidence_sha256"],
            course.proposals[1]["training_evidence_sha256"],
        )

    def test_invalid_trace_checker_source_or_terminal_binding_never_calls_model(self):
        variants = []
        changed = training()
        changed["trace"] += " altered"
        variants.append(changed)
        for key, value in (
            ("checker_id", "model-self-grade"),
            ("source_sha256", "3" * 64),
            ("task_id", "protected-1"),
            ("complete", False),
            ("accepted", "true"),
            ("trace_sha256", "4" * 64),
        ):
            changed = training()
            changed["outcome"][key] = value
            variants.append(changed)
        for record in variants:
            reasoner = RecordingReasoner()
            with self.subTest(record=record), self.assertRaises(ValueError):
                study().propose(reasoner, [record])
            self.assertEqual(reasoner.calls, [])

    def test_undeclared_test_task_bodies_and_extra_answer_fields_are_refused(self):
        for key, value in (
            ("task_id", "protected-1"),
            ("gold_answer", "must not enter the critic"),
            ("test_tasks", [{"body": "protected task text"}]),
        ):
            record = training()
            record[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                study().propose(RecordingReasoner(), [record])

    def test_training_failures_cannot_be_dropped_or_changed_between_proposals(self):
        with self.assertRaises(ValueError):
            study(train_ids=["train-1", "train-2"]).propose(
                RecordingReasoner(), [training()]
            )
        course = study()
        course.propose(RecordingReasoner(), [training()])
        changed = training()
        changed["outcome"]["accepted"] = True
        with self.assertRaises(ValueError):
            course.propose(RecordingReasoner("Check NULL behavior."), [changed])

    def test_failed_proposal_consumes_allowance_and_retains_usage(self):
        course = study(max_candidates=1)
        with self.assertRaises(ValueError):
            course.propose(RecordingReasoner("é" * 2049), [training()])
        self.assertEqual(course.proposals[0]["status"], "failed")
        self.assertEqual(course.proposals[0]["usage"]["completion_tokens"], 20)
        with self.assertRaises(ValueError):
            course.propose(RecordingReasoner(), [training()])

    def test_transport_failure_retains_ambiguous_usage_without_retry(self):
        class BrokenReasoner:
            calls = 0

            def complete(self, messages, **kwargs):
                self.calls += 1
                raise ReasonerError("model_transport_error")

        course = study(max_candidates=1)
        reasoner = BrokenReasoner()
        with self.assertRaises(ReasonerError):
            course.propose(reasoner, [training()])
        self.assertEqual(reasoner.calls, 1)
        self.assertEqual(course.proposals[0]["status"], "failed")
        self.assertIsNone(course.proposals[0]["usage"])

    def test_wrapper_preserves_original_contract_inputs_and_model_usage(self):
        reasoner = RecordingReasoner()
        candidate = {
            "policy_id": policy_identity("Check NULLs."),
            "suffix": "Check NULLs.",
        }
        wrapper = PolicyReasoner(reasoner, candidate)
        original = [
            {"role": "system", "content": "Original immutable tool contract."},
            {"role": "user", "content": "Question"},
        ]
        snapshot = copy.deepcopy(original)
        result = wrapper.complete(original, max_tokens=512, seed=7)
        messages, bound, seed = reasoner.calls[-1]
        self.assertEqual(original, snapshot)
        self.assertTrue(messages[0]["content"].startswith(original[0]["content"]))
        self.assertIn("original host contract", messages[0]["content"])
        self.assertIn(candidate["policy_id"], messages[0]["content"])
        self.assertEqual(messages[1], original[1])
        self.assertEqual((bound, seed), (512, 7))
        self.assertEqual(result["usage"]["completion_tokens"], 20)
        self.assertEqual(wrapper.model, reasoner.model)
        self.assertTrue(wrapper.enable_thinking)

    def test_baseline_wrapper_does_not_change_messages_and_rejects_wrong_identity(self):
        reasoner = RecordingReasoner()
        baseline = PolicyReasoner(reasoner, {"policy_id": BASELINE, "suffix": ""})
        messages = [{"role": "system", "content": "Tool contract"}]
        baseline.complete(messages)
        self.assertEqual(reasoner.calls[0][0], messages)
        with self.assertRaises(ValueError):
            PolicyReasoner(reasoner, {"policy_id": BASELINE, "suffix": "Override"})
        with self.assertRaises(ValueError):
            baseline.complete([{"role": "user", "content": "No system contract"}])

    def test_complete_validation_selects_strict_gain_and_freezes_copy(self):
        course = study()
        candidate = course.propose(RecordingReasoner(), [training()])
        for task_id, accepted in zip(course.validation_ids, [False, True], strict=True):
            verdict(course, BASELINE, task_id, accepted)
            verdict(course, candidate["policy_id"], task_id, True)
        frozen = course.freeze()
        self.assertTrue(frozen["candidate_promoted"])
        self.assertEqual(frozen["validation_scores"][BASELINE], 1)
        self.assertEqual(frozen["validation_scores"][candidate["policy_id"]], 2)
        self.assertEqual(frozen["test_effectiveness"], "unmeasured")
        self.assertEqual(frozen["selected_policy"], candidate)
        self.assertEqual(len(frozen["validation"]), 4)
        self.assertEqual(PolicyReasoner(RecordingReasoner(), frozen).policy, candidate)
        altered = copy.deepcopy(frozen)
        altered["validation_scores"][BASELINE] = 0
        with self.assertRaises(ValueError):
            PolicyReasoner(RecordingReasoner(), altered)
        identity = frozen.pop("artifact_sha256")
        self.assertEqual(identity, digest(frozen))
        frozen["selected_policy"]["suffix"] = "external mutation"
        self.assertEqual(course.freeze()["selected_policy"], candidate)
        with self.assertRaises(ValueError):
            verdict(course, BASELINE, "validation-1", False)

    def test_tie_keeps_baseline_and_does_not_claim_test_improvement(self):
        course = study()
        candidate = course.propose(RecordingReasoner(), [training()])
        for task_id in course.validation_ids:
            verdict(course, BASELINE, task_id, True)
            verdict(course, candidate["policy_id"], task_id, True)
        frozen = course.freeze()
        self.assertFalse(frozen["candidate_promoted"])
        self.assertEqual(frozen["selected_policy"]["policy_id"], BASELINE)
        self.assertEqual(frozen["test_effectiveness"], "unmeasured")

    def test_all_candidate_task_pairs_required_without_result_cherry_picking(self):
        course = study()
        first = course.propose(RecordingReasoner(), [training()])
        second = course.propose(RecordingReasoner("Check NULLs."), [training()])
        for task_id in course.validation_ids:
            verdict(course, BASELINE, task_id, False)
            verdict(course, first["policy_id"], task_id, True)
        with self.assertRaises(ValueError):
            course.freeze()
        verdict(course, second["policy_id"], "validation-1", True)
        verdict(
            course,
            second["policy_id"],
            "validation-2",
            None,
            complete=False,
        )
        with self.assertRaises(ValueError):
            course.freeze()
        with self.assertRaises(ValueError):
            verdict(course, second["policy_id"], "validation-2", True)

    def test_validation_seals_pool_and_refuses_wrong_source_or_test_identity(self):
        course = study()
        course.propose(RecordingReasoner(), [training()])
        verdict(course, BASELINE, "validation-1", False)
        for args in (
            {"checker_id": "model-self-grade"},
            {"source_id": "different-source"},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                verdict(course, BASELINE, "validation-2", True, **args)
        with self.assertRaises(ValueError):
            verdict(course, BASELINE, "protected-1", True)
        with self.assertRaises(ValueError):
            course.propose(RecordingReasoner("Another strategy."), [training()])

    def test_candidate_cannot_receive_different_validation_rows(self):
        course = study()
        candidate = course.propose(RecordingReasoner(), [training()])
        verdict(course, BASELINE, "validation-1", False)
        with self.assertRaises(ValueError):
            verdict(
                course,
                candidate["policy_id"],
                "validation-1",
                True,
                source_sha256="9" * 64,
            )


if __name__ == "__main__":
    unittest.main()

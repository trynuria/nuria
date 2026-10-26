"""Behavioral checks for bounded plans, execution and independent acceptance."""

import copy
import unittest

from cognition.workflow_planner import (
    VERSION,
    ToolUnavailable,
    catalog_view,
    digest,
    execute_plan,
    source_identity,
)


class WorkflowPlannerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.sources = {"numbers": {"type": "array", "value": [3, 1, 2]}}

        def normalize(inputs, arguments):
            self.calls.append("normalize")
            inputs["values"].append(arguments["extra"])
            return {"values": sorted(inputs["values"])}

        def summarize(inputs, arguments):
            self.calls.append("summarize")
            return {
                "report": {"sum": sum(inputs["values"]), "count": len(inputs["values"])}
            }

        def render(inputs, arguments):
            self.calls.append("render")
            return {
                "text": f"{inputs['report']['count']} values total {inputs['report']['sum']}"
            }

        def operation(inputs, outputs, handler, arguments=None):
            return {
                "inputs": inputs,
                "outputs": outputs,
                "arguments": arguments or {},
                "work_units": 2,
                "deadline_units": 1,
                "handler": handler,
            }

        self.catalog = {
            "normalize": operation(
                {"values": "array"},
                {"values": "array"},
                normalize,
                {"extra": {"type": "integer", "minimum": 0, "maximum": 5}},
            ),
            "summarize": operation(
                {"values": "array"}, {"report": "object"}, summarize
            ),
            "render": operation({"report": "object"}, {"text": "string"}, render),
        }
        self.plan = {
            "version": VERSION,
            "case_id": "case_one",
            "source_sha256": source_identity("case_one", self.sources),
            # Deliberately not topological: dependency ordering is checked.
            "nodes": [
                {
                    "id": "finish",
                    "op": "render",
                    "arguments": {},
                    "inputs": {"report": {"node": "stats", "port": "report"}},
                },
                {
                    "id": "sort",
                    "op": "normalize",
                    "arguments": {"extra": 4},
                    "inputs": {"values": {"source": "numbers"}},
                },
                {
                    "id": "stats",
                    "op": "summarize",
                    "arguments": {},
                    "inputs": {"values": {"node": "sort", "port": "values"}},
                },
            ],
            "output": {"node": "finish", "port": "text"},
        }
        self.kwargs = dict(
            catalog=self.catalog,
            sources=self.sources,
            case_id="case_one",
            goal_type="string",
            maximum_work=12,
            deadline_units=10,
        )

    @staticmethod
    def checker(record):
        value = record["final_artifact"]["value"]
        return {
            "accepted": value == "4 values total 10",
            "checker_id": "independent.sum-v1",
            "evidence_sha256": digest(
                {"observed": value, "expected": "4 values total 10"}
            ),
        }

    def run_plan(self, plan=None, **overrides):
        arguments = {
            **self.kwargs,
            "execution_id": "attempt_one",
            "execution_log": {},
            "checker": self.checker,
            **overrides,
        }
        return execute_plan(self.plan if plan is None else plan, **arguments)

    def test_actual_pipeline_topology_provenance_and_input_isolation(self):
        original = copy.deepcopy(self.sources)
        log = {}
        receipt = self.run_plan(execution_log=log)
        self.assertEqual(self.calls, ["normalize", "summarize", "render"])
        self.assertEqual(self.sources, original)
        self.assertEqual(receipt["status"], "accepted")
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["charged_work_units"], 6)
        self.assertEqual(receipt["final_artifact"]["value"], "4 values total 10")
        for key, artifact in receipt["artifacts"].items():
            self.assertEqual(artifact["case_id"], "case_one")
            self.assertEqual(artifact["source_sha256"], self.plan["source_sha256"])
            identity = artifact.pop("artifact_sha256")
            self.assertEqual(identity, digest(artifact), key)
        self.assertTrue(all(row["wall_ms"] >= 0 for row in receipt["trace"]))
        # Caller receives a copy; mutation cannot rewrite its retained receipt.
        receipt["status"] = "forged"
        self.assertEqual(log["attempt_one"]["status"], "accepted")

    def test_structural_rejections_make_no_handler_calls(self):
        variants = []
        for field, value in [
            ("op", "unknown"),
            ("op", []),
            ("id", {}),
            ("shell", "run"),
        ]:
            plan = copy.deepcopy(self.plan)
            plan["nodes"][0][field] = value
            variants.append(plan)
        for ref in [
            {"source": []},
            {"node": {}, "port": "values"},
            {"node": "sort", "port": []},
            {"source": "missing"},
        ]:
            plan = copy.deepcopy(self.plan)
            plan["nodes"][2]["inputs"]["values"] = ref
            variants.append(plan)
        for output in [{"node": [], "port": "text"}, {"node": "finish", "port": []}]:
            plan = copy.deepcopy(self.plan)
            plan["output"] = output
            variants.append(plan)
        for plan in variants:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.run_plan(plan)
        self.assertEqual(self.calls, [])

    def test_cycles_types_and_unused_nodes_rejected(self):
        cycle = copy.deepcopy(self.plan)
        cycle["nodes"][1]["inputs"]["values"] = {"node": "sort", "port": "values"}
        mismatch = copy.deepcopy(self.plan)
        mismatch["nodes"][2]["inputs"]["values"] = {"node": "finish", "port": "text"}
        unused = copy.deepcopy(self.plan)
        unused["nodes"].append(
            {
                "id": "unused",
                "op": "normalize",
                "arguments": {"extra": 0},
                "inputs": {"values": {"source": "numbers"}},
            }
        )
        for plan in (cycle, mismatch, unused):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.run_plan(plan)
        self.assertEqual(self.calls, [])

    def test_source_case_argument_and_budget_gates(self):
        for replacement in [-1, 6, True, "https://example.invalid", float("nan")]:
            plan = copy.deepcopy(self.plan)
            plan["nodes"][1]["arguments"]["extra"] = replacement
            with self.subTest(value=replacement), self.assertRaises(ValueError):
                self.run_plan(plan)
        for changes in (
            {"maximum_work": 5},
            {"deadline_units": 2},
            {"goal_type": "array"},
            {"case_id": "other_case"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_plan(**changes)
        altered = copy.deepcopy(self.sources)
        altered["numbers"]["value"].append(5)
        with self.assertRaises(ValueError):
            self.run_plan(sources=altered)
        self.assertEqual(self.calls, [])

    def test_finite_node_and_edge_limits(self):
        too_many = copy.deepcopy(self.plan)
        too_many["nodes"] *= 5
        with self.assertRaises(ValueError):
            self.run_plan(too_many)
        self.catalog["merge"] = {
            "inputs": {str_port: "array" for str_port in ["a", "b", "c", "d"]},
            "outputs": {"values": "array"},
            "arguments": {},
            "work_units": 1,
            "deadline_units": 1,
            "handler": lambda inputs, arguments: {"values": []},
        }
        plan = copy.deepcopy(self.plan)
        plan["nodes"] = [
            {
                "id": f"node{i}",
                "op": "merge",
                "arguments": {},
                "inputs": {
                    port: (
                        {"source": "numbers"}
                        if i == 0
                        else {"node": f"node{i - 1}", "port": "values"}
                    )
                    for port in ["a", "b", "c", "d"]
                },
            }
            for i in range(5)
        ]
        plan["output"] = {"node": "node4", "port": "values"}
        with self.assertRaisesRegex(ValueError, "edges"):
            self.run_plan(plan, goal_type="array")
        self.assertEqual(self.calls, [])

    def test_unavailable_tool_one_explicit_repair_and_no_budget_renewal(self):
        def unavailable(inputs, arguments):
            self.calls.append("unavailable")
            raise ToolUnavailable("private host error detail must stay private")

        self.catalog["primary"] = {**self.catalog["normalize"], "handler": unavailable}
        failed_plan = copy.deepcopy(self.plan)
        failed_plan["nodes"][1]["op"] = "primary"
        log = {}
        failed = self.run_plan(failed_plan, execution_log=log)
        self.assertEqual(failed["status"], "tool_unavailable")
        self.assertEqual(failed["charged_work_units"], 2)
        self.assertEqual(len(failed["trace"]), 1)
        self.assertNotIn("private host", str(failed))
        self.assertEqual(self.calls, ["unavailable"])
        with self.assertRaises(ValueError):
            self.run_plan(execution_id="attempt_two", execution_log=log, maximum_work=7)
        repaired = self.run_plan(execution_id="attempt_two", execution_log=log)
        self.assertEqual(repaired["status"], "accepted")
        self.assertEqual(sum(row["charged_work_units"] for row in log.values()), 8)
        self.assertEqual(log["attempt_one"]["status"], "tool_unavailable")
        for execution_id in ["attempt_two", "attempt_three"]:
            with self.assertRaises(ValueError):
                self.run_plan(execution_id=execution_id, execution_log=log)

    def test_only_unavailable_failure_allows_one_changed_plan(self):
        def unavailable(inputs, arguments):
            raise ToolUnavailable()

        self.catalog["normalize"]["handler"] = unavailable
        log = {}
        self.run_plan(execution_log=log)
        with self.assertRaises(ValueError):
            self.run_plan(execution_id="repeat", execution_log=log)
        changed = copy.deepcopy(self.plan)
        changed["nodes"][1]["arguments"]["extra"] = 5
        # A second unavailable attempt is retained; a third attempt is forbidden.
        self.run_plan(changed, execution_id="repair", execution_log=log)
        changed["nodes"][1]["arguments"]["extra"] = 3
        with self.assertRaises(ValueError):
            self.run_plan(changed, execution_id="third", execution_log=log)

    def test_wrong_output_and_checker_failure_never_claim_acceptance(self):
        self.catalog["normalize"]["handler"] = lambda inputs, arguments: {
            "values": "wrong"
        }
        failed = self.run_plan()
        self.assertEqual(failed["status"], "handler_error")
        self.assertIsNone(failed["accepted"])
        self.assertIsNone(failed["final_artifact"])
        self.assertEqual(len(failed["trace"]), 1)

    def test_independent_acceptance_and_verification_are_separate(self):
        rejected = self.run_plan(
            checker=lambda record: {
                "accepted": False,
                "checker_id": "reject-v1",
                "evidence_sha256": "a" * 64,
            }
        )
        self.assertEqual(rejected["status"], "rejected")
        unchecked = self.run_plan(checker=None)
        self.assertEqual(unchecked["status"], "delivered_unverified")
        self.assertIsNone(unchecked["accepted"])
        bad_checker = self.run_plan(checker=lambda record: {"accepted": True})
        self.assertEqual(bad_checker["status"], "verification_error")
        self.assertIsNone(bad_checker["accepted"])

    def test_custom_semantic_checks_and_catalog_authority_stay_host_side(self):
        self.sources["numbers"]["type"] = "work_rows"
        self.catalog["normalize"]["inputs"]["values"] = "work_rows"
        self.plan["source_sha256"] = source_identity("case_one", self.sources)
        with self.assertRaises(ValueError):
            self.run_plan(type_checks={"work_rows": lambda rows: False})
        self.assertEqual(self.calls, [])
        receipt = self.run_plan(
            type_checks={"work_rows": lambda rows: type(rows) is list}
        )
        self.assertTrue(receipt["accepted"])
        view = catalog_view(self.catalog)
        self.assertNotIn("handler", view["normalize"])
        self.assertEqual(len(view["normalize"]["handler_sha256"]), 64)
        with self.assertRaises(ValueError):
            self.run_plan(type_checks={"work_rows": "model supplied validator"})


if __name__ == "__main__":
    unittest.main()

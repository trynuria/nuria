import copy
import json
import unittest

from cognition.reasoner_client import digest
from cognition.working_evidence import WorkingEvidence


def receipt(tool, **data):
    value = {
        "tool": tool,
        "status": "ok",
        "complete": True,
        "source": {"db_id": "sample", "db_sha256": "a" * 64},
        **data,
    }
    value["result_sha256"] = digest(value)
    return value


class WorkingEvidenceTests(unittest.TestCase):
    def test_schema_survives_later_queries_and_keeps_its_actual_receipt(self):
        state = WorkingEvidence()
        observed = receipt(
            "describe_table", columns=["name", "type"], rows=[["amount", "INTEGER"]]
        )
        state.observe(
            {"tool": "describe_table", "arguments": {"table": "events"}}, observed
        )
        for index in range(8):
            state.observe(
                {"tool": "query", "arguments": {"sql": f"SELECT {index}"}},
                receipt("query", columns=["value"], rows=[[index]]),
            )
        view = state.view()
        self.assertEqual(view["schemas"]["events"]["rows"], [["amount", "INTEGER"]])
        self.assertEqual(
            view["schemas"]["events"]["receipt_sha256"], observed["result_sha256"]
        )
        self.assertEqual(view["observations"], 9)
        self.assertEqual(len(view["recent_queries"]), 3)

    def test_incomplete_or_failed_query_cannot_become_an_observed_answer(self):
        state = WorkingEvidence()
        observed = receipt(
            "query",
            status="row_limit",
            complete=False,
            rows=[[7]],
            error="Incomplete result",
        )
        state.observe(
            {"tool": "query", "arguments": {"sql": "SELECT amount FROM events"}},
            observed,
        )
        view = state.view()
        self.assertEqual(view["recent_queries"], [])
        self.assertEqual(view["recent_errors"][0]["status"], "row_limit")

    def test_prompt_limits_preserve_the_full_execution_data(self):
        state = WorkingEvidence()
        observed = receipt(
            "query", columns=["value"], rows=[["x" * 600] for _ in range(100)]
        )
        saved = copy.deepcopy(observed)
        state.observe(
            {"tool": "query", "arguments": {"sql": "SELECT value FROM events"}},
            observed,
        )
        view = state.view(2048)
        self.assertLessEqual(len(json.dumps(view, ensure_ascii=False).encode()), 2048)
        self.assertEqual(observed, saved)
        self.assertEqual(state.queries[0]["result_row_count"], 100)
        if view["recent_queries"]:
            self.assertGreater(view["recent_queries"][0]["prompt_rows_omitted"], 0)

    def test_utf8_document_excerpt_has_explicit_omissions_and_original_identity(self):
        state = WorkingEvidence()
        observed = receipt("read_knowledge", content="é" * 5000)
        state.observe(
            {"tool": "read_knowledge", "arguments": {"section": "schema"}}, observed
        )
        view = state.view()
        document = view["documents"]["schema"]
        self.assertEqual(len(document["content"].encode()), 4000)
        self.assertEqual(document["content_bytes_omitted"], 6000)
        self.assertEqual(document["receipt_sha256"], observed["result_sha256"])
        self.assertEqual(len(state.documents["schema"]["content"].encode()), 10000)


if __name__ == "__main__":
    unittest.main()

"""A bounded prompt view of facts obtained from actual analytical tool receipts.

The full execution trace remains outside this view. Dropping an old chat turn
must not silently discard a known schema or turn an assumption into evidence.
"""

from __future__ import annotations

import copy
import json

from cognition.reasoner_client import digest


class WorkingEvidence:
    def __init__(self):
        self.tables = None
        self.schemas = {}
        self.documents = {}
        self.queries = []
        self.errors = []
        self.observations = 0

    def observe(self, action, receipt):
        """Accept host execution receipts, never model-authored claims."""
        self.observations += 1
        identity = receipt["result_sha256"]
        if receipt.get("status") != "ok" or receipt.get("complete") is not True:
            self.errors = [
                *self.errors[-3:],
                {
                    "tool": action["tool"],
                    "arguments": copy.deepcopy(action["arguments"]),
                    "status": receipt.get("status"),
                    "error": receipt.get("error"),
                    "receipt_sha256": identity,
                },
            ]
            return
        common = {
            "receipt_sha256": identity,
            "source": copy.deepcopy(receipt["source"]),
        }
        tool, args = action["tool"], action["arguments"]
        if tool == "list_tables":
            self.tables = {**common, "rows": copy.deepcopy(receipt.get("rows", []))}
        elif tool == "describe_table":
            self.schemas[args["table"]] = {
                **common,
                "columns": copy.deepcopy(receipt.get("columns", [])),
                "rows": copy.deepcopy(receipt.get("rows", [])),
            }
        elif tool == "read_knowledge":
            self.documents[args["section"]] = {
                **common,
                "content": receipt.get("content", ""),
            }
        elif tool == "query":
            self.queries = [
                *self.queries[-2:],
                {
                    **common,
                    "sql": args["sql"],
                    "columns": copy.deepcopy(receipt.get("columns", [])),
                    "result_row_count": len(receipt.get("rows", [])),
                    "rows": copy.deepcopy(receipt.get("rows", [])[:20]),
                    "prompt_rows_omitted": max(0, len(receipt.get("rows", [])) - 20),
                },
            ]

    def view(self, maximum_bytes=12000):
        if type(maximum_bytes) is not int or not 2048 <= maximum_bytes <= 32768:
            raise ValueError("Working-evidence prompt view exceeds its bound")
        full = {
            "tables": self.tables,
            "schemas": self.schemas,
            "documents": self.documents,
            "recent_queries": self.queries,
            "recent_errors": self.errors,
        }
        result = copy.deepcopy(full)
        result["observations"] = self.observations
        result["full_state_sha256"] = digest(full)
        result["prompt_omissions"] = []

        def oversized():
            return len(json.dumps(result, ensure_ascii=False).encode()) > maximum_bytes

        # Keep the schema and document identities when shortening their data.
        for name, document in result["documents"].items():
            raw = document["content"].encode()
            if len(raw) > 4000:
                document["content"] = raw[:4000].decode("utf-8", "ignore")
                document["content_bytes_omitted"] = len(raw) - len(
                    document["content"].encode()
                )
                result["prompt_omissions"].append("document_excerpt:" + name)
        for query in result["recent_queries"]:
            while oversized() and query["rows"]:
                query["rows"] = query["rows"][: len(query["rows"]) // 2]
                query["prompt_rows_omitted"] = query["result_row_count"] - len(
                    query["rows"]
                )
        for section in ("documents", "schemas"):
            while oversized() and result[section]:
                key = next(iter(result[section]))
                del result[section][key]
                result["prompt_omissions"].append(section + ":" + key)
        while oversized() and result["recent_queries"]:
            result["recent_queries"].pop(0)
            result["prompt_omissions"].append("older_query")
        if oversized():
            result["tables"] = None
            result["recent_errors"] = [
                {"status": row["status"], "receipt_sha256": row["receipt_sha256"]}
                for row in self.errors
            ]
            result["prompt_omissions"] = ["minimal_evidence_index"]
        if oversized():
            raise ValueError("Working-evidence view cannot fit its allowance")
        return result

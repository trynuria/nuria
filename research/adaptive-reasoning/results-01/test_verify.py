import json
import sqlite3
import unittest
import uuid
from pathlib import Path

import verify


class IndependentReplayTests(unittest.TestCase):
    def database(self):
        directory = Path(__file__).parent / ".test-state"
        directory.mkdir(exist_ok=True)
        path = directory / ("fixture-" + uuid.uuid4().hex + ".sqlite")
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                "CREATE TABLE observations (value); INSERT INTO observations VALUES (NULL),(1),(1),(1.0),('1');"
            )
            connection.commit()
        finally:
            connection.close()
        return path

    def test_typed_comparison_preserves_null_duplicates_and_integer_real_distinction(
        self,
    ):
        self.assertTrue(
            verify.equal_rows(
                [[None], [1], [1]], [[1], [None], [1]], preserve_order=False
            )
        )
        self.assertFalse(verify.equal_rows([[1], [1]], [[1]], preserve_order=False))
        self.assertFalse(verify.equal_rows([[1]], [[1.0]], preserve_order=False))
        self.assertFalse(verify.equal_rows([[1]], [["1"]], preserve_order=False))
        self.assertFalse(verify.equal_rows([[1], [2]], [[2], [1]], preserve_order=True))
        self.assertTrue(
            verify.equal_rows(
                [[b"\x00\xff"]], [[{"blob_hex": "00ff"}]], preserve_order=False
            )
        )

    def test_outer_order_lexer_ignores_nested_quoted_and_commented_words(self):
        for query in (
            "SELECT * FROM observations ORDER BY value",
            "SELECT * FROM (SELECT value FROM observations ORDER BY value) ORDER BY value",
        ):
            self.assertTrue(verify.ordered(query))
        for query in (
            "SELECT 'ORDER BY'",
            'SELECT "ORDER BY" FROM observations',
            "SELECT * FROM (SELECT * FROM observations ORDER BY value)",
            "SELECT 1 -- ORDER BY 1",
            "SELECT 1 /* ORDER BY 1 */",
        ):
            self.assertFalse(verify.ordered(query))

    def test_sqlite_replay_is_read_only_and_does_not_change_source_bytes(self):
        path = self.database()
        before = verify.sha(path.read_bytes())
        result = verify.execute(path, "SELECT value FROM observations")
        self.assertEqual(len(result["rows"]), 5)
        with self.assertRaises(verify.ReplayError):
            verify.execute(path, "DELETE FROM observations")
        with self.assertRaises(sqlite3.DatabaseError):
            verify.execute(path, "SELECT load_extension('untrusted')")
        self.assertEqual(verify.sha(path.read_bytes()), before)

    def test_row_limit_and_vm_limit_refuse_incomplete_answers(self):
        path = self.database()
        with self.assertRaisesRegex(verify.ReplayError, "query_row_limit"):
            verify.execute(
                path,
                "WITH RECURSIVE numbers(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM numbers WHERE x<201) SELECT x FROM numbers",
            )
        with self.assertRaises(sqlite3.DatabaseError):
            verify.execute(
                path,
                "WITH RECURSIVE numbers(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM numbers WHERE x<10000000) SELECT sum(x) FROM numbers",
            )

    def test_receipt_hash_and_source_identity_are_separate_checks(self):
        source = {"db_id": "fixture", "db_sha256": "a" * 64}
        receipt = {
            "source": source,
            "runtime": {"implementation_sha256": "b" * 64},
            "rows": [[1]],
        }
        receipt["result_sha256"] = verify.digest(receipt, ascii=False)
        verify.check_receipt(receipt, source, "b" * 64)
        with self.assertRaisesRegex(verify.ReplayError, "receipt_source_binding"):
            verify.check_receipt(receipt, source | {"db_id": "different"}, "b" * 64)
        receipt["rows"] = [[2]]
        with self.assertRaisesRegex(verify.ReplayError, "receipt_result_hash"):
            verify.check_receipt(receipt, source, "b" * 64)

    def test_duplicate_and_nonfinite_json_are_rejected(self):
        for text in ('{"duplicate":1,"duplicate":2}', '{"number":NaN}'):
            with self.assertRaises(verify.ReplayError):
                verify.decode(text.encode())
        self.assertEqual(
            verify.decode(json.dumps({"ordinary": 1}).encode()), {"ordinary": 1}
        )


if __name__ == "__main__":
    unittest.main()

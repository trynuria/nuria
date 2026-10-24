"""Reader safety, source-label integrity and complete buyer-side partitioning."""

import json
import unittest

from cognition.pbe_benchmarks import (
    MAX_STRING,
    BenchmarkError,
    evaluate_expression,
    make_buyer_job,
    make_source_job,
    parse_problem,
    parse_sexpressions,
)


def specification(rows, *, oracle='(str.++ x "!")', output="String"):
    constraints = "\n".join(
        f"(constraint (= (f {json.dumps(value)}) {json.dumps(target)}))"
        for value, target in rows
    )
    definition = (
        "" if oracle is None else f"(define-fun f_1 ((x String)) {output} {oracle})"
    )
    return (
        f"(set-logic SLIA)\n(synth-fun f ((x String)) {output})\n"
        f"{constraints}\n(check-synth)\n{definition}"
    )


def run(expression, bindings=None):
    return evaluate_expression(parse_sexpressions(expression)[0], bindings)


def job(problem, **kwargs):
    return make_buyer_job(
        problem, identity="opaque-1", family="private", source={}, **kwargs
    )


class PBEBenchmarkTests(unittest.TestCase):
    def test_comments_doubled_quotes_and_literal_backslash_are_data(self):
        expression = '; comment\n(str.++ "a""b" "\\t")'
        self.assertEqual(run(expression), 'a"b\\t')
        with self.assertRaises(BenchmarkError):
            run('"\\u{41}"')
        with self.assertRaises(BenchmarkError):
            run('"unfinished')

    def test_smt_boundaries_and_simultaneous_let(self):
        cases = {
            '(str.substr "abcd" (- 1) 3)': "",
            '(str.substr "abcd" 1 (- 1))': "",
            '(str.at "abcd" 4)': "",
            '(str.indexof "abcd" "" 4)': 4,
            '(str.indexof "abcd" "" 5)': -1,
            '(str.replace "abc" "" "x")': "xabc",
            '(str.replace "aaaa" "aa" "x")': "xaa",
            '(str.prefixof "ab" "abcd")': True,
            '(str.suffixof "cd" "abcd")': True,
            '(str.to.int "１２")': -1,
            '(str.to.int "007")': 7,
            "(int.to.str (- 1))": "",
        }
        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(run(expression), expected)
        self.assertEqual(run("(let ((x 2) (y x)) (+ x y))", {"x": 8}), 10)

    def test_type_confusion_operator_arity_and_expansion_are_rejected(self):
        for expression in (
            "(+ true 1)",
            "(= true 1)",
            '(str.len "a" "b")',
            "(unknown 1)",
        ):
            with self.subTest(expression=expression), self.assertRaises(BenchmarkError):
                run(expression)
        with self.assertRaises(BenchmarkError):
            run("(str.++ x x)", {"x": "a" * MAX_STRING})
        with self.assertRaises(BenchmarkError):
            run("(* 9007199254740991 2)")
        with self.assertRaises(BenchmarkError):
            parse_sexpressions("(" * 60 + "1" + ")" * 60)

    def test_no_oracle_execution_or_command_escape(self):
        raw = specification([("a", "a!")]) + '\n(include "other-file.sl")'
        failed = make_source_job(raw, identity="bad", family="x", source={})
        self.assertIn("Unsupported source command", failed["unsupported"])
        self.assertEqual(failed["diagnostic_inputs"], [])
        self.assertIsNone(failed["buyer_metadata"]["original_constraint_count"])

    def test_conflicting_original_labels_and_wrong_arity_are_rejected(self):
        with self.assertRaisesRegex(BenchmarkError, "conflicting labels"):
            parse_problem(specification([("a", "a!"), ("a", "wrong")]))
        raw = specification([("a", "a!")]).replace('(f "a")', '(f "a" "b")')
        with self.assertRaisesRegex(BenchmarkError, "arity"):
            parse_problem(raw)

    def test_oracle_must_match_originals_and_its_declared_signature(self):
        problem = parse_problem(specification([("a", "a!")], oracle='(str.++ x "?")'))
        self.assertIn("contradicts", problem.oracle_issue)
        self.assertIsNotNone(job(problem)["unsupported"])
        raw = specification([("a", "a!")]).replace(
            "define-fun f_1 ((x String))", "define-fun f_1 ((x Int))"
        )
        problem = parse_problem(raw)
        self.assertIn("signature differs", problem.oracle_issue)
        with self.assertRaises(BenchmarkError):
            problem.oracle(["new"])

    def test_all_original_constraints_survive_without_diagnostic_score_overlap(self):
        rows = [(x, x + "!") for x in ("a", "b", "c", "d", "e", "e", "a")]
        result = job(parse_problem(specification(rows)), derived_score_rows=0)
        self.assertIsNone(result["unsupported"])
        self.assertEqual(len(result["diagnostic_inputs"]), 4)
        self.assertEqual(result["score_inputs"], ['["e"]', '["e"]'])
        self.assertFalse(set(result["diagnostic_inputs"]) & set(result["score_inputs"]))
        inventory = result["buyer_metadata"]["original_constraints"]
        self.assertEqual(len(inventory), len(rows))
        self.assertEqual(sum(r["role"] == "diagnostic" for r in inventory), 5)
        self.assertEqual(result["buyer_metadata"]["derived_score_rows"], 0)

    def test_extensions_are_reproducible_source_derived_and_never_training_rows(self):
        problem = parse_problem(specification([("Ann", "Ann!"), ("Bob", "Bob!")]))
        result = job(problem)
        self.assertEqual(result, job(problem))
        self.assertEqual(len(result["score_inputs"]), 24)
        self.assertFalse(set(result["diagnostic_inputs"]) & set(result["score_inputs"]))
        for encoded, output in zip(
            result["score_inputs"], result["score_outputs"], strict=True
        ):
            self.assertEqual(problem.oracle(json.loads(encoded)), output)
        self.assertIn(
            "not original real-world", result["buyer_metadata"]["derived_score_kind"]
        )

    def test_unsupported_and_insufficient_sources_remain_explicit_records(self):
        insufficient = job(parse_problem(specification([("a", "a!")], oracle=None)))
        self.assertIn("No disjoint original", insufficient["unsupported"])
        typed = job(parse_problem(specification([("a", 1)], oracle="1", output="Int")))
        self.assertIn("only String", typed["unsupported"])
        self.assertEqual(typed["buyer_metadata"]["original_constraint_count"], 1)
        with self.assertRaises(BenchmarkError):
            make_source_job(
                "bad", identity="x", family="x", source={}, derived_score_rows=-1
            )


if __name__ == "__main__":
    unittest.main()

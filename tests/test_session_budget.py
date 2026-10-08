import sqlite3
import unittest

from commerce.session_budget import SessionBudget


class SessionBudgetTests(unittest.TestCase):
    def test_pending_expenses_survive_restart_and_day_change(self):
        path = "file:nuria-session-restart?mode=memory&cache=shared"
        keeper = sqlite3.connect(path, uri=True)
        budget = SessionBudget(path, 25_000_000, 2_000_000, 200_000)
        for n in range(12):
            budget.reserve(str(n), 2_000_000, 86_399)
            budget.disclose(str(n))
        budget.db.close()
        restored = SessionBudget(path, 25_000_000, 2_000_000, 200_000)
        with self.assertRaises(ValueError):
            restored.reserve("next-day", 2_000_000, 86_401)
        self.assertEqual(restored.summary()["pending"], 24_000_000)
        restored.db.close()
        keeper.close()

    def test_repeat_settlement_is_once_only_and_unknown_cost_stays_reserved(self):
        budget = SessionBudget(":memory:", 25_000_000, 2_000_000, 200_000)
        self.assertTrue(budget.reserve("paid", 2_000_000, 10))
        self.assertFalse(budget.reserve("paid", 2_000_000, 11))
        budget.disclose("paid")
        with self.assertRaises(ValueError):
            budget.release_undisclosed("paid")
        budget.settle("paid", 10_000)
        budget.settle("paid", 10_000)
        self.assertEqual(budget.summary()["spent"], 10_000)
        with self.assertRaises(ValueError):
            budget.settle("paid", 20_000)

    def test_reopening_cannot_increase_authority(self):
        path = "file:nuria-session-authority?mode=memory&cache=shared"
        budget = SessionBudget(path, 25_000_000, 2_000_000, 200_000)
        for terms in [
            (26_000_000, 2_000_000, 200_000),
            (25_000_000, 3_000_000, 200_000),
            (25_000_000, 2_000_000, 300_000),
        ]:
            with self.assertRaises(ValueError):
                SessionBudget(path, *terms)
        self.assertEqual(budget.summary()["limit"], 25_000_000)

    def test_per_job_deadline_and_settlement_bounds(self):
        budget = SessionBudget(":memory:", 25_000_000, 2_000_000, 100)
        for amount, now in [(2_000_001, 1), (1, 100), (-1, 1), (True, 1)]:
            with self.assertRaises(ValueError):
                budget.reserve("invalid", amount, now)
        budget.reserve("valid", 10_000, 99)
        budget.disclose("valid")
        with self.assertRaises(ValueError):
            budget.settle("valid", 10_001)

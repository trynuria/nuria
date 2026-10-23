"""Upgrade/fallback and crash continuity through the real cognitive journal."""

import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cognition.adaptive_forecast import VERSION, AdaptiveForecast
from cognition.forecast import VERSION as LEGACY_VERSION
from cognition.worker import Organism

ROOT = Path(__file__).resolve().parents[1]


def fresh():
    path = ROOT / ".test-state" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def event(index, source="fixture:forecast-upgrade"):
    return {
        "id": f"upgrade-{index}-{source}",
        "source": source,
        "side": "buy" if index % 2 else "sell",
        "quote_amount": 0.5,
        "creator_fee": 0.001,
    }


class ForecastUpgradeTests(unittest.TestCase):
    def test_v1_upgrade_keeps_neural_state_cursor_genesis_and_old_metrics(self):
        directory = fresh()
        original = Organism(directory, "old-source", forecast_mode="legacy")
        for index in range(1, 4):
            original.perceive(index, event(index))
        original.save()
        neural, cursor, genesis = (
            original.brain.state_hash(),
            original.cursor,
            original.genesis,
        )
        legacy_state = original.forecasts[event(1)["source"]].state()
        original_forecast_genesis = original.forecast_genesis
        original.journal.db.close()
        upgraded = Organism(directory, "new-source")
        self.addCleanup(upgraded.journal.db.close)
        source = event(1)["source"]
        self.assertIsInstance(upgraded.forecasts[source], AdaptiveForecast)
        self.assertEqual(upgraded.forecasts[source].base.state(), legacy_state)
        self.assertEqual(upgraded.forecasts[source].metrics()["evaluated"], 0)
        self.assertEqual(upgraded.brain.state_hash(), neural)
        self.assertEqual((upgraded.cursor, upgraded.genesis), (cursor, genesis))
        self.assertEqual(upgraded.legacy_forecast_genesis, original_forecast_genesis)
        self.assertEqual(len(upgraded.journal.recent("forecast_version_change")), 1)
        effect = upgraded.perceive(4, event(4))
        old_probability = legacy_state["pending"]["probability"]
        old_repeat = legacy_state["pending"]["experts"]["repeat"]
        self.assertEqual(effect["settled_forecast_method"], LEGACY_VERSION)
        self.assertEqual(effect["settled_forecast_probability"], old_probability)
        self.assertEqual(
            effect["reward_modulation"]["signal"],
            round(old_repeat**2 - old_probability**2, 6),
        )
        upgraded.save()
        upgraded.journal.db.close()
        restored = Organism(directory, "new-source")
        self.addCleanup(restored.journal.db.close)
        self.assertEqual(restored.forecasts[source].metrics()["evaluated"], 0)
        self.assertEqual(len(restored.journal.recent("forecast_version_change")), 1)
        self.assertTrue(restored.journal.verify(full=True)["valid"])

    def test_failed_commit_recovers_prediction_memory_and_neural_state_together(self):
        directory = fresh()
        organism = Organism(directory, "new-source")
        organism.perceive(1, event(1))
        organism.save()
        before = organism.forecasts[event(1)["source"]].state()
        neural = organism.brain.state_hash()
        with patch.object(
            organism.journal, "save", side_effect=RuntimeError("fixture disk failure")
        ):
            with self.assertRaises(RuntimeError):
                organism.cycle([(2, event(2))])
        organism.journal.db.close()
        resumed = Organism(directory, "new-source")
        self.addCleanup(resumed.journal.db.close)
        self.assertEqual(resumed.forecasts[event(1)["source"]].state(), before)
        self.assertEqual(resumed.brain.state_hash(), neural)
        self.assertEqual(resumed.cursor, 1)
        resumed.perceive(2, event(2))
        self.assertEqual(
            resumed.forecasts[event(1)["source"]].metrics()["evaluated"], 1
        )

    def test_forward_fallback_retains_memory_evidence_and_uses_the_v1_shadow(self):
        directory = fresh()
        memory = Organism(directory, "new-source")
        for index in range(1, 5):
            memory.perceive(index, event(index))
        memory.save()
        source = event(1)["source"]
        state = memory.forecasts[source].state()
        neural, genesis, cursor = (
            memory.brain.state_hash(),
            memory.genesis,
            memory.cursor,
        )
        memory.journal.db.close()
        fallback = Organism(directory, "new-source", forecast_mode="legacy")
        self.assertEqual(fallback.forecasts[source].state(), state["base"])
        self.assertEqual(fallback.retained_forecast_states[source], state)
        self.assertEqual(fallback.forecast_method, LEGACY_VERSION)
        self.assertEqual(
            (fallback.brain.state_hash(), fallback.genesis, fallback.cursor),
            (neural, genesis, cursor),
        )
        fallback.perceive(5, event(5))
        fallback.save()
        fallback.journal.db.close()
        resumed = Organism(directory, "new-source")
        self.addCleanup(resumed.journal.db.close)
        self.assertEqual(resumed.forecast_method, VERSION)
        self.assertEqual(resumed.retained_forecast_states[source], state)
        # Suspended ordered state was not silently attached to a later cursor.
        self.assertEqual(resumed.forecasts[source].steps, 0)
        self.assertEqual(
            resumed.forecasts[source].base.steps, state["base"]["steps"] + 1
        )
        self.assertTrue(resumed.journal.verify(full=True)["valid"])

    def test_test_source_cannot_train_another_sources_ordered_memory(self):
        organism = Organism(fresh(), "new-source")
        self.addCleanup(organism.journal.db.close)
        organism.perceive(1, event(1, "pump"))
        before = organism.forecasts["pump"].state()
        organism.perceive(2, event(2, "test"))
        organism.perceive(3, event(3, "test"))
        self.assertEqual(organism.forecasts["pump"].state(), before)
        self.assertEqual(organism.forecasts["test"].metrics()["evaluated"], 1)


if __name__ == "__main__":
    unittest.main()

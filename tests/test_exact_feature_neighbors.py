"""Exact comparator checks; no neural or wall-time superiority assertion."""

import unittest
from unittest.mock import patch

import numpy as np

from cognition.exact_feature_neighbors import ExactFeatureNeighbors


def reference(features, labels, row, count):
    x, y = np.asarray(features, dtype=np.float64), np.asarray(labels, dtype=np.float64)
    distance = np.sum((x - row) ** 2, axis=1)
    indices = np.argsort(distance, kind="stable")[: min(count, len(x))]
    weights = 1 / (distance[indices] + 0.001)
    positive = int(y.sum())
    weights *= np.where(y[indices] == 1, 0.5 / positive, 0.5 / (len(y) - positive))
    probability = float(np.dot(weights, y[indices]) / weights.sum())
    return probability, indices.tolist(), distance[indices].tolist()


class ExactFeatureNeighborsTests(unittest.TestCase):
    def test_random_ties_fractional_and_binary_queries_match_full_sort(self):
        rng = np.random.default_rng(20261028)
        for n in (2, 9, 65):
            for dimension in (1, 7, 130):
                x = rng.integers(0, 2, (n, dimension)).astype(float)
                if dimension > 1:
                    x[:, -1] = rng.random(n)
                y = (np.arange(n) % 2).astype(float)
                queries = rng.integers(0, 2, (8, dimension)).astype(float)
                queries[::3, 0] = 0.25
                for k in (1, 9, 128):
                    model = ExactFeatureNeighbors(x, y, neighbors=k)
                    for row in queries:
                        actual = model.neighbors(row)
                        expected, indices, distances = reference(x, y, row, k)
                        self.assertEqual(actual["indices"], indices)
                        self.assertEqual(actual["squared_distances"], distances)
                        self.assertEqual(actual["probability"].hex(), expected.hex())

    def test_lower_bound_equality_keeps_earlier_global_index(self):
        x, y = [[1.0, 0.0], [0.0, 1.0], [0.0, 0.5]], [0, 1, 0]
        model = ExactFeatureNeighbors(x, y, neighbors=2)
        actual = model.neighbors([0.0, 0.0])
        self.assertEqual(actual["indices"], [2, 0])
        self.assertEqual(actual["squared_distances"], [0.25, 1.0])

    def test_fractional_binary_coordinate_uses_exact_fallback(self):
        model = ExactFeatureNeighbors([[0, 0], [1, 0], [0, 1]], [0, 1, 0])
        result = model.neighbors([0.25, 0.0])
        self.assertEqual(result["indices"], [0, 1, 2])
        self.assertEqual(model.statistics()["fallback_queries"], 1)

    def test_no_binary_reference_coordinates_is_supported(self):
        x, y = [[0.1, 0.3], [0.2, 0.4], [0.3, 0.8]], [0, 1, 0]
        model = ExactFeatureNeighbors(x, y)
        actual = model.neighbors([0.2, 0.3])
        expected, indices, _ = reference(x, y, [0.2, 0.3], 9)
        self.assertEqual(actual["indices"], indices)
        self.assertEqual(actual["probability"].hex(), expected.hex())
        self.assertEqual(model.statistics()["binary_coordinates"], 0)

    def test_reference_data_are_detached_and_read_only(self):
        x, y = np.array([[0.0], [0.5], [1.0]]), np.array([0, 1, 0])
        model = ExactFeatureNeighbors(x, y)
        expected = model.score([[0.5]])
        x[:] = 1
        y[:] = 1
        model.reset()
        np.testing.assert_array_equal(model.score([[0.5]]), expected)
        with self.assertRaises(ValueError):
            model._x[0, 0] = 0.5
        with self.assertRaises(ValueError):
            model._y[0] = 1

    def test_cache_is_bounded_resettable_and_output_is_not_aliased(self):
        model = ExactFeatureNeighbors([[0], [0.5], [1]], [0, 1, 0], cache_capacity=2)
        result = model.score([[0], [0.1], [0.2]])
        self.assertEqual(model.statistics()["cache_entries"], 2)
        self.assertEqual(model.statistics()["cache_evictions"], 1)
        expected = float(result[-1])
        result[-1] = 0.123
        self.assertEqual(model.score([[0.2]])[0], expected)
        self.assertEqual(model.statistics()["cache_hits"], 1)
        model.reset()
        for key in ("cache_entries", "cache_hits", "cache_misses", "cache_evictions"):
            self.assertEqual(model.statistics()[key], 0)

    def test_zero_capacity_disables_prediction_caching(self):
        model = ExactFeatureNeighbors([[0], [1]], [0, 1], cache_capacity=0)
        model.score([[0.25], [0.25]])
        stats = model.statistics()
        self.assertEqual(stats["cache_entries"], 0)
        self.assertEqual(stats["cache_hits"], 0)
        self.assertEqual(stats["cache_misses"], 2)

    def test_memory_statistics_distinguish_references_and_auxiliary_index(self):
        model = ExactFeatureNeighbors([[0, 0], [1, 0], [0, 1]], [0, 1, 0])
        stats = model.statistics()
        self.assertEqual(stats["reference_feature_bytes"], 3 * 2 * 8)
        self.assertEqual(stats["reference_label_bytes"], 3 * 8)
        self.assertEqual(
            stats["total_owned_ndarray_nbytes"],
            stats["reference_feature_bytes"]
            + stats["reference_label_bytes"]
            + stats["auxiliary_index_bytes"],
        )
        self.assertIn("process RSS excluded", stats["memory_scope"])

    def test_class_balance_matches_original_control(self):
        x, y = [[0], [0.25], [0.5], [0.75], [1]], [0, 0, 0, 0, 1]
        model = ExactFeatureNeighbors(x, y, neighbors=5)
        expected, _, _ = reference(x, y, [0.6], 5)
        self.assertEqual(model.score([[0.6]])[0].hex(), expected.hex())

    def test_invalid_training_types_bounds_and_labels_are_rejected(self):
        cases = [
            ([[0], [True]], [0, 1]),
            ([[0], ["1"]], [0, 1]),
            ([[0], [float("nan")]], [0, 1]),
            ([[0], [2]], [0, 1]),
            ([[0], [1]], [False, 1]),
            ([[0], [1]], [0, "1"]),
            ([[0], [1]], [0, 0]),
            ([[0], [1]], [0, 0.5]),
            ([[0]], [1]),
        ]
        for x, y in cases:
            with self.assertRaises(ValueError):
                ExactFeatureNeighbors(x, y)
        for kwargs in (
            {"neighbors": True},
            {"neighbors": 0},
            {"neighbors": 129},
            {"cache_capacity": True},
            {"cache_capacity": 4097},
        ):
            with self.assertRaises(ValueError):
                ExactFeatureNeighbors([[0], [1]], [0, 1], **kwargs)
        with patch("cognition.exact_feature_neighbors.MAX_MATRIX_VALUES", 3):
            with self.assertRaises(ValueError):
                ExactFeatureNeighbors([[0, 0], [1, 1]], [0, 1])

    def test_invalid_queries_cannot_enter_cache(self):
        model = ExactFeatureNeighbors([[0], [1]], [0, 1])
        for values in ([], [[0, 1]], [[True]], [["0"]], [[float("inf")]], [[-0.1]]):
            with self.assertRaises(ValueError):
                model.score(values)
        self.assertEqual(model.statistics()["cache_entries"], 0)

    def test_reference_identity_is_canonical_across_numeric_input_types(self):
        a = ExactFeatureNeighbors([[0], [1]], [0, 1])
        b = ExactFeatureNeighbors(np.array([[0.0], [1.0]]), np.array([0.0, 1.0]))
        self.assertEqual(a.reference_sha256, b.reference_sha256)
        changed = ExactFeatureNeighbors([[0], [0.5]], [0, 1])
        self.assertNotEqual(a.reference_sha256, changed.reference_sha256)


if __name__ == "__main__":
    unittest.main()

"""Exact indexed neighbors for bounded normalized feature observations.

Entirely binary reference coordinates supply integer Hamming lower bounds.
Only groups whose lower bound exceeds the current exact kth distance can be
excluded. Admitted rows use the original float64 difference/square/sum; equal
distances retain global reference order. Fractional queries on binary reference
coordinates fall back to full distance evaluation. No approximate neighbors,
new observations or fitted neural mechanism are introduced.
"""

from __future__ import annotations

import hashlib
import struct
import time
from collections import OrderedDict
from numbers import Real

import numpy as np

VERSION = "nuria.exact-feature-neighbors.v1"
MAX_RECORDS = 65536
MAX_DIMENSION = 512
MAX_MATRIX_VALUES = 16777216
MAX_BATCH = 4096
MAX_CACHE = 4096


def _numbers(values, shape):
    if isinstance(values, np.ndarray) and values.dtype.kind in "iuf":
        raw = values
    else:
        raw = np.asarray(values, dtype=object)
        if any(
            not isinstance(v, Real) or isinstance(v, (bool, np.bool_)) for v in raw.flat
        ):
            raise ValueError(
                "Neighbor data require real numbers, not booleans or strings"
            )
    if raw.shape != shape:
        raise ValueError("Neighbor data have inconsistent dimensions")
    result = np.array(raw, dtype=np.float64, order="C", copy=True)
    if not np.isfinite(result).all() or np.any(result < 0) or np.any(result > 1):
        raise ValueError("Neighbor observations must be finite and normalized in [0,1]")
    return result


class ExactFeatureNeighbors:
    """Optional conventional comparator with immutable copied reference data.

    Class weighting, distance arithmetic and stable ties match the simple
    normalized-feature kNN control. There is no online fitting. Reset the
    bounded prediction cache between jobs when measuring a cold job. Read-only
    NumPy arrays prevent ordinary accidental mutation; this is not an authority
    boundary against a caller deliberately replacing internal Python objects.
    """

    def __init__(
        self, reference_features, reference_labels, *, neighbors=9, cache_capacity=4096
    ):
        if (
            type(neighbors) is not int
            or not 1 <= neighbors <= 128
            or type(cache_capacity) is not int
            or not 0 <= cache_capacity <= MAX_CACHE
        ):
            raise ValueError("Neighbor and cache limits must be bounded integers")
        raw = np.asarray(reference_features)
        if (
            raw.ndim != 2
            or not 2 <= len(raw) <= MAX_RECORDS
            or not 1 <= raw.shape[1] <= MAX_DIMENSION
            or raw.size > MAX_MATRIX_VALUES
        ):
            raise ValueError("Reference dimensions exceed the finite matrix bounds")
        self._x = _numbers(reference_features, raw.shape)
        self._y = _numbers(reference_labels, (len(self._x),))
        if not np.all((self._y == 0) | (self._y == 1)):
            raise ValueError("Neighbor labels must be binary")
        self.records, self.dimension = self._x.shape
        self.positive = int(self._y.sum())
        if not 0 < self.positive < self.records:
            raise ValueError("Class-balanced neighbors require both label classes")
        self.neighbor_count, self.cache_capacity = neighbors, cache_capacity
        self._x.flags.writeable = self._y.flags.writeable = False
        identity = hashlib.sha256(
            VERSION.encode() + struct.pack("<II", self.records, self.dimension)
        )
        identity.update(self._x.astype("<f8", copy=False).tobytes())
        identity.update(self._y.astype(np.uint8).tobytes())
        self.reference_sha256 = identity.hexdigest()
        started = time.process_time()
        self._binary_mask = np.all((self._x == 0) | (self._x == 1), axis=0)
        packed = np.packbits(self._x[:, self._binary_mask].astype(np.uint8), axis=1)
        self._signatures, inverse = np.unique(packed, axis=0, return_inverse=True)
        order = np.argsort(inverse, kind="stable")
        sizes = np.bincount(inverse, minlength=len(self._signatures))
        self._groups = tuple(np.split(order, np.cumsum(sizes)[:-1]))
        self._binary_mask.flags.writeable = self._signatures.flags.writeable = False
        for group in self._groups:
            group.flags.writeable = False
        self.index_build_cpu_seconds = time.process_time() - started
        self._cache = OrderedDict()
        self.reset()

    def reset(self):
        """Clear only ephemeral predictions and counters; reference data persist."""
        self._cache.clear()
        self.cache_hits = self.cache_misses = self.cache_evictions = 0
        self.reference_rows_evaluated = self.group_levels_evaluated = 0
        self.fallback_queries = 0

    def _select_full(self, row):
        distances = np.sum((self._x - row) ** 2, axis=1)
        k = min(self.neighbor_count, self.records)
        cutoff = np.partition(distances, k - 1)[k - 1]
        lower = np.flatnonzero(distances < cutoff)
        ties = np.flatnonzero(distances == cutoff)[: k - len(lower)]
        selected = np.concatenate((lower, ties))
        indices = selected[np.lexsort((selected, distances[selected]))]
        self.reference_rows_evaluated += self.records
        return indices, distances[indices]

    def _nearest(self, row):
        binary_query = row[self._binary_mask]
        if not len(binary_query) or not np.all(
            (binary_query == 0) | (binary_query == 1)
        ):
            self.fallback_queries += 1
            indices, distances = self._select_full(row)
        else:
            signature = np.packbits(binary_query.astype(np.uint8))
            bounds = np.bitwise_count(self._signatures ^ signature).sum(axis=1)
            indices = np.empty(0, dtype=np.int64)
            distances = np.empty(0, dtype=np.float64)
            k = min(self.neighbor_count, self.records)
            for bound in np.unique(bounds):
                # Equality must remain eligible for global-index tie breaking.
                if len(indices) >= k and bound > distances[-1]:
                    break
                group_ids = np.flatnonzero(bounds == bound)
                admitted = np.concatenate([self._groups[int(g)] for g in group_ids])
                exact = np.sum((self._x[admitted] - row) ** 2, axis=1)
                self.reference_rows_evaluated += len(admitted)
                self.group_levels_evaluated += 1
                candidates = np.concatenate((indices, admitted))
                candidate_distances = np.concatenate((distances, exact))
                chosen = np.lexsort((candidates, candidate_distances))[:k]
                indices, distances = candidates[chosen], candidate_distances[chosen]
            if len(indices) != k:
                raise ValueError(
                    "Exact neighbor traversal did not produce the full set"
                )
        weights = 1 / (distances + 0.001)
        weights *= np.where(
            self._y[indices] == 1,
            0.5 / self.positive,
            0.5 / (self.records - self.positive),
        )
        probability = float(np.dot(weights, self._y[indices]) / weights.sum())
        return probability, indices, distances

    def neighbors(self, features):
        """Inspect one exact neighbor set, bypassing the prediction cache."""
        row = _numbers(features, (self.dimension,))
        probability, indices, distances = self._nearest(row)
        return {
            "probability": probability,
            "indices": indices.tolist(),
            "squared_distances": distances.tolist(),
        }

    def score(self, features):
        raw = np.asarray(features)
        if (
            raw.ndim != 2
            or not 1 <= len(raw) <= MAX_BATCH
            or raw.shape[1] != self.dimension
        ):
            raise ValueError("Scoring requires a finite batch of matching dimensions")
        batch = _numbers(features, raw.shape)
        results = []
        for row in batch:
            key = tuple(row.tolist())
            if key in self._cache:
                value = self._cache.pop(key)
                self._cache[key] = value
                self.cache_hits += 1
            else:
                value, _, _ = self._nearest(row)
                self.cache_misses += 1
                if self.cache_capacity:
                    self._cache[key] = value
                    if len(self._cache) > self.cache_capacity:
                        self._cache.popitem(last=False)
                        self.cache_evictions += 1
            results.append(value)
        return np.asarray(results)

    __call__ = score

    def statistics(self):
        """Separate auxiliary index bytes from reference data and Python memory."""
        arrays = (self._x, self._y, self._binary_mask, self._signatures, *self._groups)
        owners = {}
        for array in arrays:
            owner = array
            while isinstance(owner.base, np.ndarray):
                owner = owner.base
            owners[id(owner)] = owner.nbytes
        references = self._x.nbytes + self._y.nbytes
        total = sum(owners.values())
        return {
            "schema": VERSION,
            "reference_sha256": self.reference_sha256,
            "records": self.records,
            "dimension": self.dimension,
            "neighbors": self.neighbor_count,
            "positive_labels": self.positive,
            "binary_coordinates": int(self._binary_mask.sum()),
            "binary_groups": len(self._groups),
            "reference_feature_bytes": self._x.nbytes,
            "reference_label_bytes": self._y.nbytes,
            "auxiliary_index_bytes": total - references,
            "total_owned_ndarray_nbytes": total,
            "index_build_cpu_seconds": self.index_build_cpu_seconds,
            "cache_capacity": self.cache_capacity,
            "cache_entries": len(self._cache),
            "cache_maximum_feature_values": self.cache_capacity * self.dimension,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_evictions": self.cache_evictions,
            "fallback_queries": self.fallback_queries,
            "reference_rows_evaluated": self.reference_rows_evaluated,
            "group_levels_evaluated": self.group_levels_evaluated,
            "memory_scope": "Retained NumPy allocations counted once; Python objects, LRU entries, temporary allocations and process RSS excluded",
        }

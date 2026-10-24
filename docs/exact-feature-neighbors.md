# Exact neighbors from binary feature groups

The optional [ExactFeatureNeighbors](../cognition/exact_feature_neighbors.py)
accelerates the conventional nearest-neighbor comparator without changing its
distance definition, class weighting or tie order. It does not change the
default semantic model or supply a neural learning result.

## Why a group can be skipped

Many property-signature coordinates contain only zero or one. Split a reference
and query into those binary coordinates `b` and the remaining coordinates `c`:

```text
squared_distance(query, reference)
    = Hamming(b_query, b_reference)
      + squared_distance(c_query, c_reference)
    >= Hamming(b_query, b_reference)
```

References sharing a binary signature form one group. The integer Hamming
distance supplies a lower bound for every member. Groups are visited in
increasing lower-bound order. Once the bound exceeds the current exact kth
distance, every remaining group can be excluded. Equality remains eligible:
an equal-distance reference with an earlier global index can change a stable
tie.

Admitted references use the original float64 subtraction, square and sum over
the complete coordinate vector. This avoids changing close neighbors through
a norm/dot-product approximation. The final neighbors are sorted by distance
and original reference index, preserving the original weighted-probability
arithmetic order.

A query with a fractional value in a reference-binary coordinate falls back to
complete distance evaluation. The same happens when there are no binary
reference coordinates. The implementation does not assume that an unseen
query must preserve the training data's discrete pattern.

## Bounds and state

Inputs must be finite real observations in `[0,1]`; labels are binary and both
classes must be present. Limits are 65,536 reference rows, 512 coordinates,
16,777,216 total matrix values, 4,096 queries per batch and at most 4,096 cached
predictions. Reference arrays are copied and marked read-only. A reset clears
ephemeral predictions and counters while retaining reference data.

The prediction cache is useful only for an immutable fitted reference set. It
cannot survive a reference change. This class has no online fit operation; a
new reference set requires a new instance. Read-only flags protect against
ordinary accidental mutation, not a caller deliberately replacing internal
Python objects.

Statistics distinguish reference storage, auxiliary index storage and their
combined retained NumPy allocations. Python objects, cache entries, temporary
allocations and process RSS are excluded from those array-byte measurements.

## Measured comparison and its limits

The [complete paired development replay](../research/neural-routing-loop/development/semantic-knn-exact-01/full-both-folds-exact-replay.json)
compares the original comparator with the private indexed prototype across 384
search jobs. Every original non-CPU program, status, work and outcome field
matches; the indexed traces add their own explicit instrumentation. Both accept
333 jobs with one wrong proposal. Recorded search CPU changes from
1,064.652717 to 42.821022 seconds, a 24.86285-fold improvement in this local run.
Index construction is separately recorded. Teacher acquisition and model
fitting are outside that search-only ratio.

The optional public implementation was separately checked against 4,739
recorded original score queries: every score matched bit-for-bit. This is not
a full repeat of the 384-job timing benchmark using the public port. Timing
depends on data, hardware, grouping and cache reuse; no general product
latency or throughput multiplier is established.

For the retained 29,152-row, 130-coordinate fixture, reference features occupy
30,318,080 bytes, labels 233,216 bytes and the auxiliary group index 348,786
bytes: 30,900,082 retained NumPy bytes in total. Calling the auxiliary index
the complete model memory would omit almost all of its storage.

The conventional comparator still outperforms the spiking controller in the
[operation-selection experiment](semantic-operation-selection.md). Making that
comparator exact and faster strengthens the test; it does not establish a
neural advantage.

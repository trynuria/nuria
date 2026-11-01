# Timing-sensitive measurement

The unchanged task measures actual sleep durations. Its original checker imposes
narrow bounds on actual wall time. Python documents that system scheduling can
make sleep last longer than requested.

The first independent complete replay changed the Astra verdict from false to
true. Original programs, original checker and original scores remain unchanged.

The protocol was fixed before the following rotated physical observations:

| Program             | Original checker passes / 3 |
| ------------------- | --------------------------- |
| Unchanged reference | 2 / 3                       |
| Sol-low             | 3 / 3                       |
| Astra-medium        | 3 / 3                       |

Four scripted-clock traces then checked the actual mean and population standard
deviation, including legal oversleep outside the benchmark mean bound. Both
model programs and the reference passed. A constant-answer control failed. These
are calculation fixtures, not model or neural intelligence results.

`characterization.json` retains all 13 initial executions. The separate complete
negative-result qualification reruns all 156 programs and the fixed controls;
its additional observations remain in `../negative-qualification.json`.
`sensitivity.json` retains every possible timing label for both conditions. All
four assignments still fail the original promotion gate; the largest possible
accuracy difference is 1.28 percentage points.

The default strict replay command still reports a mismatch. The separate
negative qualifier exposes timing uncertainty, requires every other native result
to match, requires the fixed semantic controls and cannot promote any policy.
It verifies this limited negative conclusion, not exact binary reproduction.
No benchmark case or acceptance assertion is removed.

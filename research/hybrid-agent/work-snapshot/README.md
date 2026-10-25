# A question over recent Nuria work

This package retains the source and independent checker for one concrete
operational question: group recent reported work by provider, distinguish
delivered and positive-outcome tasks, retain unknown outcomes and separate
included computation from reported monetary amounts.

The expected result was frozen before the useful-source model job. This preview
contains no model answer, final grade or claim that the job has completed.

Run the standard-library checks without inference or a network connection:

```sh
python -B check_recent_work.py
python -B -m unittest test_recent_work
```

`action-task-projection.json` contains only the 40 original action-task rows and
the fields needed by the frozen JSON grouping, plus task IDs for correspondence.
`registered/` is a byte-for-byte copy of the original 40-row analytical SQLite
projection, documents and relative-path registry. It preserves the exact source
file identities used by the job. `question-and-check.json` records the question,
expected result and original frozen-checker identity. The checker independently
groups JSON, checks row correspondence and reproduces the grouping in read-only
SQLite. It does not execute generated source code.

`api-provenance.json` preserves the original HTTPS fetch time, endpoint, response
hash and safe response metadata. `derivation.json` preserves original and derived
identities and describes the allowlist. The full API response remains private:
its policy, token configuration, wallet fields and unrelated branches are not in
this package. The published projection cannot reconstruct the unpublished raw
response; its hash is an identity reference rather than an independent witness.

These are **40 recent server-reported tasks**, not lifetime provider performance
or a complete payment history. `delivery_passed` maps the existing
`acceptance.passed` flag. It does not invent an independent semantic grade; all
40 original `semantic_accepted` values are unknown. A null utility is unknown,
whereas zero is a known nonpositive value. The two included-capacity providers
report zero settled monetary amounts. Included computation is not an external
purchase, and a server-reported settlement is not chain-payment verification.

This source/checker package can support a narrowly scoped claim once an actual
answer and independent terminal check exist. It does not establish a neural
advantage, broad skill transfer or any new scientific result by itself.

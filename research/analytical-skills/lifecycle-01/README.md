# From an accepted model query to a recurring report

This replay takes the previously accepted semantic-input analyst's SQL from its
retained work-trial package, enrolls it as a checked report, and executes it through
the host worker CLI. The original model query and its accepted outcome remain
unchanged.

The historical report passes its independent raw-row check. Repeating the same
job returns the same retained artifact with **zero workspace queries and zero
independent checks**. Two declared synthetic source revisions then return their
own checked answers: a four-row boundary/NULL fixture and an empty ledger.

These results exercise a complete enrollment and execution lifecycle. The source
query was already accepted, and the new inputs are declared deterministic
fixtures. This is exact report reuse with checked fresh outputs; it is not a new
reasoning benchmark, autonomous report discovery or evidence of neural advantage.
There are no model calls and no neural credit in this replay.

## Reproduce

Run from a checkout containing the retained work-trial package and use a new
workspace directory. The replayer extracts only four allowlisted JSON/SQLite
members after verifying their public archive and manifest identities. It never
loads a neural checkpoint.

```sh
python -m scripts.verify_analytical_report_lifecycle \
  --repository . \
  --workspace /absolute/path/to/new-lifecycle-replay \
  --output /absolute/path/to/new-lifecycle-verdict.json
```

The workspace retains the prospective synthetic-input plan, copied origin
evidence, private enrollment, worker artifacts and source revisions. The replayer
checks the original evidence again afterward. Existing workspaces and output
files are preserved rather than overwritten.

## Evidence and interpretation

- [Replay verdict](replay-result.json): checked historical report, identical-job
  replay, two changed-source reports and unchanged original evidence.
- [Manifest and plan](manifest.json): original identities, implementation hashes,
  explicit synthetic inputs and the earlier checker-shape diagnostic.
- [Original work trials](../../hybrid-agent/work-trials/README.md): the accepted
  model-generated query, source and procedural-memory history.

The historical answer covers 40 recent server-reported tasks. Delivery flags and
positive utility do not establish independent semantic acceptance, and recorded
payment amounts do not attest external payments. The synthetic revisions are
separate from real work and are never added to the original ledger.

The initial development integration used a checker that compared a list of
column names with the integer expected column count. Its rejected result remains
retained. A separate follow-up corrected that input-shape error without changing
the query or expected rows. The manifest includes an explicitly labelled
projection and original record hashes; it does not claim those projections
reconstruct complete private development records.

Hashes identify records and local replay checks their consistency. They do not
provide an independent execution attestation or authenticate a host's authority.

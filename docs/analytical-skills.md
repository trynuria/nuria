# Executable analytical skills

An accepted report should be usable again without copying its old answer. The
optional analytical skill library promotes SQL from a checked analytical episode
and executes that query on a new, compatible source snapshot. Each new output
still needs an independent check.

The existing procedural memory retains accepted SQL and textual annotations for
the reasoner. This addition makes exact report reuse executable. It does not
train the language model, infer a new report from a question or prove a neural
advantage.

## From an outcome to a report

Promotion requires the actual `ProceduralMemory` episode, its committed accepted
settlement and the retained receipt of its own successful final query. The library
checks their identities against the original registered database and the current
workspace implementation. A model-written procedure or an `accepted` flag in a
tool argument cannot promote a report.

The host assigns a stable report identity and an independent checker identity.
These are trusted enrollment choices. Hashes identify retained evidence; they do
not authenticate a remote checker or prove that its verdict was correct.

```mermaid
flowchart LR
    A[Executed query and checked outcome] --> B[Exact SQL report skill]
    B --> C[Compatible immutable source revision]
    C --> D[Fresh bounded query]
    D --> E[Independent source-bound check]
    E -->|Accepted| F[Retained result]
    E -->|Rejected| G[Retain failure and revoke reuse]
```

## New data, the same question

A report contract binds the database identity, complete SQL schema, static
knowledge document hashes and workspace implementation. Rows and the dataset
revision may change. Schema definitions, business meanings, document contents or
the host report identity may not change silently.

The executor reads the new registered source, inspects its schema and runs the
retained SQL through `AnalystWorkspace`. It never returns cached answer rows.
Successful query execution produces a pending result, not a correctness verdict.
The trusted host checker receives that exact execution and source identity before
its verdict is committed.

A rejected output revokes the skill. Checker exceptions leave it pending.
Repeated settlement returns the existing verdict. Repeated execution of the same
job returns its existing execution; a changed duplicate or an interrupted
reservation is refused.

## Finite operation

The library defaults to 32 skills and 1,024 retained events. It reserves journal
space for outstanding verdicts before starting a new query. The store has one
writer and a private SQLite journal. These limits cover the library's record
count, while the existing workspace supplies query deadlines, VM-step, row and
output-byte limits.

This version supports exact SQL with the default workspace limits and static
document contracts. It does not infer typed parameters, import every historical
episode, reconcile unknown execution automatically, roll history over or enroll
itself in the production worker. Financial authority and the original neural
histories are unchanged.

## Run an enrolled report

An explicit host enrollment command can derive the bank, source and checker
identities from a retained accepted episode. It preserves the original memory,
promotes the episode's own executed SQL and independently checks the originating
report under the reviewed profile.

```sh
python -m scripts.enroll_analytical_report \
  --output /absolute/path/to/new-private-enrollment \
  --memory-snapshot /absolute/path/to/retained-memory.sqlite \
  --episode-id FULL_EPISODE_SHA256 \
  --execution-receipt /absolute/path/to/retained-query-receipt.json \
  --registry /absolute/path/to/original/registry.json \
  --db-id work \
  --report-id provider-outcomes-report-v1

python -m scripts.run_analytical_report \
  --enrollment /absolute/path/to/new-private-enrollment/enrollment.json \
  --registry /absolute/path/to/compatible/registry.json \
  --job-id report-001
```

The input is a retained snapshot, rather than an invitation to modify a running
memory. Nonempty write-ahead or rollback journals require a consistent host
snapshot first. The command refuses uncertain inputs, leaves the original files
unchanged and retains its own copied history and origin checks. Report semantics
remain a host enrollment choice; the command does not discover arbitrary new
report meanings from a prompt.

The optional host worker runs one already promoted skill without starting a model
service. The host supplies its source, checker, report, database and skill
identities, plus a private state directory. Enrollment remains explicit.

```sh
python -m scripts.run_analytical_report \
  --registry /absolute/path/to/registry.json \
  --state /absolute/path/to/private/report-worker \
  --skill-bank /absolute/path/to/existing/skills.sqlite \
  --source-id SOURCE_ID \
  --checker-id CHECKER_ID \
  --report-id REPORT_ID \
  --db-id work \
  --skill-id FULL_SKILL_SHA256 \
  --job-id report-001
```

Its reviewed profile groups individual `task_outcomes` rows independently in
Python. It checks provider counts, exact delivery flags, positive and missing
utility, included capacity and positive recorded micro-USD amounts. Delivery and
utility do not establish semantic acceptance; a recorded amount does not attest
an external payment. The checker reads the source rows, rather than using the
candidate query as its oracle.

A repeated job with the same immutable source returns its retained artifact
without another workspace query or checker call. A new source revision needs a
new job identity. Changed duplicates are refused. An absent or revoked skill is
unavailable; incompatible semantics and uncertain execution remain explicit.
There is no automatic model fallback or retry after an interrupted operation.

The independent checker is finite: at most 10,000 rows, 200 provider groups,
2 MiB of row data and its declared time/work bounds. Exceeding a bound leaves the
result pending. Workspace queries and the raw-row check are separate measured
work. These limits are operational bounds, not evidence that the service can
process unlimited history.

The worker gives report reuse no neural decision origin or neural credit.
`SourceSeriesHost` and the commerce provider retain their existing enrollment and
credit rules. The report worker is a separate host entry point, rather than a
transparent replacement inside those paths.

## Evidence and interpretation

The module tests exercise real SQLite sources, promotion provenance, fresh rows,
source incompatibility, rejected outputs, recovery and finite limits. A separate
generated-ledger course compares the executor with exact SQL caching and an
independently written conventional report template. All methods receive the same
rows and perform actual queries. No language-model calls are invented for a
baseline.

The course separates development from a pinned challenge protocol. Generated
ledgers, deterministic checks and known conventional queries are labelled as
such. Correct checked reuse is a useful software capability; parity with SQL
caching is not evidence of improved reasoning or intelligence. Schema inspection
also adds real work and is counted separately.

The live site design and production enrollment remain unchanged.

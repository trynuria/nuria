# Analyze recorded work

The hybrid analyst can answer questions about a retained Nuria work response.
This gives the reasoning path an operational use: inspect scoped outcomes and
identify missing evidence before making another resource decision.

## Prepare an immutable source

Retain the public `/api/work` JSON response and its fetch time outside the public
repository. The following command makes a private, source-bound projection:

```sh
python -m scripts.register_work_snapshot \
  /path/to/retained-work.json \
  /path/to/new-private-work-source
```

It opens no network connection, changes no application state and does not
overwrite an existing source. The registry includes the exact database and
document hashes. The original response stays in private state, alongside its
SHA256 identity.

The projection has two tables. `task_outcomes` contains only task identities,
providers, states and separate delivery, settlement and outcome fields.
`snapshot_context` records the source's reported update time, scope and coverage.
It omits wallet configuration, provider credentials and unrelated application
state from the model's registered tools.

## Ask a scoped question

Use the [registered analyst command](hybrid-usage.md) with database ID `work`.
For example:

> For each provider in this snapshot, count tasks, passed deliveries, positive
> measured outcomes and tasks with no measured outcome. Distinguish included
> computation from an external purchase. State the coverage limit.

The answer must come from its own successful executed query. Keep the returned
SQL, rows and source receipts together. A useful-looking query does not qualify
its own correctness: an independently enrolled checker must compare its answer
against the actual question and snapshot before it can earn learning credit.

## Interpret the evidence

- The response is a recent projection. It is not a lifetime provider comparison.
- `NULL` means absent or unknown. It is not zero, failure or success.
- Delivery acceptance and measured usefulness have different meanings.
- `included_capacity` and a zero recorded amount describe local computation.
  They do not establish that Nuria hired or paid an outside provider.
- A server-reported verification flag and local hash consistency do not
  independently verify a blockchain transfer.
- Snapshot results describe the retained source at its reported time. They
  cannot establish present service health or complete financial history.

The optional commerce adapter can enroll this question and its host checker as
a zero-payment analytical task. That integration remains separate from model
hosting and production activation. No answer from this tool grants financial
authority or changes the existing spending policy.

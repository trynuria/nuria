# Ask a registered-source question

The analyst can inspect a registered SQLite database, execute bounded read-only
queries and retain its answer with the source and execution receipts. A completed
query is a delivered result. Its correctness remains unverified until an
independent host checker qualifies it.

## Run one question

Start a compatible reasoning model separately on a literal loopback address.
This command does not start a server, install a model or configure hosting.

From the repository root:

```sh
python -m scripts.ask_grounded_analyst \
  --registry /path/to/registered-source/registry.json \
  --state /path/to/private/analyst-state \
  --model-identity 'model-repository@immutable-revision' \
  --job-id receipt-audit-001 \
  --db-id receipts \
  --question 'Which providers have delivered work without an independently measured outcome?'
```

Replace the source paths, database name and declared model identity with those of
the actual enrolled runtime. The default endpoint is
`http://127.0.0.1:19487/v1/chat/completions`; `--endpoint` accepts another literal
loopback HTTP address. Remote endpoints, inherited proxies and redirects are
refused. The server's model alias defaults to `default_model` and can be set with
`--model-alias`.

The declared model identity binds persistent state. It records the operator's
configuration; a response alias does not attest the server's weights.

## Register the source

The registry uses `nuria.analyst-workspace.v1`. Each database has a stable ID, a
relative SQLite path and its SHA256. Supplied `business`, `columns` and `schema`
documents also have relative paths and SHA256 identities. Every registered file
must stay beneath the registry directory. The revision is an immutable source
commit or a `sha256:` source-manifest identity.

The default workspace accepts databases up to 5 MiB and individual documents up
to 128 KiB. Each query is read-only and limited to one million SQLite progress
callbacks, 200 rows, 64 KiB of returned data and a two-second worker deadline.
An incomplete query result cannot be submitted as a complete answer. Only the
registered database and supplied knowledge are available to the model; there is
no shell, unrestricted filesystem, wallet or purchasing tool.

## Read the result

Standard output contains JSON with:

- `delivery_complete`: whether the answer has a complete successful own query.
- `final_sql`, `columns` and `rows`: the actual executed answer, when delivered.
- `source` and `execution_receipt_sha256`: source and execution identities.
- `episode_id` and `proposal_sha256`: the retained procedural-memory proposal.
- `model_usage`: observed prompt and completion tokens, including known usage on
  protocol failures; ambiguous additional usage remains explicit.
- `reserved_completion_tokens` and `unresolved_completion_ceiling`: the attempt's
  full allowance and the conservative additional liability when usage is unknown.
- `semantic_accepted`: always `null` in this command.

The full analytical artifact and a separate result file remain in private state.
Neither stdout nor a retained artifact should be published automatically: they
can contain the registered source's data.

Exit status is `0` for a delivered query, `3` for a completed attempt without a
delivered answer, `2` for a validation or host error, and `130` after interruption.
A zero exit status establishes execution, not semantic correctness.

## Bounds and recovery

Defaults are 12 model turns, 6,000 total completion tokens, 1,536 completion
tokens per turn and 12 tool calls. The corresponding `--max-*` options can lower
these limits within the reviewed bounds. Prompt consumption is measured
separately; equal completion ceilings are not equal total computation.
`--no-thinking` disables the runtime's requested thinking mode.

Use a new job ID for each deliberate attempt. Before inference, the command
creates an exclusive private reservation. A duplicate ID refuses to run, including
after a crash, invalid responses or an interrupted first request. Inspect the
original artifacts and account for unresolved usage before deliberately starting
another attempt. An interruption does not prove that inference consumed nothing.

The state directory must be private, with mode `0700`; created files use `0600`.
Source registry identity and model identity are fixed for that state. A changed
registry or model requires a separate state directory. Existing attempts are
preserved and never overwritten or automatically removed.

`--memory-mode` accepts `none`, `episode` and `procedure`; the default is
`procedure`. Retaining an ungraded proposal does not qualify it as a successful
procedure. There is no oracle argument, automatic acceptance or grading flag.
Separate host-owned qualification uses the
[procedural-memory interface](procedural-memory.md); the model cannot register
its own checker or award itself learning credit.

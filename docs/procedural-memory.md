# Procedural memory

The optional [memory module](../cognition/procedural_memory.py) retains an agent's
own SQL tool experience and separates a proposed procedure from an independently
checked terminal result. It executes no supplied SQL and changes no live worker.

## Evidence boundary

The host creates an episode before execution, binding the job, question and
workspace context hash to the exact dataset source, model identity and tool
version. A model identity must identify its pinned revision and quantization,
rather than a mutable display alias. The runtime authenticates those identities;
this module enforces their exact persistence and refuses a changed binding.

The host records each actual tool result under a distinct action identity.
Non-query tools use `sql=None`. A query receipt records the submitted SQL, actual
completion or error, result hash and declared cost units. These units retain the
caller's measurement convention; they are not automatically native CPU work or
money. Exact retries do not create a second observation. Changed retries fail.

An agent may propose a procedure containing its final SQL, a short description,
rationale and preconditions. The SQL must match a successful own execution in that
episode. The proposal freezes its action trace. A successfully executed query is
still only a proposal: it can return a plausible but incorrect answer.

Only the host can settle the proposal. It supplies a Python checker callable
outside the agent's JSON tool interface. The checker receives the committed
origin and final SQL, then returns exactly:

```json
{
  "accepted": true,
  "evidence_sha256": "64 hexadecimal characters identifying checker evidence",
  "checker_id": "a pinned independent checker revision"
}
```

The memory API accepts no expected answer, reference SQL, hidden label or
agent-supplied success flag for terminal settlement. The caller must authenticate
the checker and keep its private truth out of agent-accessible tools. An arbitrary
callable supplied by an untrusted caller would not establish independent grading.

Accepted procedures become eligible for procedure retrieval. Rejected proposals
remain failed episodes with their actual trace and evidence. Checker errors remain
unresolved and preserve their error type without storing arbitrary exception
text. They earn no terminal reward. A settled retry returns its original receipt
without invoking the checker again. The host-only feedback export binds terminal
reward to that originating episode and checker evidence.

Descriptions, rationales and preconditions remain model-authored annotations. A
terminal grade checks the final SQL on its originating task; it does not prove
those annotations correct or establish that the workflow generalizes to another
schema or question. Later reuse still needs its own execution and terminal check.

An explicit `abstain(episode_id, reason)` freezes a failed or exhausted episode
without inventing successfully executed SQL. Its checker must return
`accepted=False`; an attempted positive grade is refused as unresolved. A correctly
settled abstention contributes terminal zero feedback and remains a failed episode.
`episode_state(episode_id, max_actions=32)` returns detached origin, action,
proposal and settlement records. Any omitted action rows have an explicit count
and truncation flag. A host can refuse an in-progress restart instead of silently
repeating unaccounted tool or model work.

## Matched retrieval controls

The same ledger supports three prompt conditions:

| Mode        | Returned experience                                       |
| ----------- | --------------------------------------------------------- |
| `none`      | Empty memory text; evidence recording continues           |
| `episode`   | Own settled action traces, including rejected proposals   |
| `procedure` | Procedure descriptions and own SQL with accepted outcomes |

Each comparison arm needs its own private database so one condition cannot inherit
another's trajectory. The binding values may be identical across those separate
stores. Retrieval returns both structured `items` and the serialized `text` block,
alongside byte/token counts and a commitment to the returned memory and query.

Retrieval considers at most the latest 128 settled episodes. A deterministic
lexical score uses the earlier question and proposed procedure, without treating
receipt hashes as semantic features. This is ordinary retrieval, not a learned
representation or a demonstrated intelligence gain.

Output has item, UTF-8 byte and token bounds. Supply the runtime's exact tokenizer
through `count_tokens` for model-specific token accounting. Without it, each UTF-8
byte counts as one conservative content unit; the result labels that convention
explicitly. The count covers the returned memory block, not the surrounding chat
template. Oversized entries are omitted whole, rather than silently truncating
SQL or stripping failure status. Returned procedures are evidence-bearing past
experience, not instructions that grant tool authority.

## Outcome observations and recovery

Tool statistics count actual successes, errors and declared costs. A Beta(1,1)
observation model reports its posterior mean and standard deviation. It describes
tool validity, not whether a task is progressing or its final answer is correct.
Terminal rewards come only from the independent checker receipt.

The private SQLite file uses WAL and full synchronization. New files are created
with mode `0600`; an existing file with broader permissions is refused rather than
silently changed. Each transition commits atomically into a locally hash-linked
event history. Pending proposals survive restart, source identities remain exact,
and changed or corrupt history is refused. Local hashes establish consistency,
not an external witness or onchain attestation. Full database replacement cannot
be detected without an independently retained history head.

Versioned JSON exports include bounded records, their sequence and hash links,
the source binding and an explicit truncation flag. They are evidence exports,
not a pickle format or an untrusted restoration authority. A checker can consume
physical compute before a failed transaction; recovery does not claim exactly-once
physical query execution.

## Relationship to existing agent frameworks

[Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
provide reusable procedural documents, while
[Qwen-Agent](https://github.com/QwenLM/Qwen-Agent) provides model and tool
orchestration. This component supplies the smaller, task-specific boundary needed
here: persist actual own executions, commit a candidate, qualify it outside the
agent tool surface and retain both success and failure. It does not replace either
framework or require their general terminal, browser or account integrations.

# Checked experience transfer

The targeted navigation method **did not beat static retrieval**. Both passed
87 of 108 fresh checked tasks. Removing memory passed 83. The prospectively fixed
superiority gate failed, and no runtime policy was promoted.

| Method                                 | Accepted | Checked tasks |
| -------------------------------------- | -------: | ------------: |
| Targeted checked-experience navigation |       87 |           108 |
| Static BM25 retrieval                  |       87 |           108 |
| Same workflow without memory           |       83 |           108 |

Against static retrieval there were no discordant outcomes. Against no memory,
navigation won five cases and lost one: a 3.70 percentage-point lead, with one-sided
exact paired p = 0.109375. This misses both the four-point and p <= 0.0125 gates.
All seven task families and every failed program remain published.

The 112 selected test tasks had four unsupported reference checks before model
inference. They remain unknown with no replacements, leaving 108 valid tasks per
method. Selection used metadata before task bodies; the 485-record history and
eight checked source-bound corrections were fixed before this test.

All methods received the same model and three-call/7,200-completion-token ceiling.
Both memory arms received identical initial information, not a shared generated
draft. Targeted navigation selected different evidence on 106/108 tasks and
changed 96/108 final programs, without changing a single pass/fail outcome versus
static retrieval. `method-diagnostics.json` retains usage and selection counts.

The exact source, history and catalog were publicly sealed before inference in
[f4fa2f8](https://github.com/trynuria/nuria/commit/f4fa2f80658d5c8309e7965fee049b1f764f7804).
After all 324 proposals committed, but before any terminal grading, the complete
proposal and journal commitments were published in
[ee10324](https://github.com/trynuria/nuria/commit/ee103243e8da45dae4371e68ba0f0c4922b3fcc8).
The portable exporter verifies every program and reconstructed ungraded record
against that earlier commitment.

A separate native replay executed all 324 final programs under the enrolled
checkers. Every result matched. This establishes reproducible results in this
environment; it does not establish model or neural superiority.

`tasks.jsonl.gz` and `upstream-tasks.jsonl.gz` retain the entire selected cohort;
`test-experience.jsonl.gz` retains the common history; `executions.jsonl.gz`
retains every proposal, visible response and outcome. Provider text, original
journal events, source archives and full independent replay records are included.
Four terminal records contain redacted operator paths. Their original diagnostic
and record hashes remain disclosed; code, outcomes and proposal commitments are
unchanged. Private exact records remain retained.

```sh
python -m scripts.verify_experience_navigation research/outcome-learning/course-03 \
  --workspace run/navigation-replay --output run/navigation-verification.json
```

Install the pinned research requirements and use the enrolled isolated execution
backend. Replaying evidence makes no model requests.

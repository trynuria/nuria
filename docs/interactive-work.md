# Interactive program work

The optional interactive worker chooses its next probe after reading actual
execution output. It can inspect enrolled files, check library behavior, revise
an implementation and submit a complete program. This replaces the fixed
proposal/challenge/revision sequence for explicitly selected work; the original
sequence remains available as the control.

Every job has at most five model requests, 2,400 completion tokens per request
and 7,200 completion tokens in total, including reported reasoning tokens.
Requests also pass through the existing shared spending and resource authority.
An ambiguous provider request stops the job; it is never silently repeated.

Each probe runs in a fresh isolated directory with the same read-only enrolled
inputs. This is not a persistent shell or unrestricted repository editor. A
model must carry its complete current program into the next step. Its own tests
are fallible observations. Only the separate host acceptance check can accept
the final program and its required artifacts.

```sh
python -m scripts.run_interactive_course enrollment.json run/interactive \
  --config /path/to/private-reasoner.json \
  --resource-root /path/to/shared-resource-authority
```

The enrollment and artifact contract use the existing
[delivered-work format](program-delivery.md). Successful programs can enter the
[retained-program path](retained-program-work.md), where later enrolled data is
executed and checked afresh. Identical jobs replay their immutable receipts
without another inference request or program execution. Contradictory later
observations can withhold a job from future automatic experience.

The [implementation and evidence](../research/outcome-learning/interactive-work-08)
include negative development results and actual project work. Runtime tests use
explicit fixtures; those tests do not establish model intelligence. The worker
is not enrolled in production and establishes no neural or consciousness gain.

The design uses the small execute/observe loop illustrated by
[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py),
with Nuria's existing execution, budget, journal and delivery boundaries. No
second agent framework or model installation is required.

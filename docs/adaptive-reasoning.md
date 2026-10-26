# Adaptive reasoning

Nuria's optional reasoning path combines a pretrained model, registered data
tools, retained execution evidence and independently checked outcomes. The
current Brian2 experiment controls an initial inspection choice. That limited
control has not demonstrated better decisions than a strong schema-first agent.

The next experiment changes the reasoning strategy itself. It uses completed
work to propose reusable guidance, tests that guidance on separate validation
questions and freezes one policy before testing new questions. The implementation
is ready for a configured reasoning service. Its model effectiveness has not yet
been measured. Offline fixtures test orchestration, not intelligence.

## Learning from completed work

The [policy study](../cognition/reasoning_policy.py) accepts a declared finite
training split. Every training record binds the question, retained execution
trace, source and independent terminal outcome. The critic sees those training
records and previous proposals; it never receives validation or test answers.
It proposes a short reasoning-policy suffix while the existing tool and
acceptance rules remain binding.

Two proposals are selected prospectively. Invalid or duplicate proposals consume
their attempt; the course stops with retained evidence rather than silently
rerolling. The first validation verdict closes the candidate pool. Every usable
candidate and the baseline need complete, source-matched validation coverage.
Only a strict improvement can replace the baseline. A tie keeps it.

This is a small native experiment inspired by
[GEPA](https://arxiv.org/abs/2507.19457v2), rather than a reproduction of its
genetic/Pareto search. GEPA's published gains do not establish Nuria's results.
Prompt changes also do not train the foundation model's weights.

## Reasoning capacity

The additive [hosted client](../cognition/hosted_reasoner.py) uses OpenAI's
[Responses API](https://developers.openai.com/api/docs/guides/reasoning).
The host explicitly supplies a model, reasoning effort, current rates and one
shared spending allowance. The key stays in a private configuration directory.
No browser credential, environment key, model fallback or inference service is
selected implicitly.

Each request reserves its maximum locally accounted cost before transport.
Reasoning tokens count toward output usage. Ambiguous transport or usage keeps
the liability reserved and blocks another request, including after reopening the
ledger. Rates and the text-input token envelope are reviewed host assumptions;
this ledger is not the provider's invoice. Responses use `store: false`, which
does not imply zero provider retention. Hidden model reasoning is not retained.

The existing loopback client and original neural workers are unchanged. Hosted
reasoning is not provisioned or activated by publishing this code.

## A useful comparison

The [course preparation command](../scripts/prepare_policy_course.py) selects
six training, four validation and twelve test questions from the existing six
registered Spider databases. Selection reads row indices and database IDs while
skipping question, SQL and SQL-AST values. It excludes every task named by the
explicit prior-selection records, including previously selected development
tasks. Source and input hashes freeze before a selected question is decoded.

The [runner](../scripts/evaluate_reasoning_policy.py) compares:

| Method         | Guidance                                                 |
| -------------- | -------------------------------------------------------- |
| Schema first   | Existing analyst with direct schema inspection           |
| Learned policy | Same analyst plus the validation-selected strategy       |
| Fixed review   | Same analyst plus conventional query-review instructions |

All three receive the same checked training records, model, source data, tools
and per-job ceilings. The training sequence itself is progressive and retained.
Validation is model-selection data, not evidence of transfer. All test proposals
commit before any test label is read. Equal caps do not imply equal actual
compute; prompt/completion use, proposal cost, failures, abstentions and
unexecuted jobs are retained separately.

The complete planned denominator is 54 analytical jobs and two policy proposals.
The shared ceiling is 328,096 completion tokens and 100 MiB of retained course
evidence, with a 16 GiB disk floor. A 90-minute preflight allowance stops new model
calls before their nominal transport timeout would exceed it. Socket timeouts do
not forcibly terminate an in-flight request; an external process supervisor is
required for a hard wall-clock cutoff. These bounds do
not authorize spending: a separate explicit budget and credentials are required.
An interrupted course is retained and never automatically reissued.

Independent checking compares executed result rows, duplicate multiplicity,
NULLs, column count and required ordering. This is finite execution agreement,
not universal SQL equivalence or the official Spider test-suite metric. New task
IDs still come from a public benchmark and the same six databases; model
pretraining contamination and a possible accuracy ceiling remain limitations.
No protected Duet tasks are used.

## Run a registered question

The [hosted question command](../scripts/ask_hosted_analyst.py) uses the same
private shared budget and retains executed evidence:

```sh
python -m scripts.ask_hosted_analyst \
  --provider-config /private/nuria/reasoning.json \
  --registry /private/nuria/data/registry.json \
  --state /private/nuria/analysis \
  --job-id provider-review-001 \
  --db-id work \
  --question "Which providers delivered accepted work, and at what recorded cost?"
```

The paths are examples. The host must enroll real sources and configure its own
key, model and budget. An optional frozen policy requires an explicit file and
separate identity. A delivered SQL answer is not semantically accepted until an
independent checker qualifies it. This command grants no wallet, shell, hiring
or deployment authority.

## What would count as progress

A frozen learned policy must solve more fresh useful questions than both strong
controls under the declared allowance. The full costs of training and selection
belong in that result. Better software reliability, more attempts or a successful
scripted fixture alone cannot establish the gain. A Brian2 contribution needs a
separate matched intervention after a useful reasoning improvement exists.

The previous 9/12 neural/schema-first tie, 205 originating weight changes and
12/12 report-reuse parity remain unchanged.

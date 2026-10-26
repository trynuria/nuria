# Outcome-driven reasoning policy

Status: implementation and offline orchestration checks complete. No live model
trial has run, and no new reasoning or neural advantage is claimed.

[Protocol 01](protocol-01.json) declares a finite comparison before task bodies
are decoded or inference begins. Its SHA-256 is
`adeea584db706f45640cfb0548e56183f8debfb7a7c453a827a4fe64662f1756`.
The complete source closure is pinned in that protocol. Preparation reads Spider
row indices and database IDs, excludes all previously selected IDs and balances
new selections across six existing registered databases. The questions remain
the same public benchmark; neither new database generalization nor freedom from
model-pretraining contamination is established.

The proposed runtime is `gpt-6.1-sol` with low reasoning effort through the
Responses API. A prospective configuration identifies a proposed $10 total
testing ceiling. This is a proposed allowance, not an authorization or a funded
service. The comparison remains pending project-specific model credentials and
an explicit testing budget. Prices must be checked again before paid use.

Six checked training tasks feed two bounded policy proposals. Four separate
validation questions select one frozen policy; ties keep the original. Twelve
test questions compare schema first, learned policy and conventional fixed
review, all receiving identical checked training evidence and equal per-job
ceilings. All test proposals commit before test labels are read. Training and
policy-selection costs remain additional measured work.

The full planned denominator is 54 analytical jobs and two proposals. Failed
or duplicate proposals consume their attempt and stop this course. Resource or
transport failures retain completed, attempted and unexecuted selections; they
cannot be reported as a completed effectiveness result. There is no automatic
retry, winning-seed selection or outcome-based task replacement.

Offline tests exercise the actual SQLite/client/analyst wiring with scripted
responses. They establish protocol behavior only. A fixture that deliberately
returns a correct answer is not model reasoning and is not a new benchmark score.

See [adaptive reasoning](../../docs/adaptive-reasoning.md), the
[preparation command](../../scripts/prepare_policy_course.py) and the
[evaluation runner](../../scripts/evaluate_reasoning_policy.py). Production
activation, neural contribution and deployment remain separate work.

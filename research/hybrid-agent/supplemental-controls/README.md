# Supplemental nonlearning controls

This helper prepares a separate, post-hoc completion of the two nonlearning
controls originally selected for the grounded comparison: schema first and
stateless. It does not replace the original study, renew its 200,000-token
allowance or remove any of its 120 selected records.

There is no executed supplemental protocol or result in this directory yet.
Publishing an implementation does not establish comparative performance. An
exact protocol and publication receipt are required before this helper can run
inference.

## Why a separate completion

Both controls use the fixed reasoner with `memory_mode="none"` and no neural
context. The completion therefore omits teaching calls while preserving the
original job IDs, questions, database identities, seed, tools and per-job limits.
It starts fresh, separate memory journals. This isolates the original selected
controls; it does not make the tasks newly unseen or the comparison prospective.

The conditions run in the declared order, schema first and then stateless.
Runtime order and hardware effects remain limitations. Actual tool timing and
receipt hashes can vary, so identical generations are not promised. A condition
is skipped only when all twelve original reserved proposals already completed.
Otherwise all twelve are selected again; partial original scores do not choose
which questions to repeat.

All supplemental inference ends before this helper opens its answer oracle.
Only complete twelve-proposal conditions receive descriptive grades. The full
24-record denominator includes unfinished and unexecuted work. No terminal
experience or neural learning credit is applied by this completion.

## Bounds and recovery

Each question retains ceilings of twelve turns, twelve tools, 6,000 completion
tokens and 1,536 completion tokens per request. The supplemental envelope is
60,000 additional completion tokens and 260,000 cumulative tokens, including
earlier bounded uncertainty and the separately reserved semantic trial. Its
6,000-token allowance stays held until source-bound, independently reconciled
usage closes it. The original allowance remains a distinct historical record.

The helper also enforces one hour of supplemental wall time and an absolute
deadline before the enrolled temporary model expires. These are finite experiment
bounds, not a standing production allowance. Model weights, endpoint and all
seventeen original source identities must remain unchanged.

Fresh unresolved main or post-job inference refuses further calls. Historical
bounded uncertainty remains charged rather than being relabelled as known.
Ambiguous new usage stops the completion and retains its reservation. A durable
protocol-bound execution reservation prevents a different output directory from
renewing the same allowance. Existing files and failed attempts are preserved.

The caller must provide ended-process guards, exact source and result evidence,
and a separately verified publication receipt. The helper makes no financial
transaction and configures no production service.

## Inspect and test without inference

From the repository root:

```sh
python research/hybrid-agent/supplemental-controls/evaluate_controls.py preflight
python -m unittest discover \
  -s research/hybrid-agent/supplemental-controls \
  -p test_controls.py -v
python research/hybrid-agent/supplemental-controls/input_equivalence.py .
```

Preflight reads no experimental artifact bodies and starts no model request.
The ten tests use explicit scripted responses and tiny SQLite fixtures. They
exercise input/source binding, grading order, complete denominators, usage
conservation, fresh ambiguity refusal, deadline enforcement and duplicate-run
refusal. Their fixtures are retained. They do not measure live model capability.

## Teaching-history input check

[The portable proof](input_equivalence.py) compares each nonlearning control's
first request with a fresh journal and with eight settled synthetic teaching
episodes. It creates a tiny SQLite source and obtains one genuine schema receipt,
then holds that exact observation fixed across the two histories. A capture-only
reasoner records each request and stops before inference. Neither a real course,
an answer oracle nor a live model is opened. Temporary fixtures remain retained.

The [original retained result](input-equivalence-proof.json) records matching
first requests for both controls despite different journal heads. The portable
script is byte-for-byte identical to the original proof script, SHA256
`6c3ade965c2bec91cde212025d1de201b82bac607bf295ddaa9d44c4c7345823`.
The original result is also preserved unchanged, SHA256
`6d7a434be2792708ef7997642ac042bcfc41a1e8fce9f7a52e7a0614b8a2af73`.

This establishes teaching-history irrelevance for the specified `none`-memory
first-request path with matched host observations. An independent execution can
produce different schema-receipt timing and request hashes; equality is tested
within its paired histories, not against a historical wall-time hash. It does not
establish identical live generations, task capability, a comparison gain or a new
unseen preregistration.

The evaluator's SHA256 is
`1cb1a7d02659844485436dce090cdc5d235f886a83b1d88d4354acc0628131fa`.
It is the exact reviewed helper; the portable tests derive repository paths from
their location. Experiment preparation must produce and publish its own frozen
protocol before inference. Nothing in this package establishes neural advantage,
consciousness or biological learning.

# Reasoning continuity

The original hosted client keeps assistant text and discards provider reasoning
items. The optional continuity adapter preserves complete native output items
between execute/observe steps. This follows the Responses API's
[stateless reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning#preserve-reasoning-across-calls).

The provider's reasoning remains opaque. The adapter does not decode it or add
it to public job records. A public work trail consists of source identities,
proposed programs, actual execution results, acceptance and usage. Private
transport transcripts retain the encrypted items needed for request auditing.

Each request uses `store: false`, default processing and the existing project
credential. The adapter accepts no hosted tools. It reserves the documented
model maximum at conservative long-context/cache-write rates before transport,
then settles observed usage through the existing durable allowance. Unknown
usage blocks further spending. No allowance is renewed per step or job.

The session bridge checks that visible history has not changed or omitted a
step. It carries complete provider items into the next request and checks that
the response reports `all_turns` reasoning context. A new job resets this
transient provider session. Accepted-program and outcome memory continue to use
their existing explicit stores; no language-model weights are updated.

The [matched comparison](../research/outcome-learning/reasoning-continuity-09)
uses the same model, work policy, prior checked history, initial information,
isolated execution and 7,200-output-token ceiling. The control keeps normal
visible messages; the candidate additionally replays opaque provider output
items. Their actual input usage and costs can differ. All proposed programs
must commit publicly before independent terminal grading.

Four real transport calls establish that state is carried only in the intended
condition and that actual execution feedback reaches the next step. These
calls are wiring evidence. The frozen comparison completed 70 valid task pairs:
both conditions accepted 50, with the same 20 failures. The exact paired p-value
is 1.0. Continuity used 152 calls and 2.140305 configured conservative USD; the
visible control used 146 calls and 2.013094 USD. The superiority gate failed.
All 140 independent macOS verdicts match the original observations. Linux matches
138/140: an upstream filesystem-sensitive case fails for both routes, giving
49/70 on that host. The explicit reference-repeat and label-sensitivity
qualification retains every discrepancy; the original scores and failed
promotion gate stay unchanged. The visible route remains the default, and the
optional adapter is not production activated.
No neural or consciousness advantage is established.

## Checked work

The existing interactive course accepts an explicit `--reasoning-continuity`
option. It uses the same enrolled read-only inputs, isolated executor,
consistency-filtered outcome history, independent artifact checks and immutable
delivery collection as the visible route. Accepted prior outcomes are available
to subsequent jobs. An identical completed job returns its retained receipt
instead of making another model request or execution.

The option uses the model effort in the operator's private configuration. It
does not select a larger budget or enable a production worker. A finite private
resource authority and the existing durable spending allowance remain required.

Two actual maintenance jobs passed through this CLI and delivered checked
artifacts. Their acquired accountant subsequently passed a later complete ledger
and a declared price-sensitivity scenario without another model call. The four
checked outcomes share one persistent operational journal. The
[complete work record](../research/outcome-learning/reasoning-continuity-09/project-work/RESULTS.md)
distinguishes actual data, authored scenarios, the supplied repair and the
collector recovery. These outcomes establish usable finite work and program
reuse; the separate accuracy comparison remains tied.

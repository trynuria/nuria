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
calls are wiring evidence, not an intelligence result. The comparison determines
whether continuity improves useful decisions. This adapter is not activated in
production, and it establishes no neural or consciousness advantage by itself.

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

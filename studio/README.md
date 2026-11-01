# Nuria Studio

Studio turns a broad creative brief into competing executable browser worlds.
It keeps each prototype's files, supplies actual execution feedback, records an
explicit critique and retains the selected simulation engine as a tool. Later
projects can wrap that tool; the independent engine checker records actual calls
into it. The critic's next brief and lessons shape the subsequent project.

The public Studio is read-only. Visitors can explore published variants, change
their controls, inspect the decision and follow tool ancestry. Embedded creations
run in an opaque-origin sandbox with no network, forms or parent access; local
downloads let an invention export its own work. They are
simulations inspired by dated public neural measurements, not new biological or
consciousness findings. All variants and failures remain in the project history.

The first series is available in the [live notebook](https://nuria.network/studio/):
[What a Signal Leaves Behind](https://nuria.network/studio/#invention-001),
[The Loom That Misses a Stitch](https://nuria.network/studio/#invention-002), and
[The Echo Customs House](https://nuria.network/studio/#invention-003). The public
[/api/studio](https://nuria.network/api/studio) projection provides current stages,
critiques, engine results and ancestry. Each creation directory exposes its four
source files, enrolled inputs, retained toolkit and SHA-256 manifest. Operator
browser refinements are recorded separately from the model's build steps.

## Runtime

`python -m studio.worker --root STATE --token-file TOKEN --node NODE` runs an
autonomous campaign within the existing budget. There is no default project-count
stop. Use `--max-projects N` for an explicitly finite trial. A single writer lock prevents
competing workers. SQLite retains stages and exact model request IDs. The private
inference gateway also retains request IDs, so a lost response cannot cause a
second paid request. Unknown provider settlement stops subsequent spending.

The gateway uses Nuria's existing hosted reasoner and its existing allowance
ledger. It does not renew that allowance. A separate campaign ceiling also
preserves the conservative remaining amount recorded at enrollment. The gateway
listens only on loopback and requires a private token. It runs on the same server
under a separate service account; the worker cannot read the provider credential
or modify accounting. No owner's computer or SSH tunnel is required. Production
starts with `--require-existing-state`: missing accounting fails closed rather
than creating a fresh allowance. Migrating accounting requires freezing the old
writer first and preserving the entire ledger and request journal, including
their original limits, charges and request identities.

Projects persist through restarts. Each candidate has up to six build steps,
with up to 10,000 output tokens per build step, all inside the shared dollar
ceiling and original shared request limit. After selection, the critic's next
brief starts another project. A failed project instead supplies its failure
evidence to a new attempt. The planner sees a bounded working set of reusable
tools while the full archive remains retained; it can also choose a fresh
foundation. Every third successor is prompted to challenge the portfolio's
habits. Completed projects never regenerate on a refresh.

The gateway checks the full next-call reservation before spending. Exhausted
money, the original request cap, an unresolved provider charge or an operator
pause stops further paid calls. These limits survive service and machine
restarts; restarting never grants another allowance. Known malformed or oversized
inputs are rejected before a request reservation and can feed a repair attempt.

Use `python -m studio.cli --root STATE status`, `pause`, `resume`,
`submit "creative brief"` or `feedback PROJECT "specific feedback"` through
existing private operator access. Pause applies after the current bounded step.
Manual briefs use the same allowance and do not create a new budget.

## Execution and evidence

The four editable artifact files are `index.html`, `style.css`, `app.js` and
`engine.js`. The worker alone supplies `inputs.json` and any retained toolkit.
Path traversal and changes to those supplied files are rejected. The engine
exports `create`, `step` and `metrics`; the host checks deterministic replay,
finite outputs, evolution on several seeds, causal effects of controls and a
30-second simulation with bounded state.
When an engine exposes `runTests()`, the same sandbox also executes those
project-defined logic tests with a four-second limit. The returned per-test
results and measured outputs feed subsequent builds and critique; a reported
failure blocks promotion. These are model-authored tests, not independent proof
of the invention's intended behavior. Browser-only tests still require browser
inspection. The critic receives the delivered file manifest and enrolled source
snapshot so that omitted code excerpts are not mistaken for missing files.
Tests run in an offline bubblewrap namespace with a read-only candidate mount,
small resource bounds, and no credentials or private host files.

These checks establish a limited executable contract. They do not establish
artistic quality, scientific novelty, general correctness, or better intelligence
than a simpler agent. The model critic sees code and engine results; its visual
judgment is provisional until browser inspection or human feedback. Learning
here means project experience, retained executable tools and preferences; the
foundation model's weights are unchanged.

The implementation reuses the existing Nuria transport and accounting, using a
small candidate-and-selection loop inspired by [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)
and executable skill accumulation described by [Voyager](https://voyager.minedojo.org/).
It is not an implementation of either research system. A larger evolutionary
framework can replace the small search loop if a real project needs that scale.

## Deployment

Deploy this package as a separate component; do not replace the original neural
workers or their checkpoints. `studio.server` serves the public projection,
static Studio and bounded creation paths on loopback port 3042. Route only
`/studio/`, `/api/studio` and `/creations/` through the existing authenticated-origin
proxy. Creation responses require the supplied sandbox CSP and same-origin frame
permission. No public endpoint can enqueue work or spend inference allowance.

Ubuntu's unprivileged namespace restriction may require an application-specific
AppArmor profile for the sandbox launcher. Keep that profile limited to a
root-owned launcher executable accessible only to the Studio worker; do not turn
off the system-wide restriction. The worker must pass an isolated execution
self-check before starting paid work. The serving process is a separate user.

Run `python -m unittest tests.test_studio -v` and `node --check studio/web/studio.js`.
The real Linux namespace check is additionally required on the intended host.
Both the worker and inference service must be enabled at boot. Keep the gateway
private; expose only the read-only public projection through the website proxy.

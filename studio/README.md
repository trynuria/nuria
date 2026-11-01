# Nuria Studio

Studio invents interactive tools, games, worlds, stories and instruments. It
proposes two mechanisms, builds them, executes their logic and browser journeys,
critiques the delivered results, and retains useful engines for later work.
A portfolio scheduler explores the least-visited creative direction; every fourth
project improves a completed work. One critic's next brief cannot take over every
subsequent project. Old prototypes and failures remain in the archive.

The public Studio is read-only. Visitors can explore published variants, change
their controls, inspect the decision and follow tool ancestry. Embedded creations
run in an opaque-origin sandbox with no network, forms or parent access; local
downloads let an invention export its own work. Neural snapshots are optional
inspiration, not a required theme. These creations do not establish biological
or consciousness findings. All variants and failures remain in the project history.

Explore the [live Studio](https://nuria.network/studio/), including
[One Cut, Many Places](https://nuria.network/studio/#invention-009),
[One More Bite](https://nuria.network/studio/#invention-010),
[Pack Together](https://nuria.network/studio/#invention-011),
[Who Said It](https://nuria.network/studio/#invention-014), and
[Turn Phrase](https://nuria.network/studio/#invention-015).
The gallery shows real browser captures and supports browsing by direction.
The public [/api/studio](https://nuria.network/api/studio) projection records stages,
critiques, engine results and ancestry. Creation directories expose source,
contracts, browser reports, desktop/phone images and SHA-256 manifests.

## Runtime

`python -m studio.worker --root STATE --token-file TOKEN --node NODE --browser-runtime BROWSER_VENV` runs an
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
with up to 10,000 output tokens per step, within the same dollar and request caps.
The six directions are visual instruments, games, useful tools, explorable
worlds, participatory stories, and rhythm/motion. Exploration slots seek different
mechanisms; improvement slots use the critic's concrete feedback. A failed
candidate stays unpublished and does not end the campaign. The planner sees a
bounded working set of compatible reusable tools; the full archive is retained.
Unchanged source reuses its check results. A crashed browser is retried once
locally, then the candidate is retained as unavailable without buying model
retries for that infrastructure failure. Completed work never regenerates on a
page refresh.

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

The four editable files are `index.html`, `style.css`, `app.js` and `engine.js`.
The host supplies immutable `inputs.json`, `contract.json` and any retained toolkit.
Path traversal and writes to these dependencies are rejected. The current engine
contract is `create(seed, source)`, `act(state, action)`, `metrics(state)` and
`runTests()`. Domain-specific actions replace mandatory animation and generic
sliders. The host freezes actions and a concrete browser journey before building.
It checks deterministic replay on three seeds, distinct intervention outcomes,
bounded serialized state, finite measurements and at least three named logic
tests. Project tests are model-authored; execution is independent, but the tests
are not independent proof of correctness. Legacy engines keep their original
`create/step/metrics` contract and history.

[Playwright](https://playwright.dev/python/docs/intro) runs Chromium in a separate
offline bubblewrap namespace. It opens each creation at 1280×800 and 390×844,
performs the declared clicks/edits, observes a changed result, checks overflow
and script errors, and verifies any declared download. Screenshots and DOM
observations feed the archive; the critic receives DOM reports, manifests and
bounded code excerpts. It does **not** receive screenshot vision. These checks
exercise declared journeys, not every possible input or a human usability study.
Generated code sees no host network, credentials or private files. Only candidate
files, the installed browser/runtime and a bounded result directory are mounted.

Novelty, usefulness and craft scores are model judgments, used to choose among
completed work. They are not objective benchmarks. The small portfolio scheduler
adopts the idea of keeping different kinds of promising solutions from
[quality-diversity search](https://arxiv.org/abs/1504.04909); it is not MAP-Elites or
an implementation of a research system. Learning here means retained executable
tools, project experience and critique. Foundation-model weights are unchanged.

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

Provision a dedicated virtual environment with `studio/requirements-browser.txt`
and run `python -m playwright install --with-deps chromium --only-shell`, using
`PLAYWRIGHT_BROWSERS_PATH=BROWSER_VENV/browsers`. Browser checks need a private
`/proc` mount; qualify the complete worker unit on the intended host before paid
work. Keep the sandbox launcher and browser installation read-only to the worker.
The runtime uses outer bubblewrap isolation instead of Chromium's inner sandbox.

Run `python -m unittest tests.test_studio tests.test_studio_frontiers -v` and `node --check studio/web/studio.js`.
The real Linux namespace check is additionally required on the intended host.
Both the worker and inference service must be enabled at boot. Keep the gateway
private; expose only the read-only public projection through the website proxy.

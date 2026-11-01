# Nuria Studio

Studio invents interactive tools, games, worlds, stories and instruments. It
proposes two mechanisms, builds them, executes their logic and browser journeys,
critiques the delivered results, challenges selected outcomes with separate
executable criteria, repairs demonstrated failures, and retains qualified engines
for later work.
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
[Borrow the Bounce](https://nuria.network/studio/#invention-033), a garden where
moving three conserved laws builds a rescue route and then a second passenger
journey. Three successive verified versions carry forward the actual source;
the latest catches and releases the ferry by relocating Grip. Earlier explorations include
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

Planning has at most three attempts. Development plans may use up to 6,000 output
tokens while requesting compact descriptions and retaining the 20 KB plan limit.
Truncated replies preserve the actual provider error in subsequent feedback;
they are not silently retried as generic format failures. Original paid requests
and charges remain recorded, including failed attempts.

The gateway checks the full next-call reservation before spending. Exhausted
money, the original request cap, an unresolved provider charge or an operator
pause stops further paid calls. These limits survive service and machine
restarts; restarting never grants another allowance. Known malformed or oversized
inputs are rejected before a request reservation and can feed a repair attempt.

Use `python -m studio.cli --root STATE status`, `pause`, `resume`,
`submit "creative brief"` or `feedback PROJECT "specific feedback"` through
existing private operator access. Pause applies after the current bounded step.
Manual briefs use the same allowance and do not create a new budget.
The critic also receives the two latest operator observations on that project,
so a selection can account for concrete user feedback alongside execution evidence.

`focus "a sustained creative brief" --frontier world` switches subsequent automatic
work to one continuing invention. Each round develops two competing improvements
from the latest outcome-verified version's actual source and dependency closure.
Source hashes and parent links preserve that lineage. Failed rounds do not replace
the verified starting point. Criticism feeds the next round, with an explicit demand
for a visible new capability rather than cosmetic variations. `clear-focus` restores
the diverse portfolio after the active project. Both modes retain all existing
allowance, execution and acceptance limits; focus persists on the server.

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
Chromium has a 32 MB per-file ceiling for its disposable profile; exports remain
limited to 2 MB. A 4 MB ceiling caused reproducible SIGXFSZ startup failures on
an SVG world even though the same artifact passed under the bounded 32 MB ceiling.
Engine cases and browser journeys allow at most 24 actions, so a complete later
challenge can be exercised without replacing real gameplay with a shortcut.
Browser actions wait for their actual control to become ready, and an extra
acceptance journey waits for its expected visible text. Each wait is bounded to
ten seconds and the whole offline browser check to ninety seconds; CPU, memory,
task, source and export limits remain separate. State-search palettes retain the
twelve-action bound and existing state/depth limits.

## Outcome acceptance and repair

A selected browser-tested prototype now enters a separate examination before its
project can complete or its engine can enter the reusable repertoire. The examiner
receives the actual engine, the visitor promise and specific critique. It authors
2–6 declarative outcome criteria in a fresh model context; it does not reuse the
builder's `runTests()`. The same provider model is used, so these are independently
specified checks, not independent human judgment or a different-model consensus.

The host executes fixed action cases or a bounded breadth-first state search.
Reports contain measured values, concrete witnesses/counterexamples, source hashes
and whether a search closed the entire reachable space under its declared actions.
Depth/capacity exhaustion is explicitly different from proving an outcome
unreachable. Criteria are frozen before repair. The builder can change only the
four artifact files; it cannot edit acceptance criteria or original contracts.
An optional extra browser journey checks expected visible text on desktop and phone.

A valid failing check sends the same creation into up to three repair attempts.
The original version and browser evidence are archived before editing. Every repair
must pass the original engine checks, the unchanged outcome criteria and browser
journeys. Unchanged repair source stops further model retries. Durable request replay
also covers a crash after writing a repair, so recovery does not buy another call.
Passing repairs supersede the previous engine in future tool selection while keeping
its code and lineage in history. Actual repair lessons feed subsequent projects.
Invalid examiner specifications get at most one correction; unexecutable criteria
are recorded as examination failures, not automatically blamed on the invention.

Browser-tested alternatives can remain visible as prototypes before acceptance.
Their displayed check status is distinct from verified outcomes. Unresolved repairs
remain in the notebook; the campaign continues within its original limits. Older
completed work is not retroactively labeled outcome-verified. Selected historical
work can be enrolled through the private `acceptance_queue` state.

The initial [story repair](https://nuria.network/studio/#invention-021) used explicit
operator-supplied acceptance criteria: the original hard-loaf ending was absent
from all ten reachable allocations of three nights. The same frozen criteria then
accepted a repair that preserved the fresh-bread, empty-handed and reset outcomes.
Criteria origin is recorded separately from the host's execution evidence.
Creation folders expose `challenge.json`, `outcome-baseline.json` and
`outcome-result.json`; the manifest binds these to the delivered source.

This uses the established idea of testing command sequences and properties from
[model-based testing](https://fast-check.dev/docs/advanced/model-based-testing/).
The existing discrete engine interface and tiny finite worlds make a bounded
checker sufficient here; no new agent framework or hosted evaluation service is
needed. [Portable evaluation criteria](https://developers.openai.com/cookbook/examples/evaluation/moving-from-openai-evals-to-promptfoo#what-changes-when-you-move-to-promptfoo)
remain separate from inference transport and the unchanged allowance ledger.

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

Run `python -m unittest tests.test_studio tests.test_studio_frontiers tests.test_studio_outcomes -v` and `node --check studio/web/studio.js`.
The real Linux namespace check is additionally required on the intended host.
Both the worker and inference service must be enabled at boot. Keep the gateway
private; expose only the read-only public projection through the website proxy.

Public HTML references local scripts and styles by content hash so a CDN cannot
mix a repaired creation with an earlier cached engine. Creation responses use
`no-store`; Studio switches an embedded creation only when its published source
manifest changes. Archived files and their evidence hashes remain unchanged.

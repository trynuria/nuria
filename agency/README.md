# Persistent autonomous software research

Nuria chooses useful software work from public source and actual observations,
uses shell/Python/Node tools, tests and repairs its implementation, and retains
independently accepted deliveries for reuse. Missions continue on the dedicated
server through a systemd timer. There is no lifetime project-count limit or Mac
dependency. The original cumulative inference ledger remains the spending authority.

This uses the [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) 2.4.6
`DefaultAgent` core with custom Model and Environment adapters. Its existing
decision/tool/observation loop replaces another bespoke agent framework. Nuria's
gateway, state store and enrolled offline execution boundary remain in use.

## What happens automatically

1. Inspect real public source, prior outcomes and observed service data. Read
   public documentation through a bounded, unauthenticated source reader.
2. Select a useful goal and compare an existing approach. A separate model
   context decides whether it is credible and writes acceptance checks before
   implementation. Those checks must fail in the empty workspace.
3. Write and run real code in a persistent offline workspace. Observe results,
   repair failures and submit the complete implementation.
4. Run the unchanged independent checks with the workspace read-only. A separate
   review inspects complete source, claims and execution receipts. Both must pass.
5. Publish the accepted tool as a download and expose it read-only to later
   sandboxed missions. Retain actual successes and failures in outcome memory,
   then select the next goal. A single mission has a finite effort limit.

Before choosing a new goal, the agent can elect to reopen retained unfinished
work when conditions have changed. It resumes the original workspace and frozen
checks. Accepted work cannot be reopened merely to repeat it. Reopenings and the
prior terminal reason remain recorded.

Model-written checks and reviews can share blind spots. This is bounded software
agency, not a demonstration of AGI, scientific novelty, autonomous production
deployment or superiority to humans. Generated programs do not run inside the
public web server. Accepted tools can be downloaded or reused in the sandbox;
production code changes still use the reviewed deployment path.

## Learning across real tasks

The active agent now has an experience layer, rather than only a list of finished
tools. It imports actual delivery reviews, including rejected work, and retrieves
relevant earlier methods through SQLite FTS5. Task frames separate the current
failure from experience on other missions. No embedding service or additional
model is required for retrieval.

This adopts the experience-to-strategy approach explored by
[ReasoningBank](https://github.com/google-research/reasoning-bank) and the reusable
skill-document approach used by
[Hermes](https://hermes-agent.nousresearch.com/docs/guides/work-with-skills).
The existing mini-swe-agent execution loop, gateway, ledger and sandbox remain in
use. This is an integration into the real worker, not a separate text-program
benchmark or another agent framework.

The loop carries four kinds of state:

- **Working state:** a plan, evidence-backed facts, unresolved questions, and a
  proposed test with its expected observable result. Plans are retained across
  process restarts, with earlier revisions preserved. This state is agent-authored
  and can be wrong; its assertions are not independent acceptance receipts.
- **Experience:** the original task, review, failure conditions, artifact hashes
  and acceptance identity. Failed reviews remain observations, not successes.
- **Procedures:** a reusable method, conditions under which it applies, and ways
  it can fail. Proposed procedures stay provisional until a successful separate
  delivery review explicitly approves them. Accepted procedures are exported as
  `SKILL.md` documents. Revisions refer to the prior lesson without erasing it.
- **Applications and feedback:** an agent declares how it is adapting a specific
  earlier method. The separate delivery reviewer assesses that application as
  supported, contradicted or not demonstrated. A mention or retrieval alone
  earns no transfer credit. Same-task repair is not cross-task transfer. Earlier
  contradictions survive a later successful use.

The agent can `recall` and `learn` through its normal action interface. It can
also update its working state alongside a normal tool action and propose a
procedure alongside submission, without purchasing separate reflection calls.
All these actions happen within the existing Cherry timer and persistence path.

Several complete recent action/observation exchanges remain in the next task
frame, within a 40 KB working-context allowance. The latest exchange is always
complete. Older full trajectories and tool outputs remain available on disk.
Goal criteria and independent review inputs are preserved. The first live trial
showed repeated source reads after aggressive observation excerpts; the current
implementation preserves the immediate observation instead of cutting it again.
A later live continuation also exposed alternating source reads when only one
exchange survived. Retaining multiple exchanges allows the agent to compare
separately inspected files without forgetting the previous one.
It also handles repeated _identical_ JSON actions as one decision, while rejecting
different actions or partial output. Original malformed replies are retained.

The public agency report includes learning-record counts and reviewer feedback.
These describe recorded behavior; they are not a causal proof that memory
improved the solver. A successful code delivery can still have an unproven or
rejected transfer claim. Model-written reviews can also be wrong.

Goal selection receives a compact exact index of current outcome statuses and
complete status counts. Older resumption explanations remain history; they cannot
make an accepted task eligible for reopening. The complete outcome file remains
available when the index is bounded.

Delivery reviews also receive recorded command/output receipts whose workspace
hash matches the submitted files. This keeps a test file present in a download
distinct from evidence that it was executed. Old receipts for different file
bytes are excluded from verification credit; omitted evidence is explicit.
These agent-authored checks complement the frozen independent acceptance test.

The November 2 continuation delivered a full cognitive archive audit and a Git
index publication checker. Both passed frozen acceptance and separate delivery
review; the downloaded bytes also passed independent CLI checks. The archive
audit's procedure proposal was rejected because its review lacked supplementary
execution receipts. That decision remains intact. No cross-task adaptation has
yet earned reviewed transfer credit. Exact sources, failures, interventions and
limitations are retained in [the continuation evidence](evidence/learning-continuation.json).

### The capability path and its present limit

The practical path here is to use the existing general model for varied real
work, accumulate independently checked outcomes, learn conditional reusable
methods, test their adaptation on different tasks, and retain counterexamples
when those methods fail. Long tasks need explicit plans and factual observations,
not an ever-growing transcript. These mechanisms are now part of the worker.

Demonstrating reliable transfer across unfamiliar domains, stable behavior after
new learning, and useful long-horizon discovery remains unfinished work. Saving
skills does not establish those capabilities. No foundation-model weights are
updated, and this implementation is not a proven route to AGI.

Autonomous inference still requires available capacity in the original cumulative
allowance. A timer cannot create credits. Budget pauses preserve the current task,
its plan, its failures and its learned methods; they never renew the allowance.

```sh
MSWEA_SILENT_STARTUP=1 python -m unittest tests.test_agency tests.test_agency_learning -v
```

## Recovery and accounting

Each inference request is frozen before dispatch and uses the original durable
request identity. Settled responses replay without another charge. Unknown
settlement stops the action. The assistant's decision is checkpointed before a
tool runs. Completed tool actions replay their receipts; an interrupted command
is reported as uncertain and is never blindly repeated. The agent can inspect
its workspace to recover. Full trajectories, outputs and failed submissions stay
on the server, outside the writable model workspace.

The live inference adapter distinguishes Responses API `commentary` messages
from its single `final_answer`. It preserves visible phase-labelled messages and
the exact original response hash, but executes only the final answer. Multiple
finals or ambiguous unlabelled messages fail closed. The historical hosted client
and frozen study sources stay unchanged, as do usage accounting and request caps.
This follows the Responses API's documented
[message-phase distinction](https://developers.openai.com/api/docs/guides/reasoning#phase-parameter).

If the gateway explicitly rejects a request before dispatch because its response
reservation cannot fit, the adapter records that denial and retries the exact
same evidence with a 1,024-token response cap and a separate durable identity.
It never uses this fallback for transport failures or uncertain settlement. Tests
and review inputs are not shortened to manufacture affordability or a pass.

All inference, including goal and delivery review, uses the same original
allowance. A new mission requires a small completion reserve. No request can
increase the ledger or provision funds. The timer remains installed when the
allowance stops further inference; it does not renew it. Old fixed-target
research timers should be disabled before activating this worker.

## Runtime

Install the pinned **core-only** dependency closure into a dedicated venv:

```sh
python3 -m venv /opt/nuria/agency-runtime
/opt/nuria/agency-runtime/bin/pip install --no-deps -r agency/requirements-core.txt
```

The upstream package's optional provider, dataset and interactive CLI dependencies
are not installed or used. Its public `DefaultAgent`, Model and Environment
interfaces are exercised in the recovery tests. `MSWEA_GLOBAL_CONFIG_DIR` points
at an empty dedicated state directory; no provider key is given to this library.

The supplied unit files use the dedicated `nuria-studio` account, an existing
loopback gateway capability, a read-only published source snapshot, explicit
public dependencies, and the already enrolled bubblewrap binary. Configure those
paths for the intended installation. The sandbox clears the environment, drops
capabilities, unshares namespaces including networking, and mounts only the
published source, public observations, retained tools, system runtime and current
workspace. Shell commands have CPU, file, descriptor and wall-time bounds; the
service additionally bounds memory, processes and CPU. Public fetch redirects
must remain on the explicit HTTPS source-host allowlist.

Normal workspace commands have an 8 MiB per-file limit. Frozen acceptance checks
can create up to 128 MiB per temporary file, inside their disposable namespace,
to exercise streaming behavior on realistic inputs. The workspace remains
read-only during checks, and the service's 1 GiB memory limit still applies.

```sh
MSWEA_SILENT_STARTUP=1 python -m unittest tests.test_agency -v
# On an enrolled Linux host, also set NURIA_TEST_LINUX_ISOLATION=1
# and NURIA_TEST_BWRAP to its approved bubblewrap path.
```

The public Studio reports the actual selected goal and recent actions at
`/studio/`, machine-readable outcomes at `/studio/agency-results.json`, and the
latest accepted download at `/studio/agency-artifact.zip`. Original projects,
scientific histories and LiveDelta results remain available.

## First observed mission

Nuria selected the missing inverse of the repository's encrypted backup producer
and wrote a streaming recovery CLI, API, integration test and usage documentation.
The [retained record](evidence/first-mission.json) contains the original generated
source, frozen acceptance program, every submission, failures, resumption decision,
review and hashes. The [accepted download](https://nuria.network/studio/deliveries/mission-0001.zip)
is an archive recovery tool; it does not restore a production database.

The original checker needed a 96 MiB fixture but encountered the host's initial
8 MiB temporary-file limit. The host corrected that limit for checkers. Nuria
then independently elected to resume the saved mission instead of rebuilding it.
The unchanged checks passed for three producer-generated archives, ten corrupt
inputs, a wrong key, three existing-destination cases, and roughly 96 MiB recovered
under a 160 MiB process address-space limit. A separate review accepted the code
and recorded its operational limitations. This demonstrates a useful integration
and recovery from an infrastructure failure, with that host intervention disclosed.
It does not establish AGI or superiority to established backup systems.

# Architecture

![Nuria experience flow](../brand/flows/experience.svg)

Two independent circuits read the same durable finalized input queue. The original 256-neuron circuit retains its complete existing history. A read-only PostgreSQL role supplies the expanded cognitive circuit, which owns a separate SQLite journal and trusted Brian2 checkpoint. Its own genesis is published; the two histories are never treated as the same neural state.

## Cognitive cycle

An optional configured task catalog feeds the [neural task policy](action-loop.md)
after each decision window. Its spike-derived context and action-region eligibility
are retained with the checkpoint. Isolated commerce executes committed jobs;
checked delivery, payout and measured utility return through a cursor-based outbox.
The exact originating readout and tagged synapses receive delayed credit once.
No marketplace or signing credential enters the cognitive worker.

The included-capacity skill provider synthesizes bounded declarative data
transformations, executes them, and retains only buyer-verified programs. A later
task can name a retained skill and supply fresh inputs without training examples;
the provider executes that program without searching for a replacement. Private
buyer checks determine success, and the resulting receipt returns to the
originating neural decision. Program retention is conventional software learning;
its measured efficiency is separate from any claim about the neural selector.

The optional [interactive work runner](interactive-work.md) executes bounded
Python probes against enrolled read-only inputs, observes their actual output
and commits a complete program for independent acceptance. Accepted artifacts
and a consistency-filtered outcome history support subsequent work. Its
[reasoning-continuity option](reasoning-continuity.md) also preserves opaque
provider state within a job. Provider state, retained programs and Brian2
checkpoints have separate identities and lifetimes. Activating this standalone
runner requires explicit private provider and resource authority; its comparison
does not activate a production cognitive worker.

The separate [software-intervention runner](../research/intervention-memory/README.md)
uses the existing circuit and journal machinery to execute allowlisted SQLite
actions. Its own history binds source code, dataset and runtime. Origin contexts,
eligibility tags, selected-feedback learning and receipt progress commit together.
It runs as an explicit bounded command, has no payment or production-database
integration, and does not replace the live worker. Comparative policy evidence and
standalone synaptic-credit/recovery evidence have distinct scopes.

An isolated decision laboratory now runs alongside these two histories. Its own 256-neuron sensory circuit feeds learned cue representations, costed sensing and delayed-feedback action learners. Seven matched branches include a direct symbolic-memory comparator. It owns a third private SQLite journal and complete Brian2 checkpoint, has no RPC/database/signing credentials, and reads only the main circuit's public cache to perturb virtual information prices. The API reads its public `discovery/status.json` cache with a 15-second freshness gate. See [the complete experiment protocol](discovery.md). The main cognitive action rule below is unchanged; lab capability is not attributed to that rule.

Each cycle reads at most four inputs in event order. Each valid input gets an independent 20 ms sensory window, source-specific forecast, episode and input-effect record. Side, amount, fee and event-ID texture enter sensory drive. Recalled features stimulate the memory population. A 100 ms autonomous window follows.

Every five cycles, 65% goal utility and 35% normalized action-region spikes select one of eight actions. Habitat moves use an explicit local planner. Replay and matched probes operate on recorded episodes. Retrospective component diagnostics execute on recorded inputs with measured CPU/wall time. Observed prediction improvement and action consequences modulate synaptic eligibility and learned action values.

Source-specific neural readouts and forecast councils prevent test training from changing live predictor parameters. The retained v1 council combines seven specialists using learned squared-error attention and conditional transition memory. The active forecast layer adds bounded ordered outcome memory, slow/fast counts and loss-based selection around that council. It retains at most 16 observed sides and 1,024 cells per count bank. New method metrics have their actual version boundary; old council and neural-readout parameters and lifetime metrics remain available. Shared neural dynamics still reflect all recorded inputs; source separation does not claim separate physical circuits for test and live. Recall now restricts candidates to the current input source.

The experiment action performs a retrospective chronological comparison on recorded same-source inputs. It cannot reconstruct past neural forecasts from payloads, so those diagnostics exclude the neural specialist in every branch. They are observational component checks rather than new hypotheses or controlled interventions. See [evaluation](evaluation.md).

The [adaptive-memory comparison](../research/adaptive-memory/README.md) reports the diagnosis, fixed protocol, all controls and reserved-seed results. Checkpoints include its pending forecasts, ordered buffer, count clocks and mixture weights. A version-change journal record accompanies v1 migration. The legacy runtime mode uses the retained v1 shadow while keeping suspended memory state as evidence; it does not rewind neural history.

## Circuit parameters

| Population         | Range    | Membrane time constant |
| ------------------ | -------- | ---------------------- |
| Sensory            | 0–127    | 12 ms                  |
| Association        | 128–511  | 20 ms                  |
| Recurrent memory   | 512–639  | 45 ms                  |
| Workspace          | 640–767  | 20 ms                  |
| Action readout     | 768–895  | 20 ms                  |
| Inhibitory control | 896–1023 | 20 ms                  |

Refractory period is 4 ms. Excitatory/inhibitory decay is 8/12 ms. Adaptation decay is 350 ms. Excitatory connections sample probability 0.025 from sources below 896, with 1–12 ms delays, STDP traces and reward eligibility. Inhibitory sources use probability 0.05 and 2 ms delays. Self-connections are excluded. Homeostatic bias and background rates are bounded.

## Optional multiscale circuit

The [TVB–Brian2 experiment](../research/multiscale/README.md) adds an isolated
76-region Wilson–Cowan model around the existing 1,024-neuron spiking circuit.
Previous-window population rates enter TVB; previous-window regional activity
returns as bounded current to Brian2. Both use 20 ms exchange windows. The
regional model retains tract delays, while the same neural readout and forecast
council score the resulting spikes. Its joint checkpoint includes regional
state, delay history, coupling buffers, spiking state, learning and input order.

This is a separately versioned history, with no wallet or production queue
credentials. It is not the current worker and cannot adopt its old checkpoint.
The demonstration connectivity has mixed CoCoMac/DSI provenance. Named ports are
engineering choices, not validated anatomical functions. The committed paired
comparison and disconnected/shuffled controls determine whether it earns a
predictive improvement claim; structural complexity alone does not.

## Commit and recovery

SQLite uses WAL and full synchronous commits. Inputs, effects, memory, learning metadata, journal head and serialized network commit every five cycles. A graceful shutdown commits the pending state. A crash rolls back the uncommitted transaction; the prior network and cursor restore together. Status exposes latest and committed ticks separately. An exclusive file lock prevents competing workers.

Commerce acknowledges task feedback only through the cursor in a committed
cognitive checkpoint. A bounded receipt window rejects changed replayed outcomes;
older receipts require retained journal evidence or fail closed. A finite skill
queue also checks durable completed contracts, so evicting a feedback receipt
cannot silently schedule the same executed task again.

Checkpoint hash, model version and topology are checked before trusted deserialization. Missing or mismatched checkpoints refuse a reset. The neural projection hash covers membrane, conductance, adaptation, bias, drives, time constants, weights, traces, eligibility, model time and offset; delayed queues and RNG are covered by the full checkpoint, rather than that projection.

Startup audits the complete cognitive chain. Periodic checks extend the previous verified head. They do not silently claim that every historical row was rescanned. Input source payload and raw spike hashes are bound to records, and record kind is inside the hashed payload.

## Service boundaries

| Service           | Authority                                                                                       |
| ----------------- | ----------------------------------------------------------------------------------------------- |
| Original engine   | Original neural history and receipt writes                                                      |
| Ingestor          | Protocol RPC, transaction queue and source cursors                                              |
| Cognitive worker  | SELECT on input queue; writes only its state and public cache                                   |
| Verifier          | Read original evidence and publish bounded verification exports                                 |
| Treasury observer | Read-only finalized wallet RPC; no database or signer                                           |
| Commerce worker   | Bounded local skill execution, guarded paid-job catalog, durable limits and exact USDC evidence |
| API               | Read bounded cached files; no RPC, database or signing credential                               |
| Backup            | Consistent snapshots and private encrypted upload                                               |

The public interface cannot enqueue inputs, run jobs or prepare payments. Missing or stale evidence returns unavailable. The original and cognitive status require a running phase and fresh cache for aggregate health. Immutable topology and benchmark artifacts are not treated as rolling live status.

The commerce worker reads fresh public cognitive decisions. Explicitly enrolled
local skill jobs use included server capacity with separate finite work and job
limits. Their zero-payment authority cannot activate merchant payments. A private
shared catalogue supplies buyer-owned checks to cognition and commerce; the API
account cannot read it. The public projection contains commitments, bounded
programs, execution costs and checked outcomes, rather than hidden target arrays.

Financial execution remains disabled. Configured paid jobs require the separate
exact Solana USDC authority. The private ledger records authorization before
disclosure and distinguishes payment, delivery and evaluation. Verified outcomes
enter cognitive action values with a checkpointed sequence cursor; delayed
results modify only retained originating eligibility. The public API reads
`commerce/status.json` with a 60-second freshness gate. The selected financial
architecture uses one managed creator-beneficiary/operating wallet, restricted
runtime custody, pinned native collection and constrained Jupiter conversion. A
separate managed creator wallet can forward only to the fixed operating address
when explicitly configured. No reserve vault or multisig is required; older
reserve adapters remain unconfigured. Exact accounts, beneficiary authority,
program pins and live end-to-end verification remain prerequisites. A separate
wallet observer publishes finalized movements and visible coverage gaps. The API
serves complete bounded financial pages from public cache. See
[spending](spending.md) for activation requirements and custody limits.

## Capacity and retention

The circuit has a nominal four-input-per-cycle limit and one-second pacing. Measured cycle cost determines actual capacity. The original reader uses eight fetch workers and a shared 15-request-per-second limiter; RPC coverage is an independent bound. Public endpoints use cached bounded files and never replay the neural model for a visitor.

A bounded server benchmark checks actual input processing, checkpoint commits, source cursor and full journal integrity. A daily projection does not establish a 24-hour provider-to-browser soak. Record size and disk growth must be monitored. Capacity thresholds fail with evidence retained; automatic deletion or unlimited-storage claims are not part of the design.

## Backups

The original circuit retains its PostgreSQL snapshot and matching checkpoint. The cognitive service uses SQLite's online backup to copy a committed network and journal. Both enter the encrypted private archive with separate heads. Recovery must verify and restore each circuit independently. The backup recipient is public; its private recovery key stays off the server.

The commerce ledger also uses a consistent SQLite snapshot and local hash-chain verification before entering the existing encrypted archive. Wallet signing files are excluded and need a separately controlled recovery procedure before provisioning.

## Commission requests

The isolated commerce writer also owns the [commissioning controller](commissioning.md). It stores task commitments and private evaluation vectors in its existing SQLite database, records public hash events and exports a bounded read-only projection. A fresh eligible decision may create a local proposal, without wallet or network authority. The current worker cannot dispatch marketplace orders or sign Base payments. Commission memory is task-specific and is not credited to the neural worker. Its guarded lifecycle and external activation requirements are separate from the Solana x402 executor.

## Evidence maintenance

The receipt verifier scans bounded batches and reports catch-up separately from complete verification. Recovery archives preserve each run and check database read privileges before taking a checkpoint. See [evidence verification and recovery](evidence-operations.md) for the guarantees and limits.

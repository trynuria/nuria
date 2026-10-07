# Architecture

Two independent circuits read the same durable finalized input queue. The original 256-neuron circuit retains its complete existing history. A read-only PostgreSQL role supplies the expanded cognitive circuit, which owns a separate SQLite journal and trusted Brian2 checkpoint. Its own genesis is published; the two histories are never treated as the same neural state.

## Cognitive cycle

Each cycle reads at most four inputs in event order. Each valid input gets an independent 20 ms sensory window, source-specific forecast, episode and input-effect record. Side, amount, fee and event-ID texture enter sensory drive. Recalled features stimulate the memory population. A 100 ms autonomous window follows.

Every five cycles, 65% goal utility and 35% normalized action-region spikes select one of eight actions. Habitat moves use an explicit local planner. Replay and matched probes operate on recorded episodes. Controlled local readout tasks execute with measured CPU/wall time. Observed prediction improvement and action consequences modulate synaptic eligibility and learned action values.

Source-specific readouts prevent test training from changing live predictor weights. Shared neural dynamics still reflect all recorded inputs; source separation does not claim separate physical circuits for test and live.

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

## Commit and recovery

SQLite uses WAL and full synchronous commits. Inputs, effects, memory, learning metadata, journal head and serialized network commit every five cycles. A graceful shutdown commits the pending state. A crash rolls back the uncommitted transaction; the prior network and cursor restore together. Status exposes latest and committed ticks separately. An exclusive file lock prevents competing workers.

Checkpoint hash, model version and topology are checked before trusted deserialization. Missing or mismatched checkpoints refuse a reset. The neural projection hash covers membrane, conductance, adaptation, bias, drives, time constants, weights, traces, eligibility, model time and offset; delayed queues and RNG are covered by the full checkpoint, rather than that projection.

Startup audits the complete cognitive chain. Periodic checks extend the previous verified head. They do not silently claim that every historical row was rescanned. Input source payload and raw spike hashes are bound to records, and record kind is inside the hashed payload.

## Service boundaries

| Service           | Authority                                                         |
| ----------------- | ----------------------------------------------------------------- |
| Original engine   | Original neural history and receipt writes                        |
| Ingestor          | Protocol RPC, transaction queue and source cursors                |
| Cognitive worker  | SELECT on input queue; writes only its state and public cache     |
| Verifier          | Read original evidence and publish bounded verification exports   |
| Treasury observer | Read-only finalized wallet RPC; no database or signer             |
| API               | Read bounded cached files; no RPC, database or signing credential |
| Backup            | Consistent snapshots and private encrypted upload                 |

The public interface cannot enqueue inputs, run jobs or prepare payments. Missing or stale evidence returns unavailable. The original and cognitive status require a running phase and fresh cache for aggregate health. Immutable topology and benchmark artifacts are not treated as rolling live status.

## Capacity and retention

The circuit has a nominal four-input-per-cycle limit and one-second pacing. Measured cycle cost determines actual capacity. The original reader uses eight fetch workers and a shared 15-request-per-second limiter; RPC coverage is an independent bound. Public endpoints use cached bounded files and never replay the neural model for a visitor.

A bounded server benchmark checks actual input processing, checkpoint commits, source cursor and full journal integrity. A daily projection does not establish a 24-hour provider-to-browser soak. Record size and disk growth must be monitored. Capacity thresholds fail with evidence retained; automatic deletion or unlimited-storage claims are not part of the design.

## Backups

The original circuit retains its PostgreSQL snapshot and matching checkpoint. The cognitive service uses SQLite's online backup to copy a committed network and journal. Both enter the encrypted private archive with separate heads. Recovery must verify and restore each circuit independently. The backup recipient is public; its private recovery key stays off the server.

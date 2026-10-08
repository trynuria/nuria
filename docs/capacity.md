# Capacity and recovery

The operating target is 100,000 finalized trades per day, an average of 1.157 inputs per second. A short replay exceeds that average; it does not establish a full-day ingestion guarantee. Provider limits, burst size, storage growth and recovery time remain part of the launch gate.

## Bounded measurements

Measurements from 8 October 2026 use the dedicated production-class host, isolated data directories and bounded CPU/memory scopes. No production history was replaced, no financial transaction was signed and no public load test was performed. [Machine-readable measurements](capacity-results.json) retain the scope of each observation.

| Check                                                   | Observed result                                                                | What it establishes                                                                                                                            |
| ------------------------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Actual Brian2 cognitive replay, 256 synthetic inputs    | 66.54 seconds; 3.85 inputs/second; 1.204-second cycle p95                      | The model can replay this fixture faster than the target daily average. It excludes live RPC retrieval and a full-day soak.                    |
| Persistent finance journal, 10,000 synthetic records    | 1,764 writes/second; 0.284-second paginated export; complete hash verification | This bounded local journal was not the bottleneck. It is not payment or provider throughput.                                                   |
| Read-only cached origin, 100 requests, concurrency four | All 200 responses; 36.2-millisecond p95                                        | The existing cache served this modest origin workload without blockchain or signing calls. It does not model a DDoS or worldwide visitor load. |
| Replay continuation, 64 additional inputs               | 6,848 bytes/input observed SQLite growth; 127.7 MiB process peak RSS           | A short storage/memory sample, including autonomous records. The growth rate is not stable enough to promise retention duration.               |

The frontend reads bounded cached snapshots. Visitors do not create individual blockchain subscriptions, transaction fetches or signer requests. Rendering has a bounded visible connection budget, and invisible or reduced-motion views suspend continuous animation. Every decoded trade still needs a durable source record; thinning a visualization must not discard source evidence.

## Bursts and finite storage

The finalized reader pages transaction history, retains its scan cursor and retries incomplete transaction fetches. Unsupported schemas or failed requests remain gaps rather than fabricated empty activity. It currently limits RPC requests globally to 15/second and uses at most six transaction-fetch workers. Several RPC calls may be needed for one trade, so those numbers are not trades/second.

A burst above the measured model catch-up rate can create a backlog. A launched mint must be tested against real provider responses, duplicates, reordering and interruptions before claiming complete coverage. Queue age, last finalized cursor, free disk and backup age need to be checked during operation. There is no unlimited queue or permanent storage guarantee.

The brief SQLite continuation would imply roughly 0.685 GB/day at 100,000 inputs if that rate continued. This is a planning estimate only: it excludes the original circuit's PostgreSQL records, Discovery, checkpoints, archives, backups and changes in autonomous activity. Keep at least 25% disk headroom and review growth during the first day. Archival, retention changes or capacity expansion require a deliberate decision; evidence must not be silently deleted to preserve uptime.

## Recovery evidence

An actual encrypted application archive was authenticated and decrypted in an isolated recovery drill. Both cognitive and Discovery checkpoints loaded, their genesis values remained intact, and their complete recovered journals verified. The recovered commerce ledger also verified.

The original circuit's PostgreSQL dump restored into a separate database. Its 44,955 receipt/spike-window records were copied into the supported offline store, and the original Brian2 checkpoint loaded at the matching tick with a valid hash chain. This tests archive contents and checkpoint loading; it does not establish production authentication failover or a measured recovery-time objective. Live neural writers continued during the drill.

## Launch and operating gates

- Connect the exact mint and confirm its complete finalized history against an independent read, including failed decoding and pending retrievals.
- Measure end-to-end lag and storage across a full day with representative bursts; the replay projection is insufficient.
- Keep one source writer, bounded retries, durable cursors and fail-closed financial authorization across recovery.
- Retain off-host encrypted backups and owner-controlled recovery authority. Test a controlled failover separately before advertising a recovery-time guarantee.
- Reassess CPU, RPC quota, disk and archive design using measured backlog and growth. A single server is a simple starting point, not perpetual capacity.

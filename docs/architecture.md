# Architecture

Nuria separates ingestion, simulation, verification and public reads. Each process has one responsibility and receives only the credentials it needs.

## Data flow

1. The trade reader discovers the configured mint's Pump bonding curve and PumpSwap pools. It scans finalized signatures using persisted cursors, fetches transaction evidence and decodes supported program events.
2. Each decoded trade enters the durable queue under `signature:event_index`. Conflicting retries do not create new inputs. Transactions that cannot be decoded remain pending for retry; unavailable evidence is not treated as an empty trade.
3. The engine consumes up to 100 queued inputs per model window. Buys and sells produce separate, logarithmically scaled drives, each bounded to 3.
4. Brian2 advances 200 ms of model time. The engine records raw spikes, input evidence hashes, membrane hashes, weight hashes, regional metrics and policy scores.
5. A checkpoint carries the network, random state, pending receipts and continuity metadata. The engine commits pending receipts to the durable store and links each input to its receipt.
6. The verifier checks persisted input, spike and receipt integrity. It publishes a bounded export of the latest 300 durable ticks outside HTTP request handling.
7. The API serves cached files. A visitor cannot directly reach the database, RPC credential or neural writer.

## Neural dynamics

The model uses 256 leaky integrate-and-fire neurons with a 20 ms membrane time constant, 4 ms refractory period and adaptation decaying over 300 ms. Excitatory and inhibitory state decay over 8 ms and 12 ms respectively.

Excitatory source neurons occupy indices 0–207. Connections are sampled with probability 0.075, exclude self-connections, and carry spike-timing plasticity. Inhibitory sources occupy indices 208–255, connect with probability 0.12, and use fixed influence. Independent 18 Hz Poisson input supplies background activity.

The policy population occupies indices 192–207, split into four groups of four. Their spike counts select `compute`, `experiments`, `reserve` or `observe`. A nonzero tie is resolved in that order; all-zero scores select `observe`. The readout produces proposals and does not execute payments.

Population names define circuit organization. Task competence requires an external objective, evaluation protocol and measured results.

## Continuity and integrity

- There is one authoritative neural writer.
- Input IDs are unique and processed inputs retain their receipt association.
- Receipt sequences are contiguous. Each hash covers the previous hash and the canonical payload.
- Input IDs and input hashes must have matching lengths.
- Spike evidence is checked against the hash of its uncompressed canonical representation.
- Existing receipts with a missing checkpoint refuse an automatic reset.
- Model or topology disagreement with a checkpoint refuses an automatic reset.
- Checkpoint restore includes Brian2's random state.
- Checkpoints use Python serialization and must be trusted before loading.

Local integrity verifies consistency within the recorded history. Independent attestation and Solana anchoring require additional systems.

## Storage and permissions

Production uses PostgreSQL. The engine writes neural history and consumes inputs. The ingestor writes source cursors, transaction status and queue entries; it does not update neural receipts. The verification role reads evidence. The API has no database credential.

SQLite supports an offline engine simulation. The separate ingestor and verification-worker entry points require PostgreSQL, so an offline engine does not supply their endpoints automatically.

## Freshness and public reads

Engine status, topology, events and recent receipts are cached separately from verification exports. Public model API data older than 15 seconds is unavailable; verification data has a 900-second bound. The health endpoint also requires a running engine phase. Missing files and malformed JSON return an unavailable response.

HTTP requests do not trigger simulation, database queries, complete-history scans or RPC calls. Download endpoints expose bounded evidence, not private databases or recovery archives.

## Fee accounting

Protocol fees, decoded creator-fee accrual, claimable funds, treasury receipts and executed spending are separate quantities. A trade event's creator fee is not evidence that money has reached a wallet. The current model reports treasury receipt and creator wallet as unknown and leaves spending disabled.

The backup implementation encrypts an application snapshot before upload and checks the uploaded ciphertext against the local archive. Its destination is private configuration supplied through `NURIA_BACKUP_BUCKET`; provider credentials are outside source control.

## Capacity

The reader uses eight fetch workers with a shared 15-request-per-second limiter. Pagination is bounded per address per scan, and unresolved transactions remain queued. The simulator's nominal 100-input window limit is separate from RPC fetch throughput and does not establish an end-to-end throughput guarantee.

Persistent history grows with recorded activity. Capacity planning needs measured queue lag, source coverage, database growth, checkpoint cost and API cache behavior. A high-volume claim requires a representative load test.

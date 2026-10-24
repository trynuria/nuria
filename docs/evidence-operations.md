# Evidence verification and recovery

The receipt verifier is a separate, read-only process. It does not run the neural simulation or execute financial actions. A receipt verifies only local input, spike and receipt hash integrity; it does not establish an independent witness or an onchain commitment.

## Bounded verification

History is scanned from genesis using ordered, limited batches. Spike prefetch has an 8 MiB compressed page budget; rows outside that cache use the same bounded individual check. A scan has a fixed target and validates its cursor against the database before continuing. Receipt sequence, parent hash, payload hash, decompressed spike hash and referenced input hashes must all agree. A regressed head, a replaced cursor or a missing row cannot produce a successful result.

The public result distinguishes a complete pass, a pass catching up, a detected mismatch and unavailable evidence. Partially checked history is never reported as fully verified. Evidence downloads cover only checked receipts and have explicit row and byte limits; a shortened export is explicitly marked as truncated, with its limits and checked range. Oversized or unavailable evidence cannot silently become a complete export. No trusted progress file can skip the initial scan after a process restart.

The worker imports no neural simulator. Its steady memory use depends on a configured batch and export limit rather than the total receipt count. Catch-up proceeds through cooperative bounded iterations; normal polling resumes when it reaches the current head. A daily full pass repeats the checks.

A bounded read-only operational pilot checked the same 10,240-receipt prefix twice per variant, in baseline/prefetch/prefetch/baseline order. Prefetch took 1.49–1.69 seconds; the prior paged reader took 3.44–3.77 seconds. All four resulting prefix hashes matched. The process peak was 37.4 MiB. These observations were made alongside a backup and exclude export work and cooperative worker waits; they are not a complete-history throughput guarantee or a portable research benchmark.

## Consistent backups

PostgreSQL and the matching original neural checkpoint are captured under the existing checkpoint lock. The exported database snapshot remains open for the dump; the lock is released after the matching checkpoint is copied. Cognitive, Discovery and commerce SQLite journals use their online backup interfaces and retain their integrity checks. These components have separate checkpoints; the archive does not claim one atomic instant across all databases.

Each backup uses a new run identity for staging, SQLite snapshots, encrypted archives and its retained result. Existing snapshots are preserved. `latest.json` is a mutable pointer to the latest successful result. Capacity and retention gates fail closed; they do not delete older archives.

The database snapshot role needs read access to every table included in its dump. After the token decoder schema, `deploy/backup-read-grants.sql` grants only `SELECT` on its two new tables to the existing engine snapshot role. The separate PostgreSQL migration keeps the decoder schema compatible with offline SQLite fixtures. A preflight reports unreadable public tables or sequences before taking the checkpoint lock. This does not authorize the verifier, web server or payment process to write those tables.

Archives are encrypted to the project recovery public key, uploaded through the existing private backup route, and downloaded for ciphertext hash comparison. Signing keys remain outside the archive roots. A successful upload and hash comparison establish storage integrity; a completed restore drill is separate evidence and must be reported separately.

## Maintenance boundaries

Verifier or backup fixes can use their own immutable source release. Only those service entry points need to move. The neural engine, cognition, ingestion, commerce and public API can retain their running processes, checkpoints and release identities. A user-interface status message can change without changing the site layout or neural behavior.

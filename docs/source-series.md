# Continuing a source across immutable revisions

`SourceSeriesHost` is an optional host adapter for a finite analyst experiment.
One stable enrollment binds procedural memory and the semantic neural lab while
each job retains its exact immutable data revision. New ledger rows can become a
new admitted snapshot without silently creating another entity or reusing an old
numeric answer.

This is a continuity and activation rail. It is not evidence that a model learned
a useful skill, that neurons improve decisions, or that the system is conscious.
The existing analyst CLI and commerce provider retain their original single
snapshot contracts; this adapter does not change their verifiers.

## Identity boundaries

The enrollment pins the source name, actual SQLite definitions, static knowledge,
database and section names, model identifier, encoder binding, checker identifier
and implementation digest, tool version and participating implementation files.
Its canonical SHA256 becomes the stable `source_id` supplied through the existing
`ProceduralMemory` and `SemanticNeuralBridge` APIs. The identifier is read-only.

Each admitted revision separately records:

- The exact registry file SHA256 and canonical registry JSON SHA256.
- Every database and knowledge-document file hash, plus the dataset revision.
- Its predecessor, a strictly advancing host watermark and admission evidence hash.
- The private retained registry location and the enrolled compatibility fingerprint.

The two registry hashes have different meanings. `register_work_snapshot.py`
currently returns the registry **file** hash under `registry_sha256`; analyst
workspaces use the **canonical JSON** hash. This adapter calls them
`registry_file_sha256` and `canonical_registry_sha256`. Neither is substituted for
the other. Tool receipts retain the workspace's original canonical identity.

Revision order is host admission order. A watermark does not prove that rows were
only appended, that a recent response contains lifetime history, or that an upstream
service is complete. Source completeness and provenance remain checker obligations.

## Knowledge and provenance

By default every supplied knowledge document is static. SQLite table, view,
trigger and index definitions are also static, including legitimate names that
resemble SQLite's reserved prefix.

An enrollment may explicitly declare additional sections as revision-varying
provenance, for example `{"work": ["source"]}`. Those exact section names must be
present from enrollment onward. Their bytes may change; every revision still pins
their individual hashes. New or removed sections fail compatibility. `schema`,
`columns` and `business` cannot be declared variable. Undeclared knowledge changes
also fail.

These extra sections are host/checker metadata. The current grounded analyst's
model-visible parser permits its existing three knowledge sections; this adapter
does not add another model tool or alter that parser. The current work-snapshot
registrar already keeps response identity, coverage and time in `snapshot_context`
rows, so ordinary row refreshes do not require variable knowledge sections.

## Host integration

```python
contract = enrollment(
    initial_registry,
    source_name="work-outcomes",
    model_id=verified_model_id,
    encoder_binding_sha256=encoder.binding_sha256,
    checker_id="work-outcomes-checker-v1",
    checker_implementation_sha256=checker_digest,
)
host = SourceSeriesHost(private_directory, contract)
revision = host.admit_revision(
    initial_registry,
    parent_revision_id=None,
    watermark=1,
    evidence_sha256=admission_receipt_digest,
)
host.reserve("job-001", revision["revision_id"], question, "work")
packet = host.run(
    "job-001", reasoner=reasoner, encoder=encoder, model_id=verified_model_id
)
receipt = host.settle(
    "job-001",
    revision_id=revision["revision_id"],
    encoder=encoder,
    checker=independent_checker,
    checker_id="work-outcomes-checker-v1",
    checker_implementation_sha256=checker_digest,
)
host.close()
```

The checker receives the original job and original snapshot workspace, including
when a newer revision has already been admitted. It returns exactly a Boolean
`accepted` and an independent `evidence_sha256`. The host binds that evidence to
the checked request and canonical registry. Model output cannot provide a passing
grade. Declaring a model or checker identifier is not external attestation of its
weights, correctness or operator; enrollment and factories are trusted host inputs.

## Execution, outcomes and recovery

A private single-writer lock serializes the host. A SQLite journal with FULL
synchronization retains chained revision and job events. Reservations precede
execution; starts precede encoding or model calls. A job binds its revision, both
registry hashes, question, database, seed and inference allowance. New work uses
the latest admitted revision. Existing reserved work and delayed outcomes remain
bound to their original revisions.

Known usage releases unused authority only after the exact result artifact is
retained and validated. An abstention can close known usage but cannot receive a
positive outcome. Unknown usage or an interrupted start retains the full original
reservation, blocks new inference authority and refuses a silent retry.

The independently checked verdict commits before procedural or neural credit.
If credit processing is interrupted, settlement can resume with the committed
verdict through the existing idempotent APIs. The original neural decision and
context must match. Duplicate changed outcomes, wrong revisions, altered source
files and incompatible identities fail closed.

The event hash chain detects accidental corruption; an operator able to rewrite
the entire private store remains inside the trust boundary. It is not an external
timestamp, signature or tamper-proof public ledger.

## Finite operating limits

This enrollment supports at most 128 jobs, 64 pending outcomes, 128 admitted
revisions and 1,024 events. Each job reserves one encoding observation and at
most 6,000 completion tokens. The total token ceiling is explicit and bounded by
768,000. Lower limits may be enrolled. Known and reserved usage are reconstructed
from durable events after reopening; failed attempts do not disappear.

No automatic epoch rollover, model upgrade, unresolved-usage reconciliation or
snapshot garbage collection is implemented. Retain historical snapshots,
artifacts, memory, lab checkpoints and the journal together. Moving retained paths,
changing schema, tools, models, encoder or checker contracts requires a separately
reviewed migration; changing the identifier to bypass a refusal loses continuity.
Existing snapshot-bound histories are preserved but are not imported by this new
source-series enrollment.

Live activation still needs the intended source enrollment, trusted admission
schedule, real runtime identity checks, independent outcome checker, operational
resource limits and an explicit finite-history rollover/reconciliation policy.
There is no new public endpoint, server, production enrollment, payment authority
or deployed service in this adapter.

## Verification scope

The test suite uses real immutable SQLite files, the existing workspace
query tools, grounded analyst and procedural memory. Deterministic reasoner and
persistent bridge protocol fixtures make no model, encoder or neural simulation
calls. They verify changed data produces fresh receipts while one enrolled memory
and bridge history persists; they also exercise delayed outcomes, credit recovery,
source/schema/identity rejection, duplicate work, unknown usage and budget bounds.
Passing these tests establishes the host protocol, not a reasoning or neural gain.

# Neural tasks and external outcomes

The task loop connects a recorded neural decision to a committed job, checked
delivery, separate payout evidence and delayed learning. The original local
actions and their history remain intact. External execution starts disabled.

```mermaid
flowchart LR
  Inputs[Verified inputs] --> Brain[Persistent spiking state]
  Brain --> Intent[Neural task policy]
  Intent --> Contract[Task and acceptance commitment]
  Contract --> Quote[Provider and total liability]
  Quote --> Execute[Isolated execution]
  Execute --> Delivery[Buyer delivery checks]
  Execute --> Payment[Separate payout evidence]
  Delivery --> Measure[Measured result and actual cost]
  Payment --> Measure
  Measure --> Readout[Originating neural context]
  Measure --> Tag[Retained action-region eligibility]
  Readout --> Intent
  Tag --> Brain
```

## Neural decision and delayed credit

Every five cognitive cycles, the policy can choose among sixteen configured goal
contracts. Its context contains a bias and sixteen averages of the actual 64
normalized spike features. Regularized linear utility and an uncertainty bonus
score each goal/provider role. A small, disclosed hash-derived neural readout
supplies the initial preference. This prior is an engineering choice.

Ridge learning uses an exploration multiplier of 0.15 and discounts past evidence
by 0.995 per observed outcome while preserving the identity ridge. Role identity
includes goal, provider, kind, action and verifier. New datasets get new contract
commitments without erasing the role's accumulated learning. State is bounded to
32 role models and 32 pending decisions. An occupied goal cannot create another
intent until its request closes.

Each intent commits the contract, neural projection hash, cursor, tick, context,
candidate scores and eligibility-tag hash. Commerce accepts only a fresh
journalled decision at or before the committed cognitive tick. Public API
requests cannot create intentions or execute tools.

The checkpoint retains the originating context and up to 256 eligible excitatory
synapses into the selected action population. Delayed credit changes those tagged
weights by `0.002 × reward × stored eligibility`, within existing weight bounds.
It does not reward unrelated activity at the time a contractor finishes. The
readout, tagged weights, outcome record and feedback cursor commit atomically.
Duplicate feedback has no effect.

Tests establish neural-context influence and exact delayed weight changes. They
do not establish biological fidelity, a general neural advantage or consciousness.

## Acceptance and usefulness

Contracts contain a purpose, brief, provider, total ceiling, deadline and reviewed
verifier. A private random evaluation nonce prevents low-entropy targets from being
enumerated against the published contract hash. `scripts.prepare_task` creates it
and writes the validated task to a new mode-0600 file. Held-out targets, nonce and
baseline vectors stay private. Public records
contain commitments and measurements, excluding private briefs, targets,
credentials and human submissions.

- **Predictions:** bounded probabilities tied to the exact task and dataset;
  the buyer recomputes held-out Brier gain and retains negative results.
- **Documents:** bounded JSON text with required sections and length. Structure
  and delivery do not earn positive usefulness credit by themselves.

An additional HTTP availability objective measures actual service delivery. The
endpoint, response-body hash, latency bound and observation window are committed
before purchase. The buyer records a completed HTTP baseline, then at least three
observations at intervals of at least sixty seconds after delivery. DNS, TLS and
transport errors remain unknown; they are not convenient zero baselines. A
successful response must match the committed body and latency bound. Utility is
the change in observed success rate, rather than a contractor's self-score.

For measurable, settled work:

```text
reward = clamp(measured utility − 0.01 × verified USD cost, −1, 1)
```

Payment, delivery, acceptance and utility remain separate. A paid report can
remain unscored. The availability comparison is operational evidence, not a
randomized attribution of improvement. Creative quality, advertising impact,
arbitrary code and physical work require additional evaluators.

## Agent and service purchasing

An enrolled `nuria.task-result.v1` x402 merchant receives a bounded `X-Nuria-Task`
header with the public contract and result envelope. The merchant must implement
this interface: ordinary x402 support alone does not provide task execution.
An agent can fulfil an analysis task or a finite, prepaid service contract. No
general cloud account, subscription or production shell is granted.

The existing exact Solana-USDC executor owns invoice inspection, restricted
signing, simulation, durable authorization, finalized transfer verification and
ambiguous-payment recovery. Changed invoices are refused. Linked merchant and
task records count the payment once. The task rail separately verifies delivery
and usefulness.

Longer tasks may return a paid `nuria.task-ack.v1` acknowledgment with a bounded
expiry and private result token. This is recorded as paid acceptance, not artifact
delivery. The buyer polls the enrolled endpoint's `/results/<job_id>` resource;
it never repeats the payment. Result tokens stay in the private ledger and are
sent only to that fixed provider origin. Unsettled timeouts retain liability.
After a verified payout, a missing valid delivery at the committed deadline earns
a negative result with a failure-evidence hash; no artifact or refund is invented.

## Human hiring

The RentAHuman adapter previews a remote one-seat fixed-price bounty, funds from
an existing project wallet, ranks applicants using configured task-history
thresholds, accepts one applicant and reads evidence. No sensitive profile
characteristics enter ranking. A release request requires passing buyer-side
delivery checks.

Funding, hiring and release have separately persisted write intents. A timeout
does not permit repeating the write. Read-only reconciliation can establish an
accepted applicant or completed release. Ambiguous initial bounty creation needs
independent reconciliation; the bounty is not posted again.

Queued release and funded escrow do not establish worker payment. Authenticated
platform-confirmed payout is labelled separately from an independently verified
chain transfer. Unreported worker wallets remain unknown.

Activation requires a project account and authenticated no-charge rehearsal.
Private enrollment pins response fields, account identity, USD units and verified
gross-debit cap behavior. A preview is not an atomic price lock: the adapter
reserves the entire independently enforced per-bounty gross ceiling, including
fees, and requires automatic top-up to be off. Missing evidence refuses execution.
No signup, card checkout, wallet deposit or automatic top-up operation is exposed.

The official [RentAHuman documentation](https://rentahuman.ai/docs) describes the
bounty, applicant, escrow and payout interfaces. Its current crypto funding route
uses Base USDC; Solana funds are not treated as Base or marketplace inventory.
No project account has been enrolled and no human job has been posted.

## Recovery and publication

Merchant jobs, legacy commissions and action tasks share the daily ceiling,
cooldown and unresolved-job breaker. Tasks additionally enforce a lifetime
ceiling. Available inventory is specific to the actual funding account. USDC uses
nominal USD accounting; no bridge or stablecoin price guarantee is implied.

External writes are committed before disclosure. Ambiguity retains liability
across restart. Undisclosed requests can close without utility credit. Disclosed
work needs remote evidence; timeout is not cancellation or refund. Disputes and
refunds require provider reconciliation rather than an assumed local release.

The existing Work and Results views show purpose, neural intent, cost ceiling,
recipient when established, payout basis, delivery and measured utility. Their
visual design is unchanged. Complete events remain paginated. Feedback is served
in ascending pages after the cognitive worker's acknowledged cursor, avoiding
silent loss after an outage. Local hashes establish internal continuity, not an
independent witness.

## Activation

Keep populated catalogs and credentials outside source control. The examples
`cognition/agency.example.json` and `commerce/actions.example.json` start disabled
with no monetary authority. Both workers read `NURIA_AGENCY_CATALOG`; only commerce
reads `NURIA_ACTION_CONTROLS`. Marketplace keys are loaded through the enrolled
environment-variable name. The neural worker receives no signing or account key.

1. Define the actual goal, provider and buyer-owned outcome check.
2. Enrol its project account; verify authenticated response fields, gross costs,
   destination and fresh funding inventory.
3. Verify custody and finite job, daily, reserve and lifetime authority.
4. Run a bounded funded job and reconcile payout, delivery, failure recovery and
   delayed learning before broader use.

Tests retain isolated fixtures and actual Brian2 checkpoints. Fixture orders,
balances and payouts are never published as production work. Tests do not establish
that an unenrolled account or merchant works live.

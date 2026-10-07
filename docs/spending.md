# Autonomous spending boundaries

The cognition service decides which allowed job it values. Financial authority belongs to a separate signer with an independently loaded policy. The public API cannot create intents, change the policy or reach signing material.

Current implementation prepares and validates payments, reserves capacity atomically and builds one unsigned SOL transfer. It neither signs nor broadcasts. Local jobs execute using included server capacity and report zero SOL spending.

## Required configuration

- Exact token mint and current creator-fee beneficiary, with protocol version and fee-sharing mode checked.
- A separate funded spending wallet. The creator wallet can fund it, but the public app receives no authority over the creator wallet.
- Published action and daily caps, reserve floor, allowed provider recipients and accepted job/invoice schema.
- A dedicated signer with key material accessible only to its service identity and encrypted recovery under project control.
- A provider whose delivered result is measurable and whose payment recipient is fixed by policy.

No shared personal wallet or previous project key is part of this configuration.

## Intent and execution

A payment intent names a unique ID, unique job, SOL amount in integer lamports, fixed recipient, fee ceiling and expiry no more than 120 seconds away. The gate checks a fresh finalized balance and subtracts outstanding reservations before applying the reserve floor. UTC daily totals include reserved amounts and fee ceilings.

Reservations use an immediate SQLite transaction. Identical retries require reconciliation with the original reservation; altered terms and reused jobs fail closed. Pending or uncertain amounts remain reserved across day boundaries and restarts. An unknown submission must be reconciled onchain before it can be retried. A confirmed failure can be released only with verified failure evidence in the future execution service.

The unsigned builder accepts no arbitrary instruction list. It produces one system transfer from the dedicated payer to the allowed recipient. The signer must recheck policy, current blockhash, fresh balance, exact fee, intent expiry and reservation, simulate the exact message and record the resulting signature. Payment success is distinct from delivered job success.

## Fee provenance

Balance is spendable inventory, not proof of creator income. The observer reports finalized wallet balance and slot. Fee accrual, vault claims, beneficiary shares and actual payouts require separate verified protocol evidence. Permissionless collection and deposits from third parties must remain distinguishable from creator-authorized actions.

## Activation gate

Financial execution stays disabled until the exact addresses, caps, recipients, funding and isolated signer are configured and tested. Once enabled, the entity selects actions within the published policy without per-action human approval. Changing the scope of financial authority is a configuration change, not a choice the public app can make.

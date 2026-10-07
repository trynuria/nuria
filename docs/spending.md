# Fee funding and autonomous purchases

The commerce worker translates fresh recorded cognitive decisions into a fixed catalog of paid jobs. It runs independently of the neural worker and the public API. Execution is disabled in the installed policy; no production purchase has been made.

## The money path

```text
Pump/PumpSwap trade
  → protocol creator-fee vault
  → verified collection or distribution to the current beneficiary
  → bounded funding of the dedicated spending wallet
  → Solana USDC inventory
  → approved x402 provider
  → finalized payment + validated delivery
  → future measured outcome + purchasing-action credit
```

These arrows are distinct integrations. The implementation includes a reviewed-standard-mode **unsigned Pump claim planner** and an **exact Solana USDC buyer**. Automatic claim broadcasting, collection-wallet funding, PumpSwap/fee-sharing claims and SOL-to-USDC conversion are **not connected**. They must not be described as completed by a balance check or an offline test.

A creator vault can aggregate income from multiple coins. Its whole balance cannot be attributed to Nuria without mint-specific trade and payout evidence. A permissionless payout says who received funds, not who endorsed the project.

## Initial paid job

The first delivery contract is `nuria.forecast.v1`:

```json
{
  "schema": "nuria.forecast.v1",
  "mint": "<configured Solana mint>",
  "source": "solana_finalized",
  "p_buy": 0.6,
  "expires_at": 1780000060
}
```

`p_buy` must be a finite probability. Expiry must be in the future and no more than ten minutes after receipt. The merchant must return data for the configured mint. These example values are a schema illustration, not a live prediction or provider.

A matching `predict`, `experiment` or `compare` action may purchase a forecast when:

```text
0.5 × uncertainty + 0.25 × surprise + 0.25 × learned provider reward
  − 0.1 × provider price ceiling / per-job cap > 0.1
```

Provider selection is a published hybrid heuristic, not unconstrained reasoning. Only the actual `solana_finalized` learning source qualifies. Test inputs cannot trigger purchases. A fresh decision must pass its local record-hash check.

The delivered forecast is evaluated against the first observable finalized trade whose block time is later than delivery. The local forecast was recorded before the purchase. Missing transaction timestamps, a gap in the recent input cache, or no future trade leaves usefulness unresolved. This is an operational comparison rather than a fair causal benchmark: the provider has later information, and the cost coefficient is a configured design choice.

```text
reward = clamp(local Brier error − paid Brier error − 0.01 × USDC price, −1, 1)
```

Verified rewards update the purchasing action's value once. The feedback cursor, neural state and cognitive journal share the checkpoint transaction. Delayed results do not modify unrelated spike eligibility traces. Provider rewards also affect future paid-job selection. Invalid delivery receives no delivery or learning credit; an expired forecast is penalized when a later verified outcome becomes available.

## Exact x402 execution

1. Fetch an unpaid invoice from the exact approved HTTPS endpoint. Private DNS addresses, redirects and credential-bearing endpoints are rejected; responses are bounded to 256 KiB.
2. Accept one x402 v2 `exact` option for Solana mainnet and native USDC only. Recipient, facilitator fee payer and price must match policy. Seller blockhashes and unsupported extensions are rejected.
3. Reserve integer micro-USDC using a SQLite immediate transaction. Apply action and UTC daily caps, reserve floor, outstanding authorizations and a global cooldown.
4. Build through pinned `x402[svm]` 2.25.0. Before the key signs, inspect the v0 message: two required signers, no address lookup tables, exact known accounts, two fixed compute instructions, one exact USDC `TransferChecked`, and a bounded memo. No approval, delegate, authority change, account close, SOL transfer or arbitrary instruction is accepted.
5. Simulate the exact partially signed transaction through project RPC. The configured facilitator pays gas; its signature remains absent until settlement.
6. Persist the authorization before disclosing it to the merchant. Send it in `PAYMENT-SIGNATURE` once.
7. Retain the merchant's `PAYMENT-RESPONSE`; verify the reported transaction through finalized RPC. Nuria's client signature must appear, and both associated-token balance deltas must equal the authorized USDC amount.
8. Record payment settlement, then separately validate delivery and later score its outcome.

A timeout after authorization disclosure is ambiguous. Reserved, authorized and uncertain funds stay reserved across restart and UTC day rollover. The worker reconciles a retained receipt, never automatically pays again. If the merchant supplies no transaction identifier, automatic recovery cannot prove settlement; the reservation remains blocked for investigation. Failed requests conservatively consume the daily allowance. Successful payment with invalid data remains a paid job without delivery credit.

## Keys and authority

`nuria-commerce` owns its private payment ledger and reads a root-managed policy. There is no inbound port or general signing endpoint. The public API only reads sanitized cache files. The neural worker has no RPC or signing credential. The SDK runs in a separate environment to preserve the existing neural dependency versions.

The initial signer adapter accepts a dedicated 0600 key file owned only by the financial service. No key has been generated or installed. This is a hot-wallet adapter, not hardware custody: host root and whoever controls policy or key replacement retain ultimate authority. Keep the treasury separate and fund only a small operating balance. Software limits do not protect a hot key stolen outside the policy service. A managed signer or onchain spending allowance can strengthen that boundary later.

Do not place wallet keys in chat, the browser, source control, research history or prompts. Recovery ownership and signer provisioning must be settled before funding. Do not reuse a personal or unrelated project's wallet.

## Required launch configuration

| Input                                                 | Why it is needed                                                                        |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Exact token mint                                      | Connect trade ingestion and all fee/provider evidence to one token                      |
| Current fee beneficiary and launch mode               | Verify the actual payout route; unsupported modes require their own adapter             |
| Dedicated spending public address and isolated signer | Give only the financial service bounded operating authority                             |
| Per-job/daily USDC caps, reserve and cooldown         | Define the scope authorized once, without per-purchase human approval                   |
| USDC inventory                                        | The x402 rail cannot pay from a SOL balance                                             |
| Real compatible provider                              | Fixed endpoint, recipient, facilitator fee payer, price ceiling and measurable delivery |
| Fee collection/funding rail                           | Needed for continuous fee-funded operation rather than an initial prefunded test        |

An x402 buyer does not inherently need a separate x402 API key. The seller may require its own API authentication or onboarding. The initial adapter intentionally accepts credential-free endpoints; authenticated merchants need a separate reviewed credential mechanism.

[Jupiter Swap API](https://developers.jup.ag/docs/swap) currently requires an API key. Automatic SOL-to-USDC conversion needs project-specific access plus an independently validated swap adapter, maximum SOL input, slippage/minimum USDC output, fee limits and reserve protection. A generic externally supplied transaction must not be forwarded to the signer. Token buybacks are a separate authorization and rail.

[Squads spending limits](https://docs.squads.so/main/development/typescript/instructions/create-config-transaction) can restrict an agent's allowance by asset and destinations. They are not a drop-in replacement for x402's direct SPL authorization or arbitrary Jupiter swaps. No Squads multisig or paid custody account has been created.

## Public evidence

`/api/commerce` publishes current policy, readiness gaps, verified fee-path observations, finalized USDC inventory, job counts, settlement records, delivery hashes, measured rewards and a local event-hash chain. It never publishes the payment authorization header or key. Local hashes detect altered records; they are not independent attestations.

The Resources tab shows payment, delivery and evaluation separately. The original `/api/treasury` remains a read-only creator-wallet balance view. SOL balance, USDC inventory and attributed income remain distinct.

## Verification

Offline tests cover hostile invoices, wrong chains/assets/recipients, private DNS, caps, duplicate decisions, concurrent database handles, crash recovery, uncertain payments, exact settlement deltas, invalid deliveries, Pump mode/program gates, real SDK partial signatures and once-only cognitive feedback. They use ephemeral unfunded fixture keys and synthetic RPC responses. A funded end-to-end merchant test and the exact launch fee path still require configuration.

References: [x402 buyers](https://docs.x402.org/getting-started/quickstart-for-buyers), [Solana signing](https://solana.com/docs/core/transactions/signing-in-production), [Pump public interfaces](https://github.com/pump-fun/pump-public-docs).

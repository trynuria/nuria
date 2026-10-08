# Financial activation

Production execution is disabled. The token mint and current creator-fee beneficiary are not configured. Offline rail tests and a restricted managed-wallet signature test do not establish a working fee-funded purchase loop.

## One managed operating wallet

The preferred route uses the same dedicated project wallet for creator-fee receipts and operating inventory. Its recovery owner stays outside the application host. Privy gives the isolated financial worker a separate, restricted runtime delegate; the public API and neural workers have no signing authority. No reserve vault or multisig is required.

The signer verifies the live owner, delegate attachment and independently owned policy hash. The runtime authorization public key must differ from the recovery owner and policy owner. This configuration uses direct P-256 authorization resources, not a voting quorum. Arbitrary message signing, key export and an allow-all runtime policy are unsupported.

Project credentials stay outside source and public cache, in 0600 files readable only by the financial service. Custody-level recipient and instruction restrictions complement local caps. The operator retains ultimate configuration authority. Local daily limits are not an independently enforced Solana custody-level cumulative cap; all funds in the operating wallet remain exposed to whatever its actual signing policy permits.

## Creator fees

```text
mint-specific trades → protocol fee buckets → sweep and claim
  → verified agent beneficiary → bounded SOL-to-USDC conversion
  → approved purchase → payment, delivery and outcome records
```

A claim pays the current protocol beneficiary. It cannot redirect an unrelated launch wallet's fees. With `funding_mode: direct_creator`, the configured creator and spending addresses must match. If a separate project managed creator wallet is necessary, `managed_creator` uses a separate delegate to forward SOL only to the operating address within explicit native-unit limits. `owner_transfer` records a manual funding dependency. None of these modes changes the Pump beneficiary.

Collection is checked no more often than every sixty seconds, with a minimum useful balance and a separate gas ceiling. This is a polling interval, not a promise that funds settle every minute. An unresolved collection blocks a replacement transaction. A creator vault can aggregate several coins; its balance is not automatically token-specific revenue. Record trade accrual, sweep, claim and wallet credit separately so the same fee is not counted repeatedly.

Pump's newer trade instructions retain fees on the bonding curve or pool. The standard native adapter now prepends a curve `sweep_creator_fee` when its bucket is nonzero. After migration it also sweeps the canonical pool, consolidates WSOL creator-vault fees through the existing `transfer_creator_fees_to_pump` instruction, and uses `collect_creator_fee_v2` to pay native SOL to the fixed beneficiary. All four unsigned instructions match current official SDK vectors. Negative virtual quote reserves caused by retained fees are accepted; positive boost reserves are not. This is offline interface verification, not a launched-token claim. Collection refuses account creation, closure, ownership change or resizing in simulation: no rent allowance is implicit. Older accounts or missing vaults may therefore require a separately reviewed setup before collection. Fee sharing, holder rewards, cashback, mayhem and nonstandard quote assets also require separate reviewed adapters. Pin the actual Pump, PumpSwap and Jupiter program-data hashes; an upgrade requires review.

## Standing limits and test authority

The production merchant ceilings are **2 USDC per job and 25 USDC per UTC day**, with a 5-USDC inventory floor, sixty-second job cooldown, three-unresolved-job breaker and 40,000 monthly signing-request ceiling. They are ceilings, not targets. Incoming trades do not each trigger a signature or purchase.

Bounded launch testing has a separate **25 USD total ceiling for the whole test session**, including pending liabilities, swap losses, native fees and rent. Restart or midnight does not renew it. Jobs remain capped at 2 USD. `SessionBudget` uses durable immediate SQLite transactions; an ambiguous disclosure reserves the full job allowance until verified reconciliation. A funded wallet balance is inventory, not permission to spend it all.

SOL conversion and collection require explicit lamport, retained-balance, slippage, gas and rent limits. No native spending allowance is inferred from a USDC merchant cap. Refresh valuation before a native expense and account for it against the same total test ceiling.

## Choosing information

The fixed SOL/USD comparison now checks a free public price source before any basic-price purchase. Its timestamp must be no more than 120 seconds old; requests are at least sixty seconds apart, with a cache that survives restart. The choice, response hash, source, original observation time and declined paid-price ceiling are journaled. Reused data keeps its original receipt time. Missing, stale, invalid or rate-limited data defers the purchase; it does not authorize an automatic paid fallback.

This rule establishes a bounded ability to refuse a redundant purchase. It does not show that free data is equivalent to every paid feed or to the structured token-forecast contract. It adds no forecast or neural-learning reward. Real free acquisition, paid settlement, delivered utility and later measured improvement remain separate evidence.

## Merchant and recovery gates

The forecast contract needs an actual provider delivering `nuria.forecast.v1`; none has been demonstrated. The separate CoinGecko adapter accepts credential-free Solana-USDC x402 invoices for market context. A funded integration request returned a facilitator validation error; finalized settlement and delivered data remain unverified. A free price endpoint is also available, so a paid price must not be described as a unique capability or learning benefit.

A disclosed authorization never triggers an automatic replacement payment. The worker first checks a retained receipt. Without one, it can inspect finalized wallet transactions back to a checkpoint recorded before signing. A matching transaction still requires exact USDC deltas. An expired blockhash can close the job only after the scan reaches that checkpoint with no matching inclusion. Missing history, transaction data or an old unanchored authorization stays unresolved. This is evidence from the configured RPC provider, not an independent guarantee of complete chain history.

Payment, delivered bytes and measured usefulness are separate gates. A market-price response receives no implemented neural-learning credit. A paid forecast can be scored against a later verified input; that operational comparison does not establish causal superiority over a simpler controller.

## Activation checklist

1. Provide the exact launched mint and verify its current beneficiary, launch mode and pool.
2. Bind that beneficiary to the intended managed operating wallet, or configure the explicit forwarding/manual path.
3. Review current protocol interfaces and deployed program hashes; verify the exact token sweep-and-claim in simulation and finalized execution, including any reviewed account-rent setup.
4. Attach the exact reviewed runtime custody policy and independent recovery owner.
5. Set native ceilings and initial SOL/USDC inventory; verify account ownership and delegate state.
6. Verify a compatible merchant's current invoice, successful finalized payment and valid delivery.
7. Rehearse restart, ambiguous response and duplicate recovery before enabling the fixed catalog.

Read-only preflight reports gaps; it does not authorize activation:

```sh
python -m commerce.readiness --policy /etc/nuria-commerce/policy.json --ledger /var/lib/nuria/commerce/commerce.sqlite3
```

Examples under `commerce/` are deliberately disabled. Jupiter conversion, collection and purchases each need their own reviewed configuration. x402 batching is a later option with channel, escrow, voucher and reconciliation assumptions; no batch channel is operational or opened automatically.

References: [Pump sweep interfaces](https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/SWEEP_FEES.md), [Privy policies](https://docs.privy.io/controls/policies/overview), [Jupiter build](https://developers.jup.ag/docs/swap/build), [Solana wallet history](https://solana.com/docs/rpc/http/getsignaturesforaddress), [blockhash validity](https://solana.com/docs/rpc/http/isblockhashvalid).

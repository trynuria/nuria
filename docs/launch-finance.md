# Financial activation

The installed services and public ledger are ready for configuration. No production wallet, funded allowance or paid merchant transaction has been created by this release. Execution remains disabled.

## Project accounts

Use a separate Privy application and a project-only Jupiter API key. The runtime receives a delegated authorization key, not the wallet owner's recovery authority. It cannot own the wallet or policy. The managed signer checks the live wallet, delegate, independently owned policy and direct owner quorums before signing a parsed transaction. It derives the runtime authorization public key and rejects membership in either owner quorum, even when quorum IDs differ. Nested or user-based ownership requires its own reviewed adapter; this configuration uses direct P-256 authorization keys. Do not enable unrestricted message signing, key export or an allow-all policy.

Set up a separate Squads V4 reserve with an independent quorum of at least two voting members. Nuria's operating member has execution permission only. Configuration authority must be the null System address, so changes go through the quorum. Bind the allowance to exactly one operating destination. An empty destination list is rejected.

Project credentials stay outside source and public cache. The managed-wallet JSON is 0600 and owned by the financial service. Owner recovery keys remain outside the application host. The creator-wallet delegate uses a separate 0600 configuration; it cannot act as the operating or reserve owner. The public API and neural worker receive neither custody credentials nor wallet secrets.

## Launch identity and allowances

For an ordinary wallet beneficiary, the creator wallet must be a separate project managed wallet, with a second delegate whose custody policy permits only an exact SOL transfer to the reserve. The `sweep` adapter forwards to that one destination with its own native-SOL, retained-balance and gas ceilings. It does not change the Pump beneficiary. If fees are already paid directly to the verified reserve vault, forwarding is unnecessary. A third-party or manually controlled creator wallet cannot be swept by Nuria's operating signer; that path is a configuration gap, not an automated loop.

Configure the exact mint and verified creator-fee beneficiary, then the exact PumpSwap pool when the coin graduates. Read-only preflight checks current recipients and mode flags. Fee sharing, holder rewards, cashback, mayhem, nonstandard quotes and unreviewed layouts stop collection. Pin the deployed Pump, PumpSwap, Squads and Jupiter program-data hashes after review; upgrades require a new review.

The agreed merchant ceilings are **2 USDC per job and 25 USDC per UTC day**. A 5 USDC inventory floor, minimum 60-second job interval, three-unresolved-job breaker and 40,000 monthly signing-request ceiling provide additional operating limits. These are ceilings, not purchase targets.

USDC reserve replenishment has its own destination-bound daily allowance of at most 25 USDC. If the reserve holds SOL fees instead, configure a separate, explicitly approved native-SOL allowance and SOL conversion ceiling. SOL caps are in lamports; they cannot promise a fixed USD value. Network fees, rent and inventory conversions are recorded separately from merchant expenditure. No native-SOL allowance or gas budget is inferred from the 25-USDC merchant ceiling.

Provide a small initial operating USDC balance and enough SOL for the configured funding/conversion network fees and token-account rent. First verify a real minimum-price purchase, its finalized recipient delta, delivered result and restart reconciliation. Funding the wallet alone is not a complete loop test.

## Read-only preflight

Run from the configured financial service environment:

```sh
python -m commerce.readiness --policy /etc/nuria-commerce/policy.json --ledger /var/lib/nuria/commerce/commerce.sqlite3
```

This does not sign or broadcast. It reports missing inputs rather than granting activation. Template files under `commerce/` are deliberately disabled. They are configuration examples, not pre-authorized recipients or live wallets.

## Merchants

The forecast contract requires an actual merchant delivering `nuria.forecast.v1`. No such provider has been demonstrated. A separate CoinGecko adapter can buy Solana/USD market context using a credential-free x402 endpoint. An unpaid mainnet-Solana-USDC invoice was observed on 2026-10-08. That observation does not prove paid delivery, forecasting value or neural learning. Revalidate the recipient, facilitator fee payer and invoice before activation.

No separate universal x402 API key is needed for that credential-free endpoint. Other merchants can require their own accounts. A provider must support the specific payment scheme and network we test; protocol-wide support does not establish merchant support.

## Higher payment volume

Incoming trades are read-only inputs, not payment or custody-signing requests. Purchases, reconciliation and wallet backfills have independent processes and ceilings. Reaching a spend or signature ceiling pauses purchases while the neural stream continues.

Privy's advertised included signing allowance is 50,000 per month; the local limit is deliberately lower. Account pricing and provider rate limits still require confirmation before funding. A Solana cumulative daily cap is enforced locally; Privy does not currently provide the same stateful Solana policy limit as Ethereum. Squads caps replenishment onchain, while existing operating balances remain an additional exposure.

x402 Solana batch channels can reduce onchain settlement frequency. They still introduce escrow, voucher authority, counterparty trust, refund rules and reconciliation. Client-signed vouchers can retain per-signature custody costs. Server-signed channels let a trusted operator claim the whole deposited amount. No batch channel is opened automatically or described as operational until an actual merchant, facilitator and deployed channel program have been verified.

References: [Privy policies](https://docs.privy.io/controls/policies/overview), [Privy pricing](https://www.privy.io/pricing), [Squads spending limits](https://docs.squads.so/main/development/reference/spending-limits), [Jupiter v2 build](https://developers.jup.ag/docs/swap/build), [x402 Solana batching](https://docs.x402.org/schemes/batch-settlement), [CoinGecko x402](https://docs.coingecko.com/ai-integration/x402).

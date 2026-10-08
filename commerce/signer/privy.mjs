import { readFile, lstat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { PrivyClient } from "@privy-io/node";
import { checkQuorums } from "./custody-policy.mjs";
import { readRequest } from "./request-input.mjs";

const ordered = (v) =>
  Array.isArray(v)
    ? v.map(ordered)
    : v && typeof v === "object"
      ? Object.fromEntries(
          Object.keys(v)
            .sort()
            .map((k) => [k, ordered(v[k])]),
        )
      : v;
const digest = (v) =>
  createHash("sha256")
    .update(JSON.stringify(ordered(v)))
    .digest("hex");
try {
  const file = process.env.NURIA_PRIVY_CREDENTIALS;
  const metadata = await lstat(file);
  if (
    metadata.isSymbolicLink() ||
    metadata.size > 16384 ||
    (metadata.mode & 0o777) !== 0o600 ||
    metadata.uid !== process.getuid()
  )
    throw Error("Private custody configuration required");
  const config = JSON.parse(await readFile(file, "utf8"));
  const input = await readRequest();
  if (
    !config.authorization_key ||
    !config.signer_id ||
    !config.policy_sha256 ||
    !config.owner_id ||
    config.signer_id === config.owner_id
  )
    throw Error("Separate owner and delegate required");
  const client = new PrivyClient({
    appId: config.app_id,
    appSecret: config.app_secret,
    maxRetries: 0,
    timeout: 15000,
  });
  const wallet = await client.wallets().get(config.wallet_id);
  const delegate = wallet.additional_signers.find(
    (s) => s.signer_id === config.signer_id,
  );
  if (
    wallet.address !== input.wallet ||
    wallet.chain_type !== "solana" ||
    wallet.owner_id !== config.owner_id ||
    !delegate ||
    delegate.override_policy_ids?.length !== 1 ||
    delegate.override_policy_ids[0] !== config.policy_id
  )
    throw Error("Custody wallet or delegate differs from reviewed configuration");
  const policy = await client.policies().get(config.policy_id);
  const terms = {
    version: policy.version,
    chain_type: policy.chain_type,
    owner_id: policy.owner_id,
    rules: policy.rules,
  };
  if (
    !policy.owner_id ||
    policy.owner_id === config.signer_id ||
    digest(terms) !== config.policy_sha256
  )
    throw Error("Custody policy is unowned or changed");
  const quorumEvidence = checkQuorums(
    await client.keyQuorums().get(config.signer_id),
    await Promise.all(
      [...new Set([config.owner_id, policy.owner_id])].map((id) =>
        client.keyQuorums().get(id),
      ),
    ),
    config.authorization_key,
  );
  if (input.operation === "check") {
    process.stdout.write(
      JSON.stringify({
        verified: true,
        ...quorumEvidence,
        wallet: wallet.address,
        policy_sha256: digest(terms),
        owner_id: config.owner_id,
        signer_id: config.signer_id,
      }),
    );
  } else if (
    input.operation === "sign" &&
    /^[a-f0-9]{64}$/.test(input.request_id) &&
    typeof input.transaction === "string" &&
    input.transaction.length < 4096
  ) {
    const signed = await client
      .wallets()
      .solana()
      .signTransaction(config.wallet_id, {
        transaction: input.transaction,
        idempotency_key: input.request_id,
        request_expiry: Date.now() + 30000,
        authorization_context: {
          authorization_private_keys: [config.authorization_key],
        },
      });
    process.stdout.write(JSON.stringify(signed));
  } else throw Error("Unsupported custody request");
} catch (_) {
  process.stderr.write(
    "Managed custody check or signature failed; no credential details logged.\n",
  );
  process.exitCode = 1;
}

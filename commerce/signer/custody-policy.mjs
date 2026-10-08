import { createPrivateKey, createPublicKey } from "node:crypto";

const publicKey = (encoded) => {
  const key = createPublicKey({
    key: Buffer.from(encoded, "base64"),
    format: "der",
    type: "spki",
  });
  if (key.asymmetricKeyDetails?.namedCurve !== "prime256v1")
    throw Error("Only P-256 authorization keys are supported");
  return key.export({ format: "der", type: "spki" }).toString("base64");
};

const keys = (quorum) => {
  if (
    !Array.isArray(quorum.authorization_keys) ||
    !quorum.authorization_keys.length ||
    quorum.authorization_keys.length > 16 ||
    !Number.isInteger(quorum.authorization_threshold) ||
    quorum.authorization_threshold < 1 ||
    quorum.authorization_threshold > quorum.authorization_keys.length ||
    quorum.user_ids?.length ||
    quorum.key_quorum_ids?.length
  )
    throw Error("Quorum ownership requires direct, reviewed authorization keys");
  const result = quorum.authorization_keys.map((item) => publicKey(item.public_key));
  if (new Set(result).size !== result.length) throw Error("Duplicate quorum keys");
  return result;
};

export function checkQuorums(delegate, owners, authorizationKey) {
  const raw = authorizationKey.replace(/^wallet-(auth|api):/, "");
  const secret = createPrivateKey({
    key: Buffer.from(raw, "base64"),
    format: "der",
    type: "pkcs8",
  });
  const runtime = createPublicKey(secret)
    .export({ format: "der", type: "spki" })
    .toString("base64");
  const signers = keys(delegate);
  if (signers.length !== 1 || signers[0] !== runtime)
    throw Error("Delegate differs from the single runtime authorization key");
  for (const owner of owners) {
    if (keys(owner).includes(runtime))
      throw Error("Runtime authorization key also controls an owner quorum");
  }
  return { direct_key_ownership: true, runtime_excluded_from_owner_quorums: true };
}

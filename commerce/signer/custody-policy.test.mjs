import test from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { checkQuorums } from "./custody-policy.mjs";

const key = () => {
  const pair = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  return {
    public: pair.publicKey.export({ format: "der", type: "spki" }).toString("base64"),
    private: pair.privateKey
      .export({ format: "der", type: "pkcs8" })
      .toString("base64"),
  };
};
const quorum = (publicKeys) => ({
  authorization_keys: publicKeys.map((public_key) => ({ public_key })),
  authorization_threshold: 1,
  user_ids: [],
  key_quorum_ids: [],
});

test("delegate and owner keys must be independent even with different quorum IDs", () => {
  const runtime = key();
  const owner = key();
  assert.equal(
    checkQuorums(
      quorum([runtime.public]),
      [quorum([owner.public])],
      `wallet-auth:${runtime.private}`,
    ).runtime_excluded_from_owner_quorums,
    true,
  );
  assert.throws(() =>
    checkQuorums(quorum([runtime.public]), [quorum([runtime.public])], runtime.private),
  );
});

test("incorrect delegate, opaque nesting and duplicate owner keys fail closed", () => {
  const runtime = key();
  const other = key();
  assert.throws(() =>
    checkQuorums(quorum([other.public]), [quorum([other.public])], runtime.private),
  );
  assert.throws(() =>
    checkQuorums(
      quorum([runtime.public]),
      [{ ...quorum([other.public]), key_quorum_ids: ["opaque"] }],
      runtime.private,
    ),
  );
  assert.throws(() =>
    checkQuorums(
      quorum([runtime.public]),
      [quorum([other.public, other.public])],
      runtime.private,
    ),
  );
});

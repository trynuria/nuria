"use strict";

// Versioned, lossless JSON updates. The RFC 6902 decoder is fast-json-patch
// 3.1.1 (MIT); tuple packing is shared with the benchmark's existing baseline.
globalThis.NuriaLiveState = (() => {
  const version = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  function expand(patch) {
    if (!Array.isArray(patch) || patch.length > 100000)
      throw new Error("Invalid patch");
    return patch.map((op) => {
      if (
        !Array.isArray(op) ||
        !Number.isInteger(op[0]) ||
        op[0] < 0 ||
        op[0] > 2 ||
        op.length !== (op[0] === 1 ? 2 : 3) ||
        typeof op[1] !== "string" ||
        (op[1] !== "" && !op[1].startsWith("/"))
      )
        throw new Error("Invalid patch operation");
      return {
        op: ["add", "remove", "replace"][op[0]],
        path: op[1],
        ...(op[0] === 1 ? {} : { value: op[2] }),
      };
    });
  }
  async function read(previous, base, signal, request = globalThis.fetch) {
    async function fetchState(incremental) {
      const response = await request(
        "/api/studio" + (incremental ? `?since=${base}` : ""),
        {
          signal,
          cache: "no-store",
          credentials: "omit",
          headers: { Accept: "application/json" },
        },
      );
      const next = response.headers.get("X-Studio-Version");
      if (!response.ok) throw new Error(`Studio response ${response.status}`);
      if (response.status === 204) {
        if (!incremental || next !== base) throw new Error("Invalid unchanged state");
        return { data: previous, version: next };
      }
      const update = response.headers.get("X-Studio-Update");
      const payload = await response.json();
      if (update === "patch-v1") {
        if (
          !incremental ||
          response.headers.get("X-Studio-Base") !== base ||
          !version(next)
        )
          throw new Error("Mismatched patch base");
        const data = globalThis.jsonpatch.applyPatch(
          previous,
          expand(payload),
          true,
          false,
          true,
        ).newDocument;
        return { data, version: next };
      }
      if (update && update !== "full") throw new Error("Unknown update format");
      // Compatible with an older server during rollback. Missing version means
      // the following poll uses a complete snapshot again.
      return { data: payload, version: version(next) ? next : null };
    }
    if (previous && version(base)) {
      try {
        return await fetchState(true);
      } catch (error) {
        if (signal?.aborted) throw error;
        // Evicted bases normally return full state directly. Invalid patches,
        // prototype-sensitive keys, or a mixed deployment recover in one read.
      }
    }
    return fetchState(false);
  }
  return { read, expand };
})();

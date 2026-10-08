const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync(require.resolve("../observatory.html"), "utf8");
const helper = source.match(
  /async function fetchJSON\(path, options\) \{[\s\S]*?\n      \}/,
)[0];

test("a stalled evidence request aborts and a subsequent refresh succeeds", async () => {
  let stalled = true;
  const fetch = async (_path, { signal }) => {
    if (!stalled) return { ok: true, json: async () => ({ fresh: true }) };
    return new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(Error("Evidence timeout")), {
        once: true,
      });
    });
  };
  const getJSON = new Function(
    "fetch",
    "AbortController",
    "setTimeout",
    "clearTimeout",
    helper + "; return fetchJSON;",
  )(fetch, AbortController, (callback) => setTimeout(callback, 20), clearTimeout);
  await assert.rejects(getJSON("/api/status"), /Evidence timeout/);
  stalled = false;
  assert.deepEqual(await getJSON("/api/status"), { fresh: true });
});

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { Readable } from "node:stream";
import test from "node:test";
import { readRequest } from "./request-input.mjs";

test("reads a complete request from a real subprocess stdin pipe", () => {
  const module = new URL("./request-input.mjs", import.meta.url).href;
  const result = spawnSync(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      `import { readRequest } from ${JSON.stringify(module)}; console.log(JSON.stringify(await readRequest()));`,
    ],
    { input: '{"operation":"check","wallet":"test"}', encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { operation: "check", wallet: "test" });
});

test("rejects oversized requests split over multiple chunks", async () => {
  await assert.rejects(
    readRequest(Readable.from([Buffer.alloc(4096), Buffer.alloc(4097)])),
    /byte limit/,
  );
});

test("rejects invalid JSON and non-object requests", async () => {
  for (const value of ["{", "null", "[]", "1"])
    await assert.rejects(readRequest(Readable.from([value])));
});

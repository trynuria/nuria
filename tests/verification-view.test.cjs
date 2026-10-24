const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("../observatory.html"), "utf8");
const handler = source.match(
  /\$\("verify"\)\.addEventListener\("click", async \(\) => \{([\s\S]*?)\n      \}\);\n      if \(reduced\)/,
)[1];

function harness(fetchJSON) {
  const button = { disabled: false };
  const result = { textContent: "" };
  const context = vm.createContext({
    $: (id) => (id === "verify" ? button : result),
    fetchJSON,
    fmt: (value) => value.toLocaleString("en-US"),
    Date,
    Number,
  });
  const click = vm.runInContext(`(async () => {${handler}\n})`, context);
  return { button, result, click };
}

const completed = () => ({
  phase: "verified",
  valid: true,
  checked: 1200,
  target_seq: 1200,
  observed_head_seq: 1200,
  verified_utc: new Date().toISOString(),
});

test("an incomplete receipt pass shows progress without claiming full verification", async () => {
  const { result, click } = harness(async () => ({
    ...completed(),
    phase: "catching_up",
    valid: false,
    checked: 1200,
    target_seq: 3600,
    observed_head_seq: 4800,
  }));
  await click();
  assert.match(result.textContent, /1,200 of 4,800/);
  assert.match(result.textContent, /Full history is not yet verified/);
  assert.doesNotMatch(result.textContent, /undefined|failed|integrity confirmed/i);
});

test("only a complete and consistent verifier result confirms receipt integrity", async () => {
  const { result, click } = harness(async () => completed());
  await click();
  assert.match(result.textContent, /^Verified 1,200 durable receipts/);
  assert.match(result.textContent, /integrity confirmed/);
});

test("a genuine mismatch reports its receipt without interpolating missing identifiers", async () => {
  for (const first_failure of [1201, undefined, null, "1201", -1]) {
    const { result, click } = harness(async () => ({
      ...completed(),
      phase: "error",
      valid: false,
      first_failure,
    }));
    await click();
    if (first_failure === 1201) assert.match(result.textContent, /receipt #1,201/);
    else assert.doesNotMatch(result.textContent, /receipt #/);
    assert.match(result.textContent, /Verification failed/);
    assert.doesNotMatch(result.textContent, /undefined|null/);
  }
});

test("unknown, unavailable and contradictory evidence never appears verified", async () => {
  for (const evidence of [
    null,
    {},
    { ...completed(), phase: "unavailable", valid: false },
    { ...completed(), phase: "new_phase" },
    { ...completed(), checked: 1000 },
    { ...completed(), valid: "true" },
    { ...completed(), verified_utc: "not a date" },
    { ...completed(), checked: "1200" },
    { ...completed(), target_seq: undefined },
    { ...completed(), observed_head_seq: 1300 },
    { ...completed(), phase: "catching_up", valid: false, target_seq: null },
  ]) {
    const { result, click } = harness(async () => evidence);
    await click();
    assert.match(result.textContent, /^Verification unavailable/);
    assert.doesNotMatch(result.textContent, /undefined|null|integrity confirmed/);
  }
});

test("the button stays disabled during a request and recovers after a network error", async () => {
  let reject;
  const { button, result, click } = harness(
    () => new Promise((_resolve, fail) => (reject = fail)),
  );
  const pending = click();
  assert.equal(button.disabled, true);
  assert.match(result.textContent, /^Checking/);
  reject(Error("Private backend error"));
  await pending;
  assert.equal(button.disabled, false);
  assert.match(result.textContent, /^Verification unavailable/);
  assert.doesNotMatch(result.textContent, /Private backend error/);
});

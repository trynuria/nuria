const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

function harness() {
  const elements = new Map();
  function element() {
    return {
      textContent: "",
      title: "",
      style: {},
      dataset: {},
      children: [],
      attributes: {},
      listeners: {},
      disabled: false,
      setAttribute(name, value) {
        this.attributes[name] = value;
      },
      removeAttribute(name) {
        delete this.attributes[name];
      },
      replaceChildren(...children) {
        this.children = children;
      },
      append(...children) {
        this.children.push(...children);
      },
      addEventListener(name, callback) {
        this.listeners[name] = callback;
      },
    };
  }
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const wallet = "11111111111111111111111111111111"; // Unfunded public fixture, no signer.
  const fresh = () => ({
    updated_utc: new Date().toISOString(),
    phase: "guarded",
    financial_execution: false,
    policy: {
      spending_wallet: wallet,
      creator_wallet: wallet,
      per_day_micro_usdc: 25000000,
      per_job_micro_usdc: 2000000,
    },
    counts: { delivered: 1 },
    records: [{ state: "delivered", hash: "a".repeat(64), job_id: "fixture" }],
    ledger_index: { pages: 1 },
    usdc_balance: { micro_usdc: 20000000, checked_at: Date.now() / 1000 },
    settled_micro_usdc: 10000,
    daily_committed_micro_usdc: 10000,
  });
  const context = vm.createContext({
    Date,
    Number,
    JSON,
    Error,
    Object,
    Array,
    document: { hidden: false, createElement: element },
    navigator: {
      clipboard: {
        writeText: async (text) => {
          context.copied = text;
        },
      },
    },
    $: get,
    fmt: String,
    setInterval: () => {},
    fetchJSON: async () => {
      if (context.failure) throw Error("Offline fixture");
      return context.evidence;
    },
  });
  context.evidence = fresh();
  vm.runInContext(
    fs.readFileSync(require.resolve("../commerce-view.js"), "utf8"),
    context,
  );
  return {
    context,
    get,
    fresh,
    poll: () => vm.runInContext("pollCommerce()", context),
  };
}

test("a failed refresh clears metrics, receipts and keyboard download", async () => {
  const { context, get, poll } = harness();
  await poll();
  assert.equal(get("commercePhase").textContent, "Execution off");
  assert.equal(get("commerceDownload").attributes.href, "/download/commerce?page=0");
  assert.equal(get("commerceDelivered").textContent, "1");
  context.failure = true;
  await poll();
  assert.equal(get("commercePhase").textContent, "Evidence unavailable");
  assert.equal(get("commerceBalance").textContent, "—");
  assert.equal(get("commerceDelivered").textContent, "—");
  assert.equal(get("commerceTimeline").children.length, 0);
  assert.equal(get("commerceDownload").attributes.href, undefined);
  assert.equal(get("commerceDownload").attributes.tabindex, "-1");
  assert.equal(context.copied, undefined);
});

test("stale, future, unknown and incomplete evidence never becomes zero activity", async () => {
  const { context, get, fresh, poll } = harness();
  const variants = [
    (value) => ({ ...value, updated_utc: new Date(Date.now() - 61000).toISOString() }),
    (value) => ({ ...value, updated_utc: new Date(Date.now() + 61000).toISOString() }),
    (value) => ({ ...value, phase: "unknown" }),
    (value) => ({ ...value, counts: null }),
    (value) => ({ ...value, counts: { delivered: -1 } }),
    (value) => ({ ...value, financial_execution: undefined }),
    (value) => ({
      ...value,
      usdc_balance: { micro_usdc: 20000000, checked_at: Date.now() / 1000 - 61 },
    }),
  ];
  for (const change of variants) {
    context.evidence = change(fresh());
    await poll();
    assert.equal(get("commerceDelivered").textContent, "—");
    assert.equal(get("commercePhase").textContent, "Evidence unavailable");
    assert.equal(get("commerceDownload").attributes.href, undefined);
  }
});

test("a verified refresh restores payment evidence without displaying wallet identity", async () => {
  const { context, get, fresh, poll } = harness();
  context.failure = true;
  await poll();
  context.failure = false;
  context.evidence = fresh();
  await poll();
  assert.equal(get("commerceDownload").attributes.href, "/download/commerce?page=0");
  assert.equal(get("commerceDownload").attributes.tabindex, "0");
  assert.equal(get("commerceTimeline").children.length, 1);
  assert.equal(
    get("commerceRecords").textContent.includes(context.evidence.policy.creator_wallet),
    false,
  );
  assert.equal(get("commerceWallet").textContent, "");
});

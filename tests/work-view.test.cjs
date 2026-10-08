const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

async function harness() {
  const elements = new Map();
  const element = () => ({
    textContent: "",
    dataset: {},
    children: [],
    attributes: {},
    listeners: {},
    scrollTop: 0,
    setAttribute(k, v) {
      this.attributes[k] = v;
    },
    removeAttribute(k) {
      delete this.attributes[k];
    },
    append(...children) {
      this.children.push(...children);
    },
    replaceChildren(...children) {
      this.children = children;
    },
    addEventListener(k, callback) {
      this.listeners[k] = callback;
    },
    focus() {},
    scrollTo() {},
  });
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const fresh = () => ({
    schema: "nuria.work.v1",
    updated_utc: new Date().toISOString(),
    phase: "guarded",
    financial_execution: false,
    jobs: [],
    total_jobs: 0,
    catalog: [],
    coverage: { truncated: false },
    money: {
      balance: null,
      committed_micro_usdc: 0,
      settled_micro_usdc: 0,
      missing: [],
      policy: {
        per_job_micro_usdc: 2000000,
        per_day_micro_usdc: 25000000,
        reserve_micro_usdc: 5000000,
        spending_wallet: null,
      },
    },
    integrity: { valid: true, events: 0, head: "0".repeat(64) },
  });
  const context = vm.createContext({
    Date,
    Number,
    JSON,
    Error,
    Object,
    Array,
    Set,
    document: {
      hidden: false,
      activeElement: null,
      createElement: element,
      querySelectorAll: () => [],
      body: { classList: { toggle() {}, remove() {}, contains: () => false } },
    },
    window: { addEventListener() {} },
    location: { hash: "" },
    history: { pushState() {}, replaceState() {} },
    navigator: {
      clipboard: {
        writeText: async (value) => {
          context.copied = value;
        },
      },
    },
    $: get,
    commerceMoney: (value) => (Number.isInteger(value) ? `${value / 1e6} USDC` : "—"),
    setInterval: () => {},
    fetchJSON: async () => {
      if (context.offline) throw Error("Offline");
      return context.evidence;
    },
  });
  context.evidence = fresh();
  vm.runInContext(fs.readFileSync(require.resolve("../work-view.js"), "utf8"), context);
  await new Promise((resolve) => setImmediate(resolve));
  return { context, get, fresh, poll: () => vm.runInContext("pollWork()", context) };
}

test("missing balance remains unknown while a verified empty journal is explicit", async () => {
  const { get } = await harness();
  assert.equal(get("workBalance").textContent, "—");
  assert.equal(get("workSpent").textContent, "0 USDC");
  assert.equal(get("workHealth").textContent, "Execution off");
  assert.equal(get("workExport").attributes.href, "/api/work");
});

test("stale, future, incomplete or contradictory records clear all current financial evidence", async () => {
  const { context, get, fresh, poll } = await harness();
  const variants = [
    (value) => ({ ...value, updated_utc: new Date(Date.now() - 61000).toISOString() }),
    (value) => ({ ...value, updated_utc: new Date(Date.now() + 61000).toISOString() }),
    (value) => ({ ...value, phase: "unknown" }),
    (value) => ({
      ...value,
      money: {
        ...value.money,
        balance: { micro_usdc: 20, checked_at: Date.now() / 1000 - 61 },
      },
    }),
    (value) => ({ ...value, integrity: { ...value.integrity, valid: false } }),
    (value) => ({ ...value, total_jobs: -1 }),
  ];
  for (const change of variants) {
    context.evidence = change(fresh());
    await poll();
    assert.equal(get("workHealth").textContent, "Evidence unavailable");
    assert.equal(get("workSpent").textContent, "—");
    assert.equal(get("workExport").attributes.href, undefined);
  }
});

test("network failure clears financial evidence and refresh recovers without changing selected tabs", async () => {
  const { context, get, fresh, poll } = await harness();
  get("work-tab-treasury").listeners.click();
  context.offline = true;
  await poll();
  assert.equal(context.copied, undefined);
  context.offline = false;
  context.evidence = fresh();
  await poll();
  assert.equal(get("work-tab-treasury").attributes["aria-selected"], "true");
  assert.equal(get("work-panel-treasury").hidden, false);
  assert.equal(get("workHealth").textContent, "Execution off");
});

test("provider text is rendered as text, with no HTML interpretation", async () => {
  const { context, get, fresh, poll } = await harness();
  context.evidence = fresh();
  context.evidence.catalog = [
    {
      name: "<img src=x onerror=alert(1)>",
      detail: "<script>bad()</script>",
      status: "planned",
    },
  ];
  await poll();
  const card = get("workCatalog").children[0];
  assert.equal(card.children[1].textContent, "<img src=x onerror=alert(1)>");
  assert.equal(card.children[2].textContent, "<script>bad()</script>");
  assert.equal(card.innerHTML, undefined);
});

test("tabs support keyboard navigation without scrolling the page", async () => {
  const { get } = await harness();
  let prevented = false;
  get("work-tab-work").listeners.keydown({
    key: "End",
    preventDefault: () => {
      prevented = true;
    },
  });
  assert.equal(prevented, true);
  assert.equal(get("work-panel-evidence").hidden, false);
  assert.equal(get("work-tab-evidence").tabIndex, 0);
});

function commission() {
  return {
    id: "a".repeat(64),
    decision_hash: "b".repeat(64),
    contract_sha256: "d".repeat(64),
    dataset_sha256: "c".repeat(64),
    kind: "agent",
    title: "<script>Evaluate retention</script>",
    purpose: "Compare held-out performance",
    state: "proposed",
    action: "experiment",
    created_at: Date.now() / 1000 - 2,
    updated_at: Date.now() / 1000 - 1,
    committed_micro_usdc: 0,
    settlement: null,
    outcome: null,
    artifact_sha256: null,
    acceptance: null,
    provider: null,
  };
}

test("a local commission is visible without implying an order, payment or outcome", async () => {
  const { context, get, fresh, poll } = await harness();
  context.evidence = fresh();
  context.evidence.commissions = [commission()];
  await poll();
  const row = get("workJobs").children[0];
  assert.equal(row.children[0].textContent, "<script>Evaluate retention</script>");
  row.listeners.click();
  const facts = get("workDetail").children[3].children;
  assert.equal(facts[2].children[1].textContent, "No verified payment");
  assert.equal(facts[6].children[1].textContent, "Not measured");
  assert.equal(get("workHealth").textContent, "Execution off");
});

test("contradictory commission evidence clears the work view instead of implying success", async () => {
  const { context, get, fresh, poll } = await harness();
  for (const change of [
    { state: "evaluated" },
    { settlement: { finalized: true, transaction: "unverified" } },
    { artifact_sha256: "d".repeat(64) },
    { outcome: { reward: 1 } },
    { updated_at: Date.now() / 1000 + 20 },
  ]) {
    context.evidence = { ...fresh(), commissions: [{ ...commission(), ...change }] };
    await poll();
    assert.equal(get("workHealth").textContent, "Evidence unavailable");
    assert.equal(get("workExport").attributes.href, undefined);
  }
});

test("verified paid outcomes include failures and remain distinct from accepted delivery", async () => {
  const { context, get, fresh, poll } = await harness();
  for (const passed of [true, false]) {
    const item = {
      ...commission(),
      state: "evaluated",
      artifact_sha256: "e".repeat(64),
      payer: "0x" + "1".repeat(40),
      recipient: "0x" + "2".repeat(40),
      offer_sha256: "f".repeat(64),
      committed_micro_usdc: 1100000,
    };
    item.acceptance = {
      passed,
      metric: "brier",
      improvement: passed ? 0.1 : -0.1,
      artifact_sha256: item.artifact_sha256,
      dataset_sha256: item.dataset_sha256,
    };
    item.settlement = {
      finalized: true,
      chain_id: 8453,
      token: "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
      transaction: "0x" + "3".repeat(64),
      sender: item.payer,
      recipient: item.recipient,
      offer_sha256: item.offer_sha256,
      amount_micro_usdc: 1000000,
      block: 100,
    };
    item.outcome = {
      reward: passed ? 0.089 : -0.111,
      accepted: passed,
      artifact_sha256: item.artifact_sha256,
      dataset_sha256: item.dataset_sha256,
    };
    context.evidence = { ...fresh(), commissions: [item] };
    await poll();
    assert.equal(get("workHealth").textContent, "Execution off");
    get("workJobs").children[0].listeners.click();
    const facts = get("workDetail").children[3].children;
    assert.equal(
      facts[5].children[1].textContent.startsWith(passed ? "Passed" : "Failed"),
      true,
    );
    assert.equal(get("workResults").children.length, 1);
  }
});

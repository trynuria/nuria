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
    assert.equal(get("copyWorkWallet").disabled, true);
  }
});

test("network failure clears wallet access and refresh recovers without changing selected tabs", async () => {
  const { context, get, fresh, poll } = await harness();
  get("work-tab-treasury").listeners.click();
  context.offline = true;
  await poll();
  await get("copyWorkWallet").listeners.click();
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

/* Host-owned checks for discrete inventions; executed only in the OS sandbox. */
"use strict";
const fs = require("fs"),
  vm = require("vm"),
  crypto = require("crypto");
const input = JSON.parse(fs.readFileSync("inputs.json", "utf8"));
const contract = JSON.parse(fs.readFileSync("contract.json", "utf8"));
const hash = (value) =>
  crypto.createHash("sha256").update(JSON.stringify(value)).digest("hex");
let reused = 0;
function load() {
  const c = vm.createContext(
    { console: { log() {}, warn() {}, error() {} } },
    { codeGeneration: { strings: false, wasm: false } },
  );
  if (fs.existsSync("toolkit.js")) {
    vm.runInContext(fs.readFileSync("toolkit.js", "utf8"), c, { timeout: 800 });
    const original = c.NuriaEngine;
    Object.defineProperty(c, "NuriaToolkit", {
      value: Object.freeze(
        Object.fromEntries(
          Object.entries(original).map(([k, v]) => [
            k,
            typeof v === "function"
              ? (...args) => {
                  reused++;
                  return original[k](...args);
                }
              : v,
          ]),
        ),
      ),
      writable: false,
    });
  }
  vm.runInContext(fs.readFileSync("engine.js", "utf8"), c, { timeout: 800 });
  for (const method of ["create", "act", "metrics", "runTests"])
    if (typeof c.NuriaEngine?.[method] !== "function")
      throw Error("Missing invention engine method: " + method);
  return c;
}
function run(seed, actions) {
  const c = load();
  c.seed = seed;
  c.input = input;
  c.actions = JSON.parse(JSON.stringify(actions));
  vm.runInContext(
    "state=NuriaEngine.create(seed,input); changed=false; for(const action of actions) { const before=JSON.stringify(state); state=NuriaEngine.act(state,action); if(JSON.stringify(state)!==before) changed=true; } measures=NuriaEngine.metrics(state)",
    c,
    { timeout: 2000 },
  );
  const serialized = JSON.stringify(c.state);
  if (!serialized || serialized.length > 1000000)
    throw Error("State is missing or exceeds 1 MB");
  // Reset can legitimately return to the initial state after useful work.
  // Reject a wholly inert journey, not a journey with a reversible outcome.
  if (!c.changed) throw Error("Declared interaction has no effect on state");
  const values = Object.values(c.measures ?? {});
  if (
    !values.length ||
    values.some((v) => typeof v !== "number" || !Number.isFinite(v))
  )
    throw Error("Measurements must be finite numbers");
  return { hash: hash(c.state), metrics: JSON.parse(JSON.stringify(c.measures)) };
}
let project_tests;
try {
  for (const name of fs.readdirSync(".").filter((n) => n.endsWith(".js")))
    new vm.Script(fs.readFileSync(name, "utf8"));
  const a = run(17, contract.engine_actions),
    replay = run(17, contract.engine_actions),
    alternate = run(17, contract.alternate_actions);
  if (a.hash !== replay.hash) throw Error("Same-seed action replay differs");
  if (a.hash === alternate.hash)
    throw Error("The alternate interaction does not change the outcome");
  const probes = [29, 104729].map((seed) => run(seed, contract.engine_actions));
  const c = load();
  vm.runInContext("report=NuriaEngine.runTests()", c, { timeout: 4000 });
  const raw = JSON.stringify(c.report);
  if (!raw || raw.length > 48000) throw Error("Test report must fit 48 KB");
  const report = JSON.parse(raw),
    results = report.results;
  const valid =
    Array.isArray(results) &&
    results.length >= 3 &&
    results.every((r) => typeof r.name === "string" && typeof r.pass === "boolean");
  const passed =
    valid &&
    results.every((r) => r.pass) &&
    (report.passed === true ||
      (report.passed === results.length && report.failed === 0));
  project_tests = { executed: true, passed, report };
  console.log(
    JSON.stringify({
      passed,
      contract_version: 2,
      details: [
        "JavaScript compiles",
        "Same-seed action replay agrees",
        "Alternate inputs change the outcome",
        "Declared actions execute at three seeds",
        "Finite bounded state and measurements",
        passed
          ? "Project-defined logic tests passed"
          : "Project tests failed or need at least three named assertions",
      ],
      metrics: a.metrics,
      alternate_metrics: alternate.metrics,
      probes: probes.map((p) => p.metrics),
      project_tests,
      reused_calls: reused,
    }),
  );
} catch (error) {
  console.log(
    JSON.stringify({
      passed: false,
      contract_version: 2,
      details: [String(error.message).slice(0, 1000)],
      project_tests,
      reused_calls: reused,
    }),
  );
  process.exitCode = 1;
}

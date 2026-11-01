/* Executed inside the OS sandbox; model-authored code cannot read host state. */
"use strict";
const fs = require("fs");
const vm = require("vm");
const crypto = require("crypto");
const hash = (x) => crypto.createHash("sha256").update(JSON.stringify(x)).digest("hex");
const source = JSON.parse(fs.readFileSync("inputs.json", "utf8"));
let reused = 0;
function load() {
  const context = vm.createContext(
    { console: { log() {}, warn() {}, error() {} } },
    { codeGeneration: { strings: false, wasm: false } },
  );
  if (fs.existsSync("toolkit.js")) {
    vm.runInContext(fs.readFileSync("toolkit.js", "utf8"), context, { timeout: 800 });
    const original = context.NuriaEngine;
    if (!original) throw Error("Retained tool has no engine");
    const toolkit = Object.freeze(
      Object.fromEntries(
        Object.entries(original).map(([name, value]) => [
          name,
          typeof value === "function"
            ? (...args) => {
                if (["create", "step", "metrics"].includes(name)) reused++;
                return original[name](...args);
              }
            : value,
        ]),
      ),
    );
    Object.defineProperty(context, "NuriaToolkit", {
      value: toolkit,
      writable: false,
      configurable: false,
    });
  }
  vm.runInContext(fs.readFileSync("engine.js", "utf8"), context, { timeout: 800 });
  for (const name of ["create", "step", "metrics"])
    if (typeof context.NuriaEngine?.[name] !== "function")
      throw Error("Missing engine method: " + name);
  context.source = source;
  return context;
}
function simulate(seed, energy, curiosity, steps = 120) {
  const c = load();
  c.seed = seed;
  c.params = { energy, curiosity };
  c.steps = steps;
  vm.runInContext("state = NuriaEngine.create(seed, source)", c, { timeout: 800 });
  const initial = hash(c.state);
  vm.runInContext(
    "for (let i=0; i<steps; i++) state=NuriaEngine.step(state, 1/60, params); metrics=NuriaEngine.metrics(state)",
    c,
    { timeout: 2000 },
  );
  const values = Object.values(c.metrics ?? {});
  if (
    values.length < 2 ||
    values.some((x) => typeof x !== "number" || !Number.isFinite(x))
  )
    throw Error("Metrics must contain at least two finite numbers");
  const result = hash(c.state);
  if (result === initial) throw Error("The world does not evolve");
  if (JSON.stringify(c.state).length > 2000000)
    throw Error("State grows beyond the artifact limit");
  return { hash: result, metrics: JSON.parse(JSON.stringify(c.metrics)) };
}
function projectTests() {
  const context = load();
  if (typeof context.NuriaEngine.runTests !== "function") return { executed: false };
  try {
    vm.runInContext("testReport = NuriaEngine.runTests()", context, { timeout: 4000 });
    const raw = JSON.stringify(context.testReport);
    if (!raw || raw.length > 48000) throw Error("Project test report must fit 48 KB");
    const report = JSON.parse(raw);
    const results = report?.results;
    if (
      !Array.isArray(results) ||
      !results.length ||
      results.some((r) => typeof r?.pass !== "boolean")
    )
      throw Error(
        "Project tests must return nonempty results with name and boolean pass",
      );
    const passed =
      results.every((r) => r.pass) &&
      (report.passed === true ||
        (report.passed === results.length && report.failed === 0));
    return { executed: true, passed, report };
  } catch (error) {
    return {
      executed: true,
      passed: false,
      error: String(error.message).slice(0, 1000),
    };
  }
}
try {
  for (const name of fs.readdirSync(".").filter((x) => x.endsWith(".js")))
    new vm.Script(fs.readFileSync(name, "utf8"));
  const a = simulate(17, 0.6, 0.6),
    b = simulate(17, 0.6, 0.6);
  if (a.hash !== b.hash) throw Error("Same-seed replay is not deterministic");
  const low = simulate(17, 0.15, 0.15),
    high = simulate(17, 0.85, 0.85);
  if (low.hash === high.hash)
    throw Error("Energy and curiosity controls have no causal effect");
  const probes = [1, 104729].map((seed) => simulate(seed, 0.5, 0.5));
  const sustained = simulate(17, 0.6, 0.6, 1800);
  const project_tests = projectTests();
  console.log(
    JSON.stringify({
      passed: !project_tests.executed || project_tests.passed,
      details: [
        "JavaScript compiles",
        "Deterministic replay",
        "120-step dynamics on three seeds",
        "Control intervention changes the state",
        "Finite measured outputs",
        "30-second simulation remains finite with bounded state",
        ...(project_tests.executed
          ? [
              project_tests.passed
                ? "Project-defined engine tests passed"
                : "Project-defined engine tests failed; see project_tests",
            ]
          : []),
      ],
      metrics: a.metrics,
      probes: probes.map((x) => x.metrics),
      sustained_metrics: sustained.metrics,
      project_tests,
      reused_calls: reused,
    }),
  );
} catch (error) {
  console.log(
    JSON.stringify({
      passed: false,
      details: [String(error.message).slice(0, 1000)],
      reused_calls: reused,
    }),
  );
  process.exitCode = 1;
}

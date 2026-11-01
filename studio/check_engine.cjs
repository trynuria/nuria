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
        ["create", "step", "metrics"].map((name) => [
          name,
          (...args) => {
            reused++;
            return original[name](...args);
          },
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
function simulate(seed, energy, curiosity) {
  const c = load();
  c.seed = seed;
  c.params = { energy, curiosity };
  vm.runInContext("state = NuriaEngine.create(seed, source)", c, { timeout: 800 });
  const initial = hash(c.state);
  vm.runInContext(
    "for (let i=0; i<120; i++) state=NuriaEngine.step(state, 1/60, params); metrics=NuriaEngine.metrics(state)",
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
  console.log(
    JSON.stringify({
      passed: true,
      details: [
        "JavaScript compiles",
        "Deterministic replay",
        "120-step dynamics on three seeds",
        "Control intervention changes the state",
        "Finite measured outputs",
      ],
      metrics: a.metrics,
      probes: probes.map((x) => x.metrics),
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

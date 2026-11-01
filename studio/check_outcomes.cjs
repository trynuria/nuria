/* Host-owned bounded acceptance search. No candidate-authored test code runs. */
"use strict";
const fs = require("fs"),
  vm = require("vm"),
  crypto = require("crypto");
const sha = (text) => crypto.createHash("sha256").update(text).digest("hex");
const raw = fs.readFileSync("challenge.json", "utf8");
const spec = JSON.parse(raw);
const clone = (x) => JSON.parse(JSON.stringify(x));
function freeze(x) {
  if (x && typeof x === "object") {
    Object.values(x).forEach(freeze);
    Object.freeze(x);
  }
  return x;
}
function verify(engine, spec, input) {
  const copy = (x) => JSON.parse(JSON.stringify(x));
  const canonical = (x) =>
    x && typeof x === "object"
      ? Array.isArray(x)
        ? x.map(canonical)
        : Object.fromEntries(
            Object.keys(x)
              .sort()
              .map((k) => [k, canonical(x[k])]),
          )
      : x;
  const identity = (x) => {
    const s = JSON.stringify(canonical(x));
    if (!s || s.length > 64000) throw Error("Acceptance state exceeds 64 KB");
    return s;
  };
  const missing = Symbol("field not created yet");
  const read = (root, path, allowMissing) => {
    let value = root;
    for (const key of path.split(".")) {
      if (value == null || !Object.prototype.hasOwnProperty.call(value, key)) {
        if (allowMissing) return missing;
        throw Error("Missing asserted field: " + path);
      }
      value = value[key];
    }
    return value;
  };
  function assertions(state, expected, allowMissing = false) {
    const observed = { state, metrics: engine.metrics(copy(state)) };
    return expected.map((a) => {
      const values = a.sum ? a.sum.map((p) => read(observed, p, allowMissing)) : null;
      const field = values ? null : read(observed, a.path, allowMissing);
      // Reachability checks include the initial state. A later action may
      // create the asserted field; its absence is never evidence for any
      // operator, including "ne". Cases and invariants stay strict.
      if (field === missing || (values && values.includes(missing)))
        return {
          path: a.path,
          sum: a.sum,
          op: a.op,
          expected: a.value,
          missing: true,
          pass: false,
        };
      if (values && values.some((v) => typeof v !== "number" || !Number.isFinite(v)))
        throw Error("Sum requires finite numeric fields");
      const actual = values ? values.reduce((a, b) => a + b, 0) : field;
      const equal =
        JSON.stringify(canonical(actual)) === JSON.stringify(canonical(a.value));
      let pass = false;
      if (a.op === "eq") pass = equal;
      else if (a.op === "ne") pass = !equal;
      else if (a.op === "finite")
        pass = typeof actual === "number" && Number.isFinite(actual);
      else if (a.op === "includes")
        pass =
          (typeof actual === "string" || Array.isArray(actual)) &&
          actual.includes(a.value);
      else if (
        typeof actual === "number" &&
        Number.isFinite(actual) &&
        typeof a.value === "number"
      ) {
        pass =
          a.op === "gt"
            ? actual > a.value
            : a.op === "gte"
              ? actual >= a.value
              : a.op === "lt"
                ? actual < a.value
                : a.op === "lte" && actual <= a.value;
      }
      return { path: a.path, sum: a.sum, op: a.op, expected: a.value, actual, pass };
    });
  }
  return spec.claims.map((claim) => {
    const start = engine.create(17, copy(input));
    const queue = [{ state: copy(start), actions: [], depth: 0 }],
      seen = new Set([identity(start)]);
    let visited = 0,
      transitions = 0,
      capped = false,
      boundary = false,
      last;
    try {
      if (claim.kind === "case") {
        let state = start;
        for (const action of claim.actions) state = engine.act(state, copy(action));
        identity(state);
        const checks = assertions(state, claim.expect);
        return {
          name: claim.name,
          kind: claim.kind,
          passed: checks.every((x) => x.pass),
          checks,
          witness: claim.actions,
          visited: 1,
        };
      }
      for (let cursor = 0; cursor < queue.length; cursor++) {
        const item = queue[cursor];
        visited++;
        last = assertions(item.state, claim.expect, claim.kind === "reachable");
        const holds = last.every((x) => x.pass);
        if (claim.kind === "reachable" && holds)
          return {
            name: claim.name,
            kind: claim.kind,
            passed: true,
            checks: last,
            witness: item.actions,
            visited,
            transitions,
          };
        if (claim.kind === "invariant" && !holds)
          return {
            name: claim.name,
            kind: claim.kind,
            passed: false,
            checks: last,
            counterexample: item.actions,
            visited,
            transitions,
          };
        for (const action of claim.actions) {
          if (++transitions > 3072) {
            capped = true;
            break;
          }
          const next = engine.act(copy(item.state), copy(action)),
            key = identity(next);
          if (seen.has(key)) continue;
          if (item.depth >= (claim.max_depth || 6)) {
            boundary = true;
            continue;
          }
          if (seen.size >= (claim.max_states || 128)) {
            capped = true;
            break;
          }
          seen.add(key);
          queue.push({
            state: copy(next),
            actions: [...item.actions, copy(action)],
            depth: item.depth + 1,
          });
        }
        if (capped) break;
      }
      const complete = !capped && !boundary;
      return {
        name: claim.name,
        kind: claim.kind,
        passed: claim.kind === "invariant" && !capped,
        visited,
        transitions,
        complete,
        scope: complete
          ? "all states reachable under the declared action palette"
          : "bounded explored states only",
        outcome:
          claim.kind === "reachable"
            ? complete
              ? "unreachable under declared actions"
              : "no witness within search bounds"
            : capped
              ? "search capacity exceeded"
              : "invariant holds within explored states",
        last_checks: last,
      };
    } catch (error) {
      return {
        name: claim.name,
        kind: claim.kind,
        passed: false,
        error: String(error.message).slice(0, 600),
        visited,
        transitions,
      };
    }
  });
}
try {
  const c = vm.createContext(
    { console: { log() {}, warn() {}, error() {} } },
    { codeGeneration: { strings: false, wasm: false } },
  );
  if (fs.existsSync("toolkit.js")) {
    vm.runInContext(fs.readFileSync("toolkit.js", "utf8"), c, { timeout: 800 });
    c.NuriaToolkit = c.NuriaEngine;
  }
  vm.runInContext(fs.readFileSync("engine.js", "utf8"), c, { timeout: 800 });
  c.spec = freeze(clone(spec));
  c.input = freeze(JSON.parse(fs.readFileSync("inputs.json", "utf8")));
  vm.runInContext("results=(" + verify.toString() + ")(NuriaEngine,spec,input)", c, {
    timeout: 4500,
  });
  const results = clone(c.results);
  console.log(
    JSON.stringify({
      passed: results.length >= 2 && results.every((x) => x.passed),
      executed: true,
      challenge_sha256: sha(raw),
      results,
      details: ["Separate frozen acceptance criteria executed in offline sandbox"],
    }),
  );
} catch (error) {
  console.log(
    JSON.stringify({
      passed: false,
      executed: false,
      challenge_sha256: sha(raw),
      details: [String(error.message).slice(0, 1000)],
    }),
  );
  process.exitCode = 1;
}

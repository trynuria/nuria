const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const math = require("../neural-math.js");

test("future spikes cannot light a neuron; recorded spikes decay in model time", () => {
  assert.equal(math.energy([0.06], 0.05), 0);
  assert.equal(math.energy([0.06], 0.06), 1);
  assert.ok(Math.abs(math.energy([0.06], 0.068) - Math.exp(-1)) < 1e-12);
  assert.equal(math.energy([0.06], 0.11), 0);
});

test("signal transit uses each recorded delay and stops after arrival", () => {
  assert.equal(math.transit([0.01], 0.009, 10), null);
  assert.equal(math.transit([0.01], 0.01, 10), 0);
  assert.ok(Math.abs(math.transit([0.01], 0.015, 10) - 0.5) < 1e-12);
  assert.equal(math.transit([0.01], 0.021, 10), null);
  assert.equal(math.transit([0.01], 0.015, 0), null);
  assert.ok(Math.abs(math.transit([0.01, 0.014], 0.019, 10) - 0.5) < 1e-12);
});

test("malformed or out-of-window spike records cannot create visible activity", () => {
  const s = {
    tick: 1,
    window_ms: 20,
    spikes: [
      [0, 0],
      [0.02, 1],
      [0.021, 0],
      [-0.01, 0],
      [0.01, 2],
      [0.01, 0.5],
      [NaN, 0],
    ],
    voltages: [0.2, 0.3],
    weights: [0.05],
  };
  const frame = math.recordedFrame(s, 2);
  assert.deepEqual(frame.counts, [1, 1]);
  assert.equal(frame.span, 0.02);
  s.voltages[0] = 9;
  s.weights[0] = 9;
  assert.equal(frame.voltages[0], 0.2);
  assert.equal(frame.weights[0], 0.05);
});

test("spike times are ordered per neuron without changing source records", () => {
  const s = {
    window_ms: 100,
    spikes: [
      [0.08, 0],
      [0.02, 0],
      [0.05, 1],
    ],
  };
  assert.deepEqual(math.recordedFrame(s, 2).byNeuron, [[0.02, 0.08], [0.05]]);
  assert.deepEqual(s.spikes[0], [0.08, 0]);
});

test("schematic layout preserves all neuron identities and region membership", () => {
  const regions = Array.from({ length: 6 }, (_, i) => ({
    id: "r" + i,
    start: i * 128,
    end: (i + 1) * 128,
  }));
  const nodes = regions.flatMap((r) =>
    Array.from({ length: 128 }, (_, i) => ({ id: r.start + i, region: r.id })),
  );
  const first = math.layout(nodes, regions),
    second = math.layout(nodes, regions);
  assert.deepEqual(first, second);
  assert.equal(first.length, 768);
  assert.equal(new Set(first.map((p) => p.id)).size, 768);
  for (let i = 0; i < first.length; i++) {
    assert.equal(first[i].region, nodes[i].region);
    assert.ok(["x", "y", "z"].every((k) => Number.isFinite(first[i][k])));
  }
  assert.ok(
    Math.max(...first.map((p) => p.z)) - Math.min(...first.map((p) => p.z)) > 0.9,
  );
});

test("projection gives nearer neurons perspective depth without losing identity", () => {
  const a = math.project({ x: 0.5, y: 0.5, z: 0.6 }, 0, 0, 100, 200, 200);
  const b = math.project({ x: 0.5, y: 0.5, z: -0.6 }, 0, 0, 100, 200, 200);
  assert.ok(a.depth > b.depth);
  assert.ok(a.x > b.x);
  assert.equal(
    math.project({ x: 0.5, y: 0.5, z: 0.6 }, 0, 0, 100, 200, 200, false).depth,
    1,
  );
});

test("inspection picks a near foreground neuron and rejects empty space", () => {
  const points = [
    { x: 100, y: 100, z: -0.4 },
    { x: 100, y: 100, z: 0.4 },
  ];
  assert.equal(math.hit(points, 100, 100), 1);
  assert.equal(math.hit(points, 120, 100), -1);
});

test("curved synapses terminate at the exact recorded source and target", () => {
  const curve = {
    a: { x: 1, y: 2 },
    c1: { x: 5, y: 4 },
    c2: { x: 8, y: 9 },
    b: { x: 10, y: 12 },
  };
  assert.deepEqual(math.bezier(curve, 0), curve.a);
  assert.deepEqual(math.bezier(curve, 1), curve.b);
});

test("holding the field preserves the captured tick; resume ingests the newest state", () => {
  const sandbox = vm.createContext({ NeuralMath: math, Map, Set, document: {}, Date });
  vm.runInContext("let paused=false;let topology={nodes:[{},{}]};", sandbox);
  vm.runInContext(
    fs.readFileSync(require.resolve("../neural-field.js"), "utf8"),
    sandbox,
  );
  vm.runInContext(
    `ingestFrame({tick:1,window_ms:100,spikes:[[0.01,0]],voltages:[0.2,0.3],weights:[0.05]}); paused=true; ingestFrame({tick:2,window_ms:100,spikes:[[0.02,1]],voltages:[0.7,0.8],weights:[0.07]});`,
    sandbox,
  );
  assert.equal(vm.runInContext("neuralFrame.tick", sandbox), 1);
  assert.equal(vm.runInContext("neuralFrame.voltages[0]", sandbox), 0.2);
  vm.runInContext(
    `paused=false;ingestFrame({tick:2,window_ms:100,spikes:[[0.02,1]],voltages:[0.7,0.8],weights:[0.07]},true);`,
    sandbox,
  );
  assert.equal(vm.runInContext("neuralFrame.tick", sandbox), 2);
  assert.ok(Math.abs(vm.runInContext("weightDeltas[0]", sandbox) - 0.02) < 1e-12);
});

test("schematic contours cannot be mistaken for additional recorded neuron identities", () => {
  for (let region = 0; region < 6; region++) {
    const contours = math.contours(region);
    assert.deepEqual(contours, math.contours(region));
    for (const ring of contours) {
      assert.ok(ring.length > 3);
      for (const point of ring) {
        assert.equal(point.id, undefined);
        assert.equal(point.region, undefined);
        assert.ok(["x", "y", "z"].every((key) => Number.isFinite(point[key])));
      }
      assert.ok(Math.abs(ring[0].x - ring.at(-1).x) < 1e-12);
      assert.ok(Math.abs(ring[0].z - ring.at(-1).z) < 1e-12);
    }
  }
});

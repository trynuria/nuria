/* Deterministic geometry and recorded-window operations, shared by the renderer and tests. */
const NeuralMath = (() => {
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const fract = (v) => v - Math.floor(v);
  const hash = (id, salt = 0) =>
    fract(Math.sin(id * 127.1 + salt * 311.7) * 43758.5453);
  const lobes = [
    [-0.99, 0.08, 0.08, 0.38, 0.61, 0.44],
    [-0.28, 0.29, -0.06, 0.66, 0.62, 0.63],
    [0.5, 0.49, -0.14, 0.4, 0.36, 0.48],
    [0.19, -0.13, 0.49, 0.4, 0.37, 0.29],
    [0.99, -0.12, 0.02, 0.37, 0.49, 0.46],
    [-0.05, -0.65, -0.17, 0.72, 0.24, 0.41],
  ];
  function layout(nodes, regions) {
    return nodes.map((node) => {
      const ri = regions.findIndex((r) => r.id === node.region);
      const region = regions[ri],
        lobe = lobes[ri % lobes.length];
      const j = node.id - region.start,
        count = region.end - region.start;
      const latitude = 1 - (2 * (j + 0.5)) / count;
      const angle = j * 2.399963229728653;
      const ring = Math.sqrt(Math.max(0, 1 - latitude * latitude));
      const fold = 0.88 + 0.12 * Math.cos(angle * 3 + latitude * 9);
      const shell =
        hash(node.id, 7) > 0.24
          ? 0.88 + hash(node.id, 11) * 0.12
          : 0.36 + hash(node.id, 19) * 0.39;
      return {
        id: node.id,
        region: node.region,
        ri,
        x: lobe[0] + Math.cos(angle) * ring * lobe[3] * fold * shell,
        y: lobe[1] + latitude * lobe[4] * shell,
        z: lobe[2] + Math.sin(angle) * ring * lobe[5] * fold * shell,
      };
    });
  }
  function project(p, yaw, pitch, scale, x, y, perspective = true) {
    const cy = Math.cos(yaw),
      sy = Math.sin(yaw),
      cp = Math.cos(pitch),
      sp = Math.sin(pitch);
    const rx = p.x * cy + p.z * sy,
      rz = -p.x * sy + p.z * cy;
    const ry = p.y * cp - rz * sp,
      z = p.y * sp + rz * cp;
    const depth = perspective ? 3.6 / (3.6 - z * 0.95) : 1;
    return { x: x + rx * scale * depth, y: y - ry * scale * depth, z, depth };
  }
  function recordedFrame(s, nodeCount) {
    const span = Math.max(0.001, Number(s.window_ms || 100) / 1000);
    const spikes = (s.spikes || [])
      .filter(
        ([time, id]) =>
          Number.isFinite(time) &&
          time >= 0 &&
          time <= span &&
          Number.isInteger(id) &&
          id >= 0 &&
          id < nodeCount,
      )
      .map(([time, id]) => ({ time, id }));
    const byNeuron = Array.from({ length: nodeCount }, () => []);
    for (const spike of spikes) byNeuron[spike.id].push(spike.time);
    for (const times of byNeuron) times.sort((a, b) => a - b);
    return {
      tick: s.tick,
      simSeconds: s.sim_seconds,
      updated: s.updated_utc,
      phase: s.phase,
      span,
      spikes,
      byNeuron,
      voltages: (s.voltages || []).slice(),
      weights: (s.weights || []).slice(),
      counts: byNeuron.map((times) => times.length),
      rates: (s.metrics?.regional_rates || []).slice(),
    };
  }
  function energy(times, cursor, decay = 0.008) {
    let result = 0;
    for (const time of times || []) {
      const age = cursor - time;
      if (age >= 0 && age < decay * 5)
        result = Math.max(result, Math.exp(-age / decay));
    }
    return result;
  }
  function transit(times, cursor, delayMs) {
    const delay = Number(delayMs) / 1000;
    if (!(delay > 0)) return null;
    for (let i = (times?.length || 0) - 1; i >= 0; i--) {
      const age = cursor - times[i];
      if (age >= 0 && age <= delay) return age / delay;
    }
    return null;
  }
  function hit(points, x, y, radius = 10) {
    let id = -1,
      best = Infinity;
    for (let i = 0; i < points.length; i++) {
      const p = points[i];
      const distance = Math.hypot(p.x - x, p.y - y);
      const score = distance - p.z * 1.5;
      if (distance <= radius && score < best) {
        id = i;
        best = score;
      }
    }
    return id;
  }
  function bezier(c, t) {
    const q = 1 - t;
    return {
      x:
        q * q * q * c.a.x +
        3 * q * q * t * c.c1.x +
        3 * q * t * t * c.c2.x +
        t * t * t * c.b.x,
      y:
        q * q * q * c.a.y +
        3 * q * q * t * c.c1.y +
        3 * q * t * t * c.c2.y +
        t * t * t * c.b.y,
    };
  }
  return { clamp, hash, layout, project, recordedFrame, energy, transit, hit, bezier };
})();
if (typeof module !== "undefined" && module.exports) module.exports = NeuralMath;

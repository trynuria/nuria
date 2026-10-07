/* Recorded neurons and sampled synapses. Spatial arrangement is a schematic. */
const clamp = NeuralMath.clamp;
let camera = { yaw: 0, pitch: 0 },
  cameraEase = { yaw: 0, pitch: 0 },
  zoomEase = 1;
let dragging = null,
  manualUntil = 0,
  pinned = -1,
  neuralFrame = null;
let frameSpikes = [],
  spikesByNeuron = [],
  displayVoltage = [],
  spriteCache = new Map();
let regionCenters = [],
  projectedCenters = [],
  edgeCurves = [],
  nodeOrder = [];
let lastHudPaint = 0,
  fieldVisible = true,
  playbackRate = 22;
let lastInspectorId = -1;
let fieldInvalidated = true,
  lastFieldFresh = null,
  lastCamera = { yaw: 0, pitch: 0 };
let fieldLayer = "firing",
  weightDeltas = [],
  renderSamples = [];
let latestEventId = null,
  latestReceiptCue = null;

function chooseRegion(id) {
  if (selected !== id) pinned = -1;
  selected = id;
  hover = -1;
  for (const button of $("legend").querySelectorAll("button"))
    button.setAttribute("aria-pressed", String(button.dataset.region === id));
}
function structure() {
  if (!topology?.nodes) return;
  basePoints = NeuralMath.layout(topology.nodes, topology.regions).map((p) => ({
    ...p,
    color: colors[p.region],
  }));
  regionCenters = topology.regions.map((r) => {
    const members = basePoints.filter((p) => p.region === r.id);
    return {
      x: members.reduce((v, p) => v + p.x, 0) / members.length,
      y: members.reduce((v, p) => v + p.y, 0) / members.length,
      z: members.reduce((v, p) => v + p.z, 0) / members.length,
    };
  });
  let wi = 0;
  weightedEdges = topology.edges.map((edge, index) => ({
    edge,
    index,
    weightIndex: edge[2] === "exc" ? wi++ : -1,
  }));
  $("legend").replaceChildren();
  $("regions").replaceChildren();
  const all = document.createElement("button");
  all.className = "filter";
  all.textContent = "All regions";
  all.dataset.region = "all";
  all.setAttribute("aria-pressed", "true");
  all.addEventListener("click", () => chooseRegion("all"));
  $("legend").append(all);
  for (const r of topology.regions) {
    const button = document.createElement("button");
    button.className = "filter";
    button.dataset.region = r.id;
    button.setAttribute("aria-pressed", "false");
    button.style.setProperty("--region-color", colors[r.id]);
    const dot = document.createElement("i");
    button.append(dot, document.createTextNode(shortNames[r.id]));
    button.addEventListener("click", () => chooseRegion(r.id));
    $("legend").append(button);
    const row = document.createElement("div");
    row.className = "region";
    const label = document.createElement("span");
    label.className = "region-name";
    label.style.setProperty("--region-color", colors[r.id]);
    label.append(
      document.createElement("i"),
      document.createTextNode(shortNames[r.id]),
    );
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("i");
    fill.id = "regionBar-" + r.id;
    fill.style.background = colors[r.id];
    bar.append(fill);
    const value = document.createElement("strong");
    value.id = "regionRate-" + r.id;
    row.append(label, bar, value);
    $("regions").append(row);
  }
  $("inspectId").max = String(topology.nodes.length - 1);
  $("sampleCount").textContent = fmt(topology.edges.length) + " sampled synapses";
}
function ingestFrame(s, force = false) {
  if (paused && neuralFrame && !force) return;
  fieldInvalidated = true;
  const previousWeights = neuralFrame?.weights || [];
  neuralFrame = NeuralMath.recordedFrame(
    s,
    topology?.nodes?.length || s.neurons || 1024,
  );
  weightDeltas = neuralFrame.weights.map((w, i) =>
    Number.isFinite(previousWeights[i]) ? w - previousWeights[i] : 0,
  );
  frameSpikes = neuralFrame.spikes;
  spikesByNeuron = neuralFrame.byNeuron;
  if (!displayVoltage.length) displayVoltage = neuralFrame.voltages.slice();
}
function project(w, h, time) {
  const idle = !dragging && Date.now() > manualUntil && !paused;
  const yaw =
    camera.yaw + 0.34 + (idle && !reduced ? Math.sin(time / 27000) * 0.28 : 0);
  const pitch =
    camera.pitch - 0.16 + (idle && !reduced ? Math.cos(time / 32000) * 0.1 : 0);
  const ease = paused ? 1 : 0.09;
  if (paused) {
    cameraEase.yaw += camera.yaw - lastCamera.yaw;
    cameraEase.pitch += camera.pitch - lastCamera.pitch;
    zoomEase = zoom;
  } else {
    cameraEase.yaw += (yaw - cameraEase.yaw) * ease;
    cameraEase.pitch += (pitch - cameraEase.pitch) * ease;
    zoomEase += (zoom - zoomEase) * ease;
  }
  lastCamera = { ...camera };
  const scale = Math.min(w * (w < 720 ? 0.34 : 0.3), (h - 145) * 0.46) * zoomEase;
  const projectPoint = (p) =>
    NeuralMath.project(
      p,
      mode === "topology" ? 0 : cameraEase.yaw,
      mode === "topology" ? 0 : cameraEase.pitch,
      scale,
      w * 0.5,
      (h - 90) * 0.52,
      mode !== "topology",
    );
  points = basePoints.map((p) => ({ ...p, ...projectPoint(p) }));
  projectedCenters = regionCenters.map(projectPoint);
  edgeCurves = weightedEdges.map((item) => {
    const a = points[item.edge[0]],
      b = points[item.edge[1]];
    const same = a.ri === b.ri,
      ca = projectedCenters[a.ri],
      cb = projectedCenters[b.ri];
    const bend = (NeuralMath.hash(item.index, 3) - 0.5) * scale * (same ? 0.12 : 0.05);
    const pull = mode === "topology" ? 0.05 : same ? 0.32 : 0.72;
    return {
      a,
      b,
      c1: {
        x: a.x + (ca.x - a.x) * pull + (b.x - a.x) * 0.12 + bend,
        y: a.y + (ca.y - a.y) * pull + (b.y - a.y) * 0.12 - bend,
      },
      c2: {
        x: b.x + (cb.x - b.x) * pull + (a.x - b.x) * 0.12 - bend,
        y: b.y + (cb.y - b.y) * pull + (a.y - b.y) * 0.12 - bend,
      },
    };
  });
  nodeOrder = points.map((p) => p.id).sort((a, b) => points[a].z - points[b].z);
  return scale;
}
function glowSprite(color) {
  if (spriteCache.has(color)) return spriteCache.get(color);
  const sprite = document.createElement("canvas");
  sprite.width = sprite.height = 64;
  const c = sprite.getContext("2d"),
    g = c.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, color + "ff");
  g.addColorStop(0.08, color + "e0");
  g.addColorStop(0.22, color + "66");
  g.addColorStop(0.52, color + "17");
  g.addColorStop(1, color + "00");
  c.fillStyle = g;
  c.fillRect(0, 0, 64, 64);
  spriteCache.set(color, sprite);
  return sprite;
}
function traceCurve(curve) {
  ctx.moveTo(curve.a.x, curve.a.y);
  ctx.bezierCurveTo(
    curve.c1.x,
    curve.c1.y,
    curve.c2.x,
    curve.c2.y,
    curve.b.x,
    curve.b.y,
  );
}
function drawBackdrop(w, h, scale) {
  ctx.save();
  const x = w / 2,
    y = (h - 90) * 0.52;
  const g = ctx.createRadialGradient(x, y, scale * 0.2, x, y, scale * 1.65);
  g.addColorStop(0, "#67867710");
  g.addColorStop(0.6, "#67867705");
  g.addColorStop(1, "#67867700");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "#a3b8af0b";
  ctx.lineWidth = 0.6;
  const step = 32;
  for (let xx = 16; xx < w; xx += step)
    for (let yy = 88; yy < h - 100; yy += step) {
      ctx.beginPath();
      ctx.moveTo(xx - 1, yy);
      ctx.lineTo(xx + 1, yy);
      ctx.stroke();
    }
  ctx.globalCompositeOperation = "screen";
  for (let i = 0; i < projectedCenters.length; i++) {
    const r = topology.regions[i];
    if (selected !== "all" && selected !== r.id) continue;
    const p = projectedCenters[i],
      size = scale * (i === 1 ? 1.2 : 0.75);
    ctx.globalAlpha = 0.025 + Math.min(0.04, (neuralFrame.rates[i] || 0) / 400);
    ctx.drawImage(glowSprite(colors[r.id]), p.x - size / 2, p.y - size / 2, size, size);
  }
  ctx.restore();
}
function drawSynapses(activity, fresh, cursor) {
  const inspected = pinned >= 0 ? pinned : hover;
  const related = (p) => selected === "all" || p.region === selected;
  ctx.lineCap = "round";
  for (const item of weightedEdges) {
    const e = item.edge,
      a = points[e[0]],
      b = points[e[1]],
      curve = edgeCurves[item.index];
    const inspect = inspected >= 0 && (e[0] === inspected || e[1] === inspected);
    const relevant = related(a) || related(b);
    const weight =
      item.weightIndex >= 0 ? (neuralFrame.weights[item.weightIndex] ?? 0.05) : 0.08;
    const depth = clamp(0.55 + (a.z + b.z) * 0.25, 0.2, 1);
    const dim = !relevant || (inspected >= 0 && !inspect);
    const changed = item.weightIndex >= 0 ? weightDeltas[item.weightIndex] || 0 : 0;
    const alpha = dim
      ? 0.015
      : inspect
        ? 0.65
        : clamp(0.075 + weight * 1.25, 0.07, 0.26) * depth;
    ctx.strokeStyle = inspect
      ? e[2] === "inh"
        ? "#a5bff2b0"
        : "#e4ead3b0"
      : e[2] === "inh"
        ? `rgba(142,171,209,${alpha * 0.55})`
        : `rgba(181,204,192,${alpha})`;
    if (fieldLayer === "weights" && !dim && !inspect) {
      ctx.strokeStyle =
        changed > 0.00001
          ? "#d0dca080"
          : changed < -0.00001
            ? "#bc9fbe80"
            : `rgba(163,183,177,${clamp(weight * 5, 0.04, 0.5) * depth})`;
    }
    ctx.lineWidth = inspect
      ? 1.15
      : fieldLayer === "weights"
        ? 0.35 + clamp(weight, 0, 0.15) * 10
        : 0.45 + Math.min(0.07, weight) * 3;
    ctx.beginPath();
    traceCurve(curve);
    ctx.stroke();
  }
  if (!fresh || paused || fieldLayer !== "firing") return;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  let emitted = 0;
  for (const item of weightedEdges) {
    const e = item.edge,
      a = points[e[0]],
      b = points[e[1]];
    if (!related(a) && !related(b)) continue;
    if (inspected >= 0 && e[0] !== inspected && e[1] !== inspected) continue;
    const travel = NeuralMath.transit(spikesByNeuron[e[0]], cursor, e[3]);
    if (travel === null || emitted >= 160) continue;
    const curve = edgeCurves[item.index],
      color = e[2] === "inh" ? "#9caee2" : a.color;
    ctx.globalAlpha = 0.55;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.0;
    ctx.beginPath();
    for (let k = 0; k <= 5; k++) {
      const p = NeuralMath.bezier(curve, clamp(travel - 0.24 + (k / 5) * 0.24, 0, 1));
      if (k === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
    const p = NeuralMath.bezier(curve, travel);
    ctx.globalAlpha = 0.72;
    ctx.drawImage(glowSprite(color), p.x - 5, p.y - 5, 10, 10);
    emitted++;
  }
  ctx.restore();
}
function drawNeurons(activity) {
  const inspected = pinned >= 0 ? pinned : hover;
  const neighbors =
    inspected >= 0
      ? new Set(
          weightedEdges
            .filter(({ edge: e }) => e[0] === inspected || e[1] === inspected)
            .flatMap(({ edge: e }) => e.slice(0, 2)),
        )
      : null;
  for (const id of nodeOrder) {
    const p = points[id],
      active = fieldLayer === "firing" ? activity[id] : 0;
    const dim =
      (selected !== "all" && p.region !== selected) ||
      (neighbors && !neighbors.has(id));
    const actualV = Number.isFinite(neuralFrame.voltages[id])
      ? neuralFrame.voltages[id]
      : 0;
    displayVoltage[id] =
      (displayVoltage[id] ?? actualV) +
      (actualV - (displayVoltage[id] ?? actualV)) * (paused ? 1 : 0.12);
    const voltage = clamp(displayVoltage[id], 0, 1.2),
      depth = clamp(0.58 + p.z * 0.48, 0.22, 1);
    const radius = (0.9 + voltage * 0.46 + active * 0.95) * p.depth;
    ctx.globalAlpha = dim ? 0.1 : depth * (0.48 + voltage * 0.38) + active * 0.5;
    if (!dim && active > 0.018) {
      const size = (7 + active * 13) * p.depth;
      ctx.globalCompositeOperation = "screen";
      ctx.globalAlpha = active * 0.85;
      ctx.drawImage(glowSprite(p.color), p.x - size / 2, p.y - size / 2, size, size);
      ctx.globalCompositeOperation = "source-over";
      ctx.globalAlpha = clamp(depth + active, 0, 1);
    }
    ctx.fillStyle =
      active > 0.25
        ? "#f7fff2"
        : fieldLayer === "voltage"
          ? voltage > 0.8
            ? "#e5eeb0"
            : voltage > 0.45
              ? "#8ab8a9"
              : "#506b82"
          : p.color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
    ctx.fill();
    if (!dim && p.z > 0.2 && radius > 1.25) {
      ctx.strokeStyle = p.color;
      ctx.globalAlpha = 0.14;
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius + 1.5, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (id === inspected) {
      ctx.globalAlpha = 1;
      ctx.strokeStyle = "#eef5dd";
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 8, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(p.x, p.y, 12, -0.3, 0.7);
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
}
function drawRegionLabels(w, h, scale) {
  if (w < 720) return;
  const anchors = [
    [-1.46, 0.42],
    [-0.61, 1.1],
    [0.81, 0.94],
    [0.59, -0.61],
    [1.49, -0.11],
    [-0.68, -0.95],
  ];
  ctx.font = "10px ui-monospace,monospace";
  for (let i = 0; i < topology.regions.length; i++) {
    const r = topology.regions[i];
    if (selected !== "all" && selected !== r.id) continue;
    const p = projectedCenters[i],
      anchor = anchors[i],
      side = anchor[0] > 0 ? 1 : -1;
    const x = clamp(w * 0.5 + anchor[0] * scale, 110, w - 110),
      y = clamp((h - 90) * 0.52 - anchor[1] * scale, 102, h - 141);
    ctx.strokeStyle = colors[r.id] + "40";
    ctx.lineWidth = 0.65;
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    ctx.lineTo(x - side * 12, y);
    ctx.lineTo(x, y);
    ctx.stroke();
    ctx.fillStyle = colors[r.id];
    ctx.textAlign = side > 0 ? "left" : "right";
    ctx.fillText(shortNames[r.id].toUpperCase(), x + side * 7, y - 5);
    ctx.fillStyle = "#87978e";
    ctx.fillText(fmt(neuralFrame.rates[i], 1) + " Hz", x + side * 7, y + 11);
    ctx.fillStyle = colors[r.id] + "a0";
    ctx.beginPath();
    ctx.arc(p.x, p.y, 2, 0, Math.PI * 2);
    ctx.fill();
  }
}
function drawRaster(w, h, cursor, compact = false) {
  const chosen = topology.regions.find((r) => r.id === selected),
    first = chosen?.start || 0,
    last = chosen?.end || topology.nodes.length,
    range = last - first;
  const left = compact ? 20 : 52,
    right = w - (compact ? 20 : 25),
    top = compact ? h - (w < 700 ? 142 : 122) : 154,
    bottom = compact ? h - (w < 700 ? 110 : 90) : h - 117;
  ctx.save();
  ctx.font = "10px ui-monospace,monospace";
  ctx.lineWidth = 0.5;
  for (const r of topology.regions) {
    if (chosen && chosen.id !== r.id) continue;
    const y = top + ((bottom - top) * (r.start - first)) / range,
      end = top + ((bottom - top) * (r.end - first)) / range;
    ctx.fillStyle = colors[r.id] + (compact ? "09" : "0c");
    ctx.fillRect(left, y, right - left, end - y);
    ctx.strokeStyle = "#a5b8ab1a";
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    if (!compact) {
      ctx.textAlign = "right";
      ctx.fillStyle = colors[r.id];
      ctx.fillText(String(r.start), left - 8, y + 8);
    }
  }
  for (let k = 0; k <= 4; k++) {
    const x = left + ((right - left) * k) / 4;
    ctx.strokeStyle = "#a3b6aa1a";
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.textAlign = k === 0 ? "left" : k === 4 ? "right" : "center";
    ctx.fillStyle = "#65746d";
    ctx.fillText(Math.round((neuralFrame.span * 1000 * k) / 4) + " ms", x, bottom + 14);
  }
  for (const spike of frameSpikes) {
    const n = topology.nodes[spike.id];
    if (chosen && chosen.id !== n.region) continue;
    const x = left + ((right - left) * spike.time) / neuralFrame.span,
      y = top + ((bottom - top) * (spike.id - first + 0.5)) / range;
    ctx.globalAlpha = paused || cursor >= spike.time ? 0.85 : 0.19;
    ctx.fillStyle = colors[n.region];
    ctx.fillRect(x - 0.6, y - (compact ? 0.6 : 1.4), 1.2, compact ? 1.2 : 2.8);
  }
  ctx.globalAlpha = 1;
  if (!paused && cursor <= neuralFrame.span) {
    const x = left + ((right - left) * cursor) / neuralFrame.span;
    ctx.strokeStyle = "#d6e6be99";
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(x, top - 3);
    ctx.lineTo(x, bottom);
    ctx.stroke();
  }
  if (!compact) {
    ctx.textAlign = "left";
    ctx.fillStyle = "#9daa9c";
    ctx.fillText("NEURON ID", left, top - 17);
    ctx.fillText("Recorded spike times · " + range + " neurons", left, bottom + 37);
  }
  ctx.restore();
}
function updateInspector() {
  const id = pinned >= 0 ? pinned : hover,
    tip = $("inspection");
  if (id < 0 || !neuralFrame) {
    tip.hidden = true;
    return;
  }
  tip.hidden = false;
  const n = topology.nodes[id],
    edges = weightedEdges.filter(({ edge: e }) => e[0] === id || e[1] === id),
    incoming = edges.filter(({ edge: e }) => e[1] === id),
    outgoing = edges.filter(({ edge: e }) => e[0] === id);
  $("inspectTitle").textContent = shortNames[n.region];
  if (lastInspectorId !== id) {
    if (Number($("inspectId").value) !== id) $("inspectId").value = String(id);
    lastInspectorId = id;
  }
  $("inspectVoltage").textContent = Number.isFinite(neuralFrame.voltages[id])
    ? neuralFrame.voltages[id].toFixed(4)
    : "Unknown";
  $("inspectSpikes").textContent = String(neuralFrame.counts[id]);
  $("inspectConnections").textContent =
    incoming.length + " in / " + outgoing.length + " out";
  const weights = outgoing
    .filter((e) => e.weightIndex >= 0)
    .map((e) => neuralFrame.weights[e.weightIndex])
    .filter(Number.isFinite);
  $("inspectWeight").textContent = weights.length
    ? (weights.reduce((a, b) => a + b, 0) / weights.length).toFixed(5)
    : "—";
  $("inspectTick").textContent = "Captured tick " + fmt(neuralFrame.tick);
  tip.classList.toggle("is-pinned", pinned >= 0);
}
function draw(now) {
  requestAnimationFrame(draw);
  if (document.hidden || !fieldVisible || now - lastPaint < 16) return;
  const paintStart = performance.now();
  const elapsed = Math.min(50, now - lastPaint);
  lastPaint = now;
  if (!paused) motionClock += elapsed;
  const { w, h, dpr } = resize(canvas);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!topology || !neuralFrame) return;
  const fresh =
    state?.phase === "running" && Date.now() - Date.parse(state.updated_utc) < 15000;
  if (paused && !fieldInvalidated && lastFieldFresh === fresh) return;
  fieldInvalidated = false;
  lastFieldFresh = fresh;
  ctx.clearRect(0, 0, w, h);
  const status = $("fieldStatus");
  status.classList.toggle("hidden", fresh);
  if (!fresh)
    status.textContent =
      state?.phase === "error"
        ? "Engine stopped — evidence retained"
        : "Waiting for fresh neural state";
  const cursor = paused
    ? neuralFrame.span
    : clamp((now - frameStart) / 1000 / playbackRate, 0, neuralFrame.span);
  const activity = spikesByNeuron.map((times) =>
    fresh && !paused ? NeuralMath.energy(times, cursor) : 0,
  );
  if (mode === "spikes") drawRaster(w, h, cursor);
  else {
    const scale = project(w, h, motionClock);
    drawBackdrop(w, h, scale);
    drawSynapses(activity, fresh, cursor);
    drawNeurons(activity);
    drawRegionLabels(w, h, scale);
    drawRaster(w, h, cursor, true);
  }
  renderSamples.push(performance.now() - paintStart);
  if (renderSamples.length > 120) renderSamples.shift();
  if (now - lastHudPaint > 100) {
    canvas.dataset.renderMs = (
      renderSamples.reduce((a, b) => a + b, 0) / renderSamples.length
    ).toFixed(2);
    canvas.dataset.renderP95 = [...renderSamples]
      .sort((a, b) => a - b)
      [Math.floor(renderSamples.length * 0.95)].toFixed(2);
    canvas.dataset.windowTick = String(neuralFrame.tick);
    lastHudPaint = now;
    const recorded = frameSpikes.filter(
      (s) => selected === "all" || topology.nodes[s.id].region === selected,
    );
    $("windowSpikes").textContent = fmt(recorded.length);
    $("windowActive").textContent = fmt(new Set(recorded.map((s) => s.id)).size);
    $("playbackTime").textContent =
      (cursor * 1000).toFixed(0).padStart(3, "0") +
      " / " +
      Math.round(neuralFrame.span * 1000) +
      " ms";
    $("frameLabel").textContent = paused
      ? "Snapshot held · tick " + fmt(neuralFrame.tick)
      : playbackRate === 1
        ? "Recorded window · model speed"
        : "Recorded window · " + playbackRate + "× slower";
    $("replayFill").style.width =
      Math.min(100, (cursor / neuralFrame.span) * 100) + "%";
    updateInspector();
  }
  if (!fresh) {
    ctx.fillStyle = "#0b0f1255";
    ctx.fillRect(0, 0, w, h);
  }
}
function bindOrbit() {
  if (typeof ResizeObserver !== "undefined")
    new ResizeObserver(() => {
      fieldInvalidated = true;
    }).observe(canvas);
  for (const event of ["click", "change", "keydown", "pointermove"])
    $("neural-field").addEventListener(event, () => {
      fieldInvalidated = true;
    });
  if (typeof IntersectionObserver !== "undefined")
    new IntersectionObserver(
      ([entry]) => {
        fieldVisible = entry.isIntersecting;
      },
      { rootMargin: "100px" },
    ).observe(canvas);
  canvas.addEventListener("pointerdown", (event) => {
    if (mode === "spikes" || event.button !== 0) return;
    dragging = {
      x: event.clientX,
      y: event.clientY,
      yaw: camera.yaw,
      pitch: camera.pitch,
      moved: false,
      pointerId: event.pointerId,
    };
    canvas.setPointerCapture(event.pointerId);
    canvas.classList.add("dragging");
    manualUntil = Date.now() + 12000;
  });
  canvas.addEventListener("pointermove", (event) => {
    if (mode === "spikes") return;
    const rect = canvas.getBoundingClientRect(),
      x = event.clientX - rect.left,
      y = event.clientY - rect.top;
    if (dragging) {
      const dx = event.clientX - dragging.x,
        dy = event.clientY - dragging.y;
      dragging.moved = dragging.moved || Math.abs(dx) + Math.abs(dy) > 5;
      if (dragging.moved) {
        camera.yaw = dragging.yaw + dx * 0.006;
        camera.pitch = clamp(dragging.pitch + dy * 0.006, -1.2, 1.2);
        hover = -1;
      }
      manualUntil = Date.now() + 12000;
      return;
    }
    if (event.pointerType === "mouse" && pinned < 0) {
      hover = NeuralMath.hit(points, x, y, 8);
      updateInspector();
    }
  });
  const end = (event) => {
    if (dragging && !dragging.moved && event.type === "pointerup") {
      const rect = canvas.getBoundingClientRect();
      const id = NeuralMath.hit(
        points,
        event.clientX - rect.left,
        event.clientY - rect.top,
        12,
      );
      pinned = id;
      hover = -1;
      updateInspector();
    }
    dragging = null;
    canvas.classList.remove("dragging");
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("pointerleave", () => {
    hover = -1;
    if (pinned < 0) updateInspector();
  });
  canvas.addEventListener("keydown", (event) => {
    if (mode === "spikes") return;
    if (
      ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "Escape"].includes(
        event.key,
      )
    ) {
      event.preventDefault();
      manualUntil = Date.now() + 12000;
      if (event.key === "ArrowLeft") camera.yaw -= 0.18;
      if (event.key === "ArrowRight") camera.yaw += 0.18;
      if (event.key === "ArrowUp") camera.pitch = clamp(camera.pitch - 0.12, -1.2, 1.2);
      if (event.key === "ArrowDown")
        camera.pitch = clamp(camera.pitch + 0.12, -1.2, 1.2);
      if (event.key === "Home") resetCamera();
      if (event.key === "Escape") {
        pinned = -1;
        hover = -1;
        updateInspector();
      }
    }
  });
  for (const button of $("fieldLayers").querySelectorAll("button"))
    button.addEventListener("click", () => {
      fieldLayer = button.dataset.layer;
      for (const item of $("fieldLayers").querySelectorAll("button"))
        item.setAttribute("aria-pressed", String(item === button));
    });
  $("resetView").addEventListener("click", resetCamera);
  $("inspectOpen").addEventListener("click", () => {
    if (!neuralFrame) return;
    pinned = neuralFrame.counts.indexOf(Math.max(...neuralFrame.counts));
    hover = -1;
    updateInspector();
    $("fieldTools").open = false;
    $("inspectId").focus();
  });
  $("inspectClose").addEventListener("click", () => {
    pinned = -1;
    hover = -1;
    updateInspector();
    $("fieldTools").querySelector("summary").focus();
  });
  $("inspectId").addEventListener("change", () => {
    const id = Number($("inspectId").value);
    if (Number.isInteger(id) && id >= 0 && id < points.length) {
      pinned = id;
      hover = -1;
      updateInspector();
    }
  });
  $("playbackRate").addEventListener("change", () => {
    playbackRate = Number($("playbackRate").value);
    frameStart = performance.now();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!$("fieldTools").contains(event.target)) $("fieldTools").open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("fieldTools").open) {
      $("fieldTools").open = false;
      $("fieldTools").querySelector("summary").focus();
    }
  });
  window.addEventListener("blur", () => {
    dragging = null;
    hover = -1;
    canvas.classList.remove("dragging");
  });
}
function resetCamera() {
  camera = { yaw: 0, pitch: 0 };
  zoom = 1;
  manualUntil = 0;
  pinned = -1;
  hover = -1;
  updateInspector();
}

let cognitionState = null,
  legacyState = null,
  cognitiveDecisionHash = null;
function cognitiveCount(id, value) {
  const element = $(id);
  element.textContent =
    Number.isFinite(value) && value >= 10000
      ? new Intl.NumberFormat("en", {
          notation: "compact",
          maximumFractionDigits: 1,
        }).format(value)
      : fmt(value);
  element.title = Number.isFinite(value) ? fmt(value) : "Evidence unavailable";
}
function cognitiveNode(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function drawHabitat(s) {
  const c = $("habitat"),
    d = resize(c),
    x = c.getContext("2d");
  if (d.w < 24 || d.h < 24) return;
  x.setTransform(d.dpr, 0, 0, d.dpr, 0, 0);
  x.clearRect(0, 0, d.w, d.h);
  const size = s.size || 12,
    cell = Math.min(d.w / size, d.h / size) - 2,
    side = cell * size,
    left = (d.w - side) / 2,
    top = (d.h - side) / 2;
  if (cell <= 2) return;
  for (let j = 0; j < size; j++)
    for (let i = 0; i < size; i++) {
      const visits = s.visits?.[j]?.[i] || 0;
      x.fillStyle = visits
        ? `rgba(144,194,124,${Math.min(0.32, 0.04 + visits * 0.018)})`
        : "#142319";
      x.fillRect(left + i * cell, top + j * cell, cell - 2, cell - 2);
    }
  for (const [i, j] of s.resources || []) {
    x.fillStyle = "#c3a56e";
    x.fillRect(
      left + (i + 0.33) * cell,
      top + (j + 0.33) * cell,
      cell * 0.24,
      cell * 0.24,
    );
  }
  if (s.position) {
    const [i, j] = s.position;
    x.fillStyle = "#d6efb7";
    x.shadowBlur = 12;
    x.shadowColor = "#a5ce87";
    x.beginPath();
    x.arc(
      left + (i + 0.45) * cell,
      top + (j + 0.45) * cell,
      cell * 0.2,
      0,
      Math.PI * 2,
    );
    x.fill();
    x.shadowBlur = 0;
  }
}
function drawLearning(rows) {
  const c = $("learningChart"),
    d = resize(c),
    x = c.getContext("2d");
  if (d.w < 30 || d.h < 30) return;
  x.setTransform(d.dpr, 0, 0, d.dpr, 0, 0);
  x.clearRect(0, 0, d.w, d.h);
  if (rows.length < 2) return;
  const left = 26,
    right = d.w - 3,
    top = 5,
    bottom = d.h - 15;
  const maximum = Math.min(
    1,
    Math.max(0.3, ...rows.map((r) => Math.max(r.brier || 0, r.baseline_brier || 0))) *
      1.12,
  );
  x.font = "8px ui-monospace,monospace";
  x.textAlign = "left";
  for (let i = 0; i <= 2; i++) {
    const value = (maximum * i) / 2,
      py = bottom - ((bottom - top) * i) / 2;
    x.strokeStyle = "#31433566";
    x.lineWidth = 0.5;
    x.beginPath();
    x.moveTo(left, py);
    x.lineTo(right, py);
    x.stroke();
    x.fillStyle = "#57735c";
    x.fillText(value.toFixed(2), 0, py + 3);
  }
  for (const [key, color] of [
    ["baseline_brier", "#718378"],
    ["brier", "#b9dba4"],
  ]) {
    x.beginPath();
    rows.forEach((r, i) => {
      const px = left + (i / (rows.length - 1)) * (right - left),
        py = bottom - (Math.min(maximum, r[key]) / maximum) * (bottom - top);
      i ? x.lineTo(px, py) : x.moveTo(px, py);
    });
    x.strokeStyle = color;
    x.setLineDash(key === "baseline_brier" ? [3, 4] : []);
    x.lineWidth = key === "brier" ? 1.5 : 1;
    x.stroke();
    x.setLineDash([]);
  }
  x.fillStyle = "#526e58";
  x.fillText("LATEST " + rows.length + " INPUTS", left, d.h - 2);
}
function renderCognition(c) {
  cognitionState = c;
  $("cogPhase").textContent =
    c.phase === "running" ? "Continuous cognition" : "Evidence unavailable";
  const decision = c.last_decision,
    selection = decision?.decision;
  $("cogAction").textContent = selection?.action || "Observing";
  const actionDescriptions = {
    explore: "Move into a less visited part of the software habitat.",
    forage: "Move toward a virtual resource in the habitat.",
    predict: "Prediction continues while awaiting the next input.",
    replay: "Re-stimulate the circuit with a remembered input.",
    experiment: "Compare forecast mechanisms on recorded inputs.",
    rest: "Recover model energy and reduce fatigue.",
    compare: "Compare the same neural state with and without an input.",
    reserve: "Keep model resources available for the next input.",
  };
  const job = decision?.outcome?.job;
  $("cogReason").textContent =
    job?.reason ||
    actionDescriptions[selection?.action] ||
    "Waiting for a recorded decision.";
  const scores = $("cogScores");
  scores.replaceChildren();
  for (const [action, value] of Object.entries(selection?.combined_scores || {})) {
    const item = cognitiveNode("div", undefined, "decision-score"),
      line = cognitiveNode("i"),
      bar = cognitiveNode("span");
    item.classList.toggle("selected", action === selection?.action);
    item.append(
      cognitiveNode("span", action),
      cognitiveNode("b", Number(value).toFixed(3)),
    );
    bar.style.width = Math.max(0, Math.min(100, value * 100)) + "%";
    line.append(bar);
    item.append(line);
    scores.append(item);
  }
  cognitiveDecisionHash = decision?.hash || null;
  $("copyDecision").disabled = !cognitiveDecisionHash;
  $("cogDecisionId").textContent = decision
    ? `Record ${fmt(decision.seq)} · tick ${fmt(decision.tick)}`
    : "Awaiting record";
  $("cogDecisionJSON").textContent = decision
    ? JSON.stringify(decision, null, 2)
    : "Awaiting a decision.";
  const sources = c.learning?.sources || {},
    source =
      c.token?.source || Object.keys(sources).find((k) => k !== "test") || "test",
    learning = sources[source],
    metrics = learning?.metrics?.[source];
  $("cogPrediction").textContent =
    learning?.prediction?.probability !== undefined
      ? (learning.prediction.probability * 100).toFixed(1) + "%"
      : "—";
  cognitiveCount("cogEvaluated", metrics?.evaluated);
  $("cogBrier").textContent = fmt(metrics?.brier, 4);
  $("cogBaseline").textContent = fmt(metrics?.baseline_brier, 4);
  $("cogSourceBadge").textContent = metrics
    ? source === "test"
      ? "Simulation inputs"
      : "Finalized Solana inputs"
    : "Awaiting data";
  $("cogLearningSource").textContent =
    source === "test"
      ? "Simulation inputs · forecast error (green) / learned repeat baseline (gray)."
      : "Finalized Solana inputs · observed next-input outcomes; prediction quality can rise or fall.";
  drawLearning(learning?.history || []);
  $("cogForecastJSON").textContent = JSON.stringify(
    {
      method: c.learning?.method,
      learning_started_utc: c.learning?.genesis_utc,
      source: source === "test" ? "simulation" : "finalized_solana",
      prediction: learning?.prediction
        ? {
            ...learning.prediction,
            source: source === "test" ? "simulation" : "finalized_solana",
          }
        : undefined,
      metrics,
      scope: c.learning?.scope,
    },
    null,
    2,
  );
  cognitiveCount("cogEpisodes", c.memory?.episodes);
  $("cogWorkspace").replaceChildren();
  for (const slot of c.workspace?.selected || []) {
    const row = cognitiveNode("div", undefined, "workspace-slot");
    row.append(
      cognitiveNode("span", slot.kind),
      cognitiveNode("b", Number(slot.salience).toFixed(3)),
    );
    $("cogWorkspace").append(row);
  }
  $("cogEnergy").textContent = Number.isFinite(c.resources?.energy)
    ? (c.resources.energy * 100).toFixed(1) + "%"
    : "—";
  cognitiveCount("cogJobs", c.experiments?.completed);
  $("cogSpent").textContent = money(
    c.treasury?.spent_lamports === undefined ? null : c.treasury.spent_lamports / 1e9,
  );
  $("cogMoves").textContent = fmt(c.habitat?.moves) + " moves";
  $("cogCollected").textContent = fmt(c.habitat?.collected) + " collected";
  drawHabitat(c.habitat || {});
  $("cogContinuity").textContent =
    `Checkpoint ${fmt(c.committed_tick)} · input ${fmt(c.committed_cursor)}`;
  $("cogContinuity").title =
    `${fmt(c.pending_ticks)} neural transitions since the saved checkpoint`;
}
function neuralView(c, legacy) {
  const n = c.neural || {},
    rates = n.regional_rates || [],
    total = (n.counts || []).reduce((a, b) => a + b, 0),
    entropy = total
      ? (n.counts || []).reduce(
          (a, v) => (v ? a - (v / total) * Math.log2(v / total) : a),
          0,
        )
      : 0;
  return {
    ...legacy,
    ...n,
    phase: c.phase,
    updated_utc: c.updated_utc,
    tick: c.tick,
    neurons: c.neurons,
    synapses: c.synapses,
    sim_seconds: n.sim_seconds,
    weights: n.sampled_weights,
    history: c.history,
    spikes_total: c.spikes_total,
    cognition: c,
    metrics: {
      rate_hz: n.rate_hz,
      changed_synapses: n.changed_synapses,
      regional_rates: rates,
      mean_weight: n.mean_weight,
      activity_entropy: entropy,
    },
  };
}
$("copyDecision").addEventListener("click", () =>
  copyValue(cognitiveDecisionHash, $("copyDecision")),
);
window.addEventListener("resize", () => {
  if (cognitionState) {
    drawHabitat(cognitionState.habitat || {});
    const source =
      cognitionState.token?.source ||
      Object.keys(cognitionState.learning?.sources || {}).find((k) => k !== "test") ||
      "test";
    drawLearning(cognitionState.learning?.sources?.[source]?.history || []);
  }
});

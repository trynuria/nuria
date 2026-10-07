let cognitionState = null,
  legacyState = null,
  cognitiveDecisionHash = null;
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
  x.setTransform(d.dpr, 0, 0, d.dpr, 0, 0);
  x.clearRect(0, 0, d.w, d.h);
  const size = s.size || 12,
    cell = Math.min(d.w / size, d.h / size) - 2,
    side = cell * size,
    left = (d.w - side) / 2,
    top = (d.h - side) / 2;
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
  x.setTransform(d.dpr, 0, 0, d.dpr, 0, 0);
  x.clearRect(0, 0, d.w, d.h);
  if (rows.length < 2) return;
  for (const [key, color] of [
    ["baseline_brier", "#6c7b6a"],
    ["brier", "#b5d99e"],
  ]) {
    x.beginPath();
    rows.forEach((r, i) => {
      const px = (i / (rows.length - 1)) * d.w,
        py = d.h - 5 - Math.min(1, r[key]) * (d.h - 10);
      i ? x.lineTo(px, py) : x.moveTo(px, py);
    });
    x.strokeStyle = color;
    x.lineWidth = 1.3;
    x.stroke();
  }
}
function renderCognition(c) {
  cognitionState = c;
  $("cogPhase").textContent =
    c.phase === "running" ? "Continuous cognition" : "Evidence unavailable";
  const decision = c.last_decision,
    selection = decision?.decision;
  $("cogAction").textContent = selection?.action || "Observing";
  $("cogReason").textContent =
    decision?.explanation || "Collecting the first decision record.";
  const scores = $("cogScores");
  scores.replaceChildren();
  for (const [action, value] of Object.entries(selection?.combined_scores || {})) {
    const item = cognitiveNode("div", undefined, "decision-score"),
      line = cognitiveNode("i"),
      bar = cognitiveNode("span");
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
    source = Object.keys(sources).find((k) => k !== "test") || "test",
    learning = sources[source],
    metrics = learning?.metrics?.[source];
  $("cogPrediction").textContent =
    learning?.prediction?.probability !== undefined
      ? (learning.prediction.probability * 100).toFixed(1) + "%"
      : "—";
  $("cogEvaluated").textContent = fmt(metrics?.evaluated);
  $("cogBrier").textContent = fmt(metrics?.brier, 4);
  $("cogBaseline").textContent = fmt(metrics?.baseline_brier, 4);
  $("cogLearningSource").textContent =
    source === "test"
      ? "Test-stream measurements. Green: prediction error. Gray: baseline. Live learning uses separate weights."
      : `${source} · observed next-input outcomes; prediction quality can rise or fall.`;
  drawLearning(learning?.history || []);
  $("cogEpisodes").textContent = fmt(c.memory?.episodes);
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
  $("cogJobs").textContent = fmt(c.experiments?.completed);
  $("cogSpent").textContent = money(
    c.treasury?.spent_lamports === undefined ? null : c.treasury.spent_lamports / 1e9,
  );
  $("cogMoves").textContent = fmt(c.habitat?.moves) + " moves";
  $("cogCollected").textContent = fmt(c.habitat?.collected) + " collected";
  drawHabitat(c.habitat || {});
  $("cogContinuity").textContent =
    `Saved tick ${fmt(c.committed_tick)} · ${fmt(c.pending_ticks)} pending · input ${fmt(c.committed_cursor)}`;
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
      Object.keys(cognitionState.learning?.sources || {}).find((k) => k !== "test") ||
      "test";
    drawLearning(cognitionState.learning?.sources?.[source]?.history || []);
  }
});

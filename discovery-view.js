let discoveryState = null,
  discoveryTask = "association",
  discoveryRequest = false,
  discoveryAvailable = false;
const discoveryLabels = {
  spike_memory: "Spike memory",
  symbolic_memory: "Symbolic memory",
  no_memory: "No cue memory",
  no_probes: "No probes",
  no_adaptation: "No adaptation",
  frozen_learning: "Frozen learning",
  random: "Random choices",
};
const discoveryDescriptions = {
  association: "Learn which choice works for each remembered cue.",
  occlusion: "Some cues are hidden. Decide whether information is worth its cost.",
  reversal: "The useful choices change without warning. Learn from new outcomes.",
  scarcity: "Conditions change, and information can become expensive.",
};
function renderDiscovery() {
  const s = discoveryState;
  if (!s) return;
  const fresh =
    discoveryAvailable &&
    s.phase === "running" &&
    Date.now() - Date.parse(s.updated_utc) < 15000;
  $("labPhase").textContent = fresh ? "Running" : "Evidence unavailable";
  $("labTrials").textContent = fmt(s.trials) + " trials";
  $("labTaskDescription").textContent = discoveryDescriptions[discoveryTask];
  const models = s.tasks?.[discoveryTask] || {},
    model = models.spike_memory;
  $("labMatrix").replaceChildren();
  for (let cue = 0; cue < 8; cue++)
    for (let arm = 0; arm < 4; arm++) {
      const value = model?.learned_success?.[cue]?.[arm];
      const cell = cognitiveNode(
        "div",
        Number.isFinite(value) ? Math.round(value * 100) + "%" : "—",
      );
      const strength = Number.isFinite(value) ? value : 0;
      cell.style.background = `rgba(145,187,117,${0.04 + 0.42 * strength})`;
      cell.title = `Cue ${cue + 1}, choice ${arm + 1}; success ${Number.isFinite(value) ? value.toFixed(3) : "unknown"}; uncertainty ${model?.outcome_uncertainty?.[cue]?.[arm]?.toFixed(3) || "unknown"}`;
      $("labMatrix").append(cell);
    }
  $("labComparisons").replaceChildren();
  for (const [branch, label] of Object.entries(discoveryLabels)) {
    const value = models[branch]?.mean_net_reward;
    const row = cognitiveNode("div", undefined, "lab-comparison"),
      line = cognitiveNode("i"),
      bar = cognitiveNode("span");
    bar.style.width = Math.max(0, Math.min(100, (value || 0) * 100)) + "%";
    line.append(bar);
    row.append(
      cognitiveNode("span", label),
      line,
      cognitiveNode("b", Number.isFinite(value) ? value.toFixed(3) : "—"),
    );
    $("labComparisons").append(row);
  }
  const symbol = models.symbolic_memory?.mean_net_reward,
    spike = model?.mean_net_reward;
  $("labResult").textContent = !fresh
    ? "Last recorded values. Current experiment evidence is unavailable."
    : model?.settled >= 40 && Number.isFinite(symbol) && Number.isFinite(spike)
      ? `Spike memory ${spike >= symbol ? "+" : "−"}${Math.abs(spike - symbol).toFixed(3)} versus symbolic memory. Live observations; fresh-seed validation is in the docs.`
      : "Gathering outcomes. A useful neural advantage must be earned against the symbolic control.";
  cognitiveCount("labSettled", model?.settled);
  cognitiveCount("labProbes", model?.probes);
  cognitiveCount("labChanges", model?.detected_changes);
  $("labBalance").textContent = Number.isFinite(model?.virtual_balance)
    ? model.virtual_balance.toFixed(1)
    : "—";
  $("labContinuity").textContent = `Saved trial ${fmt(s.record_seq)}`;
  $("labRecord").textContent = JSON.stringify(
    {
      genesis_utc: s.genesis_utc,
      source_sha256: s.source_sha256,
      record_head: s.record_head,
      integrity: s.integrity,
      sensor: s.sensor,
      last_trial: s.last_trial,
      scope: s.scope,
    },
    null,
    2,
  );
}
document.querySelectorAll("[data-lab-task]").forEach((button) =>
  button.addEventListener("click", () => {
    discoveryTask = button.dataset.labTask;
    document
      .querySelectorAll("[data-lab-task]")
      .forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    renderDiscovery();
  }),
);
async function pollDiscovery() {
  if (discoveryRequest || document.hidden) return;
  discoveryRequest = true;
  try {
    const response = await fetch("/api/discovery", {
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("Discovery evidence unavailable");
    discoveryState = await response.json();
    discoveryAvailable = true;
    renderDiscovery();
  } catch {
    discoveryAvailable = false;
    renderDiscovery();
    $("labPhase").textContent = "Evidence unavailable";
  } finally {
    discoveryRequest = false;
  }
}
pollDiscovery();
setInterval(pollDiscovery, 5000);

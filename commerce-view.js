const commerceMoney = (value) =>
  Number.isInteger(value)
    ? `${(value / 1e6).toLocaleString(undefined, { maximumFractionDigits: 6 })} USDC`
    : "—";
async function pollCommerce() {
  if (document.hidden) return;
  try {
    const evidence = await fetchJSON("/api/commerce");
    const age = Date.now() - Date.parse(evidence.updated_utc);
    if (!(age >= 0 && age < 60000)) throw Error("Stale commerce evidence");
    if (
      !["guarded", "running"].includes(evidence.phase) ||
      typeof evidence.financial_execution !== "boolean" ||
      !evidence.policy ||
      !evidence.counts ||
      typeof evidence.counts !== "object" ||
      Array.isArray(evidence.counts) ||
      !Array.isArray(evidence.records) ||
      Object.values(evidence.counts).some(
        (value) => !Number.isSafeInteger(value) || value < 0,
      )
    )
      throw Error("Incomplete commerce evidence");
    if (evidence.usdc_balance) {
      const balanceAge = Date.now() - evidence.usdc_balance.checked_at * 1000;
      if (
        !(balanceAge >= 0 && balanceAge < 60000) ||
        !Number.isSafeInteger(evidence.usdc_balance.micro_usdc) ||
        evidence.usdc_balance.micro_usdc < 0
      )
        throw Error("Unverified current balance");
    }
    const counts = evidence.counts;
    $("commercePhase").textContent =
      evidence.phase === "unknown"
        ? "Evidence unavailable"
        : evidence.financial_execution
          ? "Execution enabled"
          : "Execution off";
    $("commerceBalance").textContent = commerceMoney(evidence.usdc_balance?.micro_usdc);
    $("commerceSpent").textContent = commerceMoney(evidence.settled_micro_usdc);
    $("commerceDelivered").textContent = fmt(
      (counts.delivered || 0) + (counts.evaluated || 0),
    );
    $("commerceEvaluated").textContent = fmt(counts.evaluated || 0);
    $("commerceDailyLimit").textContent = commerceMoney(
      evidence.policy?.per_day_micro_usdc,
    );
    $("commerceJobLimit").textContent = commerceMoney(
      evidence.policy?.per_job_micro_usdc,
    );
    $("commerceToday").textContent = commerceMoney(evidence.daily_committed_micro_usdc);
    const downloadable =
      Number.isSafeInteger(evidence.ledger_index?.pages) &&
      evidence.ledger_index.pages > 0;
    $("commerceDownload").setAttribute(
      "aria-disabled",
      downloadable ? "false" : "true",
    );
    $("commerceDownload").setAttribute("tabindex", downloadable ? "0" : "-1");
    if (downloadable)
      $("commerceDownload").setAttribute("href", "/download/commerce?page=0");
    else $("commerceDownload").removeAttribute("href");
    $("commerceDownload").style.pointerEvents = downloadable ? "" : "none";
    const timeline = $("commerceTimeline");
    const events = (evidence.records || []).slice(0, 6);
    const signature = events.map((event) => event.hash).join(":");
    if (timeline.dataset.signature !== signature) {
      timeline.replaceChildren();
      for (const event of events) {
        const row = document.createElement("div");
        const label = document.createElement("span");
        label.textContent = event.state.replaceAll("_", " ");
        const detail = document.createElement("code");
        detail.textContent =
          event.transaction || event.recipient || event.job_id || event.hash;
        detail.title = detail.textContent;
        row.append(label, detail);
        timeline.append(row);
      }
      if (!events.length) {
        const empty = document.createElement("p");
        empty.textContent = "No money movements recorded.";
        timeline.append(empty);
      }
      timeline.dataset.signature = signature;
    }
    $("commerceNote").textContent =
      evidence.error ||
      (evidence.missing?.length
        ? "Payments remain disabled until provider, funding and signing checks pass."
        : "Payments, delivered data and evaluated outcomes have separate records. Each financial rail reports its own activation status.");
    $("commerceRecords").textContent = nuriaPresentationJSON(
      {
        policy: {
          per_day_micro_usdc: evidence.policy.per_day_micro_usdc,
          per_job_micro_usdc: evidence.policy.per_job_micro_usdc,
          reserve_micro_usdc: evidence.policy.reserve_micro_usdc,
          enabled: evidence.financial_execution,
        },
        rails: evidence.rails,
        integrity: evidence.integrity,
        records: evidence.records,
        learning_rule: evidence.learning_rule,
      },
      null,
      2,
    );
  } catch (_) {
    $("commerceDownload").setAttribute("aria-disabled", "true");
    $("commerceDownload").style.pointerEvents = "none";
    $("commerceDownload").setAttribute("tabindex", "-1");
    $("commerceDownload").removeAttribute("href");
    $("commerceTimeline").replaceChildren();
    $("commerceTimeline").dataset.signature = "";
    $("commerceRecords").textContent = "Current evidence is unavailable.";
    $("commercePhase").textContent = "Evidence unavailable";
    for (const name of [
      "commerceBalance",
      "commerceSpent",
      "commerceDelivered",
      "commerceEvaluated",
      "commerceDailyLimit",
      "commerceJobLimit",
      "commerceToday",
    ])
      $(name).textContent = "—";
    $("commerceNote").textContent =
      "Payment evidence is unavailable. Balances and outcomes are unknown.";
  }
}
pollCommerce();
setInterval(pollCommerce, 10000);

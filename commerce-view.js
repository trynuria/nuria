let commerceWallet = null;
const commerceMoney = (value) =>
  Number.isInteger(value)
    ? `${(value / 1e6).toLocaleString(undefined, { maximumFractionDigits: 6 })} USDC`
    : "—";
async function pollCommerce() {
  if (document.hidden) return;
  try {
    const evidence = await fetchJSON("/api/commerce");
    if (!(Date.now() - Date.parse(evidence.updated_utc) < 60000))
      throw Error("Stale commerce evidence");
    const counts = evidence.counts || {};
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
    $("commerceDownload").setAttribute(
      "aria-disabled",
      evidence.ledger_index?.pages ? "false" : "true",
    );
    $("commerceDownload").style.pointerEvents = evidence.ledger_index?.pages
      ? ""
      : "none";
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
    commerceWallet = evidence.policy?.spending_wallet || null;
    $("commerceWallet").textContent = commerceWallet || "Not configured";
    $("commerceWallet").title = commerceWallet || "";
    $("copyCommerceWallet").disabled = !commerceWallet;
    $("creatorWallet").textContent =
      evidence.policy?.creator_wallet || "Not configured";
    $("creatorWallet").title = evidence.policy?.creator_wallet || "";
    const names = {
      mint: "mint",
      creator_wallet: "fee wallet",
      spending_wallet: "spending wallet",
      verified_provider: "provider",
      spending_limits: "limits",
      isolated_signing_key: "signer",
      managed_custody_configuration: "managed signer",
      USDC_funding: "USDC funding",
    };
    $("commerceNote").textContent =
      evidence.error ||
      (evidence.missing?.length
        ? `To connect: ${evidence.missing.map((item) => names[item] || item).join(", ")}.`
        : "Payments, delivered data and evaluated outcomes have separate records. Each financial rail reports its own activation status.");
    $("commerceRecords").textContent = JSON.stringify(
      {
        policy: evidence.policy,
        rails: evidence.rails,
        fee_path: evidence.fee_path,
        integrity: evidence.integrity,
        records: evidence.records,
        learning_rule: evidence.learning_rule,
      },
      null,
      2,
    );
  } catch (_) {
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
$("copyCommerceWallet").addEventListener("click", async () => {
  if (!commerceWallet) return;
  try {
    await navigator.clipboard.writeText(commerceWallet);
    $("copyCommerceWallet").textContent = "Copied";
  } catch (_) {
    $("copyCommerceWallet").textContent = "Select address";
  }
});
pollCommerce();
setInterval(pollCommerce, 10000);

const workViews = ["work", "treasury", "results", "evidence"];
let workSnapshot = null;
let selectedWork = null;
let workRequest = false;
let workSignature = "";

function workSurface(view, updateURL = false) {
  const active = workViews.includes(view);
  document.body.classList.toggle("work-mode", active);
  $("work").hidden = !active;
  if (active) {
    document.body.classList.remove("focus-mode");
    $("focus").setAttribute("aria-pressed", "false");
    openWorkView(view);
  }
  for (const link of document.querySelectorAll(".header .nav a")) {
    const current = active
      ? link.dataset.workView === view
      : link.hasAttribute("data-observe");
    if (current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  $("pageScroll").scrollTo({ top: 0, behavior: "auto" });
  if (updateURL) history.pushState(null, "", active ? "#" + view : "#neural-field");
}

function openWorkView(view, focus = false) {
  if (!workViews.includes(view)) return;
  for (const name of workViews) {
    const active = name === view;
    $("work-tab-" + name).setAttribute("aria-selected", String(active));
    $("work-tab-" + name).tabIndex = active ? 0 : -1;
    $("work-panel-" + name).hidden = !active;
  }
  if (focus) $("work-tab-" + view).focus({ preventScroll: true });
  if (document.body.classList.contains("work-mode")) {
    for (const link of document.querySelectorAll(".header .nav a")) {
      if (link.dataset.workView === view) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }
}
for (const name of workViews) {
  const button = $("work-tab-" + name);
  button.addEventListener("click", () => {
    openWorkView(name);
    history.replaceState(null, "", "#" + name);
  });
  button.addEventListener("keydown", (event) => {
    const current = workViews.indexOf(name);
    let next;
    if (event.key === "ArrowRight") next = (current + 1) % workViews.length;
    if (event.key === "ArrowLeft")
      next = (current + workViews.length - 1) % workViews.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = workViews.length - 1;
    if (next === undefined) return;
    event.preventDefault();
    openWorkView(workViews[next], true);
    history.replaceState(null, "", "#" + workViews[next]);
  });
}
document.querySelectorAll("[data-work-view]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    workSurface(link.dataset.workView, true);
  });
});
document
  .querySelectorAll(
    '[href="#neural-field"],.logo[href="#overview"],[href="#modelInspection"],[data-open]',
  )
  .forEach((link) => {
    link.addEventListener("click", () => {
      if (document.body.classList.contains("work-mode")) workSurface("observe", true);
    });
  });
window.addEventListener("hashchange", () => workSurface(location.hash.slice(1)));
window.addEventListener("popstate", () => workSurface(location.hash.slice(1)));
workSurface(location.hash.slice(1));

function workNode(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function workEmpty(title, description) {
  const node = workNode("div", undefined, "work-empty");
  node.append(workNode("h3", title), workNode("p", description));
  return node;
}
function workFacts(values) {
  const list = workNode("dl", undefined, "work-contract");
  for (const [label, text] of values) {
    const row = workNode("div");
    row.append(workNode("dt", label), workNode("dd", text ?? "Not recorded"));
    list.append(row);
  }
  return list;
}
function workLabel(value) {
  return value.replaceAll("_", " ");
}
function validWorkEvidence(evidence, now = Date.now()) {
  const age = now - Date.parse(evidence?.updated_utc);
  const integer = (value) => Number.isSafeInteger(value) && value >= 0;
  if (
    !(age >= 0 && age < 60000) ||
    evidence?.schema !== "nuria.work.v1" ||
    !["guarded", "running"].includes(evidence.phase) ||
    typeof evidence.financial_execution !== "boolean" ||
    !Array.isArray(evidence.jobs) ||
    evidence.jobs.length > 80 ||
    !integer(evidence.total_jobs) ||
    evidence.total_jobs < evidence.jobs.length ||
    !evidence.money?.policy ||
    !integer(evidence.money.committed_micro_usdc) ||
    !integer(evidence.money.settled_micro_usdc) ||
    !Array.isArray(evidence.money.missing) ||
    !Array.isArray(evidence.catalog) ||
    evidence.catalog.length > 12 ||
    evidence.catalog.some(
      (item) =>
        typeof item.name !== "string" ||
        typeof item.detail !== "string" ||
        !["planned", "gated", "prepared", "connected"].includes(item.status),
    ) ||
    evidence.integrity?.valid !== true ||
    !integer(evidence.integrity.events) ||
    !/^[a-f0-9]{64}$/.test(evidence.integrity.head) ||
    new Set(evidence.jobs.map((job) => job.id)).size !== evidence.jobs.length ||
    evidence.jobs.some(
      (job) =>
        typeof job.id !== "string" ||
        typeof job.title !== "string" ||
        typeof job.purpose !== "string" ||
        typeof job.acceptance !== "string" ||
        !integer(job.amount_micro_usdc) ||
        !["pending", "closed", "awaiting_delivery", "delivered", "evaluated"].includes(
          job.job_state,
        ) ||
        ![
          "reserved",
          "not_disclosed",
          "unresolved",
          "expired_unsettled",
          "settled",
        ].includes(job.payment_state) ||
        !["pending", "measured", "not_implemented"].includes(job.outcome_state) ||
        (job.reward !== null &&
          (typeof job.reward !== "number" || !Number.isFinite(job.reward))) ||
        (job.payment_state === "settled" &&
          (!job.transaction || !Number.isSafeInteger(job.settlement_slot))) ||
        (["delivered", "evaluated"].includes(job.job_state) &&
          !/^[a-f0-9]{64}$/.test(job.artifact_sha256)) ||
        (job.outcome_state === "measured" &&
          (job.job_state !== "evaluated" || job.reward === null)),
    )
  )
    throw Error("Incomplete work evidence");
  const balance = evidence.money.balance;
  if (
    balance &&
    (!integer(balance.micro_usdc) ||
      !(
        now - balance.checked_at * 1000 >= 0 && now - balance.checked_at * 1000 < 60000
      ))
  )
    throw Error("Unverified work balance");
  const commissions = evidence.commissions || [];
  const hash = (value) => /^[a-f0-9]{64}$/.test(value);
  if (
    !Array.isArray(commissions) ||
    commissions.length > 40 ||
    new Set(commissions.map((item) => item.id)).size !== commissions.length ||
    commissions.some(
      (item) =>
        !hash(item.id) ||
        !hash(item.decision_hash) ||
        !hash(item.contract_sha256) ||
        !hash(item.dataset_sha256) ||
        !["agent", "human", "compute"].includes(item.kind) ||
        typeof item.title !== "string" ||
        item.title.length > 500 ||
        typeof item.purpose !== "string" ||
        item.purpose.length > 500 ||
        ![
          "proposed",
          "reserved",
          "dispatching",
          "uncertain",
          "submitted",
          "overdue",
          "delivered",
          "accepted",
          "rejected",
          "evaluated",
          "cancelled",
        ].includes(item.state) ||
        !integer(item.committed_micro_usdc) ||
        !Number.isFinite(item.created_at) ||
        !Number.isFinite(item.updated_at) ||
        item.updated_at < item.created_at ||
        item.updated_at * 1000 > now + 2000 ||
        (item.state === "proposed" &&
          (item.committed_micro_usdc !== 0 ||
            item.settlement ||
            item.artifact_sha256)) ||
        (["delivered", "accepted", "rejected", "evaluated"].includes(item.state) &&
          !hash(item.artifact_sha256)) ||
        (item.acceptance &&
          (typeof item.acceptance.passed !== "boolean" ||
            item.acceptance.metric !== "brier" ||
            item.acceptance.artifact_sha256 !== item.artifact_sha256 ||
            item.acceptance.dataset_sha256 !== item.dataset_sha256 ||
            !Number.isFinite(item.acceptance.improvement))) ||
        (item.settlement &&
          (item.settlement.finalized !== true ||
            item.settlement.chain_id !== 8453 ||
            item.settlement.token !== "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913" ||
            !/^0x[a-fA-F0-9]{64}$/.test(item.settlement.transaction) ||
            !/^0x[a-fA-F0-9]{40}$/.test(item.settlement.recipient) ||
            !/^0x[a-fA-F0-9]{40}$/.test(item.settlement.sender) ||
            !integer(item.settlement.block) ||
            !integer(item.settlement.amount_micro_usdc) ||
            item.settlement.sender !== item.payer ||
            item.settlement.recipient !== item.recipient ||
            item.settlement.amount_micro_usdc > item.committed_micro_usdc ||
            item.settlement.offer_sha256 !== item.offer_sha256)) ||
        (item.outcome &&
          (item.state !== "evaluated" ||
            !item.settlement ||
            !item.acceptance ||
            item.outcome.accepted !== item.acceptance.passed ||
            item.outcome.artifact_sha256 !== item.artifact_sha256 ||
            item.outcome.dataset_sha256 !== item.dataset_sha256 ||
            !Number.isFinite(item.outcome.reward) ||
            Math.abs(item.outcome.reward) > 1)) ||
        (item.state === "evaluated" && !item.outcome),
    ) ||
    (evidence.commission_totals &&
      (!integer(evidence.commission_totals.total) ||
        evidence.commission_totals.total < commissions.length ||
        !integer(evidence.commission_totals.committed_micro_usdc) ||
        !integer(evidence.commission_totals.settled_micro_usdc)))
  )
    throw Error("Incomplete commissioned-work evidence");
  return evidence;
}

function renderWorkDetail() {
  const commission = workSnapshot?.commissions?.find(
    (item) => "commission:" + item.id === selectedWork,
  );
  if (commission) {
    const payment = commission.settlement;
    const acceptance = commission.acceptance;
    $("workDetail").replaceChildren(
      workNode(
        "span",
        `${commission.kind} · ${commission.provider || "provider not selected"}`,
        "work-kicker",
      ),
      workNode("h3", commission.title),
      workNode("p", commission.purpose),
      workFacts([
        ["Request", workLabel(commission.state)],
        ["Cost ceiling", commerceMoney(commission.committed_micro_usdc)],
        [
          "Payment",
          payment
            ? `${commerceMoney(payment.amount_micro_usdc)} · finalized on Base`
            : "No verified payment",
        ],
        ["Recipient", commission.recipient],
        [
          "Delivery",
          commission.artifact_sha256
            ? "Structured artifact received"
            : "No artifact received",
        ],
        [
          "Acceptance",
          acceptance
            ? `${acceptance.passed ? "Passed" : "Failed"} · Brier improvement ${acceptance.improvement.toFixed(6)}`
            : "Not checked",
        ],
        [
          "Outcome",
          commission.outcome
            ? `Task reward ${commission.outcome.reward.toFixed(6)} · includes committed cost`
            : "Not measured",
        ],
        ["Transaction", payment?.transaction],
        ["Artifact SHA-256", commission.artifact_sha256],
        ["Dataset SHA-256", commission.dataset_sha256],
        ["Task contract SHA-256", commission.contract_sha256],
        ["Offer SHA-256", commission.offer_sha256],
        ["Decision hash", commission.decision_hash],
      ]),
    );
    return;
  }
  const job = workSnapshot?.jobs.find((item) => item.id === selectedWork);
  if (!job) {
    $("workDetail").replaceChildren(
      workNode("span", "ONE PERSISTENT ENTITY", "work-kicker"),
      workNode("h3", "Every job has a purpose."),
      workNode(
        "p",
        "Each commission will show what Nuria requested, what it paid, what arrived and whether it helped.",
      ),
      workFacts([
        ["Purpose", "What the work is meant to improve"],
        ["Delivery", "What the provider must return"],
        ["Acceptance", "How the result will be checked"],
        ["Outcome", "What changed after it was used"],
      ]),
    );
    return;
  }
  $("workDetail").replaceChildren(
    workNode("span", job.provider, "work-kicker"),
    workNode("h3", job.title),
    workNode("p", job.purpose),
    workFacts([
      [
        "Payment",
        `${commerceMoney(job.amount_micro_usdc)} · ${workLabel(job.payment_state)}`,
      ],
      ["Recipient", job.recipient],
      [
        "Delivery",
        `${workLabel(job.job_state)} · ${job.delivery_schema || "schema not recorded"}`,
      ],
      ["Acceptance", job.acceptance],
      [
        "Outcome",
        job.outcome_state === "measured"
          ? `Measured reward: ${job.reward.toFixed(6)}. This task-specific score is not a general learning result.`
          : workLabel(job.outcome_state),
      ],
      ["Transaction", job.transaction],
      ["Artifact SHA-256", job.artifact_sha256],
      ["Decision hash", job.decision_hash],
    ]),
  );
}
function renderWork(evidence) {
  workSnapshot = evidence;
  $("workHealth").textContent = evidence.financial_execution
    ? "Execution enabled"
    : "Execution off";
  $("workHealth").dataset.state = evidence.financial_execution ? "running" : "guarded";
  const commissions = evidence.commissions || [];
  const requests = [
    ...evidence.jobs,
    ...commissions.map((item) => ({
      ...item,
      id: "commission:" + item.id,
      provider: item.provider || `${item.kind} · provider not selected`,
      job_state: item.state,
      amount_micro_usdc: item.committed_micro_usdc,
    })),
  ].sort((a, b) => b.created_at - a.created_at);
  const signature = JSON.stringify(requests);
  if (signature !== workSignature) {
    const list = $("workJobs");
    const scroll = list.scrollTop;
    const focused = document.activeElement?.dataset.workId;
    const rows = requests.map((job) => {
      const row = workNode("button", undefined, "work-job");
      row.type = "button";
      row.dataset.workId = job.id;
      row.setAttribute("aria-pressed", String(job.id === selectedWork));
      row.append(
        workNode("span", job.title, "work-job-title"),
        workNode("span", job.provider, "work-job-provider"),
      );
      const meta = workNode("span", undefined, "work-job-meta");
      meta.append(
        workNode("span", workLabel(job.job_state)),
        workNode("span", commerceMoney(job.amount_micro_usdc)),
      );
      row.append(meta);
      row.addEventListener("click", () => {
        selectedWork = job.id;
        for (const child of list.children)
          child.setAttribute(
            "aria-pressed",
            String(child.dataset.workId === selectedWork),
          );
        renderWorkDetail();
      });
      return row;
    });
    list.replaceChildren(
      ...(rows.length
        ? rows
        : [
            workEmpty(
              "No commissioned work yet",
              "Production jobs appear here when a configured provider receives a real request. Local experiments and test payments are kept separate.",
            ),
          ]),
    );
    list.scrollTop = scroll;
    if (focused)
      rows
        .find((row) => row.dataset.workId === focused)
        ?.focus({ preventScroll: true });
    workSignature = signature;
  }
  renderWorkDetail();
  $("workCatalog").replaceChildren(
    ...evidence.catalog.map((item) => {
      const card = workNode("article");
      card.append(
        workNode("span", workLabel(item.status), "work-kicker"),
        workNode("h3", item.name),
        workNode("p", item.detail),
      );
      return card;
    }),
  );
  const money = evidence.money,
    policy = money.policy;
  $("workBalance").textContent = commerceMoney(money.balance?.micro_usdc);
  $("workCommitted").textContent = commerceMoney(money.committed_micro_usdc);
  $("workSpent").textContent = commerceMoney(money.settled_micro_usdc);
  $("workExecution").textContent = evidence.financial_execution ? "Enabled" : "Off";
  $("workCreator").textContent = policy.creator_wallet || "Not configured";
  $("workWallet").textContent = policy.spending_wallet || "Not configured";
  $("copyWorkWallet").disabled = !policy.spending_wallet;
  $("workPolicy").textContent =
    `${commerceMoney(policy.per_day_micro_usdc)} / day · ${commerceMoney(policy.per_job_micro_usdc)} / job · ${commerceMoney(policy.reserve_micro_usdc)} reserve`;
  $("workMoneyNote").textContent = money.balance
    ? "Balance is an observed finalized USDC balance. A deposit is not proof of creator fees or a useful purchase."
    : "No operating wallet balance has been established. Creator fees, claimed funds and available treasury are separate observations.";
  const measured = evidence.jobs.filter((job) => job.outcome_state === "measured");
  measured.push(
    ...commissions
      .filter((item) => item.outcome)
      .map((item) => ({ ...item, reward: item.outcome.reward })),
  );
  $("workResults").replaceChildren(
    ...(measured.length
      ? measured.map((job) => {
          const card = workNode("article", undefined, "work-result");
          card.append(
            workNode("h3", job.title),
            workNode("p", job.purpose),
            workFacts([
              ["Measured reward", job.reward.toFixed(6)],
              ["Artifact", job.artifact_sha256],
              [
                "Attribution",
                "Task-specific evaluated outcome; no claim of whole-system superiority.",
              ],
            ]),
          );
          return card;
        })
      : [
          workEmpty(
            "No measured paid outcomes",
            "Delivery comes first. Results appear after the declared evaluation has run; a successful transfer alone earns no learning credit.",
          ),
        ]),
  );
  $("workEvidence").replaceChildren(
    workFacts([
      ["Journal events", String(evidence.integrity.events)],
      ["Recorded head", evidence.integrity.head],
      [
        "Coverage",
        `${evidence.jobs.length} recent jobs of ${evidence.total_jobs} recorded${evidence.coverage?.truncated ? "; use the payment ledger for complete event history" : ""}`,
      ],
      [
        "Commission requests",
        `${commissions.length} recent of ${evidence.commission_totals?.total ?? commissions.length}; proposals do not establish orders or payments`,
      ],
      [
        "Base cost ceilings",
        commerceMoney(evidence.commission_totals?.committed_micro_usdc),
      ],
      [
        "Base verified payments",
        commerceMoney(evidence.commission_totals?.settled_micro_usdc),
      ],
      [
        "Verification",
        "Local hash continuity. Payment, delivery and outcome require their own evidence; this is not an independent attestation.",
      ],
    ]),
  );
  $("workExport").setAttribute("href", "/api/work");
  $("workExport").setAttribute("aria-disabled", "false");
  $("workExport").setAttribute("tabindex", "0");
}
function clearWork() {
  workSnapshot = null;
  workSignature = "";
  $("workHealth").textContent = "Evidence unavailable";
  $("workHealth").dataset.state = "unknown";
  for (const id of ["workBalance", "workCommitted", "workSpent"])
    $(id).textContent = "—";
  $("workExecution").textContent = "Unknown";
  for (const id of ["workCreator", "workWallet", "workPolicy"])
    $(id).textContent = "Unavailable";
  $("copyWorkWallet").disabled = true;
  $("workJobs").replaceChildren(
    workEmpty(
      "Work evidence unavailable",
      "The latest record could not be verified. Previous balances and job states are not displayed as current.",
    ),
  );
  $("workDetail").replaceChildren(
    workEmpty(
      "Awaiting a verified record",
      "Payment, delivery and outcome are unknown.",
    ),
  );
  $("workResults").replaceChildren(
    workEmpty(
      "Outcome evidence unavailable",
      "The latest result has not been verified.",
    ),
  );
  $("workCatalog").replaceChildren();
  $("workEvidence").replaceChildren(
    workNode("p", "Current journal evidence is unavailable."),
  );
  $("workMoneyNote").textContent =
    "Financial evidence is unavailable. Missing observations are unknown.";
  $("workExport").removeAttribute("href");
  $("workExport").setAttribute("aria-disabled", "true");
  $("workExport").setAttribute("tabindex", "-1");
}
async function pollWork() {
  if (document.hidden || workRequest) return;
  workRequest = true;
  try {
    renderWork(validWorkEvidence(await fetchJSON("/api/work")));
  } catch (_) {
    clearWork();
  } finally {
    workRequest = false;
  }
}
$("copyWorkWallet").addEventListener("click", async () => {
  const wallet = workSnapshot?.money.policy.spending_wallet;
  if (!wallet) return;
  try {
    await navigator.clipboard.writeText(wallet);
    $("copyWorkWallet").textContent = "Copied";
  } catch (_) {
    $("copyWorkWallet").textContent = "Select address";
  }
});
pollWork();
setInterval(pollWork, 5000);

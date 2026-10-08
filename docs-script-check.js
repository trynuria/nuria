"use strict";
function initNuriaControls() {
  for (const field of document.querySelectorAll("input, textarea, form")) {
    field.setAttribute("autocomplete", "off");
    if (field.tagName === "FORM") continue;
    field.setAttribute("autocorrect", "off");
    field.setAttribute("autocapitalize", "none");
    field.setAttribute("spellcheck", "false");
    field.setAttribute("data-1p-ignore", "true");
    field.setAttribute("data-lpignore", "true");
    field.setAttribute("data-bwignore", "true");
  }
  for (const control of document.querySelectorAll("[data-select]")) {
    const trigger = control.querySelector("[data-select-trigger]");
    const popup = control.querySelector("[role=listbox]");
    const input = control.querySelector("input[type=hidden]");
    const label = trigger.querySelector("[data-select-label]");
    const options = [...popup.querySelectorAll("[role=option]")];
    let focused = 0,
      search = "",
      typedAt = 0;
    const selected = () => options.findIndex((o) => o.dataset.value === input.value);
    function close(returnFocus = false) {
      popup.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (returnFocus) trigger.focus({ preventScroll: true });
    }
    function focus(index) {
      focused = Math.max(0, Math.min(options.length - 1, index));
      for (const [i, option] of options.entries())
        option.tabIndex = i === focused ? 0 : -1;
      options[focused].focus({ preventScroll: true });
    }
    function open(index = selected()) {
      popup.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      focus(index < 0 ? 0 : index);
    }
    function choose(option) {
      input.value = option.dataset.value;
      label.textContent = option.textContent.trim();
      for (const item of options)
        item.setAttribute("aria-selected", String(item === option));
      close(true);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    trigger.addEventListener("click", () => (popup.hidden ? open() : close()));
    for (const option of options)
      option.addEventListener("click", () => choose(option));
    control.addEventListener("keydown", (event) => {
      const key = event.key;
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(key)) {
        event.preventDefault();
        event.stopPropagation();
        if (popup.hidden)
          open(key === "Home" ? 0 : key === "End" ? options.length - 1 : selected());
        else
          focus(
            key === "Home"
              ? 0
              : key === "End"
                ? options.length - 1
                : focused + (key === "ArrowDown" ? 1 : -1),
          );
      } else if ((key === "Enter" || key === " ") && !popup.hidden) {
        event.preventDefault();
        event.stopPropagation();
        choose(options[focused]);
      } else if (key === "Escape" && !popup.hidden) {
        event.preventDefault();
        event.stopPropagation();
        close(true);
      } else if (key === "Tab" && !popup.hidden) {
        close(true);
      } else if (
        key.length === 1 &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey
      ) {
        const now = performance.now();
        search = (now - typedAt < 700 ? search : "") + key.toLowerCase();
        typedAt = now;
        const index = options.findIndex((o) =>
          o.textContent.trim().toLowerCase().startsWith(search),
        );
        if (index >= 0) {
          event.preventDefault();
          if (popup.hidden) open(index);
          else focus(index);
        }
      }
    });
    document.addEventListener("pointerdown", (event) => {
      if (!control.contains(event.target)) close();
    });
    control.closest("details")?.addEventListener("toggle", (event) => {
      if (!event.target.open) close();
    });
    window.addEventListener("blur", () => close());
  }
}

function initNuriaDocs() {
  const byId = (id) => document.getElementById(id),
    articles = [...document.querySelectorAll(".docs-article")];
  const scroll = byId("docsScroll"),
    frame = byId("docsFrame"),
    dialog = byId("docsSearchDialog"),
    input = byId("docsSearchInput");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let current = "introduction",
    observer = null,
    searchReturn = null;
  const searchIndex = [];
  for (const article of articles) {
    const chapter = article.id.slice(4),
      headings = [...article.querySelectorAll("h2")];
    searchIndex.push({
      chapter,
      anchor: chapter,
      title: article.dataset.title,
      context: article.querySelector(".docs-lead").textContent,
    });
    for (const heading of headings) {
      let text = [],
        next = heading.nextElementSibling;
      while (next && next.tagName !== "H2") {
        if (next.classList.contains("capability-map")) {
          text.push(
            [...next.querySelectorAll(".capability-item")]
              .map((item) => {
                const name = item.querySelector("h4").textContent.trim();
                const status = item
                  .querySelector(".capability-status")
                  .textContent.trim();
                const evidence = item.querySelector("p").textContent.trim();
                return name + ". " + status + ". " + evidence;
              })
              .join(" "),
          );
        } else text.push(next.textContent);
        next = next.nextElementSibling;
      }
      if (!text.length && heading.parentElement.classList.contains("docs-question"))
        text = [heading.parentElement.querySelector("p").textContent];
      searchIndex.push({
        chapter,
        anchor: heading.id,
        title: heading.textContent,
        context: text.join(" ").replace(/\s+/g, " ").trim(),
      });
    }
  }
  const capabilityMap = byId("capabilityMap");
  for (const button of capabilityMap.querySelectorAll("[data-capability-filter]")) {
    button.addEventListener("click", () => {
      const status = button.dataset.capabilityFilter;
      let visible = 0;
      for (const item of capabilityMap.querySelectorAll("[data-capability-status]")) {
        item.hidden = status !== "all" && item.dataset.capabilityStatus !== status;
        if (!item.hidden) visible++;
      }
      for (const group of capabilityMap.querySelectorAll(".capability-group")) {
        const items = [...group.querySelectorAll("[data-capability-status]")];
        const shown = items.filter((item) => !item.hidden).length;
        group.hidden = shown === 0;
        group.querySelector(".capability-group-head > span").textContent =
          shown + (shown === 1 ? " capability" : " capabilities");
      }
      for (const filter of capabilityMap.querySelectorAll("[data-capability-filter]"))
        filter.setAttribute("aria-pressed", String(filter === button));
      byId("capabilityCount").textContent =
        status === "all"
          ? "Showing all 51 capabilities"
          : "Showing " + visible + " of 51 capabilities";
    });
  }
  function setMenu(open) {
    frame.classList.toggle("docs-menu-open", open);
    byId("docsMenu").setAttribute("aria-expanded", String(open));
    if (open) byId("docsSidebar").querySelector("[aria-current=page]")?.focus();
  }
  function navigate(anchor, focus = true) {
    history.pushState(null, "", "/?page=docs#" + anchor);
    route(focus);
  }
  function route(focus = false) {
    let anchor;
    try {
      anchor = decodeURIComponent(location.hash.slice(1));
    } catch {
      anchor = "introduction";
    }
    const requested = anchor.split("--")[0];
    current = articles.some((a) => a.id === "doc-" + requested)
      ? requested
      : "introduction";
    const article = byId("doc-" + current);
    for (const a of articles) a.hidden = a !== article;
    document.title = article.dataset.title + " — Nuria Docs";
    byId("docsBreadcrumb").textContent = article.dataset.title;
    byId("docsMobileCurrent").textContent = article.dataset.title;
    for (const a of document.querySelectorAll(".docs-sidebar [data-chapter]")) {
      if (a.dataset.chapter === current) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    byId("docsOutline").replaceChildren();
    for (const heading of article.querySelectorAll("h2")) {
      const a = document.createElement("a");
      a.href = "/?page=docs#" + heading.id;
      a.textContent = heading.textContent;
      a.dataset.chapter = current;
      byId("docsOutline").append(a);
    }
    byId("docsPager").replaceChildren();
    const index = articles.indexOf(article);
    for (const [position, label] of [
      [index - 1, " Previous"],
      [index + 1, "Next "],
    ]) {
      if (position < 0 || position >= articles.length) {
        const spacer = document.createElement("span");
        spacer.className = "docs-next";
        byId("docsPager").append(spacer);
        continue;
      }
      const next = articles[position],
        a = document.createElement("a"),
        small = document.createElement("small");
      a.className = "docs-next";
      a.href = "/?page=docs#" + next.id.slice(4);
      a.dataset.chapter = next.id.slice(4);
      small.textContent = label;
      a.append(small, document.createTextNode(next.dataset.title));
      byId("docsPager").append(a);
    }
    setMenu(false);
    observer?.disconnect();
    const headings = [...article.querySelectorAll("h2")];
    observer = new IntersectionObserver(
      (entries) => {
        const active = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (active)
          for (const a of byId("docsOutline").querySelectorAll("a")) {
            if (a.hash.slice(1) === active.target.id)
              a.setAttribute("aria-current", "location");
            else a.removeAttribute("aria-current");
          }
      },
      { root: scroll, rootMargin: "-12% 0px -55% 0px", threshold: 0 },
    );
    headings.forEach((h) => observer.observe(h));
    const target =
      anchor.includes("--") && byId(anchor) && article.contains(byId(anchor))
        ? byId(anchor)
        : null;
    if (target) {
      const margin = parseFloat(getComputedStyle(target).scrollMarginTop) || 26;
      scroll.scrollTo({
        top:
          scroll.scrollTop +
          target.getBoundingClientRect().top -
          scroll.getBoundingClientRect().top -
          margin,
        behavior: "auto",
      });
      if (focus) {
        target.tabIndex = -1;
        target.focus({ preventScroll: true });
      }
    } else {
      scroll.scrollTo({ top: 0, behavior: "auto" });
      if (focus) article.querySelector("h1").focus({ preventScroll: true });
    }
  }
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-chapter]");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return;
    event.preventDefault();
    navigate(link.hash.slice(1));
  });
  window.addEventListener("hashchange", () => route());
  window.addEventListener("popstate", () => route());
  byId("docsMenu").addEventListener("click", () =>
    setMenu(!frame.classList.contains("docs-menu-open")),
  );
  byId("docsMenuOverlay").addEventListener("click", () => {
    setMenu(false);
    byId("docsMenu").focus();
  });
  const docsApp = document.querySelector(".docs-app"),
    skipLink = document.querySelector(".skip");
  function closeSearch() {
    dialog.hidden = true;
    docsApp.inert = false;
    skipLink.inert = false;
    searchReturn?.focus({ preventScroll: true });
  }
  function renderSearch() {
    const words = input.value.toLowerCase().trim().split(/\s+/).filter(Boolean),
      results = byId("docsSearchResults");
    results.replaceChildren();
    const rank = (item) =>
      words.reduce(
        (total, word) =>
          total +
          (item.title.toLowerCase().includes(word) ? 12 : 0) +
          (item.anchor.includes(word) ? 8 : 0) +
          (item.chapter.includes(word) ? 4 : 0),
        0,
      );
    const matches = (
      words.length
        ? searchIndex
            .filter((item) =>
              words.every((word) =>
                (
                  item.title +
                  " " +
                  item.context +
                  " " +
                  item.chapter +
                  " " +
                  item.anchor
                )
                  .toLowerCase()
                  .includes(word),
              ),
            )
            .sort((a, b) => rank(b) - rank(a))
        : searchIndex.filter((item) => item.anchor === item.chapter)
    ).slice(0, 12);
    if (!matches.length) {
      const empty = document.createElement("div");
      empty.className = "docs-search-empty";
      empty.textContent = "No matching notes. Try “spikes”, “fees” or “receipts”.";
      results.append(empty);
    }
    for (const item of matches) {
      const button = document.createElement("button"),
        title = document.createElement("strong"),
        description = document.createElement("span");
      button.className = "docs-search-result";
      title.textContent = item.title;
      description.textContent = words.length
        ? item.context.slice(0, 140) + (item.context.length > 140 ? "…" : "")
        : item.context;
      button.append(title, description);
      button.addEventListener("click", () => {
        closeSearch();
        navigate(item.anchor);
      });
      results.append(button);
    }
  }
  function openSearch() {
    searchReturn = document.activeElement;
    dialog.hidden = false;
    docsApp.inert = true;
    skipLink.inert = true;
    input.value = "";
    renderSearch();
    input.focus();
  }
  byId("docsSearch").addEventListener("click", openSearch);
  byId("docsSearchClose").addEventListener("click", closeSearch);
  input.addEventListener("input", renderSearch);
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) closeSearch();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      dialog.hidden ? openSearch() : closeSearch();
      return;
    }
    if (event.key === "Escape") {
      if (!dialog.hidden) {
        event.preventDefault();
        closeSearch();
      } else if (frame.classList.contains("docs-menu-open")) {
        setMenu(false);
        byId("docsMenu").focus();
      }
      return;
    }
    if (dialog.hidden) return;
    const results = [...byId("docsSearchResults").querySelectorAll("button")],
      index = results.indexOf(document.activeElement);
    if (event.key === "ArrowDown" && results.length) {
      event.preventDefault();
      results[(index + 1) % results.length].focus();
    } else if (event.key === "ArrowUp" && results.length) {
      event.preventDefault();
      index <= 0 ? input.focus() : results[index - 1].focus();
    } else if (
      event.key === "Enter" &&
      document.activeElement === input &&
      results.length
    ) {
      event.preventDefault();
      results[0].click();
    } else if (event.key === "Tab") {
      const focusables = [input, byId("docsSearchClose"), ...results],
        at = focusables.indexOf(document.activeElement);
      if (event.shiftKey && at === 0) {
        event.preventDefault();
        focusables.at(-1).focus();
      } else if (!event.shiftKey && at === focusables.length - 1) {
        event.preventDefault();
        input.focus();
      }
    }
  });
  for (const button of document.querySelectorAll("[data-copy-code]"))
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(
          button.closest(".docs-code").querySelector("code").textContent,
        );
        button.textContent = "Copied";
        setTimeout(() => (button.textContent = "Copy"), 1500);
      } catch {
        button.textContent = "Select to copy";
        setTimeout(() => (button.textContent = "Copy"), 1500);
      }
    });
  document.querySelector(".skip").addEventListener("click", (event) => {
    event.preventDefault();
    byId("docs-content").focus({ preventScroll: true });
    scroll.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
  });
  route();
}

if (new URLSearchParams(location.search).get("page") === "docs") {
  const content = document.getElementById("nuriaDocsTemplate").content.cloneNode(true);
  document.body.classList.add("docs-page");
  document.body.replaceChildren(content);
  initNuriaControls();
  initNuriaDocs();
} else {
  initNuriaControls();

  ("use strict");
  const $ = (id) => document.getElementById(id),
    fmt = (x, d = 0) =>
      Number.isFinite(x)
        ? x.toLocaleString(undefined, { maximumFractionDigits: d })
        : "—";
  const canvas = $("network"),
    ctx = canvas.getContext("2d"),
    chart = $("activity"),
    cx = chart.getContext("2d");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let state = null,
    topology = null,
    basePoints = [],
    points = [],
    weightedEdges = [],
    lastTick = -1,
    frameStart = 0,
    lastPaint = 0,
    zoom = 1,
    hover = -1,
    polling = false,
    eventCounter = 0,
    trackedInput = null;
  let mode = "neural",
    selected = "all",
    paused = reduced,
    motionClock = 0,
    activeTab = "experience";
  const colors = {
    buy: "#c7f4b0",
    sensory: "#bdcf9f",
    workspace: "#d8c18b",
    sell: "#dfa6b2",
    association: "#b8d2cb",
    memory: "#b9b0d8",
    policy: "#c8dba4",
    inhibition: "#96b6d2",
  };
  const shortNames = {
    buy: "Buy input",
    sensory: "Sensory",
    workspace: "Workspace",
    sell: "Sell input",
    association: "Association",
    memory: "Memory",
    policy: "Policy",
    inhibition: "Control",
  };
  function resize(c) {
    const r = c.getBoundingClientRect(),
      dpr = Math.min(
        devicePixelRatio || 1,
        2,
        Math.sqrt(4000000 / Math.max(1, r.width * r.height)),
      );
    const w = Math.round(r.width * dpr),
      h = Math.round(r.height * dpr);
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
    }
    return { w: r.width, h: r.height, dpr };
  }
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
    return {
      clamp,
      hash,
      layout,
      project,
      recordedFrame,
      energy,
      transit,
      hit,
      bezier,
    };
  })();
  if (typeof module !== "undefined" && module.exports) module.exports = NeuralMath;

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
      const bend =
        (NeuralMath.hash(item.index, 3) - 0.5) * scale * (same ? 0.12 : 0.05);
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
      ctx.drawImage(
        glowSprite(colors[r.id]),
        p.x - size / 2,
        p.y - size / 2,
        size,
        size,
      );
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
      ctx.fillText(
        Math.round((neuralFrame.span * 1000 * k) / 4) + " ms",
        x,
        bottom + 14,
      );
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
    if (w < 1 || h < 120) return;
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
        if (event.key === "ArrowUp")
          camera.pitch = clamp(camera.pitch - 0.12, -1.2, 1.2);
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

  function drawHistory() {
    const { w, h, dpr } = resize(chart);
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cx.clearRect(0, 0, w, h);
    const history = state?.history || [];
    if (history.length < 2) return;
    const max = Math.max(12, ...history.map((r) => r.rate));
    const coords = history.map((p, i) => [
      (i / (history.length - 1)) * w,
      h - 4 - (p.rate / max) * (h - 8),
    ]);
    const gradient = cx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, "#c5fba430");
    gradient.addColorStop(1, "#c5fba400");
    cx.beginPath();
    cx.moveTo(0, h);
    for (const [x, y] of coords) cx.lineTo(x, y);
    cx.lineTo(w, h);
    cx.closePath();
    cx.fillStyle = gradient;
    cx.fill();
    cx.beginPath();
    coords.forEach(([x, y], i) => (i ? cx.lineTo(x, y) : cx.moveTo(x, y)));
    cx.strokeStyle = "#b7e6a4";
    cx.lineWidth = 1.2;
    cx.stroke();
  }
  function money(value) {
    return Number.isFinite(value) ? value.toFixed(6) + " SOL" : "—";
  }
  function render(s) {
    if (s.tick !== lastTick) {
      ingestFrame(s);
      lastTick = s.tick;
      frameStart = performance.now();
    }
    state = s;
    const live = !!s.feed?.mint,
      healthy = s.phase === "running" && Date.now() - Date.parse(s.updated_utc) < 15000;
    $("statusPill").textContent = healthy
      ? "Live model"
      : s.phase === "error"
        ? "Engine stopped"
        : "Awaiting state";
    $("statusPill").parentElement.style.color = healthy
      ? "var(--green)"
      : "var(--pink)";
    $("graphBadge").textContent = healthy
      ? "Live neural activity"
      : "Awaiting neural activity";
    $("updateTime").textContent = s.updated_utc
      ? "Updated " + new Date(s.updated_utc).toLocaleTimeString()
      : "Awaiting server";
    $("sourceTitle").textContent = live
      ? "Solana · " + s.feed.phase
      : "Simulation input stream";
    $("sourceDetail").textContent = live
      ? s.feed.error || "Finalized inputs from the connected protocol reader."
      : "Simulated sensory inputs; no finalized trade feed is connected.";
    $("tradeControls").classList.add("hidden");
    $("feedBadge").textContent = live
      ? "Solana · " + s.feed.phase
      : "Simulation inputs";
    $("fieldSource").textContent = live
      ? "Inputs: finalized Solana"
      : "Inputs: simulation";
    $("coverage").textContent = s.feed?.coverage || "Coverage unknown";
    if (s.error) $("inputStatus").textContent = s.error;
    else if (
      healthy &&
      ($("inputStatus").textContent.startsWith("Connection unavailable") ||
        $("inputStatus").textContent === "Waiting for the server.")
    )
      $("inputStatus").textContent =
        "Connected. Neural transitions are recorded on the server.";
    cognitiveCount("neurons", s.neurons);
    cognitiveCount("synapses", s.synapses);
    cognitiveCount("changed", s.metrics?.changed_synapses);
    $("simTime").textContent =
      fmt(paused && neuralFrame ? neuralFrame.simSeconds : s.sim_seconds, 1) + " s";
    $("rate").textContent = fmt(s.metrics?.rate_hz, 1);
    $("tick").textContent = "TICK " + fmt(s.tick);
    $("entropy").textContent = fmt(s.metrics?.activity_entropy, 3);
    $("meanWeight").textContent = fmt(s.metrics?.mean_weight, 5);
    for (const [r, i] of (topology?.regions || []).map((r, i) => [r, i])) {
      $("regionRate-" + r.id).textContent = fmt(s.metrics?.regional_rates?.[i], 1);
      $("regionBar-" + r.id).style.width =
        Math.min(100, ((s.metrics?.regional_rates?.[i] || 0) / 30) * 100) + "%";
    }
    const c = s.cognition,
      decision = c?.last_decision?.decision,
      treasury = c?.treasury;
    $("policyChoice").textContent = decision?.action || "Observing";
    $("scores").replaceChildren();
    for (const [name, value] of Object.entries(decision?.combined_scores || {})) {
      const score = document.createElement("div");
      score.className = "score" + (decision.action === name ? " active" : "");
      const strong = document.createElement("strong");
      strong.textContent = Number(value).toFixed(3);
      score.append(document.createTextNode(name), strong);
      $("scores").append(score);
    }
    $("feeTotal").textContent = Number.isFinite(treasury?.balance_sol)
      ? treasury.balance_sol.toFixed(6)
      : "—";
    const quoteUnit = s.token?.quote_unit || s.feed?.quote_unit || "SOL";
    const quoteDecimals = s.token?.quote_decimals ?? s.feed?.quote_decimals ?? 9;
    const accrual = s.feed?.recorded_accrual?.find((item) => item.unit === quoteUnit);
    $("observedFees").textContent = Number.isFinite(accrual?.amount)
      ? accrual.amount.toFixed(Math.min(9, quoteDecimals)) + " " + quoteUnit
      : "Unknown";
    $("fundsReceived").textContent = Number.isFinite(treasury?.verified_fee_receipts)
      ? money(treasury.verified_fee_receipts)
      : "Unknown";
    $("computeAmount").textContent =
      fmt(c?.experiments?.latest?.cpu_seconds, 3) + " s / last job";
    $("experimentsAmount").textContent = fmt(c?.experiments?.completed);
    $("reserveAmount").textContent = treasury?.policy?.enabled
      ? "Connected"
      : "Awaiting configuration";
    const energy = Number.isFinite(c?.resources?.energy) ? c.resources.energy * 100 : 0;
    $("allocationPercent").textContent = Math.round(energy) + "%";
    $("allocationRing").style.background =
      `conic-gradient(#c5fba4 0% ${energy}%,#203629 ${energy}% 100%)`;
    $("head").textContent = s.receipt_head || "Waiting for the latest receipt.";
    $("receiptCount").textContent = fmt(s.durable_receipts);
    $("spikeTotal").textContent = fmt(s.spikes_total);
    $("queueCount").textContent = fmt(s.queued_inputs);
    $("unresolved").textContent = fmt(
      s.feed?.unresolved_transactions ?? s.unresolved_transactions,
    );
    $("chainStatus").textContent = s.receipt_pending
      ? fmt(s.receipt_pending) + " recent ticks awaiting durable checkpoint."
      : "Latest ticks saved to a durable checkpoint.";
    drawHistory();
  }

  async function fetchJSON(path, options) {
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal });
      const value = await response.json();
      if (!response.ok) throw Error(value.error || "Request failed");
      return value;
    } finally {
      clearTimeout(deadline);
    }
  }

  let eventRows = [],
    receiptCache = new Map(),
    selectedInput = null,
    followingLatest = true,
    selectedReceiptHash = null;
  function selectedEvent() {
    return eventRows.find((e) => e.id === selectedInput) || null;
  }
  function renderTrace() {
    const e = selectedEvent(),
      receipt = e?.receipt ? receiptCache.get(e.receipt) : null;
    $("followLatest").textContent = followingLatest
      ? "Following latest"
      : "Follow latest ";
    $("followLatest").setAttribute("aria-pressed", String(followingLatest));
    if (!e) {
      $("traceInput").textContent = "Awaiting a signal";
      return;
    }
    $("traceInput").textContent =
      (e.source === "test" ? "Simulation " : "") +
      (e.side === "buy" ? "buy " : "sell ");
    $("traceInput").style.color = e.side === "buy" ? "var(--green)" : "var(--pink)";
    $("traceAmount").textContent =
      fmt(e.quote_amount, Math.min(9, e.quote_decimals ?? 4)) +
      " " +
      (e.quote_unit || "unknown quote");
    $("traceSource").textContent =
      (e.source === "test" ? "Simulation" : "Finalized Solana") +
      " / " +
      (Number.isInteger(e.block_time)
        ? new Date(e.block_time * 1000).toLocaleString()
        : new Date(e.created_utc).toLocaleTimeString());
    $("traceRegion").textContent =
      (e.side === "buy" ? "Buy" : "Sell") +
      " route in the original 256-neuron history" +
      (receipt
        ? " · recorded drive " +
          fmt(receipt.sensory_drive?.[e.side === "buy" ? 0 : 1], 4)
        : "");
    const ri = e.side === "buy" ? 0 : 1;
    $("traceSpikes").textContent = fmt(receipt?.metrics?.spikes);
    $("traceRate").textContent = fmt(
      ri >= 0 ? receipt?.metrics?.regional_rates?.[ri] : null,
      1,
    );
    $("traceWeights").textContent = receipt
      ? receipt.before_weights_sha256 !== receipt.after_weights_sha256
        ? "Synaptic weights changed in this window."
        : "Synaptic weight hash unchanged in this window."
      : "Awaiting its saved neural window.";
    $("traceReceipt").textContent = receipt
      ? "Receipt #" + receipt.seq + " / tick " + receipt.tick
      : e.receipt
        ? "Receipt #" + e.receipt + " · outside the recent cache."
        : "Queued · awaiting a durable receipt";
    selectedReceiptHash = receipt?.hash || null;
    $("traceHash").textContent = selectedReceiptHash || "";
    $("copyReceipt").disabled = !selectedReceiptHash;
    for (const row of $("events").querySelectorAll("button"))
      row.setAttribute("aria-pressed", String(row.dataset.input === selectedInput));
  }
  async function loadEvents() {
    const [rows, receipts] = await Promise.all([
      fetchJSON("/api/events"),
      fetchJSON("/api/receipts"),
    ]);
    for (const r of receipts) receiptCache.set(r.seq, r);
    // Keep a bounded inspection cache, including the selected input's receipt.
    if (receiptCache.size > 300) {
      const selectedSeq = selectedEvent()?.receipt;
      for (const key of [...receiptCache.keys()].sort((a, b) => a - b)) {
        if (receiptCache.size <= 240) break;
        if (key !== selectedSeq) receiptCache.delete(key);
      }
    }
    eventRows = rows;
    if (followingLatest || !selectedInput || !rows.some((e) => e.id === selectedInput))
      selectedInput = rows[0]?.id || null;
    // Preserve a focused row during polling; replacing it would strand keyboard users.
    const focusedId = document.activeElement?.dataset?.input;
    const priorScroll = $("events").scrollTop,
      priorFirst = $("events").querySelector("[data-input]")?.dataset.input;
    $("events").replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Waiting for the next experience.";
      $("events").append(empty);
    }
    for (const e of rows) {
      if (e.id === trackedInput && e.receipt)
        $("inputStatus").textContent =
          "Simulation " + e.side + " recorded in receipt #" + e.receipt + ".";
      const row = document.createElement("button");
      row.type = "button";
      row.className = "event " + (e.side === "buy" ? "buy-event" : "sell-event");
      row.dataset.input = e.id;
      row.setAttribute(
        "aria-label",
        (e.source === "test" ? "Simulation " : "") +
          e.side +
          " " +
          fmt(e.quote_amount, Math.min(9, e.quote_decimals ?? 4)) +
          " " +
          e.quote_unit +
          ", " +
          (e.receipt ? "receipt " + e.receipt : "pending receipt"),
      );
      row.setAttribute("aria-pressed", String(e.id === selectedInput));
      const main = document.createElement("div");
      main.className = "event-main";
      const title = document.createElement("strong");
      title.textContent =
        (e.source === "test" ? "Simulation " : "") +
        (e.side === "buy" ? "buy" : "sell");
      const detail = document.createElement("small");
      detail.textContent = e.signature ? e.signature : "Simulation · " + e.id.slice(-8);
      main.append(title, detail);
      const meta = document.createElement("div");
      meta.className = "event-meta";
      meta.textContent =
        fmt(e.quote_amount, Math.min(9, e.quote_decimals ?? 4)) + " " + e.quote_unit;
      const label = document.createElement("small");
      label.textContent = e.receipt ? "Receipt #" + e.receipt : "Saving receipt";
      meta.append(label);
      row.append(main, meta);
      row.addEventListener("click", () => {
        selectedInput = e.id;
        followingLatest = false;
        renderTrace();
        chooseRegion("sensory");
        const inspector = document.querySelector(".input-inspector");
        inspector.open = true;
        inspector.scrollIntoView({ behavior: reduced ? "auto" : "smooth" });
      });
      $("events").append(row);
      if (focusedId === e.id) row.focus({ preventScroll: true });
    }
    const added = priorFirst ? rows.findIndex((e) => e.id === priorFirst) : 0;
    if (priorScroll > 0) $("events").scrollTop = priorScroll + Math.max(0, added) * 76;
    renderTrace();
  }
  $("followLatest").addEventListener("click", () => {
    followingLatest = true;
    selectedInput = eventRows[0]?.id || null;
    renderTrace();
  });
  $("traceFocus").addEventListener("click", () => {
    const e = selectedEvent();
    if (e) chooseRegion("sensory");
    canvas.focus({ preventScroll: true });
  });
  async function copyValue(value, button) {
    if (!value) return;
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(value);
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = original), 1500);
    } catch {
      button.textContent = "Select to copy";
      setTimeout(() => (button.textContent = original), 1500);
    }
  }
  $("copyReceipt").addEventListener("click", () =>
    copyValue(selectedReceiptHash, $("copyReceipt")),
  );
  $("openInspection").addEventListener(
    "click",
    () => ($("modelInspection").open = true),
  );
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
    $("labTaskDescription").textContent = discoveryDescriptions[discoveryTask];
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
        ? `Spike memory ${spike >= symbol ? "+" : "−"}${Math.abs(spike - symbol).toFixed(3)} vs symbolic control. Live result; validation in docs.`
        : "Gathering outcomes. Compare learned choices with the controls.";
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
      $("commerceBalance").textContent = commerceMoney(
        evidence.usdc_balance?.micro_usdc,
      );
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
      $("commerceToday").textContent = commerceMoney(
        evidence.daily_committed_micro_usdc,
      );
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
      $("commerceRecords").textContent = JSON.stringify(
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
          ![
            "pending",
            "closed",
            "awaiting_delivery",
            "delivered",
            "evaluated",
          ].includes(job.job_state) ||
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
          now - balance.checked_at * 1000 >= 0 &&
          now - balance.checked_at * 1000 < 60000
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
    $("workHealth").dataset.state = evidence.financial_execution
      ? "running"
      : "guarded";
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
                "Jobs appear here when a configured provider receives a real request. Each payment, delivery and outcome has its own record.",
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
    $("workPolicy").textContent = "Unavailable";
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
  pollWork();
  setInterval(pollWork, 5000);

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      if (!topology?.nodes) {
        topology = await fetchJSON("/api/cognition/topology");
        structure();
      }
      const [legacy, cognitive] = await Promise.all([
        fetchJSON("/api/status"),
        fetchJSON("/api/cognition/status"),
      ]);
      legacyState = legacy;
      renderCognition(cognitive);
      render(neuralView(cognitive, legacy));
      if (eventCounter++ % 3 === 0) await loadEvents();
    } catch (error) {
      $("statusPill").textContent = "Connection unavailable";
      $("statusPill").parentElement.style.color = "var(--pink)";
      $("graphBadge").textContent = state
        ? "Last received state"
        : "Evidence unavailable";
      $("cogPhase").textContent = cognitionState
        ? "Last received evidence"
        : "Evidence unavailable";
      $("feedBadge").textContent = "Evidence unavailable";
      $("fieldSource").textContent = "Input source unavailable";
      if (!state) {
        $("fieldStatus").textContent = "Neural evidence unavailable";
        $("fieldStatus").classList.remove("hidden");
      }
      $("inputStatus").textContent = "Connection unavailable. Waiting for the server.";
    } finally {
      polling = false;
    }
  }
  async function stimulus(side) {
    $("testBuy").disabled = $("testSell").disabled = true;
    try {
      const result = await fetchJSON("/api/demo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ side, amount: Number($("testAmount").value) }),
      });
      trackedInput = result.id;
      $("inputStatus").textContent =
        "Test " + side + " queued. Waiting for its neural receipt.";
      await loadEvents();
    } catch (error) {
      $("inputStatus").textContent = error.message;
    } finally {
      $("testBuy").disabled = $("testSell").disabled = false;
    }
  }
  function selectTab(name, focus = false) {
    activeTab = name;
    for (const item of ["experience", "resources", "evidence"]) {
      const button = $("tab-" + item),
        active = item === name;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      $("panel-" + item).hidden = !active;
    }
    if (focus) $("tab-" + name).focus();
  }
  for (const name of ["experience", "resources", "evidence"]) {
    $("tab-" + name).addEventListener("click", () => selectTab(name));
    $("tab-" + name).addEventListener("keydown", (event) => {
      const names = ["experience", "resources", "evidence"],
        index = names.indexOf(name);
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        selectTab(names[(index + (event.key === "ArrowRight" ? 1 : 2)) % 3], true);
      }
    });
  }
  for (const link of document.querySelectorAll("[data-open]"))
    link.addEventListener("click", () => selectTab(link.dataset.open));
  function setMode(next) {
    mode = next;
    hover = -1;
    for (const id of ["zoomIn", "zoomOut", "resetView"])
      $(id).classList.toggle("hidden", next === "spikes");
    chooseRegion(selected);
    if (next === "spikes") pinned = -1;
    updateInspector();
    for (const [name, id] of [
      ["neural", "viewNeural"],
      ["topology", "viewTopology"],
      ["spikes", "viewSpikes"],
    ])
      $(id).setAttribute("aria-pressed", String(name === next));
    canvas.setAttribute(
      "aria-label",
      next === "spikes"
        ? "Actual recorded spike times by neuron in the latest neural window"
        : next === "topology"
          ? "Schematic two-dimensional view of actual model neurons and sampled connections"
          : "Actual Brian2 neurons and sampled connections in a schematic three-dimensional view; drag or use arrow keys to rotate",
    );
    $("frameLabel").hidden = false;
    $("frameLabel").textContent = paused
      ? "Snapshot held · tick " + fmt(neuralFrame?.tick)
      : playbackRate === 1
        ? "Recorded window · model speed"
        : "Recorded window · " + playbackRate + "× slower";
    $("inspectOpen").classList.toggle("hidden", next === "spikes");
    $("fieldLayers").classList.toggle("hidden", next === "spikes");
    $("windowCaption").classList.toggle("hidden", next === "spikes");
  }
  $("viewNeural").addEventListener("click", () => setMode("neural"));
  $("viewTopology").addEventListener("click", () => setMode("topology"));
  $("viewSpikes").addEventListener("click", () => setMode("spikes"));
  $("zoomIn").addEventListener("click", () => (zoom = Math.min(2.2, zoom + 0.15)));
  $("zoomOut").addEventListener("click", () => (zoom = Math.max(0.6, zoom - 0.15)));
  $("focus").addEventListener("click", () => {
    const focused = document.body.classList.toggle("focus-mode");
    $("focus").setAttribute("aria-pressed", String(focused));
    $("focus").setAttribute(
      "aria-label",
      focused ? "Exit focus view" : "Focus neural field",
    );
    if (focused)
      $("neural-field").scrollIntoView({ behavior: reduced ? "auto" : "smooth" });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("focus-mode"))
      $("focus").click();
  });
  $("motion").addEventListener("click", () => {
    paused = !paused;
    if (!paused && state) {
      ingestFrame(state, true);
      frameStart = performance.now();
    }
    $("motion").setAttribute("aria-pressed", String(paused));
    $("motion").setAttribute(
      "aria-label",
      paused ? "Resume visual motion" : "Pause visual motion",
    );
    $("motion").innerHTML = paused
      ? '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M5 3l7 5-7 5z"/></svg>'
      : '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M6 3v10M10 3v10" stroke-width="1.5"/></svg>';
    setMode(mode);
  });
  bindOrbit();
  $("testBuy").setAttribute("aria-label", "Send test buy");
  $("testSell").setAttribute("aria-label", "Send test sell");
  $("testBuy").addEventListener("click", () => stimulus("buy"));
  $("testSell").addEventListener("click", () => stimulus("sell"));
  $("verify").addEventListener("click", async () => {
    $("verify").disabled = true;
    $("verifyResult").textContent =
      "Checking saved receipts, inputs and spike windows…";
    try {
      const result = await fetchJSON("/api/verify");
      $("verifyResult").textContent = result.valid
        ? "Verified " +
          fmt(result.checked) +
          " durable receipts as of " +
          new Date(result.verified_utc).toLocaleTimeString() +
          ". Input, spike and receipt integrity confirmed."
        : "Verification failed at receipt #" + result.first_failure;
    } catch (error) {
      $("verifyResult").textContent = error.message;
    } finally {
      $("verify").disabled = false;
    }
  });
  if (reduced) {
    $("motion").innerHTML =
      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M5 3l7 5-7 5z"/></svg>';
    $("motion").setAttribute("aria-pressed", "true");
    $("motion").setAttribute("aria-label", "Resume visual motion");
    setMode("neural");
  }
  document.querySelectorAll('a[href^="#"]').forEach((a) =>
    a.addEventListener("click", (event) => {
      if (a.dataset.workView) return;
      const target = document.getElementById(a.hash.slice(1));
      if (!target) return;
      event.preventDefault();
      if (target.id === "overview")
        $("pageScroll").scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
      else
        target.scrollIntoView({
          behavior: reduced ? "auto" : "smooth",
          block: "start",
        });
      if (a.classList.contains("skip")) canvas.focus({ preventScroll: true });
    }),
  );
  setInterval(poll, 1400);
  poll();
  requestAnimationFrame(draw);
}

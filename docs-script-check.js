"use strict";
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
        text.push(next.textContent);
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
  initNuriaDocs();
} else {
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
    lastReceived = 0,
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
    pointer = { x: 0, y: 0 },
    activeTab = "experience";
  const centers = [
    [-0.75, 0.27, -0.1],
    [-0.75, -0.3, 0.02],
    [-0.05, 0.28, -0.08],
    [0.05, -0.29, 0.1],
    [0.79, 0.01, 0.08],
    [0.1, 0.0, 0.45],
  ];
  const colors = {
    buy: "#c7f4b0",
    sensory: "#bdd6a0",
    workspace: "#cbbf99",
    sell: "#dfa6b2",
    association: "#a9bebc",
    memory: "#b8afc4",
    policy: "#c5ceb7",
    inhibition: "#89b2a3",
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
      dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.round(r.width * dpr),
      h = Math.round(r.height * dpr);
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
    }
    return { w: r.width, h: r.height, dpr };
  }
  function chooseRegion(id) {
    selected = id;
    hover = -1;
    for (const button of $("legend").querySelectorAll("button"))
      button.setAttribute("aria-pressed", String(button.dataset.region === id));
    $("neuronTip").textContent =
      mode === "spikes"
        ? id === "all"
          ? "Recorded spike times · latest neural window."
          : shortNames[id] + " · recorded spike times."
        : id === "all"
          ? "Drag to orbit.\nArrow keys rotate."
          : shortNames[id] + " highlighted. Hover to inspect.";
  }
  function structure() {
    if (!topology?.nodes) return;
    basePoints = topology.nodes.map((n) => {
      const ri = topology.regions.findIndex((r) => r.id === n.region),
        r = topology.regions[ri],
        j = n.id - r.start,
        count = r.end - r.start,
        z = 1 - (2 * (j + 0.5)) / count,
        phi = j * 2.399963,
        ring = Math.sqrt(1 - z * z),
        c = centers[ri],
        radius = ri === 4 ? 0.23 : ri === 5 ? 0.4 : 0.48;
      return {
        x: c[0] + Math.cos(phi) * ring * radius,
        y: c[1] + z * radius * 0.8,
        z: c[2] + Math.sin(phi) * ring * radius * 0.65,
        region: n.region,
        color: colors[n.region],
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
  }
  // Nuria observatory renderer. Geometry and travelling trails are illustrative;
  // node state, connectivity, weights and spike times come from Brian2.
  let camera = { yaw: 0, pitch: 0 },
    cameraEase = { yaw: 0, pitch: 0 },
    zoomEase = 1,
    dragging = null;
  let drawTime = 0,
    frameSpikes = [],
    spikesByNeuron = [],
    displayVoltage = [],
    spriteCache = new Map();
  let manualUntil = 0,
    latestEventId = null,
    latestReceiptCue = null;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function ingestFrame(s) {
    frameSpikes = (s.spikes || []).map(([time, id]) => ({ time, id }));
    spikesByNeuron = Array.from({ length: s.neurons || 256 }, () => []);
    for (const spike of frameSpikes)
      if (spikesByNeuron[spike.id]) spikesByNeuron[spike.id].push(spike.time);
    if (!displayVoltage.length) displayVoltage = (s.voltages || []).slice();
  }
  function project(w, h, time) {
    const idle = !dragging && Date.now() > manualUntil;
    const targetYaw =
      camera.yaw + 0.25 + (idle && !reduced ? Math.sin(time / 19000) * 0.12 : 0);
    const targetPitch =
      camera.pitch - 0.13 + (idle && !reduced ? Math.cos(time / 23000) * 0.06 : 0);
    cameraEase.yaw += (targetYaw - cameraEase.yaw) * 0.07;
    cameraEase.pitch += (targetPitch - cameraEase.pitch) * 0.07;
    zoomEase += (zoom - zoomEase) * 0.085;
    const scale = Math.min(w * 0.355, h * 0.46) * zoomEase;
    const cy = Math.cos(mode === "topology" ? 0 : cameraEase.yaw),
      sy = Math.sin(mode === "topology" ? 0 : cameraEase.yaw);
    const cp = Math.cos(mode === "topology" ? 0 : cameraEase.pitch),
      sp = Math.sin(mode === "topology" ? 0 : cameraEase.pitch);
    points = basePoints.map((p) => {
      let x = p.x * cy + p.z * sy,
        z = -p.x * sy + p.z * cy,
        y = p.y * cp - z * sp;
      z = p.y * sp + z * cp;
      const depth = mode === "topology" ? 1 : 2.9 / (2.9 - z * 0.72);
      return {
        x: w * 0.5 + x * scale * depth,
        y: h * 0.49 - y * scale * depth,
        z,
        depth,
        region: p.region,
        color: p.color,
      };
    });
    return scale;
  }
  function glowSprite(color) {
    if (spriteCache.has(color)) return spriteCache.get(color);
    const sprite = document.createElement("canvas");
    sprite.width = sprite.height = 80;
    const c = sprite.getContext("2d"),
      g = c.createRadialGradient(40, 40, 0, 40, 40, 40);
    g.addColorStop(0, color + "ff");
    g.addColorStop(0.09, color + "df");
    g.addColorStop(0.25, color + "62");
    g.addColorStop(0.6, color + "12");
    g.addColorStop(1, color + "00");
    c.fillStyle = g;
    c.fillRect(0, 0, 80, 80);
    spriteCache.set(color, sprite);
    return sprite;
  }
  function edgeCurve(item) {
    const a = points[item.edge[0]],
      b = points[item.edge[1]],
      bend = mode === "topology" ? 0 : ((item.index % 7) - 3) * 2.2;
    return {
      a,
      b,
      c: { x: (a.x + b.x) / 2 + bend, y: (a.y + b.y) / 2 - bend - (a.z + b.z) * 4 },
    };
  }
  function curvePoint(curve, t) {
    const q = 1 - t;
    return {
      x: q * q * curve.a.x + 2 * q * t * curve.c.x + t * t * curve.b.x,
      y: q * q * curve.a.y + 2 * q * t * curve.c.y + t * t * curve.b.y,
    };
  }
  function traceCurve(curve) {
    ctx.moveTo(curve.a.x, curve.a.y);
    ctx.quadraticCurveTo(curve.c.x, curve.c.y, curve.b.x, curve.b.y);
  }
  function spikeEnergy(id, cursor, fresh) {
    if (!fresh || paused) return 0;
    let energy = 0;
    for (const time of spikesByNeuron[id] || []) {
      const age = cursor - time;
      if (age >= 0 && age < 0.07) energy = Math.max(energy, Math.exp(-age / 0.027));
    }
    return energy;
  }
  function regionalAtmosphere(w, h) {
    if (mode === "topology") return;
    ctx.globalCompositeOperation = "screen";
    for (const [r, i] of topology.regions.map((r, i) => [r, i])) {
      if (selected !== "all" && selected !== r.id) continue;
      let x = 0,
        y = 0;
      for (let id = r.start; id < r.end; id++) {
        x += points[id].x;
        y += points[id].y;
      }
      x /= r.end - r.start;
      y /= r.end - r.start;
      const radius = Math.min(w * 0.28, h * 0.27),
        rate = state.metrics?.regional_rates?.[i] || 0;
      ctx.globalAlpha = 0.025 + Math.min(0.045, rate / 300);
      ctx.drawImage(
        glowSprite(colors[r.id]),
        x - radius,
        y - radius,
        radius * 2,
        radius * 2,
      );
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
  }
  function fieldGuides(w, h, scale) {
    const x = w * 0.5,
      y = h * 0.49;
    ctx.strokeStyle = "#7e998229";
    ctx.lineWidth = 0.65;
    for (const side of [-1, 1]) {
      const px = x + side * Math.min(scale * 1.38, w * 0.44);
      ctx.beginPath();
      ctx.moveTo(px, y - 6);
      ctx.lineTo(px, y + 6);
      ctx.moveTo(px - 3, y);
      ctx.lineTo(px + 3, y);
      ctx.stroke();
    }
  }
  function drawSpikes(w, h, cursor = 0) {
    const span = (state?.window_ms || 100) / 1000;
    const left = 40,
      right = w - 25,
      top = 122,
      bottom = h - 125;
    const chosen = topology.regions.find((region) => region.id === selected),
      first = chosen?.start || 0,
      last = chosen?.end || 1024,
      range = last - first;
    ctx.font = "12px ui-monospace,monospace";
    ctx.lineWidth = 0.6;
    for (const r of topology.regions) {
      if (chosen && chosen.id !== r.id) continue;
      const y = top + (bottom - top) * ((r.start - first) / range),
        end = top + (bottom - top) * ((r.end - first) / range);
      ctx.globalAlpha = 0.025;
      ctx.fillStyle = colors[r.id];
      ctx.fillRect(left, y, right - left, end - y);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = "#96a68d14";
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
      ctx.stroke();
    }
    for (let k = 0; k <= 4; k++) {
      const x = left + ((right - left) * k) / 4;
      ctx.strokeStyle = "#a2b39d15";
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.fillStyle = "#738477";
      ctx.textAlign = "center";
      ctx.fillText(
        Math.round((k * (state?.window_ms || 100)) / 4) + " ms",
        x,
        bottom + 18,
      );
    }
    for (const spike of frameSpikes) {
      const n = topology.nodes[spike.id];
      if (selected !== "all" && n.region !== selected) continue;
      const x = left + ((right - left) * spike.time) / span,
        y = top + ((bottom - top) * (spike.id - first + 0.5)) / range;
      const seen = paused || cursor >= spike.time;
      ctx.globalAlpha = seen ? 0.85 : 0.17;
      ctx.fillStyle = colors[n.region];
      ctx.fillRect(x - 0.6, y - 1.7, 1.2, 3.4);
    }
    ctx.globalAlpha = 1;
    if (!paused && cursor < span) {
      const x = left + ((right - left) * cursor) / span,
        g = ctx.createLinearGradient(x - 20, 0, x, 0);
      g.addColorStop(0, "#c5fba400");
      g.addColorStop(1, "#c5fba414");
      ctx.fillStyle = g;
      ctx.fillRect(x - 20, top, 20, bottom - top);
      ctx.strokeStyle = "#c5fba483";
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
    }
    ctx.fillStyle = "#74897a";
    ctx.textAlign = "left";
    ctx.fillText(
      range + " NEURONS" + (chosen ? " / " + shortNames[chosen.id].toUpperCase() : ""),
      left,
      top - 13,
    );
    ctx.fillText("ACTUAL SPIKE TIMES / LATEST WINDOW", left, bottom + 35);
  }
  function drawRegionLabels(w, h) {
    if (selected === "all" && w < 750) return;
    ctx.font = "12px ui-monospace,monospace";
    for (const [r, i] of topology.regions.map((r, i) => [r, i])) {
      if (selected !== "all" && selected !== r.id) continue;
      let x = 0,
        y = 0;
      for (let id = r.start; id < r.end; id++) {
        x += points[id].x;
        y += points[id].y;
      }
      x /= r.end - r.start;
      y /= r.end - r.start;
      const side = x > w / 2 ? 1 : -1,
        tx = clamp(x + side * 46, 90, w - 110),
        ty = clamp(y - 35, 100, h - 90);
      ctx.strokeStyle = colors[r.id] + "55";
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(tx, ty);
      ctx.lineTo(tx + side * 16, ty);
      ctx.stroke();
      ctx.textAlign = side > 0 ? "left" : "right";
      const label = shortNames[r.id].toUpperCase(),
        reading = fmt(state.metrics?.regional_rates?.[i], 1) + " Hz";
      ctx.strokeStyle = "#080f0dee";
      ctx.lineWidth = 4;
      ctx.strokeText(label, tx + side * 20, ty - 5);
      ctx.strokeText(reading, tx + side * 20, ty + 12);
      ctx.fillStyle = colors[r.id];
      ctx.fillText(label, tx + side * 20, ty - 5);
      ctx.fillStyle = "#b4c5b0";
      ctx.fillText(reading, tx + side * 20, ty + 12);
    }
  }
  function draw(now) {
    requestAnimationFrame(draw);
    if (document.hidden || now - lastPaint < 32) return;
    const elapsed = Math.min(50, now - lastPaint);
    lastPaint = now;
    if (!paused) motionClock += elapsed;
    const { w, h, dpr } = resize(canvas);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!topology || !state) return;
    const fresh =
      state.phase === "running" && Date.now() - Date.parse(state.updated_utc) < 15000;
    const status = $("fieldStatus");
    status.classList.toggle("hidden", fresh);
    if (!fresh)
      status.textContent =
        state.phase === "error"
          ? "Engine stopped — evidence retained"
          : "Waiting for fresh neural state";
    const span = (state?.window_ms || 100) / 1000;
    const cursor = paused
      ? 0
      : Math.min(span * 1.4, ((now - frameStart) / 2200) * span);
    if ($("replayFill"))
      $("replayFill").style.width =
        (paused ? 0 : Math.min(100, (cursor / span) * 100)) + "%";
    if (mode === "spikes") {
      drawSpikes(w, h, fresh ? cursor : span);
      return;
    }
    const scale = project(w, h, motionClock);
    regionalAtmosphere(w, h);
    fieldGuides(w, h, scale);
    const related = (n) => selected === "all" || n.region === selected;
    const activity = points.map((p, id) => spikeEnergy(id, cursor, fresh));
    ctx.lineCap = "round";
    for (const item of weightedEdges) {
      const e = item.edge,
        a = points[e[0]],
        b = points[e[1]],
        relevant = related(a) || related(b),
        inspect = hover >= 0 && (e[0] === hover || e[1] === hover);
      if (!inspect && mode !== "topology" && item.index % 2 !== 0) continue;
      const weight =
        item.weightIndex >= 0 ? (state.weights?.[item.weightIndex] ?? 0.05) : 0.08;
      const alpha = relevant
        ? Math.min(
            0.26,
            0.05 + weight * 1.1 + activity[e[0]] * 0.1 + (a.z + b.z) * 0.025,
          )
        : 0.007;
      ctx.lineWidth = inspect ? 1.1 : e[2] === "inh" ? 0.35 : 0.35 + weight * 0.8;
      ctx.strokeStyle = inspect
        ? "#deffd1ab"
        : e[2] === "inh"
          ? "rgba(125,163,153," + (relevant ? 0.06 : 0.006) + ")"
          : "rgba(160,188,168," + alpha + ")";
      ctx.beginPath();
      traceCurve(edgeCurve(item));
      ctx.stroke();
    }
    // A sampled travelling trace follows a real source spike along a real edge.
    // Its travel duration is a visual convention, not a measured axonal delay.
    if (fresh && !paused) {
      ctx.globalCompositeOperation = "lighter";
      let emitted = 0;
      for (const item of weightedEdges) {
        if (item.index % 7 !== 0 || emitted > 110) continue;
        const e = item.edge;
        if (!related(points[e[0]]) && !related(points[e[1]])) continue;
        let time = null;
        for (const t of spikesByNeuron[e[0]] || [])
          if (cursor - t >= 0 && cursor - t < 0.045) time = t;
        if (time === null) continue;
        const travel = (cursor - time) / 0.045,
          curve = edgeCurve(item),
          color = points[e[0]].color;
        ctx.strokeStyle = color + "8f";
        ctx.lineWidth = 0.8;
        ctx.beginPath();
        for (let k = 0; k <= 7; k++) {
          const p = curvePoint(curve, clamp(travel - 0.16 + (k / 7) * 0.16, 0, 1));
          if (k === 0) ctx.moveTo(p.x, p.y);
          else ctx.lineTo(p.x, p.y);
        }
        ctx.stroke();
        const p = curvePoint(curve, travel);
        ctx.globalAlpha = 0.6;
        ctx.drawImage(glowSprite(color), p.x - 6, p.y - 6, 12, 12);
        ctx.globalAlpha = 1;
        emitted++;
      }
      ctx.globalCompositeOperation = "source-over";
    }
    const order = topology.nodes
      .map((n) => n.id)
      .sort((a, b) => points[a].z - points[b].z);
    for (const id of order) {
      const p = points[id],
        active = activity[id],
        dim = !related(p),
        actualV = Math.max(0, state.voltages?.[id] ?? 0);
      displayVoltage[id] =
        (displayVoltage[id] ?? actualV) +
        (actualV - (displayVoltage[id] ?? actualV)) * 0.09;
      const radius =
        (0.72 + Math.min(1.2, displayVoltage[id]) * 0.48 + active * 0.85) * p.depth;
      if (!dim) {
        const intensity = paused
          ? Math.min(0.17, (spikesByNeuron[id]?.length || 0) * 0.035)
          : active * 0.9;
        if (intensity > 0.018) {
          const glow = 5 + active * 11;
          ctx.globalCompositeOperation = "screen";
          ctx.globalAlpha = intensity;
          ctx.drawImage(
            glowSprite(p.color),
            p.x - glow,
            p.y - glow,
            glow * 2,
            glow * 2,
          );
          ctx.globalCompositeOperation = "source-over";
        }
      }
      ctx.globalAlpha = dim
        ? 0.1
        : clamp(0.4 + displayVoltage[id] * 0.3 + active * 0.6 + p.z * 0.24, 0.16, 1);
      ctx.fillStyle = active > 0.08 || selected !== "all" ? p.color : "#b9c9bb";
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fill();
      if (active > 0.2 && !dim) {
        ctx.globalAlpha = active * 0.8;
        ctx.fillStyle = "#f6fff0";
        ctx.beginPath();
        ctx.arc(p.x, p.y, 0.8 * p.depth, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      if (id === hover) {
        ctx.strokeStyle = "#eef6df";
        ctx.lineWidth = 0.8;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 7, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
    drawRegionLabels(w, h);
    if (!fresh) {
      ctx.fillStyle = "#0b0d0e99";
      ctx.fillRect(0, 0, w, h);
    }
  }
  function bindOrbit() {
    canvas.addEventListener("pointerdown", (event) => {
      if (mode === "spikes") return;
      dragging = {
        x: event.clientX,
        y: event.clientY,
        yaw: camera.yaw,
        pitch: camera.pitch,
        moved: false,
      };
      canvas.setPointerCapture(event.pointerId);
      canvas.classList.add("dragging");
      manualUntil = Date.now() + 8000;
    });
    canvas.addEventListener("pointermove", (event) => {
      if (mode === "spikes") return;
      const rect = canvas.getBoundingClientRect(),
        x = event.clientX - rect.left,
        y = event.clientY - rect.top;
      if (dragging) {
        const dx = event.clientX - dragging.x,
          dy = event.clientY - dragging.y;
        dragging.moved = Math.abs(dx) + Math.abs(dy) > 3;
        if (dragging.moved) {
          camera.yaw = dragging.yaw + dx * 0.009;
          camera.pitch = clamp(dragging.pitch + dy * 0.007, -0.65, 0.65);
          hover = -1;
          $("inspection").style.display = "none";
        }
        manualUntil = Date.now() + 8000;
        return;
      }
      hover = points.findIndex((p) => Math.hypot(p.x - x, p.y - y) < 9);
      const tip = $("inspection");
      if (hover >= 0 && state) {
        const n = topology.nodes[hover];
        tip.replaceChildren();
        const title = document.createElement("strong");
        title.textContent =
          shortNames[n.region] + " / neuron " + String(hover).padStart(3, "0");
        const reading = document.createElement("div");
        reading.textContent =
          "Membrane " + Number(state.voltages?.[hover] ?? 0).toFixed(3);
        const spikes = document.createElement("div");
        spikes.textContent = (state.counts?.[hover] ?? 0) + " spikes / latest window";
        tip.append(title, reading, spikes);
        tip.style.left = clamp(x + 12, 8, rect.width - 213) + "px";
        tip.style.top = clamp(y - 30, 65, rect.height - 100) + "px";
        tip.style.display = "block";
      } else tip.style.display = "none";
    });
    const end = () => {
      dragging = null;
      canvas.classList.remove("dragging");
    };
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
    canvas.addEventListener("pointerleave", () => {
      hover = -1;
      $("inspection").style.display = "none";
    });
    canvas.addEventListener("keydown", (event) => {
      if (mode === "spikes") return;
      if (
        ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home"].includes(event.key)
      ) {
        event.preventDefault();
        manualUntil = Date.now() + 8000;
        if (event.key === "ArrowLeft") camera.yaw -= 0.18;
        if (event.key === "ArrowRight") camera.yaw += 0.18;
        if (event.key === "ArrowUp")
          camera.pitch = clamp(camera.pitch - 0.12, -0.65, 0.65);
        if (event.key === "ArrowDown")
          camera.pitch = clamp(camera.pitch + 0.12, -0.65, 0.65);
        if (event.key === "Home") resetCamera();
      }
    });
    $("resetView").addEventListener("click", resetCamera);
  }
  function resetCamera() {
    camera = { yaw: 0, pitch: 0 };
    zoom = 1;
    manualUntil = 0;
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
    lastReceived = Date.now();
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
      ? s.feed.error || "Mint " + s.feed.mint
      : "Test signals; no mint connected. Configure a mint to observe finalized Pump/PumpSwap trades.";
    $("tradeControls").classList.add("hidden");
    $("feedBadge").textContent = live
      ? "Solana · " + s.feed.phase
      : "Simulation inputs";
    $("coverage").textContent = s.feed?.coverage || "Coverage unknown";
    if (s.error) $("inputStatus").textContent = s.error;
    else if (
      healthy &&
      ($("inputStatus").textContent.startsWith("Connection unavailable") ||
        $("inputStatus").textContent === "Waiting for the server.")
    )
      $("inputStatus").textContent =
        "Connected. Neural transitions are recorded on the server.";
    $("neurons").textContent = fmt(s.neurons);
    $("synapses").textContent = fmt(s.synapses);
    $("changed").textContent = fmt(s.metrics?.changed_synapses);
    $("simTime").textContent = fmt(s.sim_seconds, 1) + " s";
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
    $("observedFees").textContent = Number.isFinite(
      s.treasury?.live_observed_accrual?.SOL,
    )
      ? money(s.treasury.live_observed_accrual.SOL)
      : "Unknown";
    $("creatorWallet").textContent = treasury?.wallet || "Not connected";
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
    $("unresolved").textContent = fmt(s.unresolved_transactions);
    $("chainStatus").textContent = s.receipt_pending
      ? fmt(s.receipt_pending) + " recent ticks awaiting durable checkpoint."
      : "Latest ticks saved to a durable checkpoint.";
    $("tokenLink").classList.toggle("connected", live);
    $("mintAddress").textContent = s.feed?.mint || "";
    drawHistory();
  }

  async function fetchJSON(path, options) {
    const response = await fetch(path, options);
    const value = await response.json();
    if (!response.ok) throw Error(value.error || "Request failed");
    return value;
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
      (e.source === "test" ? "Test " : "") + (e.side === "buy" ? "buy " : "sell ");
    $("traceInput").style.color = e.side === "buy" ? "var(--green)" : "var(--pink)";
    $("traceAmount").textContent =
      fmt(e.quote_amount, 4) + " " + (e.quote_unit || "unknown quote");
    $("traceSource").textContent =
      (e.source === "test" ? "Simulation" : e.source) +
      " / " +
      new Date(e.created_utc).toLocaleTimeString();
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
          "Test " + e.side + " recorded in receipt #" + e.receipt + ".";
      const row = document.createElement("button");
      row.type = "button";
      row.className = "event " + (e.side === "buy" ? "buy-event" : "sell-event");
      row.dataset.input = e.id;
      row.setAttribute(
        "aria-label",
        (e.source === "test" ? "Test " : "") +
          e.side +
          " " +
          fmt(e.quote_amount, 4) +
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
        (e.source === "test" ? "Test " : "") + (e.side === "buy" ? "buy" : "sell");
      const detail = document.createElement("small");
      detail.textContent = e.signature ? e.signature : "Simulation · " + e.id.slice(-8);
      main.append(title, detail);
      const meta = document.createElement("div");
      meta.className = "event-meta";
      meta.textContent = fmt(e.quote_amount, 4) + " " + e.quote_unit;
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
  $("copyMint").addEventListener("click", () =>
    copyValue(state?.feed?.mint, $("copyMint")),
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
      source = Object.keys(sources).find((k) => k !== "test") || "test",
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
        ? "Test inputs"
        : source
      : "Awaiting data";
    $("cogLearningSource").textContent =
      source === "test"
        ? "Test inputs · forecast error (green) / learned repeat baseline (gray)."
        : `${source} · observed next-input outcomes; prediction quality can rise or fall.`;
    drawLearning(learning?.history || []);
    $("cogForecastJSON").textContent = JSON.stringify(
      {
        method: c.learning?.method,
        learning_started_utc: c.learning?.genesis_utc,
        source,
        prediction: learning?.prediction,
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
      $("graphBadge").textContent = "Last received state";
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
    $("inspection").style.display = "none";
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
    $("frameLabel").hidden = !paused && next !== "spikes";
    $("frameLabel").textContent = paused
      ? "Motion paused · live data continues"
      : next === "spikes"
        ? "Recorded spike raster · latest window"
        : "";
  }
  $("viewNeural").addEventListener("click", () => setMode("neural"));
  $("viewTopology").addEventListener("click", () => setMode("topology"));
  $("viewSpikes").addEventListener("click", () => setMode("spikes"));
  $("zoomIn").addEventListener("click", () => (zoom = Math.min(1.5, zoom + 0.1)));
  $("zoomOut").addEventListener("click", () => (zoom = Math.max(0.65, zoom - 0.1)));
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

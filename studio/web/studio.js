"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const state = {
    data: null,
    project: null,
    candidate: null,
    frameUrl: null,
    fetching: false,
    signatures: {},
    frontier: "all",
    galleryLimit: 6,
  };
  const finished = new Set([
    "complete",
    "completed",
    "published",
    "accepted",
    "retained",
  ]);
  const text = (value, fallback = "") => (typeof value === "string" ? value : fallback);
  const array = (value) => (Array.isArray(value) ? value : []);
  const identifier = (value) =>
    typeof value === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(value) ? value : null;
  const label = (value) => text(value, "waiting").replace(/[_-]+/g, " ");
  const count = (value) => (Number.isSafeInteger(value) && value >= 0 ? value : 0);
  const element = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  };
  const date = (value, compact = false) => {
    if (!value) return "Time not recorded";
    const parsed = new Date(value);
    if (!Number.isFinite(parsed.getTime())) return "Time not recorded";
    return parsed.toLocaleString(
      undefined,
      compact
        ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
        : {
            month: "short",
            day: "numeric",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          },
    );
  };
  const changed = (key, value) => {
    const signature = JSON.stringify(value);
    if (state.signatures[key] === signature) return false;
    state.signatures[key] = signature;
    return true;
  };
  function artifactUrl(value) {
    if (
      typeof value !== "string" ||
      !/^\/creations\/[A-Za-z0-9_-]+\/(?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+$/.test(
        value,
      )
    )
      return null;
    if (value.split("/").some((part) => part === "." || part === "..")) return null;
    const url = new URL(value, location.origin);
    return url.origin === location.origin ? url.pathname : null;
  }
  function setFrame(url, title, force = false) {
    const iframe = byId("artifact");
    iframe.title = title;
    if (!force && url === state.frameUrl) return;
    state.frameUrl = url;
    if (url) {
      iframe.src = url;
      iframe.hidden = false;
      byId("experience-placeholder").hidden = true;
      byId("restart").hidden = false;
      byId("open-creation").href = url;
      byId("open-creation").hidden = false;
    } else {
      iframe.removeAttribute("src");
      iframe.hidden = true;
      byId("experience-placeholder").hidden = false;
      byId("restart").hidden = true;
      byId("open-creation").hidden = true;
      byId("open-creation").removeAttribute("href");
    }
  }
  function selectProject(id, scroll = false) {
    if (!array(state.data?.projects).some((project) => project.id === id)) return;
    if (id !== state.project) state.candidate = null;
    state.project = id;
    const hash = identifier(id);
    if (hash && location.hash !== `#${hash}`)
      history.replaceState(null, "", `#${hash}`);
    renderProjects();
    renderProject();
    if (scroll)
      byId("project-detail").scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "start",
      });
  }
  function renderProjects() {
    const all = array(state.data?.projects).slice().reverse();
    const matching = all.filter(
      (project) =>
        state.frontier === "all" ||
        project.frontier?.id === state.frontier ||
        (state.frontier === "archive" && !project.frontier),
    );
    const projects = matching.slice(0, state.galleryLimit);
    const list = byId("project-list");
    const directions = [
      { id: "all", name: "All work" },
      ...array(state.data?.portfolio),
      { id: "archive", name: "First experiments" },
    ];
    if (changed("frontiers", [directions, state.frontier])) {
      const focused = document.activeElement?.dataset?.frontier;
      byId("frontier-list").replaceChildren(
        ...directions.map((direction) => {
          const button = element("button", "frontier-button", direction.name);
          button.type = "button";
          button.dataset.frontier = direction.id;
          button.setAttribute("aria-pressed", String(state.frontier === direction.id));
          button.addEventListener("click", () => {
            state.frontier = direction.id;
            state.galleryLimit = 6;
            renderProjects();
          });
          return button;
        }),
      );
      if (focused)
        [...byId("frontier-list").children]
          .find((button) => button.dataset.frontier === focused)
          ?.focus({ preventScroll: true });
    }
    byId("gallery-summary").textContent =
      `${matching.length} projects · newest first · ${all.filter((p) => finished.has(p.status) && p.contract_version === 2).length} browser-tested selections across ${array(state.data?.portfolio).filter((p) => p.completed > 0).length} directions`;
    byId("more-projects").hidden = projects.length >= matching.length;
    const summary = projects.map((project) => ({
      id: project.id,
      title: project.title,
      brief: project.brief,
      status: project.status,
      created_at: project.created_at,
      selected: project.selected,
      candidates: project.candidates?.map((c) => [c.id, c.title, c.preview_url]),
    }));
    if (!changed("project-list", [summary, state.project])) return;
    const focused = document.activeElement?.dataset?.project;
    list.replaceChildren();
    projects.forEach((project) => {
      const winner = array(project.candidates).find((c) => c.id === project.selected);
      const concept = array(project.concepts).find((c) => c.id === winner?.id);
      const button = element("button", "project-card");
      button.type = "button";
      button.dataset.project = project.id;
      button.setAttribute("aria-pressed", String(project.id === state.project));
      const top = element("div", "project-card-top");
      top.append(
        element(
          "span",
          "",
          project.frontier?.name || `PROJECT ${project.id.replace("invention-", "")}`,
        ),
        element(
          "span",
          `state-label${finished.has(project.status) ? " finished" : ""}`,
          label(project.status),
        ),
      );
      button.append(top);
      const preview = artifactUrl(winner?.preview_url);
      if (preview) {
        const img = element("img", "project-preview");
        img.src = preview;
        img.alt = "";
        img.loading = "lazy";
        img.width = 1280;
        img.height = 800;
        button.append(img);
      }
      button.append(
        element("h3", "", text(winner?.title, text(project.title, "Untitled project"))),
        element(
          "p",
          "",
          text(concept?.payoff, text(project.brief).split(/(?<=[.!?])\s/)[0]),
        ),
        element("span", "project-card-arrow", "↗"),
      );
      button.addEventListener("click", () => selectProject(project.id, true));
      list.append(button);
      if (focused === project.id) button.focus({ preventScroll: true });
    });
    byId("empty-projects").hidden = projects.length > 0;
    if (!projects.length) {
      byId("empty-title").textContent = "Room for a first question.";
      byId("empty-copy").textContent =
        "No projects have been published yet. This notebook will fill as Nuria works.";
    }
  }
  function renderCandidate(project) {
    const candidates = array(project.candidates);
    const selectedId =
      typeof project.selected === "object" ? project.selected?.id : project.selected;
    if (!candidates.some((candidate) => candidate.id === state.candidate)) {
      state.candidate =
        candidates.find((candidate) => candidate.id === selectedId)?.id ??
        candidates.find((candidate) => artifactUrl(candidate.artifact_url))?.id ??
        candidates[0]?.id ??
        null;
    }
    if (
      changed("variants", [
        project.id,
        candidates.map((candidate) => [
          candidate.id,
          candidate.title,
          candidate.status,
        ]),
        state.candidate,
      ])
    ) {
      const variants = byId("variants");
      variants.replaceChildren();
      candidates.forEach((candidate, index) => {
        const button = element("button", "variant-button");
        button.type = "button";
        button.setAttribute("aria-pressed", String(state.candidate === candidate.id));
        button.append(
          element("span", "variant-number", String(index + 1).padStart(2, "0")),
          document.createTextNode(text(candidate.title, `Variant ${index + 1}`)),
        );
        button.addEventListener("click", () => {
          state.candidate = candidate.id;
          const latest = array(state.data?.projects).find(
            (item) => item.id === project.id,
          );
          if (latest) renderCandidate(latest);
        });
        variants.append(button);
      });
      if (!candidates.length)
        variants.append(element("span", "variant-button", "PROTOTYPES / PENDING"));
    }
    const candidate = candidates.find((item) => item.id === state.candidate);
    const url = artifactUrl(candidate?.artifact_url);
    setFrame(
      url,
      `${text(candidate?.title, text(project.title, "Nuria"))} — interactive creation`,
    );
    const checks = candidate?.checks;
    const checked = typeof checks?.passed === "boolean";
    byId("candidate-description").textContent = candidate
      ? `${text(candidate.title, "Interactive creation")}${candidate.revision ? ` · revision ${candidate.revision}` : ""}`
      : "Waiting for a first prototype";
    byId("candidate-checks").textContent =
      checks?.outcomes_passed === false
        ? "Outcome checks need repair"
        : checked
          ? checks.passed
            ? checks.outcomes_passed
              ? "Outcome checks + desktop/phone passed"
              : checks.browser_passed
                ? "Desktop + phone interactions passed"
                : "Engine checks passed"
            : "Execution checks need attention"
          : candidate
            ? "Checks pending"
            : "";
    byId("candidate-checks").classList.toggle(
      "failed",
      (checked && !checks.passed) || checks?.outcomes_passed === false,
    );
    if (candidate?.artifact_url && !url) {
      byId("experience-heading").textContent = "This artifact cannot be opened.";
      byId("experience-message").textContent =
        "The studio only opens artifacts from its own creation archive.";
    } else {
      byId("experience-heading").textContent =
        candidate && !finished.has(candidate.status)
          ? "An experiment is in progress."
          : "An idea is taking shape.";
      byId("experience-message").textContent = candidate
        ? "This variant does not have a playable artifact yet. Its recorded work is in the notebook below."
        : "The first playable artifact will appear here when it is available.";
    }
  }
  function renderProject() {
    const project = array(state.data?.projects).find(
      (item) => item.id === state.project,
    );
    byId("project-detail").hidden = !project;
    if (!project) {
      setFrame(null, "Nuria project experience");
      return;
    }
    byId("project-kicker").textContent =
      `${project.frontier?.name || "SELECTED PROJECT"} / ${project.mode === "improve" ? "IMPROVEMENT / " : ""}${label(project.status).toUpperCase()}`;
    byId("project-title").textContent = text(project.title, "Untitled project");
    byId("project-brief").textContent = text(project.brief).split(/(?<=[.!?])\s/)[0];
    byId("full-project-brief").textContent = text(project.brief);
    byId("selection-reason").textContent =
      text(project.selection_reason) ||
      text(project.review?.rationale) ||
      text(project.review?.summary) ||
      "The alternatives are still being explored. A recorded selection will appear here.";
    const verification = project.verification;
    byId("verification-note").hidden = !verification;
    byId("verification-summary").textContent = verification
      ? `${label(verification.status)}${verification.passed_claims ? ` · ${verification.passed_claims} outcome checks` : ""}${verification.repair_attempts ? ` · ${verification.repair_attempts} repair attempts` : ""}. ${text(verification.summary)}`
      : "";
    renderCandidate(project);
    if (changed("concepts", [project.id, project.concepts, project.selected])) {
      const node = byId("concepts");
      node.replaceChildren();
      array(project.concepts).forEach((concept) => {
        const selected =
          concept.id ===
          (typeof project.selected === "object"
            ? project.selected?.id
            : project.selected);
        const card = element("article", `concept${selected ? " selected" : ""}`);
        card.append(
          element("h4", "", text(concept.title, "An idea")),
          element("p", "", text(concept.thesis) || text(concept.interaction)),
        );
        node.append(card);
      });
      if (!node.children.length)
        node.append(element("p", "quiet", "No concepts recorded yet."));
    }
    if (changed("lineage", [project.id, project.skills_used, project.skills_created])) {
      const node = byId("project-skills");
      node.replaceChildren();
      [
        [project.skills_used, "Brought into this project", "↳"],
        [project.skills_created, "Retained from this project", "+"],
      ].forEach(([skills, description, mark]) => {
        array(skills).forEach((skill) => {
          const row = element("div", "skill-connection");
          const body = element(
            "div",
            "",
            text(skill.name, text(skill.id, "Retained technique")),
          );
          body.append(element("small", "", description));
          row.append(element("span", "", mark), body);
          node.append(row);
        });
      });
      if (!node.children.length)
        node.append(
          element(
            "p",
            "quiet",
            "New techniques can become part of Nuria's repertoire when the work is checked.",
          ),
        );
    }
    byId("source-summary").textContent = text(
      project.source?.summary,
      "The project’s source material has not been described yet.",
    );
    byId("source-time").textContent = project.source?.captured_at
      ? `Captured ${date(project.source.captured_at)}`
      : "";
    if (changed("events", [project.id, project.events])) {
      const node = byId("events");
      node.replaceChildren();
      const events = array(project.events);
      events.forEach((event) => {
        const item = element("li", "event");
        const time = element("time", "", date(event.at, true));
        if (Number.isFinite(new Date(event.at).getTime()))
          time.dateTime = new Date(event.at).toISOString();
        const body = element("div", "event-body");
        body.append(
          element("span", "event-kind", label(event.kind)),
          element("p", "", text(event.summary, "A project step was recorded.")),
        );
        item.append(time, body);
        node.append(item);
      });
      byId("no-events").hidden = events.length > 0;
    }
  }
  function renderRepertoire() {
    const skills = array(state.data?.skills);
    if (changed("skills", skills)) {
      const list = byId("skill-list");
      list.replaceChildren();
      skills.forEach((skill, index) => {
        const item = element("article", "skill-card");
        item.append(
          element(
            "span",
            "skill-card-label",
            `TECHNIQUE ${String(index + 1).padStart(2, "0")}`,
          ),
          element("h3", "", text(skill.name, "Retained technique")),
          element("p", "", text(skill.description)),
        );
        const origin = element("div", "skill-origin");
        const project = array(state.data?.projects).find(
          (p) => p.id === skill.project_id,
        );
        if (project) {
          const button = element(
            "button",
            "",
            `From ${text(project.title, "a previous project")} ↗`,
          );
          button.type = "button";
          button.addEventListener("click", () => selectProject(project.id, true));
          origin.append(button);
        }
        const reuses = count(skill.reuses);
        origin.append(
          element("span", "", `${reuses} ${reuses === 1 ? "reuse" : "reuses"}`),
        );
        item.append(origin);
        list.append(item);
      });
      byId("empty-skills").hidden = skills.length > 0;
    }
    const interests = array(state.data?.interests).filter(
      (item) => typeof item === "string",
    );
    if (changed("interests", interests)) {
      byId("interests").replaceChildren(
        ...interests.map((interest) => element("span", "", interest)),
      );
      byId("interests-section").hidden = !interests.length;
    }
  }
  function renderStatus() {
    const data = state.data;
    const phase = text(data.phase, "ready");
    const active = [
      "running",
      "creating",
      "working",
      "building",
      "thinking",
      "exploring",
      "reviewing",
    ].includes(phase);
    const heartbeat = new Date(data.heartbeat ?? data.updated_at).getTime();
    const stale =
      active && Number.isFinite(heartbeat) && Date.now() - heartbeat > 180000;
    const phaseNames = {
      creating: "Creating a new work",
      campaign_complete: "Current series complete",
      bridge_offline: "Reconnecting to inference",
      budget_paused: "Paused at the inference allowance",
      needs_attention: "Work needs attention",
      running: "At work",
      working: "At work",
      building: "Making something",
      thinking: "Developing an idea",
      exploring: "Exploring possibilities",
      reviewing: "Reviewing the work",
      checking_outcomes: "Challenging claimed outcomes",
      repairing: "Repairing a measured failure",
      idle: "Between projects",
      ready: "Ready for a new question",
      paused: "Work paused",
      complete: "Current work complete",
      completed: "Current work complete",
      budget_exhausted: "Inference allowance exhausted",
      waiting_for_reasoner: "Reasoning connection paused",
    };
    byId("worker-status").textContent = stale
      ? "Worker update delayed"
      : phaseNames[phase] || label(phase);
    byId("status-dot").className = `status-dot ${stale ? "offline" : "connected"}`;
    const projects = array(data.projects).length;
    const skills = array(data.skills).length;
    byId("studio-counts").textContent =
      `${projects} ${projects === 1 ? "project" : "projects"} / ${skills} retained ${skills === 1 ? "technique" : "techniques"}`;
    byId("updated").textContent =
      `${data.campaign?.mode === "autonomous" ? "Autonomous · " : ""}Updated ${date(data.updated_at, true)}`;
  }
  async function refresh() {
    if (state.fetching) return;
    state.fetching = true;
    byId("retry").disabled = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch("/api/studio", {
        signal: controller.signal,
        cache: "no-store",
        credentials: "omit",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`Studio response ${response.status}`);
      const data = await response.json();
      if (
        !data ||
        typeof data !== "object" ||
        !Array.isArray(data.projects) ||
        data.projects.some((project) => !project || !identifier(project.id))
      )
        throw new Error("Invalid studio response");
      state.data = data;
      if (!data.projects.some((project) => project.id === state.project)) {
        let hash = null;
        try {
          hash = identifier(decodeURIComponent(location.hash.slice(1)));
        } catch {
          /* A malformed hash does not select a project. */
        }
        state.project =
          data.projects.find((project) => project.id === hash)?.id ??
          data.projects
            .slice()
            .reverse()
            .find((project) => finished.has(project.status))?.id ??
          data.projects.at(-1)?.id ??
          null;
        state.candidate = null;
      }
      renderStatus();
      renderProjects();
      renderProject();
      renderRepertoire();
      byId("connection-error").hidden = true;
    } catch {
      byId("connection-error").hidden = false;
      byId("connection-message").textContent = state.data
        ? "Live updates are unavailable. The last received work is still shown below."
        : "The studio is temporarily unavailable. Please try again in a moment.";
      byId("status-dot").className = "status-dot offline";
      byId("worker-status").textContent = "Connection unavailable";
      if (!state.data) {
        byId("studio-counts").textContent = "Waiting for project records";
        byId("empty-title").textContent = "The notebook is out of reach.";
        byId("empty-copy").textContent =
          "Project records could not be loaded. Use Try again above to reconnect.";
      }
    } finally {
      clearTimeout(timeout);
      state.fetching = false;
      byId("retry").disabled = false;
      byId("projects").setAttribute("aria-busy", "false");
    }
  }
  byId("retry").addEventListener("click", refresh);
  byId("more-projects").addEventListener("click", () => {
    state.galleryLimit += 6;
    renderProjects();
  });
  byId("restart").addEventListener("click", () => {
    if (state.frameUrl) {
      const iframe = byId("artifact");
      setFrame(state.frameUrl, iframe.title, true);
    }
  });
  window.addEventListener("hashchange", () => {
    let id = null;
    try {
      id = identifier(decodeURIComponent(location.hash.slice(1)));
    } catch {
      return;
    }
    if (id) selectProject(id);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  refresh();
  setInterval(() => {
    if (!document.hidden) refresh();
  }, 30000);
})();

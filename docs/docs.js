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

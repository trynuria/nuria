function nuriaPresentationJSON(value) {
  const identityFields = new Set([
    "token",
    "mint",
    "quote_mint",
    "pool",
    "wallet",
    "creator_wallet",
    "spending_wallet",
    "reserve_wallet",
    "verified_creator",
    "creator",
    "creator_vault",
    "quote_creator_vault",
    "token_mode",
    "mode",
  ]);
  return JSON.stringify(
    value,
    (key, item) => {
      if (identityFields.has(key)) return undefined;
      if (typeof item === "string" && item.startsWith("solana_test_finalized:"))
        return "finalized_solana";
      return item;
    },
    2,
  );
}

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

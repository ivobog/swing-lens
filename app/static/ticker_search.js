(() => {
  "use strict";
  const dock = document.getElementById("ticker-search");
  if (!dock) return;
  const toggle = document.querySelector("[data-ticker-search]");
  const input = dock.querySelector("input");
  const list = dock.querySelector("[role=listbox]");
  const status = dock.querySelector("[data-ticker-status]");
  const retry = dock.querySelector("[data-ticker-retry]");
  const preview = dock.querySelector("[data-ticker-preview]");
  let timer, searchController, previewController;
  let searchVersion = 0, previewVersion = 0;
  let results = [], highlighted = -1, selected = null;
  let returnFocus = null;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function openDock() {
    if (dock.hidden) returnFocus = document.activeElement;
    dock.hidden = false;
    document.body.classList.add("ticker-search-open");
    toggle.setAttribute("aria-expanded", "true");
    input.focus();
    if (input.value.trim()) scheduleSearch();
  }

  function closeDock() {
    clearTimeout(timer);
    searchController?.abort();
    previewController?.abort();
    searchVersion++;
    previewVersion++;
    if (preview.textContent.startsWith("Loading ")) {
      preview.replaceChildren(node("p", "Select a ticker for a quick decision preview."));
    }
    dock.hidden = true;
    document.body.classList.remove("ticker-search-open");
    toggle.setAttribute("aria-expanded", "false");
    const focusable = returnFocus?.isConnected && returnFocus.matches(
      "a[href], button, input, select, textarea, [tabindex]"
    );
    (focusable ? returnFocus : toggle).focus();
  }

  toggle.addEventListener("click", openDock);
  dock.querySelector("[data-ticker-close]").addEventListener("click", closeDock);
  // Capture observes existing dialogs before their Escape handlers hide them.
  document.addEventListener("keydown", (event) => {
    const dialogOpen = [...document.querySelectorAll("[role=dialog], dialog[open]")]
      .some((element) => element.getClientRects().length > 0);
    if (event.defaultPrevented || dialogOpen || event.isComposing) return;
    const editable = event.target.closest?.("input, textarea, select, [contenteditable]:not([contenteditable=false])");
    if (event.key === "/" && !editable && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      openDock();
    } else if (event.key === "Escape" && !dock.hidden && dock.contains(event.target)) {
      event.preventDefault();
      closeDock();
    }
  }, true);

  function highlight(index) {
    highlighted = index;
    [...list.children].forEach((option, i) => { option.dataset.highlighted = String(i === index); });
    const option = list.children[index];
    if (option) {
      input.setAttribute("aria-activedescendant", option.id);
      option.scrollIntoView({block: "nearest"});
    } else input.removeAttribute("aria-activedescendant");
  }

  function renderResults() {
    list.replaceChildren();
    results.forEach((item, index) => {
      const option = node("li");
      option.id = `ticker-option-${index}`;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(item.identity === selected));
      option.append(node("strong", item.ticker), node("span", item.company_name || "Company name unavailable"),
        node("span", item.qualifier));
      option.addEventListener("click", () => { highlight(index); select(item); });
      list.append(option);
    });
    input.setAttribute("aria-expanded", String(results.length > 0));
    highlight(results.length ? 0 : -1);
  }

  async function search(version) {
    searchController = new AbortController();
    const query = input.value.trim();
    if (!query) {
      status.textContent = "Search tickers already known to SwingLens.";
      return;
    }
    status.textContent = "Searching local tickers…";
    list.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(`/api/tickers/search?${new URLSearchParams({q: query})}`, {signal: searchController.signal});
      if (!response.ok) throw new Error("search failed");
      const payload = await response.json();
      if (version !== searchVersion) return;
      results = payload.results;
      renderResults();
      status.textContent = results.length ? `${results.length} matching identities` : "No matching local tickers.";
    } catch (error) {
      if (error.name === "AbortError" || version !== searchVersion) return;
      status.textContent = "Ticker search could not load. Please retry.";
      retry.hidden = false;
    } finally {
      if (version === searchVersion) list.removeAttribute("aria-busy");
    }
  }

  function scheduleSearch() {
    clearTimeout(timer);
    searchController?.abort();
    const version = ++searchVersion;
    results = [];
    renderResults();
    retry.hidden = true;
    list.removeAttribute("aria-busy");
    status.textContent = input.value.trim() ? "Searching local tickers…" : "Search tickers already known to SwingLens.";
    timer = setTimeout(() => search(version), 200);
  }
  input.addEventListener("input", scheduleSearch);
  retry.addEventListener("click", scheduleSearch);
  input.addEventListener("keydown", (event) => {
    if (event.isComposing || !results.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      highlight((highlighted + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length);
    } else if (event.key === "Enter" && highlighted >= 0) {
      event.preventDefault();
      select(results[highlighted]);
    }
  });

  async function select(item) {
    selected = item.identity;
    [...list.children].forEach((option, i) => option.setAttribute("aria-selected", String(results[i].identity === selected)));
    previewController?.abort();
    const version = ++previewVersion;
    previewController = new AbortController();
    preview.replaceChildren(node("p", `Loading ${item.ticker} preview…`));
    try {
      const response = await fetch(`/api/tickers/${encodeURIComponent(item.ticker)}/summary?${new URLSearchParams({identity: item.identity})}`, {signal: previewController.signal});
      if (!response.ok) throw new Error("preview failed");
      const payload = await response.json();
      if (version !== previewVersion) return;
      renderPreview(payload);
    } catch (error) {
      if (error.name === "AbortError" || version !== previewVersion) return;
      const button = node("button", "Retry preview", "secondary");
      button.type = "button";
      button.addEventListener("click", () => select(item));
      preview.replaceChildren(node("p", "Ticker preview could not load."), button);
    }
  }

  function renderPreview(payload) {
    const item = payload.identity;
    preview.replaceChildren(node("h3", item?.ticker || "Select an identity"));
    if (item) preview.append(node("p", item.company_name || "Company name unavailable"), node("small", item.qualifier));
    if (!payload.decision) {
      const messages = {
        no_eligible_decision: "No eligible decision available. Evidence may be incomplete, legacy or not yet published.",
        identity_ambiguous: "This symbol has multiple identities. Select a qualified result.",
        decision_identity_unverified: "No verified decision for this identity. Run scores do not identify its exchange.",
      };
      preview.append(node("p", messages[payload.state] || "Decision unavailable."));
      if (payload.workspace_url) {
        preview.append(workspaceLink(payload.workspace_url));
      } else {
        const disabled = node("button", "Open Workspace");
        disabled.type = "button";
        disabled.disabled = true;
        preview.append(disabled);
      }
      return;
    }
    const d = payload.decision;
    preview.append(node("p", d.combined_decision || "Decision label unavailable"));
    const scores = node("dl");
    const score = (value) => value == null ? null : Number(value).toFixed(2);
    for (const [label, value] of [["Final score", score(d.final_score)], ["Fundamental", score(d.fundamental_score)],
      ["Technical", score(d.dual_score)], ["Combined rank", d.final_rank], ["Research size", d.position_size_hint],
      ["Sector", d.sector]]) {
      scores.append(node("dt", label), node("dd", value ?? "Unavailable"));
    }
    const cutoff = new Date(d.cutoff).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
    });
    preview.append(scores, node("small", `Decision: Run ${d.run_id} · Cutoff: ${cutoff}`));
    const warnings = [...new Set([...(d.warning_flags_json || []), ...(d.earnings_warning_flags_json || [])])];
    if (warnings.length) {
      preview.append(node("strong", "Warnings"));
      const warningList = node("ul");
      warnings.forEach((warning) => warningList.append(node("li", warning)));
      preview.append(warningList);
    }
    preview.append(node("small", "CERI, lifecycle and Winner availability: not checked in this preview."));
    preview.append(workspaceLink(payload.workspace_url),
      node("small", "Opens the full decision workspace in a separate window or tab."));
  }

  function workspaceLink(url) {
    const link = node("a", "Open Workspace ↗", "ticker-workspace");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.addEventListener("click", (event) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      // Reserve one same-origin blank window without noopener's ambiguous null
      // return. Clear opener before navigation. A blocked reservation leaves the
      // native target=_blank link as the one fallback; never call open twice.
      let popup;
      try { popup = window.open("", "_blank", "popup,width=1280,height=900"); } catch (_) { return; }
      if (!popup) return;
      try {
        popup.opener = null;
        popup.location.replace(link.href);
        event.preventDefault();
      } catch (_) { popup.close(); }
    });
    return link;
  }
})();

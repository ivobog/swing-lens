(() => {
  "use strict";
  const key = "swinglens.workspace.sections.v1";
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(key) || "{}"); } catch (_) { /* storage optional */ }
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) saved = {};
  const controllers = new Set();
  const resume = [];
  let active = true;
  const workspace = document.querySelector("[data-ticker-workspace]");
  const runSelector = workspace?.querySelector("#decision-run");
  const renderedRun = workspace?.dataset.selectedRun;
  const restoreRunSelection = () => {
    if (runSelector && renderedRun) runSelector.value = renderedRun;
  };
  restoreRunSelection();
  window.addEventListener("pagehide", () => {
    active = false;
    controllers.forEach((controller) => controller.abort());
  });
  window.addEventListener("pageshow", (event) => {
    restoreRunSelection();
    // Browsers may restore edited form values after dispatching pageshow.
    requestAnimationFrame(restoreRunSelection);
    if (!event.persisted) return;
    active = true;
    resume.forEach((refresh) => refresh());
  });
  document.querySelectorAll("[data-workspace-section]").forEach((section) => {
    const name = section.dataset.workspaceSection;
    if (typeof saved[name] === "boolean") section.open = saved[name];
    section.addEventListener("toggle", () => {
      saved[name] = section.open;
      try { sessionStorage.setItem(key, JSON.stringify(saved)); } catch (_) { /* storage optional */ }
    });
    if (!section.dataset.intelligenceUrl) return;
    const summary = section.querySelector("[data-intelligence-summary]");
    const status = section.querySelector("[data-intelligence-status]");
    const content = section.querySelector("[data-intelligence-content]");
    const retry = section.querySelector("[data-intelligence-retry]");
    let version = 0;
    let loaded = false;
    let loadingDetails = false;
    let detailUrl = section.dataset.intelligenceUrl;
    async function load(detail) {
      if (detail && (loaded || loadingDetails)) return;
      const requestVersion = ++version;
      const controller = new AbortController();
      controllers.add(controller);
      if (detail) {
        loadingDetails = true;
        status.textContent = "Loading intelligence…";
      }
      retry.hidden = true;
      try {
        const response = await fetch((detail ? detailUrl : section.dataset.intelligenceUrl) +
          (detail && !new URL(detailUrl, location.href).searchParams.has("detail") ? "&detail=true" : ""), {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("Section request failed");
        const data = await response.json();
        if (!active || !section.isConnected || version !== requestVersion) return;
        summary.textContent = data.summary + (data.as_of ? ` · As of ${data.as_of}` : "");
        if (detail) {
          // HTML is rendered by the same-origin Jinja partial with autoescaping;
          // external descriptions never become client-created HTML templates.
          content.innerHTML = data.html;
          status.textContent = "";
          loaded = true;
        }
      } catch (error) {
        if (error.name === "AbortError" || !active || version !== requestVersion) return;
        summary.textContent = "Intelligence unavailable — retry this section.";
        status.textContent = "Intelligence data could not load. Other workspace sections remain available.";
        retry.hidden = false;
      } finally {
        controllers.delete(controller);
        if (detail && version === requestVersion) loadingDetails = false;
      }
    }
    section.addEventListener("toggle", () => { if (section.open) load(true); });
    content.addEventListener("click", (event) => {
      const link = event.target.closest("[data-intelligence-page]");
      if (!link) return;
      event.preventDefault();
      if (loadingDetails) return;
      detailUrl = link.href;
      loaded = false;
      load(true);
    });
    retry.addEventListener("click", () => load(section.open));
    resume.push(() => {
      loadingDetails = false;
      if (!loaded) load(section.open);
    });
    load(section.open);
  });
})();

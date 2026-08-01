document.addEventListener("DOMContentLoaded", () => {
  bindSetupLifecycleDetailButtons();
  bindSetupLifecycleAlertActions();
});

function bindSetupLifecycleDetailButtons() {
  document.querySelectorAll("[data-slse-detail-url]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.slseDetailTarget);
      if (!target) return;
      const content = target.querySelector("[data-slse-detail-content]");
      const isHidden = target.hidden;
      target.hidden = !isHidden;
      button.setAttribute("aria-expanded", String(isHidden));
      button.textContent = isHidden ? "Collapse" : "Expand";
      if (!isHidden || !content || content.dataset.loaded === "true") return;

      content.innerHTML = "<h3>Episode</h3><p>Loading evidence...</p>";
      try {
        const response = await fetch(button.dataset.slseDetailUrl, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        content.innerHTML = renderEpisodeDetail(payload);
        content.dataset.loaded = "true";
      } catch (_error) {
        content.innerHTML = "<h3>Episode</h3><p>Episode evidence could not be loaded.</p>";
      }
    });
  });
}

function bindSetupLifecycleAlertActions() {
  document.querySelectorAll("[data-slse-alert-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-slse-alert-row]");
      const status = row ? row.querySelector("[data-slse-alert-status]") : null;
      button.disabled = true;
      try {
        const response = await fetch(button.dataset.slseAlertAction, {
          method: "POST",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        if (status) status.textContent = payload.status;
      } catch (_error) {
        if (status) status.textContent = "Update failed";
      } finally {
        button.disabled = false;
      }
    });
  });
}

function renderEpisodeDetail(payload) {
  const episode = payload.episode || {};
  const events = payload.lifecycle_events || [];
  const changes = payload.signal_changes || [];
  return `
    <h3>Episode ${escapeHtml(episode.id || "")}</h3>
    <dl class="compact-dl">
      <dt>Family</dt><dd>${escapeHtml(episode.setup_family || "")}</dd>
      <dt>State</dt><dd>${escapeHtml(episode.current_state || "")}</dd>
      <dt>Actionability</dt><dd>${escapeHtml(episode.current_actionability || "")}</dd>
      <dt>Events</dt><dd>${events.length}</dd>
      <dt>Signal changes</dt><dd>${changes.length}</dd>
    </dl>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-ibmi-operations]");
  if (!root) return;
  const csrf = root.dataset.csrfToken;
  const result = root.querySelector("[data-ibmi-result]");
  root.querySelectorAll("[data-ibmi-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const payload = {};
      for (const [key, value] of data.entries()) {
        if (key === "tickers" || key === "presets") {
          payload[key] = String(value).split(",").map((item) => item.trim()).filter(Boolean);
        } else if (key === "dry_run" || key === "force") {
          payload[key] = true;
        } else if (value !== "") {
          payload[key] = value;
        }
      }
      if (form.querySelector('[name="dry_run"]') && !("dry_run" in payload)) payload.dry_run = false;
      if (form.querySelector('[name="force"]') && !("force" in payload)) payload.force = false;
      result.hidden = false;
      result.textContent = "Queuing…";
      try {
        const response = await fetch(form.dataset.endpoint, {
          method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
          body: JSON.stringify(payload),
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail?.message || body.detail || "Request failed");
        result.textContent = `Queued job ${body.job_id} (${body.status}).`;
      } catch (error) {
        result.textContent = String(error.message || error);
      }
    });
  });
});

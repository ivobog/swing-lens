document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-winner-probability-page]").forEach((page) => {
    page.querySelectorAll("select[data-auto-submit]").forEach((select) => {
      select.addEventListener("change", () => {
        const form = select.closest("form");
        if (form) form.requestSubmit();
      });
    });
  });
  bindWinnerJsonForms();
});

function bindWinnerJsonForms() {
  document.querySelectorAll("[data-winner-json-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const output = form.querySelector("[data-winner-form-output]");
      const button = form.querySelector("button[type='submit']");
      const originalLabel = button ? button.textContent : "";
      if (button) {
        const label = button.getAttribute("data-loading-label");
        if (label) button.textContent = label;
        button.disabled = true;
      }
      if (output) {
        output.setAttribute("role", "status");
        output.setAttribute("aria-live", "polite");
        output.textContent = "Queueing...";
      }
      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: { Accept: "application/json" },
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail?.message || payload.detail || `HTTP ${response.status}`);
        if (output) {
          output.textContent = payload.coalesced
            ? `Already running ${payload.job_type || "job"} ${payload.job_id || ""}; opened the active job.`
            : `Queued ${payload.job_type || "job"} ${payload.job_id || ""}`;
        }
      } catch (error) {
        if (output) {
          output.setAttribute("role", "alert");
          output.textContent = `${error.message || "Request failed"}. Try again from this page.`;
        }
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = originalLabel;
        }
      }
    });
  });
}

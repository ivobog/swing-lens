document.addEventListener("DOMContentLoaded", () => {
  bindCeriAlertActions();
  bindCeriJsonForms();
});

function bindCeriAlertActions() {
  document.querySelectorAll("[data-ceri-alert-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-ceri-alert-row]");
      const status = row ? row.querySelector("[data-ceri-alert-status]") : null;
      button.disabled = true;
      try {
        const response = await fetch(button.dataset.ceriAlertAction, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "x-csrf-token": "ceri-local-admin",
          },
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

function bindCeriJsonForms() {
  document.querySelectorAll("[data-ceri-json-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const output = form.querySelector("[data-ceri-form-output]");
      const button = form.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      if (output) output.textContent = "Queueing...";
      try {
        const body = formBody(form);
        const response = await fetch(form.action, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "x-csrf-token": body.csrf_token || "ceri-local-admin",
          },
          body: JSON.stringify(body),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail?.message || `HTTP ${response.status}`);
        if (output) {
          output.textContent = `Queued ${payload.job_type || "job"} ${payload.job_id || ""}`;
        }
      } catch (error) {
        if (output) output.textContent = error.message || "Request failed";
      } finally {
        if (button) button.disabled = false;
      }
    });
  });
}

function formBody(form) {
  const formData = new FormData(form);
  const body = {};
  formData.forEach((value, key) => {
    if (value === "") return;
    body[key] = value;
  });
  return body;
}

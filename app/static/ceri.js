document.addEventListener("DOMContentLoaded", () => {
  bindCeriAlertActions();
  bindCeriAlertDetails();
  bindCeriJsonForms();
  document.querySelectorAll("[data-ceri-optional-numbers]").forEach((form) => {
    form.addEventListener("formdata", ({ formData }) => {
      for (const control of form.querySelectorAll('input[type="number"]:not([required])')) {
        if (control.value === "") formData.delete(control.name);
      }
    });
  });
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
            "x-csrf-token": button.dataset.ceriCsrfToken || "",
          },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        if (status) {
          status.setAttribute("role", "status");
          status.setAttribute("aria-live", "polite");
          status.textContent = payload.status;
        }
      } catch (_error) {
        if (status) {
          status.setAttribute("role", "alert");
          status.textContent = "Update failed. Try the action again.";
        }
      } finally {
        button.disabled = false;
      }
    });
  });
}

function bindCeriAlertDetails() {
  document.querySelectorAll("[data-ceri-alert-detail]").forEach((detail) => {
    const status = detail.querySelector("[data-ceri-detail-status]");
    const output = detail.querySelector("[data-ceri-detail-output]");
    const retry = detail.querySelector("[data-ceri-detail-retry]");
    let loaded = false;
    let pending;
    const load = async () => {
      if (!detail.open || pending || loaded) return;
      pending = new AbortController();
      detail.setAttribute("aria-busy", "true");
      retry.hidden = true;
      status.textContent = "Loading technical details…";
      try {
        const response = await fetch(detail.dataset.ceriAlertDetail, {
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: pending.signal,
        });
        if (response.status === 404) {
          status.textContent = "Technical details are unavailable.";
          loaded = true;
          return;
        }
        if (!response.ok) throw new Error("Could not load alert details");
        const payload = await response.json();
        if (payload === null || Object.keys(payload).length === 0) {
          status.textContent = "No technical details were retained.";
        } else {
          output.textContent = JSON.stringify(payload, null, 2);
          output.hidden = false;
          status.textContent = "Technical details loaded.";
        }
        loaded = true;
      } catch (error) {
        if (error.name !== "AbortError") {
          status.textContent = "Could not load technical details.";
          retry.hidden = false;
        }
      } finally {
        pending = undefined;
        detail.removeAttribute("aria-busy");
      }
    };
    detail.addEventListener("toggle", load);
    retry.addEventListener("click", load);
    window.addEventListener("pagehide", () => pending?.abort());
  });
}

function bindCeriJsonForms() {
  document.querySelectorAll("[data-ceri-json-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const output = form.querySelector("[data-ceri-form-output]");
      const button = form.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      if (output) {
        output.setAttribute("role", "status");
        output.setAttribute("aria-live", "polite");
        output.textContent = "Queueing...";
      }
      try {
        const body = formBody(form);
        const response = await fetch(form.action, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "x-csrf-token": form.dataset.ceriCsrfToken || "",
          },
          body: JSON.stringify(body),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail?.message || `HTTP ${response.status}`);
        if (output) {
          output.textContent = payload.coalesced
            ? `Already running ${payload.job_type || "job"} ${payload.job_id || ""}; opened the active job.`
            : `Queued ${payload.job_type || "job"} ${payload.job_id || ""}`;
        }
      } catch (error) {
        if (output) {
          output.setAttribute("role", "alert");
          output.textContent = `${error.message || "Request failed"}. Check required fields and try again.`;
        }
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
    if (key === "csrf_token") return;
    body[key] = value;
  });
  return body;
}

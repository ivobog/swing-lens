document.addEventListener("DOMContentLoaded", () => {
  bindConfirmActions();
  bindLoadingForms();
  bindCockpitTables();
  bindFileInputs();
});

function bindLoadingForms() {
  document.querySelectorAll("[data-loading-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      const button = event.submitter || form.querySelector("button[type='submit']");
      if (!button) return;
      const label = button.getAttribute("data-loading-label");
      if (label) button.textContent = label;
      button.disabled = true;
    });
  });
}

function bindCockpitTables() {
  document.querySelectorAll("[data-cockpit-table]").forEach((table) => {
    const section = table.closest("section");
    const controls = section ? section.querySelector("[data-cockpit-controls]") : null;
    const empty = section ? section.querySelector("[data-filter-empty]") : null;
    const rows = Array.from(table.querySelectorAll("[data-cockpit-row]"));

    rows.forEach((row) => {
      const detailRow = row.nextElementSibling;
      const toggle = row.querySelector("[data-detail-toggle]");
      if (!toggle || !detailRow || !detailRow.matches("[data-detail-row]")) return;

      toggle.addEventListener("click", () => {
        const isHidden = detailRow.hidden;
        detailRow.hidden = !isHidden;
        toggle.setAttribute("aria-expanded", String(isHidden));
        toggle.textContent = isHidden ? "Hide" : "Details";
      });
    });

    if (!controls) return;

    const inputs = Array.from(controls.querySelectorAll("input, select"));
    const applyFilters = () => {
      let visibleCount = 0;

      rows.forEach((row) => {
        const visible = rowMatchesFilters(row, controls);
        const detailRow = row.nextElementSibling;
        row.hidden = !visible;

        if (detailRow && detailRow.matches("[data-detail-row]")) {
          if (!visible) {
            detailRow.hidden = true;
            const toggle = row.querySelector("[data-detail-toggle]");
            if (toggle) {
              toggle.setAttribute("aria-expanded", "false");
              toggle.textContent = "Details";
            }
          }
        }

        if (visible) visibleCount += 1;
      });

      if (empty) empty.hidden = visibleCount !== 0;
    };

    inputs.forEach((input) => {
      input.addEventListener("input", applyFilters);
      input.addEventListener("change", applyFilters);
    });
  });
}

function rowMatchesFilters(row, controls) {
  const query = valueOf(controls, "[data-filter-search]").toLowerCase();
  const decision = valueOf(controls, "[data-filter-decision]");
  const sector = valueOf(controls, "[data-filter-sector]");
  const minFinal = numberValueOf(controls, "[data-filter-final]");
  const minFundamental = numberValueOf(controls, "[data-filter-fundamental]");
  const minTechnical = numberValueOf(controls, "[data-filter-technical]");
  const incompleteOnly = checkedValueOf(controls, "[data-filter-incomplete]");
  const warningsOnly = checkedValueOf(controls, "[data-filter-warnings]");

  const haystack = [
    row.dataset.ticker,
    row.dataset.company,
    row.dataset.sector,
    row.dataset.fundamentalLabel,
    row.dataset.technicalClassification,
  ]
    .join(" ")
    .toLowerCase();

  if (query && !haystack.includes(query)) return false;
  if (decision && row.dataset.decision !== decision) return false;
  if (sector && row.dataset.sector !== sector) return false;
  if (incompleteOnly && row.dataset.incomplete !== "true") return false;
  if (warningsOnly && row.dataset.hasWarning !== "true") return false;
  if (!scoreAtLeast(row.dataset.finalScore, minFinal)) return false;
  if (!scoreAtLeast(row.dataset.fundamentalScore, minFundamental)) return false;
  if (!scoreAtLeast(row.dataset.technicalScore, minTechnical)) return false;

  return true;
}

function valueOf(root, selector) {
  const element = root.querySelector(selector);
  return element ? element.value.trim() : "";
}

function numberValueOf(root, selector) {
  const value = valueOf(root, selector);
  return value === "" ? null : Number(value);
}

function checkedValueOf(root, selector) {
  const element = root.querySelector(selector);
  return element ? element.checked : false;
}

function scoreAtLeast(rawScore, minimum) {
  if (minimum === null || Number.isNaN(minimum)) return true;
  if (!rawScore) return false;
  return Number(rawScore) >= minimum;
}

function bindFileInputs() {
  document.querySelectorAll("input[type='file']").forEach((input) => {
    input.addEventListener("change", () => {
      const label = document.querySelector(`[for='${input.id}'] span`);
      if (label && input.files.length) label.textContent = input.files[0].name;
    });
  });
}

function bindConfirmActions() {
  document.querySelectorAll("[data-confirm]").forEach((element) => {
    element.addEventListener("submit", (event) => {
      if (!window.confirm(element.dataset.confirm)) event.preventDefault();
    });
  });
}

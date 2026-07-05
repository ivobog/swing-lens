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
    const toolbar = section ? section.querySelector("[data-cockpit-toolbar]") : null;
    const controls = section ? section.querySelector("[data-cockpit-controls]") : null;
    const empty = section ? section.querySelector("[data-filter-empty]") : null;
    const feedback = toolbar ? toolbar.querySelector("[data-copy-feedback]") : null;
    let rows = Array.from(table.querySelectorAll("[data-cockpit-row]"));
    const quickFilters = new Set();

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

    table.querySelectorAll("[data-copy-single]").forEach((button) => {
      button.addEventListener("click", () => {
        copyTickers([button.dataset.copySingle], feedback);
      });
    });

    table.querySelectorAll("[data-sort-key]").forEach((button) => {
      button.addEventListener("click", () => {
        rows = sortCockpitRows(table, rows, button);
      });
    });

    if (toolbar) {
      toolbar.querySelectorAll("[data-quick-filter]").forEach((button) => {
        button.addEventListener("click", () => {
          const filter = button.dataset.quickFilter;
          if (quickFilters.has(filter)) {
            quickFilters.delete(filter);
            button.classList.remove("active");
            button.setAttribute("aria-pressed", "false");
          } else {
            quickFilters.add(filter);
            button.classList.add("active");
            button.setAttribute("aria-pressed", "true");
          }
          applyCockpitFilters(rows, controls, empty, quickFilters);
        });
        button.setAttribute("aria-pressed", "false");
      });

      toolbar.querySelectorAll("[data-copy-tickers]").forEach((button) => {
        button.addEventListener("click", () => {
          copyTickers(collectTickers(rows, button.dataset.copyTickers), feedback);
        });
      });
    }

    if (!controls) return;

    const inputs = Array.from(controls.querySelectorAll("input, select"));
    const applyFilters = () => applyCockpitFilters(rows, controls, empty, quickFilters);

    inputs.forEach((input) => {
      input.addEventListener("input", applyFilters);
      input.addEventListener("change", applyFilters);
    });

    const clearButton = controls.querySelector("[data-filter-clear]");
    if (clearButton) {
      clearButton.addEventListener("click", () => {
        inputs.forEach((input) => {
          if (input.type === "checkbox") input.checked = false;
          else input.value = "";
        });
        quickFilters.clear();
        if (toolbar) {
          toolbar.querySelectorAll("[data-quick-filter]").forEach((button) => {
            button.classList.remove("active");
            button.setAttribute("aria-pressed", "false");
          });
        }
        applyFilters();
      });
    }
  });
}

function applyCockpitFilters(rows, controls, empty, quickFilters) {
  let visibleCount = 0;

  rows.forEach((row) => {
    const visible = rowMatchesFilters(row, controls, quickFilters);
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
}

function rowMatchesFilters(row, controls, quickFilters) {
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
  if (!rowMatchesQuickFilters(row, quickFilters)) return false;

  return true;
}

function rowMatchesQuickFilters(row, quickFilters) {
  if (!quickFilters || quickFilters.size === 0) return true;
  if (quickFilters.has("top10") && numberFromDataset(row.dataset.rank) > 10) return false;
  if (quickFilters.has("top20") && numberFromDataset(row.dataset.rank) > 20) return false;
  if (quickFilters.has("strong") && row.dataset.decision !== "Strong candidate") return false;
  if (quickFilters.has("candidate") && row.dataset.candidatePlus !== "true") return false;
  if (quickFilters.has("clean") && row.dataset.clean !== "true") return false;
  if (quickFilters.has("warnings") && row.dataset.hasWarning !== "true") return false;
  if (quickFilters.has("incomplete") && row.dataset.incomplete !== "true") return false;
  if (quickFilters.has("hide-avoid") && row.dataset.avoid === "true") return false;
  return true;
}

function sortCockpitRows(table, rows, button) {
  const key = button.dataset.sortKey;
  const type = button.dataset.sortType || "text";
  const currentDirection = button.dataset.sortDirection;
  const defaultDirection = type === "number" && key !== "rank" ? "desc" : "asc";
  const direction = currentDirection === "asc" ? "desc" : currentDirection === "desc" ? "asc" : defaultDirection;
  const body = table.tBodies[0];
  const pairs = rows.map((row) => [row, row.nextElementSibling]);

  pairs.sort(([left], [right]) => compareRows(left, right, key, type, direction));
  pairs.forEach(([row, detail]) => {
    body.append(row);
    if (detail && detail.matches("[data-detail-row]")) body.append(detail);
  });

  table.querySelectorAll("[data-sort-key]").forEach((sortButton) => {
    sortButton.classList.remove("sorted-asc", "sorted-desc");
    sortButton.removeAttribute("data-sort-direction");
    sortButton.closest("th").removeAttribute("aria-sort");
  });
  button.dataset.sortDirection = direction;
  button.classList.add(direction === "asc" ? "sorted-asc" : "sorted-desc");
  button.closest("th").setAttribute("aria-sort", direction === "asc" ? "ascending" : "descending");

  return pairs.map(([row]) => row);
}

function compareRows(left, right, key, type, direction) {
  const multiplier = direction === "asc" ? 1 : -1;
  if (type === "number") {
    const leftValue = numberFromDataset(datasetValue(left, key));
    const rightValue = numberFromDataset(datasetValue(right, key));
    return (leftValue - rightValue) * multiplier;
  }
  return String(datasetValue(left, key) || "").localeCompare(String(datasetValue(right, key) || "")) * multiplier;
}

function datasetValue(row, key) {
  const camelKey = key.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
  return row.dataset[camelKey];
}

function collectTickers(rows, mode) {
  const selected = rows.filter((row) => {
    if (mode === "visible") return !row.hidden;
    if (mode === "top10") return numberFromDataset(row.dataset.rank) <= 10;
    if (mode === "candidates") return row.dataset.candidatePlus === "true";
    if (mode === "warnings") return row.dataset.hasWarning === "true";
    if (mode === "incomplete") return row.dataset.incomplete === "true";
    return false;
  });
  return uniqueTickers(selected.map((row) => row.dataset.ticker));
}

function uniqueTickers(tickers) {
  const seen = new Set();
  return tickers.filter((ticker) => {
    if (!ticker || seen.has(ticker)) return false;
    seen.add(ticker);
    return true;
  });
}

function copyTickers(tickers, feedback) {
  const unique = uniqueTickers(tickers);
  const text = unique.join(", ");
  if (!text) {
    setCopyFeedback(feedback, "No tickers to copy.");
    return;
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => setCopyFeedback(feedback, `Copied ${unique.length} ticker${unique.length === 1 ? "" : "s"}.`),
      () => fallbackCopy(text, feedback),
    );
  } else {
    fallbackCopy(text, feedback);
  }
}

function fallbackCopy(text, feedback) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
  setCopyFeedback(feedback, "Copied tickers.");
}

function setCopyFeedback(feedback, message) {
  if (!feedback) return;
  feedback.textContent = message;
  window.setTimeout(() => {
    feedback.textContent = "";
  }, 2500);
}

function valueOf(root, selector) {
  const element = root.querySelector(selector);
  return element ? element.value.trim() : "";
}

function numberValueOf(root, selector) {
  const value = valueOf(root, selector);
  return value === "" ? null : Number(value);
}

function numberFromDataset(rawValue) {
  if (rawValue === undefined || rawValue === null || rawValue === "") return Number.POSITIVE_INFINITY;
  const value = Number(rawValue);
  return Number.isNaN(value) ? Number.POSITIVE_INFINITY : value;
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

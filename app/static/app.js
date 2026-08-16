document.addEventListener("DOMContentLoaded", () => {
  bindIbGatewayPreflight();
  bindConfirmActions();
  bindLoadingForms();
  bindCockpitTables();
  bindCoverageTables();
  bindFetchProgressPolling();
  bindPipelineProgressPolling();
  bindFileInputs();
});

function bindIbGatewayPreflight() {
  const form = document.querySelector("[data-ib-pipeline-form]");
  const panel = document.querySelector("[data-ib-preflight-panel]");
  if (!form || !panel) return;

  const statusUrl = form.dataset.ibStatusUrl;
  const launchUrl = form.dataset.ibLaunchUrl;
  const policy = form.querySelector("[data-market-data-policy]");
  const submitButton = form.querySelector("button[type='submit']");
  const indicator = document.querySelector("[data-ib-connection-status]");
  const indicatorLabel = indicator?.querySelector("[data-ib-connection-label]");
  const title = panel.querySelector("[data-ib-preflight-title]");
  const detail = panel.querySelector("[data-ib-preflight-detail]");
  const waiting = panel.querySelector("[data-ib-waiting-message]");
  const launchButton = panel.querySelector("[data-ib-launch]");
  const retryButton = panel.querySelector("[data-ib-retry]");
  const readyButton = panel.querySelector("[data-ib-run-ready]");
  const cacheButton = panel.querySelector("[data-ib-cache]");
  const cancelButton = panel.querySelector("[data-ib-cancel]");
  let approved = false;
  let checking = false;
  let pollTimer = null;

  const setIndicator = (state, label) => {
    if (indicator) indicator.dataset.state = state;
    if (indicatorLabel) indicatorLabel.textContent = label;
  };

  const showUnavailable = (status) => {
    if (title) title.textContent = "Interactive Brokers is not connected";
    if (detail) detail.textContent = status?.message || "IB Gateway API is unavailable.";
    if (waiting) waiting.hidden = true;
    if (readyButton) readyButton.hidden = true;
    if (launchButton) launchButton.hidden = false;
    if (cacheButton) cacheButton.hidden = false;
    panel.hidden = false;
    launchButton?.focus();
  };

  const showReady = (status) => {
    setIndicator("ready", "IB Connected");
    if (title) title.textContent = "IB Gateway connected — API ready";
    if (detail) detail.textContent = status?.message || "IB Gateway API connection successful.";
    if (waiting) waiting.hidden = true;
    if (launchButton) launchButton.hidden = true;
    if (cacheButton) cacheButton.hidden = true;
    if (readyButton) readyButton.hidden = false;
    panel.hidden = false;
    readyButton?.focus();
    stopPolling();
  };

  const checkStatus = async ({ showPanel = false } = {}) => {
    if (checking || !statusUrl) return null;
    checking = true;
    setIndicator("checking", "Checking IB…");
    try {
      const response = await fetch(statusUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const status = await response.json();
      if (status.status === "READY" && status.api_connected) {
        setIndicator("ready", "IB Connected");
        if (showPanel || !panel.hidden) showReady(status);
      } else {
        setIndicator("offline", "IB Offline");
        if (showPanel) showUnavailable(status);
      }
      return status;
    } catch (_error) {
      setIndicator("offline", "IB status unavailable");
      if (showPanel) showUnavailable({ message: "SwingLens could not check IB API status." });
      return null;
    } finally {
      checking = false;
    }
  };

  const poll = async () => {
    const status = await checkStatus({ showPanel: true });
    if (!status || status.status !== "READY") {
      pollTimer = window.setTimeout(poll, 2500);
    }
  };

  function stopPolling() {
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
  }

  const submitWithPolicy = (value) => {
    stopPolling();
    if (policy) policy.value = value;
    approved = value === "REQUIRE_IB";
    if (value === "ALLOW_CACHE_FALLBACK") form.dataset.confirmed = "true";
    panel.hidden = true;
    form.requestSubmit();
  };

  form.addEventListener("submit", async (event) => {
    if (form.dataset.confirmed === "true") return;
    if (approved) {
      approved = false;
      return;
    }
    event.preventDefault();
    if (submitButton) submitButton.disabled = true;
    const status = await checkStatus({ showPanel: false });
    if (submitButton) submitButton.disabled = false;
    if (status?.status === "READY" && status.api_connected) {
      submitWithPolicy("REQUIRE_IB");
    } else {
      showUnavailable(status);
    }
  });

  retryButton?.addEventListener("click", () => checkStatus({ showPanel: true }));
  readyButton?.addEventListener("click", () => submitWithPolicy("REQUIRE_IB"));
  cacheButton?.addEventListener("click", () => submitWithPolicy("ALLOW_CACHE_FALLBACK"));
  cancelButton?.addEventListener("click", () => {
    stopPolling();
    panel.hidden = true;
    submitButton?.focus();
  });
  panel.addEventListener("click", (event) => {
    if (event.target === panel) cancelButton?.click();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) cancelButton?.click();
  });
  launchButton?.addEventListener("click", async () => {
    if (!launchUrl) return;
    launchButton.disabled = true;
    if (title) title.textContent = "Connecting to Interactive Brokers";
    if (detail) detail.textContent = "Starting the configured IB Gateway executable…";
    try {
      const response = await fetch(launchUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRF-Token": form.dataset.csrfToken || "",
        },
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result?.detail?.message || `HTTP ${response.status}`);
      if (detail) detail.textContent = result.message;
      if (!["STARTED", "ALREADY_RUNNING"].includes(result.status)) return;
      if (waiting) waiting.hidden = false;
      if (cacheButton) cacheButton.hidden = true;
      stopPolling();
      pollTimer = window.setTimeout(poll, 1000);
    } catch (error) {
      if (detail) detail.textContent = error.message || "IB Gateway launch failed.";
    } finally {
      launchButton.disabled = false;
    }
  });

  checkStatus();
}

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
      row.addEventListener("click", (event) => {
        if (event.defaultPrevented || shouldIgnoreRowNavigation(event)) return;
        if (row.dataset.href) window.location.href = row.dataset.href;
      });

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

function shouldIgnoreRowNavigation(event) {
  return Boolean(
    event.target.closest(
      "[data-no-row-nav], a, button, input, select, textarea, label, summary",
    ),
  );
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
  if (quickFilters.has("hide-earnings-blocked") && row.dataset.earningsRisk === "blocked") return false;
  if (
    quickFilters.has("earnings-risk")
    && !["blocked", "high", "medium", "unknown"].includes(row.dataset.earningsRisk)
  ) {
    return false;
  }
  if (quickFilters.has("earnings-clear") && row.dataset.earningsRisk !== "clear") return false;
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
    sortButton.closest("th").setAttribute("aria-sort", "none");
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

function bindCoverageTables() {
  document.querySelectorAll("[data-coverage-page]").forEach((section) => {
    const rows = Array.from(section.querySelectorAll("[data-coverage-row]"));
    const filters = Array.from(section.querySelectorAll("[data-coverage-filter]"));
    const empty = section.querySelector("[data-coverage-empty]");
    const feedback = section.querySelector("[data-copy-feedback]");

    const applyFilters = () => {
      const activeStatuses = new Set(
        filters.filter((filter) => filter.checked).map((filter) => filter.value),
      );
      let visibleCount = 0;
      rows.forEach((row) => {
        const visible = activeStatuses.size === 0 || activeStatuses.has(row.dataset.coverageStatus);
        row.hidden = !visible;
        if (visible) visibleCount += 1;
      });
      if (empty) empty.hidden = visibleCount !== 0;
    };

    filters.forEach((filter) => filter.addEventListener("change", applyFilters));

    const clearButton = section.querySelector("[data-coverage-clear]");
    if (clearButton) {
      clearButton.addEventListener("click", () => {
        filters.forEach((filter) => {
          filter.checked = false;
        });
        applyFilters();
      });
    }

    section.querySelectorAll("[data-copy-coverage]").forEach((button) => {
      button.addEventListener("click", () => {
        const mode = button.dataset.copyCoverage;
        const tickers = rows
          .filter((row) => {
            if (mode === "visible") return !row.hidden;
            if (mode === "not-ready") return row.dataset.coverageStatus !== "ready";
            return false;
          })
          .map((row) => row.dataset.ticker);
        copyTickers(tickers, feedback);
      });
    });
  });
}

function bindFetchProgressPolling() {
  const root = document.querySelector("[data-fetch-progress]");
  if (!root) return;

  const statusUrl = root.dataset.statusUrl;
  const terminalStatuses = new Set((root.dataset.terminalStatuses || "").split(","));
  if (!statusUrl) return;

  let failureCount = 0;
  const poll = async () => {
    try {
      const response = await fetch(statusUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      failureCount = 0;
      setReconnectState(root, "[data-progress-reconnect]", false);
      updateFetchProgress(root, data);
      if (!terminalStatuses.has(data.status)) {
        window.setTimeout(poll, 3000);
      }
    } catch (_error) {
      failureCount += 1;
      if (failureCount >= 2) setReconnectState(root, "[data-progress-reconnect]", true);
      window.setTimeout(poll, 5000);
    }
  };

  if (!terminalStatuses.has(textOf(root, "[data-progress-status]"))) {
    window.setTimeout(poll, 1000);
  }
}

function updateFetchProgress(root, data) {
  setText(root, "[data-progress-status]", data.status);
  setText(root, "[data-progress-started]", data.started_at || "");
  setText(root, "[data-progress-completed]", data.completed_at || "");
  setText(root, "[data-progress-current]", data.current_ticker || "");
  setText(root, "[data-progress-percentage]", `${Number(data.percentage || 0).toFixed(1)}%`);
  setText(root, "[data-progress-completed-items]", data.completed_items);
  setText(root, "[data-progress-total-items]", data.total_items);
  setText(root, "[data-progress-planned]", data.planned_request_count);
  setText(root, "[data-progress-executed]", data.executed_request_count);
  setText(root, "[data-progress-inserted]", data.inserted_count);
  setText(root, "[data-progress-updated]", data.updated_count);
  setText(root, "[data-progress-revised]", data.revised_count);
  setText(root, "[data-progress-unchanged]", data.unchanged_count);
  setText(root, "[data-progress-failures]", data.failure_count);
  setText(root, "[data-progress-skipped]", data.skipped_count);

  const message = root.querySelector("[data-progress-message]");
  if (message) {
    message.textContent = data.message || "";
    message.hidden = !data.message;
  }

  const fill = root.querySelector("[data-progress-fill]");
  const percentage = Math.min(Number(data.percentage || 0), 100);
  if (fill) fill.style.width = `${percentage}%`;
  const bar = root.querySelector("[data-progress-bar]");
  if (bar) {
    bar.setAttribute("aria-valuenow", String(Math.round(percentage)));
    bar.setAttribute(
      "aria-valuetext",
      `${data.completed_items || 0} of ${data.total_items || 0} fetch items complete`,
    );
  }
  setText(
    root,
    "[data-progress-live]",
    `${data.status}: ${data.completed_items || 0} of ${data.total_items || 0} items complete.`,
  );

  updateFetchItemRows(data.items || []);
}

function updateFetchItemRows(items) {
  items.forEach((item) => {
    const selector = `[data-fetch-item-row][data-ticker="${cssEscape(item.ticker)}"][data-what-to-show="${cssEscape(item.what_to_show)}"]`;
    const row = document.querySelector(selector);
    if (!row) return;
    setText(row, "[data-item-status]", item.status);
    setText(row, "[data-item-action]", item.action || "");
    setText(row, "[data-item-fetched]", item.fetched);
    setText(row, "[data-item-inserted]", item.inserted);
    setText(row, "[data-item-updated]", item.updated);
    setText(row, "[data-item-revised]", item.revised);
    setText(row, "[data-item-unchanged]", item.unchanged);
    setText(row, "[data-item-attempts]", item.attempt_count);
    setText(row, "[data-item-error]", item.error_message || "");
  });
}

function bindPipelineProgressPolling() {
  const root = document.querySelector("[data-pipeline-progress]");
  if (!root) return;

  const statusUrl = root.dataset.statusUrl;
  const terminalStatuses = new Set((root.dataset.terminalStatuses || "").split(","));
  if (!statusUrl) return;

  let failureCount = 0;
  const poll = async () => {
    try {
      const response = await fetch(statusUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      failureCount = 0;
      setReconnectState(root, "[data-pipeline-reconnect]", false);
      updatePipelineProgress(root, data);
      if (!terminalStatuses.has(data.status)) {
        window.setTimeout(poll, 3000);
      }
    } catch (_error) {
      failureCount += 1;
      if (failureCount >= 2) setReconnectState(root, "[data-pipeline-reconnect]", true);
      window.setTimeout(poll, 5000);
    }
  };

  if (!terminalStatuses.has(textOf(root, "[data-pipeline-status]"))) {
    window.setTimeout(poll, 1000);
  }
}

function updatePipelineProgress(root, data) {
  const current = data.current_step_label || "";
  const message = data.error_message || data.message || "";

  setText(root, "[data-pipeline-status]", data.status);
  setText(root, "[data-pipeline-status-metric]", data.status);
  setText(root, "[data-pipeline-created]", data.created_at || "");
  setText(root, "[data-pipeline-started]", data.started_at || "");
  setText(root, "[data-pipeline-completed]", data.completed_at || "");
  setText(root, "[data-pipeline-current]", current);
  setText(root, "[data-pipeline-current-metric]", current || "None");
  setText(root, "[data-pipeline-job-status]", data.job_status || "");
  setText(root, "[data-pipeline-job-metric]", data.job_status || "None");
  setText(root, "[data-pipeline-cancel-metric]", data.job_cancel_requested ? "Requested" : "No");
  const result = data.result || {};
  setText(root, "[data-pipeline-ranking-status]", result.ranking_status || "Pending");
  setText(root, "[data-pipeline-ranking-profiles]", result.ranking_profiles || 0);
  setText(root, "[data-pipeline-ranking-results]", result.ranking_results || 0);
  setText(root, "[data-pipeline-market-policy]", result.market_data_policy || "REQUIRE_IB");
  setText(root, "[data-pipeline-market-mode]", result.market_data_mode || "Pending");
  setText(
    root,
    "[data-pipeline-ib-available]",
    result.ib_api_available_at_execution === null || result.ib_api_available_at_execution === undefined
      ? "Pending"
      : String(result.ib_api_available_at_execution),
  );
  setText(root, "[data-pipeline-fresh-fetches]", result.fresh_fetch_count || 0);
  setText(root, "[data-pipeline-cache-used]", result.cache_used_count || 0);
  setText(root, "[data-pipeline-expected-session]", result.latest_expected_market_session || "Unknown");
  setText(root, "[data-pipeline-actual-session]", result.actual_latest_data_session || "Unknown");
  const degraded = root.querySelector("[data-pipeline-degraded]");
  if (degraded) {
    degraded.hidden = !result.degraded;
    if (result.degraded) {
      degraded.textContent =
        "Degraded run: cached market data was explicitly allowed. Winner Evidence capture is skipped and technical confidence is capped low for this pipeline.";
    }
  }
  setText(root, "[data-pipeline-percentage]", `${Number(data.percentage || 0).toFixed(1)}%`);
  setText(root, "[data-pipeline-completed-steps]", data.completed_steps);
  setText(root, "[data-pipeline-total-steps]", data.total_steps);

  const messageElement = root.querySelector("[data-pipeline-message]");
  if (messageElement) {
    messageElement.textContent = message;
    messageElement.hidden = !message;
  }

  const fill = root.querySelector("[data-pipeline-fill]");
  const percentage = Math.min(Number(data.percentage || 0), 100);
  if (fill) fill.style.width = `${percentage}%`;
  const bar = root.querySelector("[data-pipeline-bar]");
  if (bar) {
    bar.setAttribute("aria-valuenow", String(Math.round(percentage)));
    bar.setAttribute(
      "aria-valuetext",
      `${data.completed_steps || 0} of ${data.total_steps || 0} pipeline steps complete`,
    );
  }
  setText(
    root,
    "[data-pipeline-live]",
    `${data.status}: ${data.completed_steps || 0} of ${data.total_steps || 0} steps complete.`,
  );

  updatePipelineStepRows(data.steps || []);
}

function updatePipelineStepRows(steps) {
  steps.forEach((step) => {
    const selector = `[data-pipeline-step-row][data-step-name="${cssEscape(step.step_name)}"]`;
    const row = document.querySelector(selector);
    if (!row) return;
    setText(row, "[data-step-status]", step.status);
    setText(row, "[data-step-started]", step.started_at || "");
    setText(row, "[data-step-completed]", step.completed_at || "");
    setText(row, "[data-step-message]", step.message || "");
    setText(row, "[data-step-error]", step.error_message || "");
  });
}

function setText(root, selector, value) {
  const element = root.querySelector(selector);
  if (element) element.textContent = value;
}

function textOf(root, selector) {
  const element = root.querySelector(selector);
  return element ? element.textContent.trim() : "";
}

function cssEscape(value) {
  if (window.CSS && window.CSS.escape) return window.CSS.escape(String(value));
  return String(value).replace(/"/g, '\\"');
}

function setReconnectState(root, selector, visible) {
  const element = root.querySelector(selector);
  if (element) element.hidden = !visible;
}

function bindConfirmActions() {
  const panel = buildConfirmPanel();
  let pendingForm = null;

  document.querySelectorAll("[data-confirm]").forEach((element) => {
    element.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      if (element.dataset.confirmed === "true") {
        delete element.dataset.confirmed;
        return;
      }
      event.preventDefault();
      pendingForm = element;
      showConfirmPanel(panel, element);
    });
  });

  panel.querySelector("[data-confirm-cancel]").addEventListener("click", () => {
    pendingForm = null;
    hideConfirmPanel(panel);
  });
  panel.querySelector("[data-confirm-continue]").addEventListener("click", () => {
    if (!pendingForm) return;
    pendingForm.dataset.confirmed = "true";
    hideConfirmPanel(panel);
    pendingForm.requestSubmit();
    pendingForm = null;
  });
  panel.addEventListener("click", (event) => {
    if (event.target === panel) {
      pendingForm = null;
      hideConfirmPanel(panel);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      pendingForm = null;
      hideConfirmPanel(panel);
    }
  });
}

function buildConfirmPanel() {
  let panel = document.querySelector("[data-confirm-panel]");
  if (panel) return panel;
  panel = document.createElement("div");
  panel.className = "confirm-panel";
  panel.hidden = true;
  panel.setAttribute("data-confirm-panel", "");
  panel.innerHTML = `
    <div class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-message">
      <h2 id="confirm-title">Confirm Action</h2>
      <p id="confirm-message" data-confirm-message></p>
      <p class="muted-inline">If matching work is already queued or running, SwingLens will reuse the active job where that workflow supports coalescing.</p>
      <div class="actions">
        <button class="secondary" type="button" data-confirm-cancel>Cancel</button>
        <button type="button" data-confirm-continue>Continue</button>
      </div>
    </div>
  `;
  document.body.append(panel);
  return panel;
}

function showConfirmPanel(panel, form) {
  const message = panel.querySelector("[data-confirm-message]");
  if (message) message.textContent = form.dataset.confirm || "Continue with this action?";
  panel.hidden = false;
  const continueButton = panel.querySelector("[data-confirm-continue]");
  if (continueButton) continueButton.focus();
}

function hideConfirmPanel(panel) {
  panel.hidden = true;
}

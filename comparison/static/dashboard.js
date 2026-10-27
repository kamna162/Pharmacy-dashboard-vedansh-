"use strict";

const COLORS = {
  navy: "#102f49",
  teal: "#168f88",
  green: "#277a59",
  red: "#c94f4b",
  blue: "#4b86c5",
  orange: "#e4953f",
  grid: "#e9eff3",
  muted: "#4a5d6e",
};
const PERIODS = ["1-10 Days", "11-20 Days", "21-31 Days"];
const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const DASHBOARD_HOSPITALS = ["Surya Hospital", "NIMMS Hospital", "Vedansh Hospital"];
const COMPARISON_SERIES_KEYS = ["first", "second", "third"];
const DASHBOARD_CATEGORY_MAX_ROWS = 15;
const DASHBOARD_CATEGORY_ROW_HEIGHT = 28;
const $ = (selector) => document.querySelector(selector);
const state = {
  options: null,
  dashboard: null,
  toastTimer: null,
  chartFrame: 0,
  charts: [],
  comparisonMoverMetric: "ipdRevenue",
  comparisonMoverDimension: "departments",
  comparisonView: "numbers",
  comparisonLoading: false,
  appliedRange: null,
  rangeHospital: null,
};

function money(value) {
  const amount = Number(value || 0);
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

function compactMoney(value) {
  const amount = Number(value || 0);
  if (Math.abs(amount) >= 10000000) return `₹${(amount / 10000000).toFixed(1)}Cr`;
  if (Math.abs(amount) >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  if (Math.abs(amount) >= 1000) return `₹${(amount / 1000).toFixed(0)}k`;
  return `₹${amount.toFixed(0)}`;
}

function targetMoney(value) {
  const amount = Number(value || 0);
  if (Math.abs(amount) >= 10000000) {
    return `₹${(amount / 10000000).toFixed(2)}Cr`;
  }
  return compactMoney(amount);
}

function number(value) {
  return Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function dateText(value) {
  if (!value) return "Not available";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("is-error", isError);
  element.classList.add("is-visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => element.classList.remove("is-visible"), 3600);
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    throw new Error(body && body.error ? body.error : `Request failed (${response.status}).`);
  }
  return body;
}

function addOptions(select, values, { placeholder = null, selected = null } = {}) {
  select.replaceChildren();
  if (placeholder !== null) {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = placeholder;
    select.append(empty);
  }
  const selectedValue = selected == null ? null : String(selected);
  values.forEach((entry) => {
    const option = document.createElement("option");
    const optionValue = String(typeof entry === "object" ? entry.value : entry);
    option.value = optionValue;
    option.textContent = String(typeof entry === "object" ? entry.label : entry);
    option.selected = Array.isArray(selected)
      ? selected.map(String).includes(optionValue)
      : selectedValue !== null && selectedValue === optionValue;
    select.append(option);
  });
  if (select.multiple) renderMultiSelect(select);
}

function multiSelectWidget(select) {
  return $(`#${select.id.replace("-select", "-filter-widget")}`);
}

function renderMultiSelect(select) {
  const widget = multiSelectWidget(select);
  const optionsContainer = widget.querySelector(".multi-select-options");
  optionsContainer.replaceChildren();
  [...select.options].filter((option) => option.value).forEach((option, index) => {
    const label = document.createElement("label");
    label.className = "multi-select-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = option.value;
    checkbox.checked = option.selected;
    checkbox.id = `${select.id}-option-${index}`;
    const text = document.createElement("span");
    text.textContent = option.textContent;
    label.append(checkbox, text);
    optionsContainer.append(label);
  });
  updateMultiSelect(select);
}

function updateMultiSelect(select) {
  const widget = multiSelectWidget(select);
  const selected = new Set(selectedValues(select));
  widget.querySelectorAll(".multi-select-option input").forEach((checkbox) => {
    checkbox.checked = selected.has(checkbox.value);
  });
  const count = selected.size;
  widget.querySelector(`#${select.id.replace("-select", "")}-filter-count`).textContent =
    `${count} selected`;
  const selectAllButton = widget.querySelector('[data-multiselect-action="toggle-all"]');
  selectAllButton.textContent = count === select.options.length && count > 0
    ? "Unselect all"
    : "Select all";
}

function bindMultiSelect(selectId) {
  const select = $(`#${selectId}`);
  const widget = multiSelectWidget(select);
  const trigger = widget.querySelector(".multi-select-trigger");
  const menu = widget.querySelector(".multi-select-menu");

  trigger.addEventListener("click", () => {
    const opening = menu.hidden;
    document.querySelectorAll(".multi-select-menu").forEach((otherMenu) => {
      otherMenu.hidden = true;
      otherMenu.previousElementSibling.setAttribute("aria-expanded", "false");
    });
    menu.hidden = !opening;
    trigger.setAttribute("aria-expanded", String(opening));
  });

  widget.addEventListener("change", (event) => {
    const checkbox = event.target.closest(".multi-select-option input");
    if (!checkbox) return;
    const option = [...select.options].find((item) => item.value === checkbox.value);
    if (option) option.selected = checkbox.checked;
    updateMultiSelect(select);
  });

  widget.querySelector('[data-multiselect-action="toggle-all"]').addEventListener("click", () => {
    const selectAll = selectedValues(select).length !== select.options.length;
    [...select.options].forEach((option) => {
      option.selected = selectAll && Boolean(option.value);
    });
    updateMultiSelect(select);
  });

  widget.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || menu.hidden) return;
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    trigger.focus();
  });
}

function selectedValues(select) {
  return [...select.selectedOptions].map((option) => option.value).filter(Boolean);
}

function selectedFilters() {
  return {
    hospital: $("#hospital-select").value,
    month: [],
    period: [],
    range: state.appliedRange,
  };
}

function setView(name) {
  document.querySelectorAll(".view-tab").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === name);
    button.setAttribute("aria-pressed", String(button.dataset.view === name));
  });
  $("#overview-view").classList.toggle("is-hidden", name !== "overview");
  $("#comparison-view").classList.toggle("is-hidden", name !== "comparison");
  updateHeaderDateRange();
  if (name === "overview") scheduleCharts();
  if (name === "comparison" && state.comparisonData && !state.comparisonData.isZeroState) {
    scheduleComparisonChart();
  }
  if (name === "comparison"
    && (!state.comparisonData || state.comparisonData.isZeroState)
    && !state.comparisonLoading) {
    loadComparison();
  }
  if (name === "comparison" && !state.comparisonOptionsLoaded) {
    loadComparisonOptions();
  }
}

function updatePrintContext() {
  const context = $("#print-context");
  if (!context) return;
  const activeView = document.querySelector(".view-tab.is-active")?.dataset.view || "overview";
  const lines = [];
  const addLine = (label, value) => {
    if (value) lines.push(`${label}: ${value}`);
  };
  if (activeView === "overview") {
    addLine("Dashboard", "Overview");
    const dashboard = state.dashboard;
    addLine("Hospital", dashboard?.dataset?.hospital_name || $("#hospital-select")?.value);
    if (dashboard?.selectedRange) {
      addLine(
        "Date range",
        `${dateText(dashboard.selectedRange.start)} – ${dateText(dashboard.selectedRange.end)}`,
      );
    }
    addLine("Month filter", dashboard?.filters?.month?.join(", ") || "All months");
    addLine("Period filter", dashboard?.filters?.period?.join(", ") || "All periods");
  } else {
    const mode = $("#comparison-view-mode")?.value;
    if (mode === "prediction") {
      const prediction = window.__PREDICTION_REPORT_DATA__;
      addLine("Dashboard", "Prediction");
      addLine("Hospital", prediction?.hospital || $("#prediction-hospital")?.value);
      addLine("Prediction", prediction?.description
        || $("#prediction-mode")?.selectedOptions[0]?.textContent);
      addLine("Forecast horizon", prediction
        ? `${prediction.horizon} ${prediction.unit}`
        : $("#prediction-horizon")?.selectedOptions[0]?.textContent);
    } else {
      const comparison = state.comparisonData;
      addLine("Dashboard", "Comparison");
      addLine("Hospital", $("#comparison-hospital")?.value);
      addLine("Compared periods", comparison?.labels?.join(" vs ")
        || [...document.querySelectorAll("#comparison-dynamic-fields [name]")]
          .map((input) => input.selectedOptions?.[0]?.textContent || input.value)
          .join(" vs "));
      addLine("Display", mode === "charts" ? "Charts" : "Numbers");
    }
  }
  context.textContent = lines.join(" · ");
}

async function downloadInteractiveHtml() {
  const button = $("#download-interactive");
  const status = $("#download-interactive-status");
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = "Preparing HTML…";
  status.textContent = "Preparing the offline dashboard. This may take a few minutes.";
  try {
    const response = await fetch("/api/export/interactive.html");
    if (!response.ok) {
      const contentType = response.headers.get("content-type") || "";
      const error = contentType.includes("application/json")
        ? await response.json()
        : { error: await response.text() };
      throw new Error(error.error || `Download failed (${response.status}).`);
    }
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "Vedansh_Medicare_Interactive_Dashboard.html";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 60000);
    status.textContent = "Interactive HTML downloaded.";
  } catch (error) {
    status.textContent = "";
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Download";
  }
}

async function initialize() {
  try {
    const options = await request("/api/options");
    state.options = options;
    startDevelopmentReload();
    $("#upload-management").open = false;
    const hospitalOptions = DASHBOARD_HOSPITALS;
    addOptions($("#hospital-select"), ["All Hospitals", ...hospitalOptions], {
      selected: "All Hospitals",
    });
    addOptions($("#upload-hospital-select"), DASHBOARD_HOSPITALS, {
      selected: "NIMMS Hospital",
    });
    addOptions($("#comparison-hospital"), ["All Hospitals", ...hospitalOptions], {
      selected: "All Hospitals",
    });
    $("#date-range").textContent = options.dateRange
      ? `${dateText(options.dateRange.start)} – ${dateText(options.dateRange.end)}`
      : "No reporting data";
    state.comparisonOptionsLoaded = Array.isArray(options.comparison?.years);
    createComparisonFields();
    bindEvents();
    const initialLoads = [loadUploads()];
    if (options.datasets.length) initialLoads.push(loadDashboard());
    await Promise.all(initialLoads);
    if (!options.datasets.length) renderNoData();
  } catch (error) {
    toast(error.message, true);
  }
}

function bindEvents() {
  $("#hospital-select").addEventListener("change", async () => {
    state.appliedRange = null;
    state.rangeHospital = null;
    $("#range-type").value = "Full Range";
    updateRangeControlVisibility();
    if ($("#hospital-select").value !== "All Hospitals") {
      $("#upload-hospital-select").value = $("#hospital-select").value;
    }
    await loadUploads();
    await loadDashboard();
  });
  $("#upload-hospital-select").addEventListener("change", loadUploads);
  $("#upload-management").addEventListener("toggle", () => {
    if ($("#upload-management").open) {
      loadScraperStatus();
    }
  });
  document.addEventListener("click", (event) => {
    const managementPanel = $("#upload-management");
    if (managementPanel.open && !managementPanel.contains(event.target)) {
      managementPanel.open = false;
    }
  });
  $("#scraper-import").addEventListener("click", importScraperReports);
  $("#range-type").addEventListener("change", updateRangeControlVisibility);
  $("#range-month-year-select").addEventListener("change", () => {
    updateMonthSelect(
      $("#range-month-year-select"),
      $("#range-month-select"),
      state.dashboard?.dateOptions?.monthsByYear || [],
    );
  });
  $("#range-period-year-select").addEventListener("change", () => {
    updateMonthSelect(
      $("#range-period-year-select"),
      $("#range-period-month-select"),
      state.dashboard?.dateOptions?.monthsByYear || [],
    );
  });
  $("#apply-range").addEventListener("click", applyDateRange);
  $("#clear-filters").addEventListener("click", clearDashboardFilters);
  $("#refresh-data").addEventListener("click", () => refreshOptions(
    $("#hospital-select").value,
  ));
  $("#export-button").addEventListener("click", exportCsv);
  $("#print-button").addEventListener("click", () => {
    updatePrintContext();
    window.print();
  });
  window.addEventListener("beforeprint", updatePrintContext);
  $("#download-interactive").addEventListener("click", downloadInteractiveHtml);
  $("#upload-files").addEventListener("change", uploadReports);
  $("#upload-list").addEventListener("click", manageReportUpload);
  document.querySelectorAll(".view-tab").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  $("#comparison-mode").addEventListener("change", () => {
    createComparisonFields();
    renderComparison(zeroComparisonData());
  });
  $("#comparison-hospital").addEventListener("change", () => {
    renderComparison(zeroComparisonData());
  });
  $("#apply-comparison").addEventListener("click", loadComparison);
  $("#comparison-view-mode").addEventListener("change", (event) => {
    setComparisonView(event.target.value);
  });
  $("#reset-comparison").addEventListener("click", resetComparison);
  $("#comparison-content").addEventListener("click", (event) => {
    const moverTab = event.target.closest("[data-mover-dimension]");
    if (moverTab) {
      state.comparisonMoverDimension = moverTab.dataset.moverDimension;
      $("#comparison-content").querySelectorAll("[data-mover-dimension]").forEach((tab) => {
        tab.setAttribute("aria-selected", String(tab === moverTab));
      });
      if (state.comparisonData) renderComparisonMovers(state.comparisonData);
      return;
    }
    const category = event.target.closest("[data-comparison-category]");
    if (category) showComparisonChartValues(category);
  });
  $("#comparison-content").addEventListener("change", (event) => {
    if (!event.target.matches('input[name="comparison-mover-metric"]')) return;
    state.comparisonMoverMetric = event.target.value;
    if (state.comparisonData) renderComparisonMovers(state.comparisonData);
  });
  const comparisonTipSelector = "[data-comparison-category], [data-comparison-tip]";
  const comparisonTipTarget = (event) => event.target.closest?.(comparisonTipSelector);
  const comparisonContent = $("#comparison-content");
  comparisonContent.addEventListener("mouseover", (event) => {
    const target = comparisonTipTarget(event);
    if (target) showComparisonTipFor(target, event);
  });
  comparisonContent.addEventListener("mousemove", (event) => {
    if (comparisonTipTarget(event)) positionComparisonTip(event);
  });
  comparisonContent.addEventListener("mouseout", (event) => {
    const target = comparisonTipTarget(event);
    if (target && !target.contains(event.relatedTarget)) hideComparisonTip();
  });
  comparisonContent.addEventListener("click", (event) => {
    const target = comparisonTipTarget(event);
    if (target) showComparisonTipFor(target, event);
  });
  comparisonContent.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && event.target.matches(comparisonTipSelector)) {
      event.preventDefault();
      const box = event.target.getBoundingClientRect();
      showComparisonTipFor(event.target, { clientX: box.left + box.width / 2, clientY: box.bottom });
    }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest?.(comparisonTipSelector)) hideComparisonTip();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideComparisonTip();
  });
  if ("ResizeObserver" in window) {
    const chartResizeObserver = new ResizeObserver(() => {
      scheduleCharts();
      scheduleComparisonChart();
      window.dispatchEvent(new Event("dashboard-chart-resize"));
    });
    document.querySelectorAll(".chart-wrap").forEach((chart) => {
      chartResizeObserver.observe(chart);
    });
  }
  window.addEventListener("resize", scheduleCharts);
}

function updateRangeControlVisibility() {
  const type = $("#range-type").value;
  $("#range-controls").dataset.rangeType = type;
  const custom = type === "Custom From-To";
  $("#range-year-field").hidden = type !== "Year";
  $("#range-month-year-field").hidden = type !== "Month";
  $("#range-month-field").hidden = type !== "Month";
  $("#range-period-year-field").hidden = type !== "Period";
  $("#range-period-month-field").hidden = type !== "Period";
  $("#range-period-field").hidden = type !== "Period";
  $("#range-anchor-field").hidden = !["Week", "Day"].includes(type);
  $("#range-from-field").hidden = !custom;
  $("#range-to-field").hidden = !custom;
  const hasData = state.dashboard?.hasData;
  const hasBounds = Boolean(
    state.dashboard?.dateRange?.start && state.dashboard?.dateRange?.end,
  );
  $("#apply-range").disabled = !hasData || (type !== "Full Range" && !hasBounds);
}

function updateMonthSelect(yearSelect, monthSelect, monthsByYear, selectedMonth) {
  const year = Number(yearSelect.value);
  const available = monthsByYear.find((entry) => entry.year === year)?.months || [];
  addOptions(monthSelect, available, {
    selected: available.some((entry) => String(entry.value) === String(selectedMonth))
      ? String(selectedMonth)
      : available[0]?.value,
  });
}

function updateRangeDateOptions(dateOptions, saved = null) {
  const options = dateOptions || { years: [], monthsByYear: [] };
  const years = (options.years || []).map((year) => Number(year)).filter(Number.isFinite);
  [
    "#range-year-select",
    "#range-month-year-select",
    "#range-period-year-select",
  ].forEach((selector) => {
    const select = $(selector);
    const requestedYear = selector === "#range-year-select"
      ? saved?.year
      : selector === "#range-month-year-select"
        ? saved?.monthYear
        : saved?.periodYear;
    const selected = years.includes(Number(requestedYear))
      ? Number(requestedYear)
      : years.length
        ? years[years.length - 1]
        : null;
    addOptions(select, years, { selected });
    select.disabled = years.length === 0;
  });
  updateMonthSelect(
    $("#range-month-year-select"),
    $("#range-month-select"),
    options.monthsByYear || [],
    saved?.month,
  );
  updateMonthSelect(
    $("#range-period-year-select"),
    $("#range-period-month-select"),
    options.monthsByYear || [],
    saved?.periodMonth,
  );
  $("#range-period-select").disabled = years.length === 0;
  addOptions($("#range-period-select"), PERIODS, {
    selected: PERIODS.includes(saved?.period) ? saved.period : PERIODS[0],
  });
}

function setRangeInputBounds(dateRange) {
  const minDate = dateRange?.start || "";
  const maxDate = dateRange?.end || "";
  const disabled = !minDate || !maxDate;
  ["#range-anchor", "#range-from", "#range-to"].forEach((selector) => {
    const input = $(selector);
    input.min = minDate;
    input.max = maxDate;
    input.disabled = disabled;
  });
}

function initializeRangeControls(data) {
  const hospital = data.dataset?.hospital_name;
  if (!hospital) return false;
  setRangeInputBounds(data.dateRange);
  if (state.rangeHospital === hospital) {
    updateRangeDateOptions(data.dateOptions, {
      year: $("#range-year-select").value,
      monthYear: $("#range-month-year-select").value,
      month: $("#range-month-select").value,
      periodYear: $("#range-period-year-select").value,
      periodMonth: $("#range-period-month-select").value,
      period: $("#range-period-select").value,
    });
    return false;
  }
  state.rangeHospital = hospital;
  const minDate = data.dateRange?.start;
  const maxDate = data.dateRange?.end;
  state.appliedRange = null;
  $("#range-type").value = "Full Range";
  updateRangeDateOptions(data.dateOptions);
  $("#range-anchor").value = maxDate || "";
  $("#range-from").value = minDate || "";
  $("#range-to").value = maxDate || "";
  updateRangeControlVisibility();
  return false;
}

function clampDate(value, minDate, maxDate) {
  return value < minDate ? minDate : value > maxDate ? maxDate : value;
}

function addDays(dateValue, days) {
  const date = new Date(`${dateValue}T00:00:00`);
  date.setDate(date.getDate() + days);
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function calculateDateRange(type, bounds) {
  const { start: minDate, end: maxDate } = bounds;
  if (type === "Custom From-To") {
    const start = $("#range-from").value;
    const end = $("#range-to").value;
    if (!start || !end || start < minDate || end > maxDate || start > end) {
      throw new Error("Choose a valid custom range within the available dates.");
    }
    return { start, end };
  }
  if (type === "Year") {
    const year = $("#range-year-select").value;
    if (!year) throw new Error("Choose a year with available data.");
    return {
      start: clampDate(`${year}-01-01`, minDate, maxDate),
      end: clampDate(`${year}-12-31`, minDate, maxDate),
    };
  }
  if (type === "Month") {
    const year = $("#range-month-year-select").value;
    const month = $("#range-month-select").value;
    if (!year || !month) throw new Error("Choose a month with available data.");
    const monthText = String(month).padStart(2, "0");
    const monthStart = `${year}-${monthText}-01`;
    const lastDay = new Date(
      Number(year), Number(month), 0,
    ).getDate();
    const monthEnd = `${year}-${monthText}-${String(lastDay).padStart(2, "0")}`;
    return {
      start: clampDate(monthStart, minDate, maxDate),
      end: clampDate(monthEnd, minDate, maxDate),
    };
  }
  if (type === "Period") {
    const year = $("#range-period-year-select").value;
    const month = $("#range-period-month-select").value;
    const period = $("#range-period-select").value;
    if (!year || !month || !period) {
      throw new Error("Choose a month and period with available data.");
    }
    const monthText = String(month).padStart(2, "0");
    const lastDay = new Date(Number(year), Number(month), 0).getDate();
    const firstDay = period === PERIODS[0] ? 1 : period === PERIODS[1] ? 11 : 21;
    const finalDay = period === PERIODS[0] ? 10 : period === PERIODS[1] ? 20 : lastDay;
    return {
      start: clampDate(`${year}-${monthText}-${String(firstDay).padStart(2, "0")}`, minDate, maxDate),
      end: clampDate(`${year}-${monthText}-${String(finalDay).padStart(2, "0")}`, minDate, maxDate),
    };
  }
  const anchor = $("#range-anchor").value;
  if (!anchor || anchor < minDate || anchor > maxDate) {
    throw new Error("Choose a date within the available data range.");
  }
  if (type === "Week") {
    const weekday = new Date(`${anchor}T00:00:00`).getDay();
    const mondayOffset = (weekday + 6) % 7;
    return {
      start: clampDate(addDays(anchor, -mondayOffset), minDate, maxDate),
      end: clampDate(addDays(anchor, 6 - mondayOffset), minDate, maxDate),
    };
  }
  return { start: anchor, end: anchor };
}

async function applyDateRange() {
  const hospital = $("#hospital-select").value;
  const bounds = state.dashboard?.dateRange;
  const type = $("#range-type").value;
  if (!hospital || (type !== "Full Range" && (!bounds?.start || !bounds?.end))) {
    toast("There are no hospital dates available to select.", true);
    return;
  }
  try {
    const range = type === "Full Range" ? null : calculateDateRange(type, bounds);
    state.appliedRange = range;
    await loadDashboard();
  } catch (error) {
    toast(error.message, true);
  }
}

async function clearDashboardFilters() {
  $("#range-type").value = "Full Range";
  state.appliedRange = null;
  const bounds = state.dashboard?.dateRange;
  $("#range-from").value = bounds?.start || "";
  $("#range-to").value = bounds?.end || "";
  $("#range-anchor").value = bounds?.end || "";
  updateRangeControlVisibility();
  await loadDashboard();
}

function renderUploadList(uploads) {
  const list = $("#upload-list");
  list.replaceChildren();
  if (!uploads.length) {
    const empty = document.createElement("p");
    empty.className = "upload-list-empty";
    empty.textContent = "No raw reports uploaded for this hospital.";
    list.append(empty);
    return;
  }
  uploads.forEach((upload) => {
    const row = document.createElement("div");
    row.className = "upload-list-row";
    const description = document.createElement("span");
    description.className = "upload-list-description";
    description.textContent = `${upload.file_name} · ${upload.kind} · ${number(upload.rows_used)} rows used · ${upload.included ? "included" : "excluded"}`;
    const actions = document.createElement("span");
    actions.className = "upload-list-actions";
    const toggle = document.createElement("button");
    toggle.className = "button button-secondary";
    toggle.type = "button";
    toggle.dataset.action = "toggle";
    toggle.dataset.uploadId = upload.upload_id;
    toggle.dataset.included = String(upload.included);
    toggle.textContent = upload.included ? "Exclude" : "Include";
    const remove = document.createElement("button");
    remove.className = "button button-secondary";
    remove.type = "button";
    remove.dataset.action = "delete";
    remove.dataset.uploadId = upload.upload_id;
    remove.textContent = "Delete";
    actions.append(toggle, remove);
    row.append(description, actions);
    list.append(row);
  });
}

async function loadUploads() {
  const hospital = $("#upload-hospital-select").value;
  if (!hospital) {
    renderUploadList([]);
    $("#upload-summary-count").textContent = "0 reports";
    return;
  }
  try {
    const result = await request(`/api/uploads?hospitalName=${encodeURIComponent(hospital)}`);
    renderUploadList(result.uploads || []);
    $("#upload-summary-count").textContent =
      `${number((result.uploads || []).length)} reports`;
  } catch (error) {
    toast(error.message, true);
  }
}

function renderScraperStatus(status) {
  const message = $("#scraper-status");
  const list = $("#scraper-report-list");
  const importButton = $("#scraper-import");
  list.replaceChildren();
  if (!status.folderExists) {
    message.textContent = `Scraper folder not found: ${status.folder}`;
  } else if (!status.ready) {
    const lastImport = status.lastImport?.imported_at
      ? ` Last successful import: ${dateText(status.lastImport.imported_at)}.`
      : "";
    message.textContent = status.reportFileCount
      ? `${number(status.reportFileCount)} report file(s) found, but a complete Admission, Discharge, and Service batch is not ready.`
      : `No unprocessed scraper reports found.${lastImport}`;
  } else {
    message.textContent = `${number(status.reportFileCount)} report file(s) found. Latest complete batch: ${dateText(status.candidateDate)}. ${number(status.pendingFileCount)} report(s) are new.`;
    status.candidateFiles.forEach((file) => {
      const row = document.createElement("p");
      row.className = "scraper-report-row";
      row.textContent = `${file.fileName} · ${file.imported ? "already imported" : "pending"}`;
      list.append(row);
    });
  }
  const archive = status.archive || {};
  $("#scraper-archive-status").textContent = archive.days
    ? `${number(archive.rows)} older rows archived across ${number(archive.days)} day(s)${archive.from && archive.to ? ` · ${dateText(archive.from)} to ${dateText(archive.to)}` : ""}.`
    : "Only the latest scraper batch is kept; no older scraper data is archived.";
  importButton.disabled = !status.ready || !status.needsImport;
  importButton.textContent = status.ready && !status.needsImport
    ? "Up to date"
    : "Import reports";
}

async function loadScraperStatus() {
  try {
    const status = await request("/api/scraper/status");
    renderScraperStatus(status);
  } catch (error) {
    $("#scraper-status").textContent = `Could not check scraper reports: ${error.message}`;
    $("#scraper-import").disabled = true;
    toast(error.message, true);
  }
}

async function importScraperReports() {
  const button = $("#scraper-import");
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = "Importing…";
  $("#scraper-status").textContent = "Importing scraper reports and preparing the archive…";
  let importComplete = false;
  try {
    const response = await request("/api/scraper/import", { method: "POST" });
    const result = response.result;
    importComplete = true;
    toast(
      `Imported ${number(result.active_rows)} latest-batch rows and removed ${number(result.rawReportsPruned)} previous scraper report(s).`,
    );
    await refreshOptions("NIMMS Hospital");
    await loadScraperStatus();
  } catch (error) {
    $("#scraper-status").textContent = importComplete
      ? `Reports imported, but dashboard refresh failed: ${error.message}`
      : `Import failed: ${error.message}`;
    toast(error.message, true);
    button.disabled = importComplete;
    button.textContent = importComplete ? "Imported" : "Import reports";
  }
}

async function manageReportUpload(event) {
  const button = event.target.closest("button[data-upload-id]");
  if (!button || button.disabled) return;
  const uploadId = button.dataset.uploadId;
  const row = button.closest(".upload-list-row");
  const rowButtons = [...row.querySelectorAll("button")];
  const status = $("#upload-status");
  const originalText = button.textContent;
  rowButtons.forEach((actionButton) => { actionButton.disabled = true; });
  if (button.dataset.action === "delete") {
    button.textContent = "Deleting…";
    status.textContent = "Deleting report and updating dashboard…";
  } else {
    status.textContent = "Updating report and dashboard…";
  }
  try {
    if (button.dataset.action === "delete") {
      if (!window.confirm("Permanently delete this uploaded report?")) return;
      await request("/api/uploads/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uploadId }),
      });
      row.remove();
      const remaining = document.querySelectorAll("#upload-list .upload-list-row").length;
      if (!remaining) renderUploadList([]);
      $("#upload-summary-count").textContent = `${number(remaining)} reports`;
      toast("Report deleted and hospital data rebuilt.");
    } else {
      await request("/api/uploads/include", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          uploadId,
          included: button.dataset.included !== "true",
        }),
      });
      toast("Report inclusion updated and hospital data rebuilt.");
    }
    await refreshOptions($("#hospital-select").value);
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button.isConnected) {
      rowButtons.forEach((actionButton) => { actionButton.disabled = false; });
      button.textContent = originalText;
    }
    status.textContent = "";
  }
}

async function uploadReports() {
  const input = $("#upload-files");
  if (input.disabled) return;
  const files = [...input.files];
  if (!files.length) return;
  if (files.some((file) => file.size > 25 * 1024 * 1024)) {
    toast("Each selected file must be no larger than 25 MB.", true);
    input.value = "";
    return;
  }
  if (files.reduce((total, file) => total + file.size, 0) > 79 * 1024 * 1024) {
    toast("The selected files exceed the 80 MB total request limit.", true);
    input.value = "";
    return;
  }
  const hospitalName = $("#upload-hospital-select").value;
  if (!hospitalName) {
    toast("Choose a hospital before uploading.", true);
    return;
  }
  const form = new FormData();
  files.forEach((file) => form.append("file", file));
  form.append("hospitalName", hospitalName);
  const hospitalSelect = $("#upload-hospital-select");
  const status = $("#upload-status");
  input.disabled = true;
  hospitalSelect.disabled = true;
  status.textContent = `Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`;
  try {
    const response = await request("/api/upload", { method: "POST", body: form });
    toast(response.upload.inserted
      ? `Saved ${number(response.upload.inserted)} records for ${hospitalName}.`
      : "The selected reports were already saved.");
    status.textContent = "Updating dashboard data…";
    await refreshOptions(hospitalName);
  } catch (error) {
    toast(error.message, true);
  } finally {
    input.disabled = false;
    hospitalSelect.disabled = false;
    input.value = "";
    status.textContent = "";
  }
}

async function loadDashboard() {
  const filters = selectedFilters();
  if (!filters.hospital) {
    renderNoData();
    return;
  }
  $("#apply-range").disabled = true;
  try {
    const query = new URLSearchParams({ hospital: filters.hospital });
    if (filters.range?.start && filters.range?.end) {
      query.set("startDate", filters.range.start);
      query.set("endDate", filters.range.end);
    }
    filters.month.forEach((month) => query.append("month", month));
    filters.period.forEach((period) => query.append("period", period));
    const data = await request(`/api/dashboard?${query.toString()}`);
    const restoreRange = initializeRangeControls(data);
    state.dashboard = data;
    renderDashboard(data);
    if (restoreRange) await loadDashboard();
  } catch (error) {
    toast(error.message, true);
  } finally {
    updateRangeControlVisibility();
  }
}

function renderNoData() {
  $("#kpi-grid").replaceChildren();
  renderKpis({
    admissions: 0,
    discharges: 0,
    opd_consultation: 0,
    revenue: 0,
    ipd_revenue: 0,
    opd_revenue: 0,
    cash_due: 0,
    credit_due: 0,
    alos: 0,
    arpp: 0,
    per_day_patient_charge: 0,
    per_day_revenue: 0,
  });
  $("#date-range").textContent = "No saved hospital data";
  $("#no-data-in-range").hidden = !state.appliedRange;
  setRangeInputBounds(null);
}

function renderDashboard(data) {
  if (!data.hasData) {
    renderNoData();
    return;
  }
  updateRangeControlVisibility();
  $("#no-data-in-range").hidden = data.recordCount !== 0 || !state.appliedRange;
  updateHeaderDateRange();
  const yearly = data.charts.granularity === "yearly";
  [
    ["#revenue-chart", yearly ? "IPD revenue by year" : "IPD revenue trend"],
    ["#flow-chart", yearly ? "Patient flow by year" : "Patient flow"],
    ["#opd-trend-chart", yearly ? "OPD consultation trend by year" : "OPD consultation trend"],
  ].forEach(([selector, title]) => {
    $(selector).closest(".chart-card").querySelector(".chart-heading h3").textContent = title;
  });
  renderKpis(data.kpis);
  renderInsights(data.charts.insights, data.charts, data.kpis);
  drawDashboardCharts(data.charts);
}

function renderKpis(kpis = {}) {
  const showNumber = (value, options) =>
    typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString("en-IN", options)
      : "—";
  const showMoney = (value) =>
    typeof value === "number" && Number.isFinite(value) ? money(value) : "—";
  const exactNumber = (value) =>
    typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString("en-IN", { maximumFractionDigits: 8 })
      : "—";
  const exactMoney = (value) =>
    typeof value === "number" && Number.isFinite(value)
      ? `₹${value.toLocaleString("en-IN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`
      : "—";
  const descriptors = [
    ["Admissions", showNumber(kpis.admissions, { maximumFractionDigits: 0 }), COLORS.teal, kpis.admissions],
    ["Discharges", showNumber(kpis.discharges, { maximumFractionDigits: 0 }), COLORS.teal, kpis.discharges],
    ["OPD consultations", showNumber(kpis.opd_consultation, { maximumFractionDigits: 0 }), COLORS.teal, kpis.opd_consultation],
    ["Total revenue", showMoney(kpis.revenue), COLORS.teal, kpis.revenue, "currency"],
    ["IPD revenue", showMoney(kpis.ipd_revenue), COLORS.teal, kpis.ipd_revenue, "currency"],
    ["OPD revenue", showMoney(kpis.opd_revenue), COLORS.teal, kpis.opd_revenue, "currency"],
    ["Cash due", showMoney(kpis.cash_due), COLORS.teal, kpis.cash_due, "currency"],
    ["Credit due", showMoney(kpis.credit_due), COLORS.teal, kpis.credit_due, "currency"],
    ["Average length of stay", `${showNumber(kpis.alos, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${typeof kpis.alos === "number" && Number.isFinite(kpis.alos) ? " days" : ""}`, COLORS.teal, kpis.alos],
    ["ARPP", showMoney(kpis.arpp), COLORS.teal, kpis.arpp, "currency"],
    ["Per day per patient charge", showMoney(kpis.per_day_patient_charge), COLORS.teal, kpis.per_day_patient_charge, "currency"],
    ["Per day revenue", showMoney(kpis.per_day_revenue), COLORS.teal, kpis.per_day_revenue, "currency"],
  ];
  const grid = $("#kpi-grid");
  grid.replaceChildren();
  descriptors.forEach(([label, value, accent, actual, kind]) => {
    const card = document.createElement("article");
    card.className = "kpi-card";
    card.style.setProperty("--accent", accent);
    card.title = `Actual value: ${kind === "currency" ? exactMoney(actual) : exactNumber(actual)}`;
    const title = document.createElement("span");
    title.className = "kpi-label";
    title.textContent = label;
    const valueElement = document.createElement("div");
    valueElement.className = "kpi-value";
    valueElement.textContent = value;
    card.append(title, valueElement);
    grid.append(card);
  });
}

function insightShare(value, total) {
  return total > 0 ? `${(value / total * 100).toFixed(0)}%` : "—";
}

function insightLeader(rows, labelKey, valueKey, total) {
  const leader = (rows || [])
    .filter((row) => Number(row[valueKey]) > 0)
    .sort((left, right) => Number(right[valueKey]) - Number(left[valueKey]))[0];
  if (!leader) return "—";
  const value = Number(leader[valueKey]);
  return `${leader[labelKey] || leader.label || "Unknown"} · ${compactMoney(value)}`
    + ` (${insightShare(value, total)} of IPD revenue)`;
}

function legacyInsightItems(insights, charts, kpis) {
  const ipdRevenue = Number(kpis.ipd_revenue) || 0;
  const opdRevenue = Number(kpis.opd_revenue) || 0;
  const totalRevenue = ipdRevenue + opdRevenue;
  const trend = [...(charts.revenueTrend || [])]
    .sort((left, right) => Number(right.value) - Number(left.value))[0];
  const panels = charts.panels || [];
  const pending = Number(kpis.credit_due) || 0;
  const panelBilling = panels.reduce(
    (total, row) => total + Number(row["IPD Revenue"] || 0), 0,
  );
  const topPanel = [...panels].sort(
    (left, right) => Number(right["IPD Revenue"] || 0) - Number(left["IPD Revenue"] || 0),
  )[0];
  return [
    { icon: "🎯", label: "Days daily target was met", value: "Unavailable — refresh dashboard data" },
    {
      icon: "🏠",
      label: "Best earning day",
      value: trend ? `${trend.label} · ${compactMoney(trend.value)}` : "—",
    },
    {
      icon: "📀",
      label: "Revenue from IPD vs OPD",
      value: totalRevenue
        ? `IPD ${insightShare(ipdRevenue, totalRevenue)} · OPD ${insightShare(opdRevenue, totalRevenue)}`
        : "—",
    },
    {
      icon: "🏥",
      label: "Top earning department",
      value: insightLeader(charts.ipdRevenueDepartments, "label", "value", ipdRevenue),
    },
    {
      icon: "🩺",
      label: "Top earning doctor",
      value: insightLeader(charts.ipdRevenueDoctors, "label", "value", ipdRevenue),
    },
    {
      icon: "🏷️",
      label: "Top paying panel",
      value: topPanel
        ? `${topPanel.Panel || "Unknown"} · ${compactMoney(topPanel["IPD Revenue"])}`
          + ` (${insightShare(Number(topPanel["IPD Revenue"]), ipdRevenue)} of IPD revenue)`
        : insights.panel || "—",
    },
    {
      icon: "💳",
      label: "Payment still pending",
      value: `${money(pending)}${panelBilling ? ` (${insightShare(pending, panelBilling)} of IPD billing)` : ""}`,
    },
    { icon: "🧾", label: "OPD gross amount", value: money(kpis.opd_gross_amount) },
    { icon: "✅", label: "OPD final amount (received)", value: money(opdRevenue) },
    { icon: "🏷️", label: "OPD discount / concession", value: money(kpis.opd_discount) },
  ];
}

function renderInsights(insights = {}, charts = {}, kpis = {}) {
  const grid = $("#insights-grid");
  grid.replaceChildren();
  const items = Array.isArray(insights.items) && insights.items.length
    ? insights.items
    : legacyInsightItems(insights, charts, kpis);
  items.forEach((item) => {
    const row = document.createElement("article");
    row.className = "insight-row";
    const icon = document.createElement("span");
    icon.className = "insight-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = item.icon || "";
    const label = document.createElement("span");
    label.className = "insight-label";
    label.textContent = item.label;
    const value = document.createElement("strong");
    value.className = "insight-value";
    value.textContent = item.value || "—";
    row.append(icon, label, value);
    grid.append(row);
  });
}

function emptyRow(text, columns) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.className = "empty-row";
  cell.colSpan = columns;
  cell.textContent = text;
  row.append(cell);
  return row;
}

function appendCell(row, text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = String(text ?? "—");
  if (className) cell.className = className;
  row.append(cell);
  return cell;
}

function drawDashboardCharts(charts) {
  const totals = charts.totals || {};
  const horizontalCharts = [
    {
      canvas: $("#department-chart"),
      rows: compactDashboardChartRows(charts.dischargeDepartments),
      options: { color: COLORS.teal, horizontal: true, total: totals.dischargeDepartments },
    },
    {
      canvas: $("#discharge-doctor-chart"),
      rows: compactDashboardChartRows(charts.dischargeDoctors),
      options: { color: COLORS.teal, horizontal: true, total: totals.dischargeDoctors },
    },
    {
      canvas: $("#ipd-department-chart"),
      rows: compactDashboardChartRows(charts.ipdRevenueDepartments),
      options: {
        color: COLORS.teal, horizontal: true, formatter: compactMoney,
        total: totals.ipdRevenueDepartments,
      },
    },
    {
      canvas: $("#ipd-doctor-chart"),
      rows: compactDashboardChartRows(charts.ipdRevenueDoctors),
      options: {
        color: COLORS.teal, horizontal: true, formatter: compactMoney,
        total: totals.ipdRevenueDoctors,
      },
    },
    {
      canvas: $("#admission-department-chart"),
      rows: compactDashboardChartRows(charts.admissionDepartments),
      options: { color: COLORS.teal, horizontal: true, total: totals.admissionDepartments },
    },
    {
      canvas: $("#admission-doctor-chart"),
      rows: compactDashboardChartRows(charts.admissionDoctors),
      options: { color: COLORS.teal, horizontal: true, total: totals.admissionDoctors },
    },
    {
      canvas: $("#opd-doctor-chart"),
      rows: compactDashboardChartRows(charts.opdDoctors),
      options: { color: COLORS.teal, horizontal: true, total: totals.opdDoctors },
    },
    {
      canvas: $("#services-chart"),
      rows: compactDashboardChartRows(charts.opdServices),
      options: { color: COLORS.teal, horizontal: true, total: totals.opdServices },
    },
  ];
  const sharedChartHeight = Math.max(
    18 + DASHBOARD_CATEGORY_MAX_ROWS * DASHBOARD_CATEGORY_ROW_HEIGHT,
    ...horizontalCharts.map(({ canvas, rows, options }) =>
      horizontalBarLayout(canvas, rows, options).height),
  );
  horizontalCharts.forEach(({ canvas, rows, options }) => {
    drawBars(canvas, rows, { ...options, layoutHeight: sharedChartHeight });
  });
  ["#revenue-chart", "#flow-chart", "#opd-trend-chart"].forEach((selector) => {
    $(selector).style.width = "";
    $(selector).style.height = "";
  });
  drawBars($("#revenue-chart"), charts.revenueTrend, {
    color: COLORS.teal, formatter: compactMoney, mode: "line",
    showAllValueLabels: true,
    showPercentLabels: false,
    showAverage: true,
    total: totals.revenueTrend,
  });
  drawGroupedBars($("#flow-chart"), charts.patientFlow, [
    { key: "Admissions", label: "Admissions", color: COLORS.teal },
    { key: "Discharges", label: "Discharges", color: COLORS.blue },
  ], totals.patientFlow, {
    showAllValueLabels: true,
    showPercent: false,
    showAverage: true,
    averageTogether: true,
  });
  drawBars($("#opd-trend-chart"), charts.opdDaily, {
    color: COLORS.teal, mode: "line",
    showAllValueLabels: true,
    showPercentLabels: false,
    showAverage: true,
    total: totals.opdDaily,
  });
  drawTargetProgress(totals.targetProgress);
  drawPanelPerformanceChart(charts.panels);
}

function compactDashboardChartRows(rows) {
  const categories = Array.isArray(rows) ? rows : [];
  if (categories.length <= DASHBOARD_CATEGORY_MAX_ROWS) return categories;

  const visible = categories.slice(0, DASHBOARD_CATEGORY_MAX_ROWS - 1);
  const hidden = categories.slice(DASHBOARD_CATEGORY_MAX_ROWS - 1);
  visible.push({
    label: "Others",
    value: hidden.reduce((total, row) => total + Number(row.value || 0), 0),
  });
  return visible;
}

function drawTargetProgress(totals = {}) {
  const actual = Number(totals.actual || 0);
  const target = Number(totals.target || 0);
  const monthlyTarget = Number(totals.monthlyTarget || 0);
  const remaining = Math.max(0, target - actual);
  const achievement = target > 0 ? actual / target * 100 : 0;
  const progress = Math.min(100, Math.max(0, achievement));
  const ring = $("#target-progress-ring");
  const circumference = 2 * Math.PI * 86;

  $("#target-progress-target").textContent = targetMoney(target);
  $("#target-progress-monthly").textContent =
    `Monthly target: ${targetMoney(monthlyTarget)}`;
  $("#target-progress-actual").textContent = compactMoney(actual);
  $("#target-progress-remaining").textContent = compactMoney(remaining);
  $("#target-progress-value").textContent = `${achievement.toFixed(1)}%`;
  $("#target-progress-arc").setAttribute(
    "stroke-dasharray", `${circumference * progress / 100} ${circumference}`,
  );
  ring.setAttribute("aria-valuenow", String(progress));
  ring.setAttribute("aria-valuetext", `${achievement.toFixed(1)}% of target`);
}

function drawPanelPerformanceChart(panels) {
  const rows = (panels || []).map((panel) => {
    const revenue = Number(panel["IPD Revenue"] || 0);
    const pending = Number(panel["Credit Due"] || 0);
    return {
      label: panel.Panel || "Unknown",
      revenue,
      collection: Math.max(0, revenue - pending),
      pending,
    };
  });
  const totals = rows.reduce((result, row) => ({
    revenue: result.revenue + row.revenue,
    collection: result.collection + row.collection,
    pending: result.pending + row.pending,
  }), { revenue: 0, collection: 0, pending: 0 });
  drawGroupedBars($("#panel-performance-chart"), rows, [
    { key: "revenue", label: "Revenue", color: COLORS.teal, formatter: compactMoney },
    { key: "collection", label: "Collection", color: COLORS.blue, formatter: compactMoney },
    { key: "pending", label: "Pending", color: COLORS.orange, formatter: compactMoney },
  ], totals, { showPercent: false });
}

function renderChartTotal(element, items) {
  const chartCard = element.closest(".chart-card");
  if (!chartCard) return;
  let footer = chartCard.querySelector(":scope > .chart-total");
  if (!footer) {
    footer = document.createElement("div");
    footer.className = "chart-total";
    element.after(footer);
  }
  renderTotalItems(footer, items);
}

function renderTotalItems(footer, items) {
  footer.replaceChildren();
  items.forEach((item) => {
    const total = document.createElement("span");
    total.className = `chart-total-item${item.compact ? " is-compact-average" : ""}`;
    if (item.title) total.title = item.title;
    const label = document.createElement("span");
    label.textContent = item.label;
    total.append(label);
    if (item.values) {
      const values = document.createElement("span");
      values.className = "chart-total-series-values";
      item.values.forEach((entry) => {
        const seriesValue = document.createElement("span");
        seriesValue.className = "chart-total-series-value";
        seriesValue.style.color = entry.color;
        seriesValue.title = entry.label;
        if (!item.hideSeriesLabels) {
          const seriesLabel = document.createElement("span");
          seriesLabel.textContent = entry.label;
          seriesValue.append(seriesLabel);
        }
        const value = document.createElement("strong");
        value.textContent = entry.formatter(entry.value);
        value.style.color = entry.color;
        seriesValue.append(value);
        values.append(seriesValue);
      });
      total.append(values);
      footer.append(total);
      return;
    }
    const value = document.createElement("strong");
    value.textContent = item.formatter(item.value);
    total.append(value);
    footer.append(total);
  });
}

function chartTotalItems(rows, total, formatter, showAverage = false) {
  const format = formatter || number;
  const value = total?.value ?? (rows || []).reduce(
    (sum, row) => sum + Number(row.value || 0), 0,
  );
  const items = [{
    label: "Grand total",
    value,
    formatter: format,
  }];
  if (showAverage) {
    items.push({
      label: "Average per displayed period",
      value: rows?.length ? value / rows.length : null,
      formatter: (average) => Number.isFinite(average) ? format(average) : "—",
    });
  }
  return items;
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width: rect.width, height: rect.height };
}

function drawValueLabel(ctx, text, x, y, color = COLORS.navy, align = "center") {
  ctx.font = "bold 10px Segoe UI, Arial";
  ctx.textAlign = align;
  ctx.textBaseline = "bottom";
  ctx.lineJoin = "round";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#fff";
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

function changeColor(current, previous) {
  if (current > previous) return COLORS.green;
  if (current < previous) return COLORS.red;
  return COLORS.navy;
}

function drawValueAndPercent(ctx, value, percentage, x, y, percentageColor = COLORS.navy) {
  drawValueLabel(ctx, percentage, x, y - 2, percentageColor);
  drawValueLabel(ctx, value, x, y + 7);
}

function percent(value, total) {
  return total > 0 ? Number(value || 0) / total * 100 : 0;
}

function percentLabel(value) {
  return `${(Number(value) || 0).toFixed(1)}%`;
}

function chartLabelPlan(rows, chartWidth) {
  const maxLabels = Math.max(1, Math.floor(chartWidth / 58));
  const interval = Math.max(1, Math.ceil(rows.length / maxLabels));
  const slotWidth = chartWidth / rows.length;
  const labelIndices = rows
    .map((_, index) => index)
    .filter((index) => index % interval === 0);
  const lastIndex = rows.length - 1;
  if (labelIndices[labelIndices.length - 1] !== lastIndex) {
    if ((lastIndex - labelIndices[labelIndices.length - 1]) * slotWidth < 58) {
      labelIndices.pop();
    }
    labelIndices.push(lastIndex);
  }
  const visibleLabels = new Set(labelIndices);
  const dailyLabels = rows.map((row) => String(row.label || "").match(/^(\d{2}) ([A-Za-z]{3})$/));
  const month = dailyLabels[0] && dailyLabels[0][2];
  const singleMonth = Boolean(month)
    && dailyLabels.every((match) => match && match[2] === month);
  return {
    valueInterval: Math.max(1, Math.ceil(46 / slotWidth)),
    showAxisLabel(index) {
      return visibleLabels.has(index);
    },
    label(index) {
      const text = String(rows[index].label || "");
      if (singleMonth) return dailyLabels[index][1];
      if (PERIODS.includes(text)) return text;
      return text.length > 9 ? text.slice(0, 8) : text;
    },
  };
}

function drawBars(canvas, rows, options = {}) {
  const horizontalLayout = options.horizontal
    ? horizontalBarLayout(canvas, rows || [], options)
    : null;
  if (horizontalLayout) {
    canvas.parentElement.style.height = `${horizontalLayout.height}px`;
  }
  const { context: ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  if (!rows || !rows.length || width < 30 || height < 40) {
    drawEmpty(ctx, width, height);
    attachChartTooltip(canvas, []);
    renderChartTotal(
      canvas.parentElement,
      chartTotalItems(rows, options.total, options.formatter, options.showAverage),
    );
    return;
  }
  if (options.horizontal) {
    drawHorizontalBars(canvas, ctx, width, height, rows, options, horizontalLayout);
    renderChartTotal(
      canvas.parentElement,
      chartTotalItems(rows, options.total, options.formatter, options.showAverage),
    );
    return;
  }
  const margin = {
    top: options.showAllValueLabels ? 62 : 34,
    right: 15,
    bottom: 38,
    left: 50,
  };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const values = rows.map((row) => Number(row.value || 0));
  const max = Math.max(...values, 1);
  const chartTotal = Number(options.total?.value)
    || values.reduce((sum, value) => sum + value, 0);
  const step = chartWidth / rows.length;
  const labelPlan = chartLabelPlan(rows, chartWidth);
  const valueInterval = options.showAllValueLabels ? 1 : labelPlan.valueInterval;
  const barWidth = Math.min(34, step * .62);
  const hitboxes = [];
  ctx.font = "11px Segoe UI, Arial";
  ctx.textBaseline = "middle";
  for (let tick = 0; tick <= 3; tick += 1) {
    const y = margin.top + chartHeight - (chartHeight * tick / 3);
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(margin.left, y); ctx.lineTo(width - margin.right, y); ctx.stroke();
    ctx.fillStyle = COLORS.muted;
    ctx.textAlign = "right";
    ctx.fillText((options.formatter || number)(max * tick / 3), margin.left - 7, y);
  }
  rows.forEach((row, index) => {
    const value = Number(row.value || 0);
    const barHeight = Math.max(1, chartHeight * value / max);
    const x = margin.left + step * index + (step - barWidth) / 2;
    const y = margin.top + chartHeight - barHeight;
    const color = options.color || COLORS.teal;
    if (options.mode === "line") {
      const centerX = x + barWidth / 2;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      if (index > 0) {
        const priorValue = Number(rows[index - 1].value || 0);
        const priorX = margin.left + step * (index - 1) + step / 2;
        const priorY = margin.top + chartHeight - chartHeight * priorValue / max;
        ctx.beginPath(); ctx.moveTo(priorX, priorY); ctx.lineTo(centerX, y); ctx.stroke();
      }
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(centerX, y, 3.2, 0, Math.PI * 2); ctx.fill();
      if (index % valueInterval === 0) {
        const previousValue = index > 0 ? Number(rows[index - 1].value || 0) : value;
        if (options.showPercentLabels === false) {
          drawValueLabel(
            ctx,
            (options.formatter || number)(value),
            centerX,
            y - 7 - (index % 3) * 12,
            changeColor(value, previousValue),
          );
        } else {
          drawValueAndPercent(
            ctx,
            (options.formatter || number)(value),
            percentLabel(percent(value, chartTotal)),
            centerX,
            y - 9,
            changeColor(value, previousValue),
          );
        }
      }
      hitboxes.push({
        x: centerX - 9, y: y - 9, width: 18, height: 18,
        label: row.label, value, formatter: options.formatter || number,
        percentage: percent(value, chartTotal),
      });
    } else {
      ctx.fillStyle = color;
      roundedRect(ctx, x, y, barWidth, barHeight, 4);
      if (index % valueInterval === 0) {
        drawValueAndPercent(
          ctx,
          (options.formatter || number)(value),
          percentLabel(percent(value, chartTotal)),
          x + barWidth / 2,
          y - 9,
        );
      }
      hitboxes.push({
        x, y, width: barWidth, height: barHeight,
        label: row.label, value, formatter: options.formatter || number,
        percentage: percent(value, chartTotal),
      });
    }
    ctx.fillStyle = COLORS.muted;
    ctx.textAlign = "center";
    if (options.showAllAxisLabels || labelPlan.showAxisLabel(index)) {
      ctx.font = PERIODS.includes(String(row.label || ""))
        ? "bold 10px Segoe UI, Arial"
        : "10px Segoe UI, Arial";
      if (PERIODS.includes(String(row.label || ""))) ctx.fillStyle = "#2c4458";
      ctx.fillText(labelPlan.label(index), x + barWidth / 2, height - 17);
    }
  });
  attachChartTooltip(canvas, hitboxes);
  renderChartTotal(
    canvas.parentElement,
    chartTotalItems(rows, options.total, options.formatter, options.showAverage),
  );
}

function wrapHorizontalLabel(ctx, text, maxWidth) {
  const words = String(text || "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return [""];
  const lines = [];
  let line = "";
  words.forEach((word) => {
    let remaining = word;
    while (remaining) {
      const candidate = line ? `${line} ${remaining}` : remaining;
      if (ctx.measureText(candidate).width <= maxWidth) {
        line = candidate;
        remaining = "";
      } else if (line) {
        lines.push(line);
        line = "";
      } else {
        let splitAt = remaining.length;
        while (splitAt > 1 && ctx.measureText(remaining.slice(0, splitAt)).width > maxWidth) {
          splitAt -= 1;
        }
        lines.push(remaining.slice(0, splitAt));
        remaining = remaining.slice(splitAt);
      }
    }
  });
  if (line) lines.push(line);
  return lines;
}

function horizontalBarLayout(canvas, rows, options) {
  const width = canvas.parentElement.clientWidth;
  const ctx = canvas.getContext("2d");
  const formatter = options.formatter || number;
  ctx.font = "bold 11px Segoe UI, Arial";
  const valueWidth = Math.max(
    0,
    ...rows.map((row) => ctx.measureText(formatter(row.value)).width),
  );
  const margin = {
    top: 8,
    right: Math.max(58, Math.ceil(valueWidth) + 16),
    bottom: 10,
    left: Math.min(190, Math.max(90, width * .34)),
  };
  ctx.font = "11px Segoe UI, Arial";
  const labels = rows.map((row) =>
    wrapHorizontalLabel(ctx, row.label, Math.max(34, margin.left - 14)),
  );
  const rowHeights = labels.map((lines) => Math.max(28, lines.length * 13 + 8));
  const naturalHeight = Math.max(158, Math.ceil(
    margin.top + margin.bottom + rowHeights.reduce((sum, item) => sum + item, 0),
  ));
  const height = Math.max(naturalHeight, Number(options.layoutHeight) || 0);
  const extraRowHeight = rowHeights.length
    ? Math.max(0, height - margin.top - margin.bottom
      - rowHeights.reduce((sum, item) => sum + item, 0)) / rowHeights.length
    : 0;
  return {
    margin,
    labels,
    rowHeights: rowHeights.map((rowHeight) => rowHeight + extraRowHeight),
    height,
  };
}

function drawHorizontalBars(canvas, ctx, width, height, rows, options, layout) {
  const list = rows;
  const { margin, labels, rowHeights } = layout;
  const available = Math.max(16, width - margin.left - margin.right);
  const max = Math.max(...list.map((row) => Number(row.value || 0)), 1);
  const chartTotal = Number(options.total?.value)
    || rows.reduce((sum, row) => sum + Number(row.value || 0), 0);
  const hitboxes = [];
  let rowTop = margin.top;
  list.forEach((row, index) => {
    const rowHeight = rowHeights[index];
    const barHeight = Math.min(17, rowHeight * .54);
    const y = rowTop + (rowHeight - barHeight) / 2;
    const barWidth = Math.max(2, available * Number(row.value || 0) / max);
    ctx.fillStyle = "#4a5d6e";
    ctx.font = "11px Segoe UI, Arial";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    const labelCenter = rowTop + rowHeight / 2;
    labels[index].forEach((line, lineIndex) => {
      const lineOffset = (lineIndex - (labels[index].length - 1) / 2) * 13;
      ctx.fillText(line, margin.left - 9, labelCenter + lineOffset);
    });
    ctx.fillStyle = "#f0f4f6";
    roundedRect(ctx, margin.left, y, available, barHeight, 4);
    ctx.fillStyle = options.color || COLORS.teal;
    roundedRect(ctx, margin.left, y, Math.min(available, barWidth), barHeight, 4);
    ctx.fillStyle = COLORS.navy;
    ctx.font = "bold 11px Segoe UI, Arial";
    ctx.textAlign = "left";
    const labelX = margin.left + Math.min(available, barWidth) + 6;
    if (options.showPercent === false) {
      ctx.fillText((options.formatter || number)(row.value), labelX, y + barHeight / 2);
    } else {
      drawValueLabel(
        ctx, percentLabel(percent(row.value, chartTotal)), labelX,
        y + barHeight / 2 - 1, COLORS.navy, "left",
      );
      drawValueLabel(
        ctx, (options.formatter || number)(row.value), labelX,
        y + barHeight / 2 + 8, COLORS.navy, "left",
      );
    }
    hitboxes.push({
      x: margin.left - 3, y: y - 2, width: available + 8, height: barHeight + 4,
      label: row.label, value: row.value, formatter: options.formatter || number,
      ...(options.showPercent === false
        ? {}
        : { percentage: percent(row.value, chartTotal) }),
    });
    rowTop += rowHeight;
  });
  attachChartTooltip(canvas, hitboxes);
}

function drawGroupedBars(canvas, rows, series, totals = {}, options = {}) {
  const { context: ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  if (!rows || !rows.length || width < 30 || height < 40) {
    drawEmpty(ctx, width, height);
    attachChartTooltip(canvas, []);
    renderChartTotal(
      canvas.parentElement,
      groupedChartTotalItems(series, totals, [], options),
    );
    return;
  }
  const margin = {
    top: Math.max(options.topMargin || 0, 34 + (series.length - 1) * 22),
    right: 12,
    bottom: 37,
    left: 43,
  };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const max = Math.max(...rows.flatMap((row) => series.map((item) => Number(row[item.key] || 0))), 1);
  const seriesTotals = Object.fromEntries(series.map((item) => [
    item.key,
    Number(totals[item.key]) || rows.reduce(
      (sum, row) => sum + Number(row[item.key] || 0), 0,
    ),
  ]));
  const step = chartWidth / rows.length;
  const labelPlan = chartLabelPlan(rows, chartWidth);
  const valueInterval = options.showAllValueLabels ? 1 : labelPlan.valueInterval;
  const widthEach = Math.min(19, step * .29);
  const hitboxes = [];
  ctx.font = "10px Segoe UI, Arial";
  ctx.textBaseline = "middle";
  const tickFormatter = series[0].formatter || number;
  for (let tick = 0; tick <= 3; tick += 1) {
    const y = margin.top + chartHeight - chartHeight * tick / 3;
    ctx.strokeStyle = COLORS.grid;
    ctx.beginPath(); ctx.moveTo(margin.left, y); ctx.lineTo(width - margin.right, y); ctx.stroke();
    ctx.fillStyle = COLORS.muted;
    ctx.textAlign = "right";
    ctx.fillText(tickFormatter(max * tick / 3), margin.left - 5, y);
  }
  rows.forEach((row, index) => {
    const groupStart = margin.left + index * step + step / 2 - (series.length * widthEach + (series.length - 1) * 3) / 2;
    series.forEach((item, seriesIndex) => {
      const value = Number(row[item.key] || 0);
      const barHeight = chartHeight * value / max;
      const x = groupStart + seriesIndex * (widthEach + 3);
      const y = margin.top + chartHeight - barHeight;
      ctx.fillStyle = item.color;
      roundedRect(ctx, x, y, widthEach, Math.max(1, barHeight), 3);
      const percentage = options.showPercent === false
        ? null
        : item.percentage
          ? item.percentage(row, value)
          : percent(value, seriesTotals[item.key]);
      if (index % valueInterval === 0) {
        if (percentage === null) {
          drawValueLabel(
            ctx,
            (item.formatter || number)(value),
            x + widthEach / 2,
            y - 4 - (options.showAllValueLabels ? seriesIndex * 12 : 0),
          );
        } else {
          drawValueAndPercent(
            ctx,
            (item.formatter || number)(value),
            percentLabel(percentage),
            x + widthEach / 2,
            y - 10 - seriesIndex * 22,
          );
        }
      }
      hitboxes.push({
        x, y, width: widthEach, height: Math.max(1, barHeight),
        label: `${row.label} · ${item.label || item.key}`,
        value,
        formatter: item.formatter || number,
        ...(percentage === null ? {} : { percentage }),
      });
    });
    ctx.fillStyle = COLORS.muted;
    ctx.textAlign = "center";
    if (options.showAllAxisLabels || labelPlan.showAxisLabel(index)) {
      ctx.font = PERIODS.includes(String(row.label || ""))
        ? "bold 10px Segoe UI, Arial"
        : "10px Segoe UI, Arial";
      if (PERIODS.includes(String(row.label || ""))) ctx.fillStyle = "#2c4458";
      ctx.fillText(labelPlan.label(index), margin.left + step * index + step / 2, height - 15);
    }
  });
  attachChartTooltip(canvas, hitboxes);
  renderChartTotal(
    canvas.parentElement,
    groupedChartTotalItems(series, totals, rows, options),
  );
}

function groupedChartTotalItems(series, totals, rows, options) {
  const seriesTotals = series.map((item) => {
    const formatter = item.formatter || number;
    const value = totals[item.key] ?? rows.reduce(
      (sum, row) => sum + Number(row[item.key] || 0), 0,
    );
    return {
      key: item.key,
      label: item.label || item.key,
      color: item.color,
      value,
      formatter,
    };
  });
  const items = seriesTotals.map((item) => ({
    label: `Grand total · ${item.label}`,
    value: item.value,
    formatter: item.formatter,
  }));
  if (options.averageTogether) {
    items.push({
      label: options.averageTogether ? "Avg / period" : "Average per displayed period",
      compact: Boolean(options.averageTogether),
      hideSeriesLabels: Boolean(options.averageTogether),
      title: options.averageTogether
        ? "Average per displayed period · Admissions, then Discharges"
        : undefined,
      values: seriesTotals.map((item) => ({
        label: item.label,
        color: item.color,
        value: rows.length ? item.value / rows.length : null,
        formatter: (value) => Number.isFinite(value) ? item.formatter(value) : "—",
      })),
    });
  } else if (options.showAverage) {
    seriesTotals.forEach((item) => {
      items.push({
        label: `Average per displayed period · ${item.label}`,
        value: rows.length ? item.value / rows.length : null,
        formatter: (value) => Number.isFinite(value) ? item.formatter(value) : "—",
      });
    });
  }
  return items;
}

function attachChartTooltip(canvas, hitboxes) {
  const wrapper = canvas.parentElement;
  let tooltip = wrapper.querySelector(".chart-tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    wrapper.append(tooltip);
  }
  canvas.onpointermove = (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const point = hitboxes.find((item) =>
      x >= item.x - 4 && x <= item.x + item.width + 4
      && y >= item.y - 4 && y <= item.y + item.height + 4
    );
    if (!point) {
      tooltip.classList.remove("is-visible");
      return;
    }
    tooltip.textContent = `${point.label}: ${point.formatter(point.value)}`
      + (Number.isFinite(point.percentage) ? ` · ${percentLabel(point.percentage)} of total` : "");
    tooltip.classList.add("is-visible");
    const maxLeft = Math.max(4, wrapper.clientWidth - tooltip.offsetWidth - 5);
    tooltip.style.left = `${Math.max(4, Math.min(maxLeft, x + 11))}px`;
    tooltip.style.top = `${Math.max(4, y - tooltip.offsetHeight - 9)}px`;
  };
  canvas.onpointerleave = () => tooltip.classList.remove("is-visible");
}

function roundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
  ctx.fill();
}

function drawEmpty(ctx, width, height) {
  ctx.fillStyle = COLORS.muted;
  ctx.font = "12px Segoe UI, Arial";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("No chart data for these filters", width / 2, height / 2);
}

function scheduleCharts() {
  if (!state.dashboard || state.chartFrame) return;
  state.chartFrame = window.requestAnimationFrame(() => {
    state.chartFrame = 0;
    drawDashboardCharts(state.dashboard.charts);
  });
}

function comparisonFiltersFromForm() {
  const mode = $("#comparison-mode").value;
  const filters = { compareBy: mode };
  document.querySelectorAll("#comparison-dynamic-fields [name]").forEach((input) => {
    filters[input.name] = input.value;
  });
  if (mode === "Period") filters.period = "All Periods";
  return filters;
}

function comparisonDateRangeFilters(filters) {
  const monthCount = Number(filters.monthsCount);
  const endYear = Number(filters.endYear);
  const endMonth = MONTH_NAMES.indexOf(filters.endMonth) + 1;
  const toIso = (date) => date.toISOString().slice(0, 10);
  const recentStart = new Date(Date.UTC(endYear, endMonth - monthCount, 1));
  const recentEnd = new Date(Date.UTC(endYear, endMonth, 0));
  const previousEnd = new Date(recentStart.getTime() - 86400000);
  const previousStart = new Date(Date.UTC(
    previousEnd.getUTCFullYear(),
    previousEnd.getUTCMonth() - monthCount + 1,
    1,
  ));
  const label = (date) => `${MONTH_NAMES[date.getUTCMonth()].slice(0, 3)} ${date.getUTCFullYear()}`;
  return {
    filters: {
      compareBy: "From-To",
      start1: toIso(previousStart),
      end1: toIso(previousEnd),
      start2: toIso(recentStart),
      end2: toIso(recentEnd),
    },
    labels: [
      `${label(previousStart)} – ${label(previousEnd)}`,
      `${label(recentStart)} – ${label(recentEnd)}`,
    ],
  };
}

function relabelComparisonPeriods(value, previousLabels, labels) {
  const replaceLabels = (text) => previousLabels.reduce(
    (result, previous, index) => result.replaceAll(previous, labels[index]),
    text,
  );
  if (Array.isArray(value)) {
    return value.map((item) => relabelComparisonPeriods(item, previousLabels, labels));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [
      replaceLabels(key),
      relabelComparisonPeriods(item, previousLabels, labels),
    ]));
  }
  if (typeof value === "string") return replaceLabels(value);
  return value;
}

async function exportCsv() {
  const activeView = document.querySelector(".view-tab.is-active")?.dataset.view || "overview";
  if (activeView === "comparison") {
    const filters = comparisonFiltersFromForm();
    if (Object.values(filters).some((value) => value === "")) {
      toast("Choose a value for each comparison filter before exporting.", true);
      return;
    }
    const query = new URLSearchParams({
      hospital: $("#comparison-hospital").value || "All Hospitals",
      filters: JSON.stringify(filters),
    });
    window.location.assign(`/api/comparison/export.csv?${query.toString()}`);
    return;
  }

  const filters = selectedFilters();
  if (!filters.hospital) {
    toast("Choose a hospital before exporting.", true);
    return;
  }
  const query = new URLSearchParams({ hospital: filters.hospital });
  if (filters.range?.start && filters.range?.end) {
    query.set("startDate", filters.range.start);
    query.set("endDate", filters.range.end);
  }
  if (filters.month.length) query.set("month", filters.month.join(","));
  if (filters.period.length) query.set("period", filters.period.join(","));
  window.location.assign(`/api/export.csv?${query.toString()}`);
}

async function refreshOptions(selectedHospital = null) {
  state.appliedRange = null;
  state.rangeHospital = null;
  $("#range-type").value = "Full Range";
  updateRangeControlVisibility();
  const options = await request("/api/options");
  state.options = options;
  const previousHospital = $("#hospital-select").value;
  const hospitalOptions = DASHBOARD_HOSPITALS;
  const hospitalChoices = ["All Hospitals", ...hospitalOptions];
  const hospital = selectedHospital && hospitalChoices.includes(selectedHospital)
    ? selectedHospital
    : hospitalChoices.includes(previousHospital)
      ? previousHospital
      : "All Hospitals";
  addOptions($("#hospital-select"), hospitalChoices, { selected: hospital });
  if (hospital !== previousHospital) {
    state.appliedRange = null;
    state.rangeHospital = null;
  }
  const currentUploadHospital = $("#upload-hospital-select").value;
  const uploadHospital = hospital !== "All Hospitals"
    ? hospital
    : DASHBOARD_HOSPITALS.includes(currentUploadHospital)
      ? currentUploadHospital
      : "NIMMS Hospital";
  addOptions($("#upload-hospital-select"), DASHBOARD_HOSPITALS, {
    selected: uploadHospital,
  });
  addOptions($("#comparison-hospital"), ["All Hospitals", ...hospitalOptions], {
    selected: $("#comparison-hospital").value || "All Hospitals",
  });
  if (Array.isArray(options.comparison?.years)) {
    state.comparisonOptionsLoaded = true;
  } else if (state.comparisonOptionsLoaded) {
    state.options.comparison = await request("/api/comparison/options");
  }
  createComparisonFields();
  await Promise.all([loadUploads(), loadDashboard()]);
}

function createComparisonFields() {
  const mode = $("#comparison-mode").value;
  const grid = $(".comparison-filter-grid");
  if (grid) grid.dataset.mode = mode;
  const holder = $("#comparison-dynamic-fields");
  holder.replaceChildren();
  const options = state.options?.comparison || { years: [], months: [] };
  const years = options.years || [];
  const months = MONTH_NAMES;
  const yearOne = years.length ? years[0] : new Date().getFullYear();
  const yearTwo = years.length > 1 ? years[years.length - 1] : yearOne;
  const addField = (label, name, values, selectedValue = null, type = "select") => {
    const wrapper = document.createElement("label");
    wrapper.className = "field";
    const caption = document.createElement("span");
    caption.textContent = label;
    let input;
    if (type === "date") {
      input = document.createElement("input");
      input.type = "date";
      input.value = selectedValue || "";
    } else {
      input = document.createElement("select");
      addOptions(input, values, { selected: selectedValue });
    }
    input.name = name;
    wrapper.append(caption, input);
    holder.append(wrapper);
  };
  const yearEntries = years.map((year) => String(year));
  if (mode === "Year") {
    addField("First year", "year1", yearEntries, String(yearOne), "select");
    addField("Second year", "year2", yearEntries, String(yearTwo), "select");
    addField("Month scope", "month", ["All Months", ...months], "All Months", "select");
  } else if (mode === "Month") {
    addField("Year", "year", yearEntries, String(yearTwo), "select");
    addField("First month", "month1", months, months[0], "select");
    addField("Second month", "month2", months, months.length > 1 ? months[months.length - 1] : months[0], "select");
  } else if (mode === "Last Months") {
    const latestDate = options.latestDate;
    const [latestYear, latestMonth] = latestDate
      ? latestDate.slice(0, 7).split("-")
      : [String(yearTwo), String(new Date().getMonth() + 1).padStart(2, "0")];
    addField("Ending year", "endYear", yearEntries, latestYear, "select");
    addField("Ending month", "endMonth", months, MONTH_NAMES[Number(latestMonth) - 1], "select");
    addField("Compare period length", "monthsCount", [
      { value: "3", label: "3 months" },
      { value: "4", label: "4 months" },
      { value: "6", label: "6 months" },
    ], "3", "select");
  } else if (mode === "Period") {
    addField("Year", "year", yearEntries, String(yearTwo), "select");
    addField("Month", "month", months, months[0], "select");
  } else {
    addField("First start", "start1", [], "", "date");
    addField("First end", "end1", [], "", "date");
    addField("Second start", "start2", [], "", "date");
    addField("Second end", "end2", [], "", "date");
  }
}

async function loadComparisonOptions(refresh = false) {
  if (state.comparisonOptionsLoading) return;
  if (state.comparisonOptionsLoaded && !refresh) return;
  state.comparisonOptionsLoading = true;
  $("#apply-comparison").disabled = true;
  try {
    state.options.comparison = await request("/api/comparison/options");
    state.comparisonOptionsLoaded = true;
    createComparisonFields();
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.comparisonOptionsLoading = false;
    $("#apply-comparison").disabled = false;
  }
}

async function loadComparison() {
  if (state.comparisonLoading) return;
  const filters = comparisonFiltersFromForm();
  if (Object.values(filters).some((value) => value === "")) {
    toast("Choose a value for each comparison filter.", true);
    return;
  }
  state.comparisonLoading = true;
  const query = new URLSearchParams({
    hospital: $("#comparison-hospital").value || "All Hospitals",
    filters: JSON.stringify(filters),
  });
  $("#apply-comparison").disabled = true;
  try {
    let data;
    try {
      data = await request(`/api/comparison?${query.toString()}`);
    } catch (error) {
      if (filters.compareBy !== "Last Months"
        || error.message !== "Choose a supported comparison type.") throw error;
      const fallback = comparisonDateRangeFilters(filters);
      const fallbackQuery = new URLSearchParams({
        hospital: $("#comparison-hospital").value || "All Hospitals",
        filters: JSON.stringify(fallback.filters),
      });
      const fallbackData = await request(`/api/comparison?${fallbackQuery.toString()}`);
      const previousLabels = fallbackData.labels || ["First period", "Second period"];
      data = relabelComparisonPeriods(fallbackData, previousLabels, fallback.labels);
      toast("Older server detected. Compared the equivalent date ranges; restart the server to update.");
    }
    renderComparison(data);
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.comparisonLoading = false;
    $("#apply-comparison").disabled = false;
  }
}

function resetComparison() {
  $("#comparison-hospital").value = "All Hospitals";
  $("#comparison-mode").value = "Year";
  createComparisonFields();
  renderComparison(zeroComparisonData());
}

function zeroComparisonData() {
  const mode = $("#comparison-mode").value;
  const selected = Object.fromEntries(
    [...document.querySelectorAll("#comparison-dynamic-fields [name]")]
      .map((input) => [input.name, input.value]),
  );
  let labels = ["First period", "Second period"];
  if (mode === "Year") {
    labels = [selected.year1, selected.year2].map((year) =>
      selected.month === "All Months" ? year : `${selected.month} ${year}`,
    );
  } else if (mode === "Month") {
    labels = [selected.month1, selected.month2];
  } else if (mode === "Last Months") {
    labels = [`Previous ${selected.monthsCount} months`, `Latest ${selected.monthsCount} months`];
  } else if (mode === "Period") {
    labels = PERIODS;
  }
  labels = labels.map((label, index) => label || `Period ${index + 1}`);
  const keys = COMPARISON_SERIES_KEYS.slice(0, labels.length);
  const metrics = [
    ["Revenue", "currency"], ["IPD revenue", "currency"], ["OPD revenue", "currency"],
    ["Revenue target", "currency"], ["Target achievement", "percent"],
    ["Admissions", "count"], ["Discharges", "count"], ["OPD consultations", "count"],
    ["Occupancy", "percent"], ["Occupied bed days", "count"], ["Average stay", "days"],
    ["Cash due", "currency"], ["Credit due", "currency"],
    ["IPD ARPP", "currency"], ["Per day revenue", "currency"],
    ["Per day per patient charge", "currency"],
  ].map(([label, kind]) => ({
    label, kind, values: keys.map(() => 0), change: "0.0%", changeValue: 0,
  }));
  const chartKeys = [
    "dischargeDepartments", "dischargeDoctors", "admissionDepartments",
    "admissionDoctors", "ipdRevenueDepartments", "ipdRevenueDoctors",
    "opdDoctors", "panels", "opdServices",
  ];
  const charts = Object.fromEntries(chartKeys.map((key) => [
    key, [{ label: "No data", ...Object.fromEntries(keys.map((seriesKey) => [seriesKey, 0])) }],
  ]));
  const chartTotals = Object.fromEntries(chartKeys.map((key) => [key, keys.map(() => 0)]));
  const yearlyTrend = mode === "Year" ? MONTH_NAMES.map((month) => ({
    label: month.slice(0, 3),
    ...Object.fromEntries(keys.map((seriesKey) => [seriesKey, 0])),
  })) : [{
    label: "IPD revenue",
    ...Object.fromEntries(keys.map((seriesKey) => [seriesKey, 0])),
  }];
  return {
    hasData: true,
    labels,
    metrics,
    charts,
    chartTotals,
    revenueTrend: {
      title: mode === "Year" ? "Monthly IPD revenue comparison" : "IPD revenue comparison",
      rows: yearlyTrend,
      totals: keys.map(() => 0),
    },
    departments: [], doctors: [], panels: [], services: [],
    managementInsights: {},
    insights: [],
    isZeroState: true,
  };
}

function renderComparison(data) {
  if (!data.hasData) {
    toast(data.message || "No saved hospital records are available to compare.", true);
    return;
  }
  $("#comparison-value-popup")?.classList.add("is-hidden");
  $("#comparison-content").classList.remove("is-hidden");
  state.comparisonData = data;
  updateHeaderDateRange();
  renderComparisonKpis(data);
  renderComparisonMovers(data);
  renderComparisonInsights(data);
  if (state.comparisonView === "numbers") renderComparisonTrendTables(data);
  setComparisonView(state.comparisonView);
}

function updateHeaderDateRange() {
  const header = $("#date-range");
  const activeView = document.querySelector(".view-tab.is-active")?.dataset.view || "overview";
  const isPrediction = activeView === "comparison" && state.comparisonView === "prediction";
  header.closest(".topbar-period")?.classList.toggle("is-hidden", isPrediction);
  if (isPrediction) return;

  if (activeView === "comparison") {
    const ranges = state.comparisonData?.periodRanges;
    if (Array.isArray(ranges) && ranges.length) {
      header.textContent = ranges
        .filter((range) => Array.isArray(range) && range.length >= 2)
        .map(([start, end]) => `${dateText(start)} – ${dateText(end)}`)
        .join(" vs ");
    }
    return;
  }

  const dateRange = state.appliedRange
    || state.dashboard?.dateRange
    || state.options?.dateRange;
  header.textContent = dateRange?.start && dateRange?.end
    ? `${dateText(dateRange.start)} – ${dateText(dateRange.end)}`
    : state.dashboard?.hasData
      ? "Data available outside the picker date window"
      : "No reporting data";
}

function setComparisonView(view) {
  state.comparisonView = ["charts", "prediction"].includes(view) ? view : "numbers";
  updateHeaderDateRange();
  const select = $("#comparison-view-mode");
  if (select) select.value = state.comparisonView;
  const content = $("#comparison-content");
  content.dataset.view = state.comparisonView;
  $("#comparison-view").classList.toggle("show-prediction-controls", state.comparisonView === "prediction");
  if (state.comparisonView === "prediction") content.classList.remove("is-hidden");
  $("#comparison-value-popup")?.classList.add("is-hidden");
  if (state.comparisonView === "charts") scheduleComparisonChart();
  if (state.comparisonView === "prediction") {
    window.dispatchEvent(new Event("comparison-prediction-selected"));
  }
}

function comparisonValue(value, kind) {
  if (kind === "currency") return money(value);
  if (kind === "percent") return `${Number(value || 0).toFixed(1)}%`;
  if (kind === "days") return `${Number(value || 0).toFixed(2)} days`;
  return number(value);
}

const COMPARISON_KPI_ORDER = [
  ["Admissions", "IPD Admission"],
  ["Discharges", "IPD Discharge"],
  ["OPD consultations", "OPD Consultation"],
  ["Revenue", "Total Revenue"],
  ["IPD revenue", "IPD Revenue"],
  ["OPD revenue", "OPD Revenue"],
  ["IPD ARPP", "IPD ARPP"],
  ["Per day revenue", "Per Day Revenue"],
  ["Cash due", "Cash Due"],
  ["Credit due", "Credit Due"],
  ["Average stay", "ALOS"],
  ["Per day per patient charge", "Per Day Per Patient Charge"],
];

function comparisonMetricValues(data, label) {
  const metric = (data.metrics || []).find((item) => item.label === label);
  if (!metric) return [];
  return (metric.values || [metric.first, metric.second]).map((value) => Number(value) || 0);
}

function comparisonKpiMetric(data, metricLabel) {
  const found = (data.metrics || []).find((item) => item.label === metricLabel);
  if (found) return found;
  // Safety net: work these out here if the server did not send them.
  const values = (label) => comparisonMetricValues(data, label);
  const divide = (top, bottom) => top.map((value, index) => (bottom[index] ? value / bottom[index] : 0));
  let derived = null;
  if (metricLabel === "IPD ARPP") derived = divide(values("IPD revenue"), values("Discharges"));
  else if (metricLabel === "Per day per patient charge") derived = divide(values("IPD revenue"), values("Occupied bed days"));
  else if (metricLabel === "Per day revenue") {
    const days = data.summary?.periodDays;
    if (!Array.isArray(days) || !days.some(Boolean)) return null;
    derived = divide(values("Revenue"), days.map((day) => Number(day) || 0));
  }
  if (!derived || !derived.length) return null;
  const metric = { label: metricLabel, kind: "currency", values: derived, first: derived[0], second: derived[derived.length - 1] };
  if (derived.length === 2) {
    if (derived[0]) {
      metric.changeValue = (derived[1] - derived[0]) / derived[0] * 100;
      metric.change = `${metric.changeValue >= 0 ? "+" : ""}${metric.changeValue.toFixed(1)}%`;
    } else {
      metric.change = "N/A";
    }
  }
  return metric;
}

function renderComparisonKpis(data) {
  const grid = $("#comparison-kpis");
  grid.replaceChildren();
  const compactPeriodLabel = (index) => {
    const range = data.periodRanges?.[index];
    if ($("#comparison-mode")?.value !== "Last Months"
      || !Array.isArray(range) || range.length < 2) return data.labels[index];
    const [start, end] = range.map((date) => new Date(`${date}T00:00:00`));
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return data.labels[index];
    const month = (date) => date.toLocaleDateString("en-US", { month: "short" });
    const startMonth = month(start);
    const endMonth = month(end);
    if (start.getFullYear() === end.getFullYear()) {
      return start.getMonth() === end.getMonth()
        ? `${startMonth} ${start.getFullYear()}`
        : `${startMonth}–${endMonth} ${end.getFullYear()}`;
    }
    return `${startMonth} ${start.getFullYear()}–${endMonth} ${end.getFullYear()}`;
  };
  COMPARISON_KPI_ORDER.forEach(([metricLabel, displayLabel]) => {
    const metric = comparisonKpiMetric(data, metricLabel);
    if (!metric) return;
    const card = document.createElement("article");
    card.className = "comparison-kpi";
    const label = document.createElement("div");
    label.className = "comparison-kpi-label";
    label.textContent = displayLabel;
    const values = document.createElement("div");
    values.className = "comparison-values";
    const metricValues = metric.values || [metric.first, metric.second];
    values.style.setProperty("--comparison-value-count", String(metricValues.length));
    metricValues.forEach((metricValue, index) => {
      const item = document.createElement("div");
      item.className = "comparison-value";
      const period = document.createElement("span");
      period.textContent = compactPeriodLabel(index);
      period.title = data.labels[index];
      const value = document.createElement("strong");
      value.textContent = comparisonValue(metricValue, metric.kind);
      item.append(period, value);
      values.append(item);
    });
    card.append(label, values);
    if (metric.change) {
      const change = document.createElement("span");
      change.className = "comparison-change";
      if (typeof metric.changeValue === "number" && metric.changeValue < 0) change.classList.add("negative");
      if (metric.change === "N/A") change.classList.add("neutral");
      change.textContent = metric.change;
      card.append(change);
    }
    grid.append(card);
  });
}

function renderComparisonMovers(data) {
const metric = state.comparisonMoverMetric;
    const dimension = state.comparisonMoverDimension;
    const periodLabels = data.labels || ["A", "B"];
    const moverRows = data.moverData?.[metric]?.[dimension];
    const sourceConfig = {
      ipdRevenue: {
        departments: ["departments", "Department", (label) => label],
        doctors: ["doctors", "Doctor", (label) => label],
        panels: ["panels", "Panel", (label) => `${label} IPD revenue`],
      },
      ipdDischarges: {
        departments: ["dischargeDepartments", "Dept. Name", (label) => label],
        doctors: ["dischargeDoctors", "Doctor Name", (label) => label],
      },
      ipdAdmissions: {
        departments: ["admissionDepartments", "Dept. Name", (label) => label],
        doctors: ["admissionDoctors", "Doctor Name", (label) => label],
      },
      opdConsultations: {
        departments: ["opdDepartments", "Dept. Name", (label) => label],
        doctors: ["opdDoctors", "Doctor Name", (label) => label],
      },
    }[metric]?.[dimension];
    const comparisonRows = sourceConfig
      ? (data[sourceConfig[0]] || []).map((row) => ({
        name: row[sourceConfig[1]] || "Unknown",
        values: periodLabels.map((label) => Number(
          row[sourceConfig[2](label)] ?? row[label] ?? 0,
        )),
      }))
      : [];
    const rows = Array.isArray(moverRows) && moverRows.length
      ? moverRows
      : comparisonRows;
    const firstIndex = 0;
    const lastIndex = periodLabels.length - 1;
    const showAllPeriods = $("#comparison-mode")?.value === "Period"
      && periodLabels.length === 3;
    const isCurrency = metric === "ipdRevenue";
    const entities = rows.map((row) => {
      const values = periodLabels.map((_, index) =>
        Number(row.values?.[index]) || 0,
      );
      return {
        name: row.name || "Unknown",
        values,
        first: values[firstIndex],
        second: values[lastIndex],
        change: values[lastIndex] - values[firstIndex],
      };
    }).sort((left, right) =>
      left.change - right.change || left.name.localeCompare(right.name),
    );
    const dimensionLabel = {
      departments: "Department",
      doctors: "Doctor",
      panels: "Panel",
    }[dimension];
    $("#comparison-movers-title").textContent =
      showAllPeriods
        ? `${dimensionLabel} comparison (all billing periods)`
        : `${dimensionLabel} comparison (${periodLabels[firstIndex]} vs ${periodLabels[lastIndex]})`;

    const formatMetric = (value) => isCurrency ? compactMoney(value) : number(value);
    const formatChange = (change, first) => {
      if (first === 0 && change > 0) return `+${formatMetric(change)} (new)`;
      const percentage = first !== 0 ? change / first * 100 : 0;
      const sign = change > 0 ? "+" : change < 0 ? "−" : "";
      return `${sign}${formatMetric(Math.abs(change))} (${sign}${Math.abs(percentage).toFixed(1)}%)`;
    };
    const container = $("#comparison-mover-list");
    container.replaceChildren();
    if (!entities.length) {
      const empty = document.createElement("p");
      empty.className = "comparison-mover-empty";
      empty.textContent = dimension === "panels" && metric !== "ipdRevenue"
        ? "Panel comparisons are available for IPD revenue only."
        : "No matching data in these periods.";
      container.append(empty);
      return;
    }

    const table = document.createElement("table");
    table.className = "comparison-mover-table";
    const head = document.createElement("thead");
    const heading = document.createElement("tr");
    [
      dimensionLabel,
      ...(showAllPeriods
        ? periodLabels
        : [periodLabels[firstIndex], periodLabels[lastIndex]]),
      showAllPeriods ? "Net change" : "Change",
    ]
      .forEach((text) => {
        const cell = document.createElement("th");
        cell.textContent = text;
        heading.append(cell);
      });
    head.append(heading);
    table.append(head);
    const body = document.createElement("tbody");
    entities.forEach((row) => {
      const tr = document.createElement("tr");
      const name = document.createElement("th");
      name.scope = "row";
      name.textContent = row.name;
      tr.append(name);
      const visibleValues = showAllPeriods
        ? row.values
        : [row.first, row.second];
      visibleValues.forEach((value, index) => {
        const cell = document.createElement("td");
        cell.className = `mover-period-value period-${String.fromCharCode(97 + index)}`;
        cell.textContent = formatMetric(value);
        cell.title = isCurrency ? money(value) : number(value);
        tr.append(cell);
      });
      const change = document.createElement("td");
      change.className = `mover-change ${
        row.change > 0 ? "increase" : row.change < 0 ? "decrease" : "neutral"
      }`;
      change.textContent = formatChange(row.change, row.first);
      tr.append(change);
      body.append(tr);
    });
    table.append(body);
    const totals = periodLabels.map((_, index) =>
      entities.reduce((sum, row) => sum + row.values[index], 0),
    );
    const foot = document.createElement("tfoot");
    const totalRow = document.createElement("tr");
    const totalName = document.createElement("th");
    totalName.scope = "row";
    totalName.textContent = `Total (${entities.length})`;
    totalRow.append(totalName);
    (showAllPeriods ? totals : [totals[firstIndex], totals[lastIndex]])
      .forEach((value, index) => {
      const cell = document.createElement("td");
      cell.className = `mover-period-value period-${String.fromCharCode(97 + index)}`;
      cell.textContent = formatMetric(value);
      cell.title = isCurrency ? money(value) : number(value);
      totalRow.append(cell);
    });
    const totalChange = document.createElement("td");
    const totalDiff = totals[lastIndex] - totals[firstIndex];
    totalChange.className = `mover-change ${
      totalDiff > 0 ? "increase" : totalDiff < 0 ? "decrease" : "neutral"
    }`;
    totalChange.textContent = formatChange(totalDiff, totals[firstIndex]);
    totalRow.append(totalChange);
    foot.append(totalRow);
    table.append(foot);
    container.append(table);
}

function comparisonChartRows(data, chartKey, rowsKey, dimension, valueKeys = null) {
  const chartRows = data.charts && data.charts[chartKey];
  if (Array.isArray(chartRows) && chartRows.length) return chartRows;

  return (data[rowsKey] || [])
    .filter((row) => row[dimension])
    .map((row) => ({
      label: row[dimension],
      ...Object.fromEntries(data.labels.map((label, index) => [
        COMPARISON_SERIES_KEYS[index],
        Number(row[valueKeys ? valueKeys[index] : label] || 0),
      ])),
    }));
}

function comparisonRevenueTrend(data) {
  if (data.revenueTrend && Array.isArray(data.revenueTrend.rows)) {
    return data.revenueTrend;
  }
  const revenue = (data.metrics || []).find((metric) => metric.label === "IPD revenue");
  const values = revenue?.values || [revenue?.first || 0, revenue?.second || 0];
  return {
    title: "IPD revenue comparison",
    rows: [{ label: "Total IPD revenue", ...Object.fromEntries(
      values.map((value, index) => [COMPARISON_SERIES_KEYS[index], value]),
    ) }],
    totals: values,
  };
}

function comparisonChartTotals(data, key, labels) {
  if (Array.isArray(data.chartTotals?.[key])) return data.chartTotals[key];

  const metricByChart = {
    dischargeDepartments: "Discharges",
    dischargeDoctors: "Discharges",
    admissionDepartments: "Admissions",
    admissionDoctors: "Admissions",
    ipdRevenueDepartments: "IPD revenue",
    ipdRevenueDoctors: "IPD revenue",
    opdDoctors: "OPD consultations",
    panels: "IPD revenue",
  };
  const metricLabel = metricByChart[key];
  if (metricLabel) {
    const metric = (data.metrics || []).find((item) => item.label === metricLabel);
    if (metric) return (metric.values || [metric.first, metric.second]).map((value) => Number(value) || 0);
  }

  if (key === "opdServices") {
    const services = data.services || [];
    return labels.map((label) => services.reduce(
      (total, row) => total + (Number(row[label]) || 0),
      0,
    ));
  }

  return labels.map(() => 0);
}

function comparisonTipElement() {
  let popup = $("#comparison-value-popup");
  if (popup) return popup;
  popup = document.createElement("aside");
  popup.id = "comparison-value-popup";
  popup.className = "comparison-value-popup is-hidden";
  popup.setAttribute("role", "tooltip");
  const title = document.createElement("strong");
  title.id = "comparison-value-popup-title";
  title.className = "comparison-value-popup-heading";
  const values = document.createElement("dl");
  values.className = "comparison-value-popup-values";
  popup.append(title, values);
  document.body.append(popup);
  return popup;
}

function hideComparisonTip() {
  $("#comparison-value-popup")?.classList.add("is-hidden");
}

function positionComparisonTip(event) {
  const popup = $("#comparison-value-popup");
  if (!popup || popup.classList.contains("is-hidden")) return;
  const gap = 14;
  const box = popup.getBoundingClientRect();
  let left = event.clientX + gap;
  let top = event.clientY + gap;
  if (left + box.width > window.innerWidth - 8) left = event.clientX - box.width - gap;
  if (top + box.height > window.innerHeight - 8) top = event.clientY - box.height - gap;
  popup.style.left = `${Math.max(8, left)}px`;
  popup.style.top = `${Math.max(8, top)}px`;
}

function showComparisonTip(title, lines, event) {
  const popup = comparisonTipElement();
  popup.querySelector("#comparison-value-popup-title").textContent = title;
  const values = popup.querySelector(".comparison-value-popup-values");
  values.replaceChildren();
  lines.forEach((line) => {
    const term = document.createElement("dt");
    term.textContent = line.label;
    const detail = document.createElement("dd");
    detail.textContent = line.value;
    if (line.tone) detail.className = line.tone;
    values.append(term, detail);
  });
  popup.classList.remove("is-hidden");
  positionComparisonTip(event);
}

function showComparisonTipFor(target, event) {
  if (target.dataset.comparisonTip) {
    const tip = JSON.parse(target.dataset.comparisonTip);
    showComparisonTip(tip.title, tip.lines, event);
    return;
  }
  const details = JSON.parse(target.dataset.comparisonValues || "[]");
  const currency = target.dataset.valueKind === "currency";
  showComparisonTip(target.dataset.comparisonCategory, details.map((item) => ({
    label: item.label,
    value: `${currency ? money(item.value) : number(item.value)} · ${percentLabel(item.percentage)}`,
  })), event);
}

function makeComparisonCategoryInteractive(group, row, series, valueKind, totals) {
  const label = row.label || "Unknown";
  group.dataset.comparisonCategory = label;
  group.dataset.comparisonValues = JSON.stringify(series.map((item, index) => ({
    label: item.label,
    value: Number(row[item.key]) || 0,
    percentage: percent(row[item.key], totals[index]),
    change: index ? comparisonShareChange(row, series, totals, index) : "neutral",
  })));
  group.dataset.valueKind = valueKind;
  group.setAttribute("role", "button");
  group.tabIndex = 0;
  group.setAttribute("aria-label", `Show comparison values for ${label}`);
}

function comparisonShareChange(row, series, totals, index) {
  const currentShare = percent(row[series[index].key], totals[index]);
  const previousShare = percent(row[series[index - 1].key], totals[index - 1]);
  if (currentShare > previousShare) return "positive";
  if (currentShare < previousShare) return "negative";
  return "neutral";
}

function renderComparisonColumnChart(
  container, rows, series, formatter = number, totalsElement = null, totals = null,
  valueKind = "count",
) {
  container.replaceChildren();
  const compact = container.classList.contains("comparison-compact-chart");
  container.style.paddingTop = compact
    ? `${20 + Math.max(0, series.length - 1) * 14}px`
    : `${36 + Math.max(0, series.length - 1) * 30}px`;
  const rowCount = Array.isArray(rows) ? rows.length : 0;
  container.style.gridTemplateColumns = `repeat(${Math.max(rowCount, 1)}, minmax(0, 1fr))`;
  container.dataset.seriesCount = String(series.length);
  if (totalsElement) {
    renderTotalItems(totalsElement, totals
      ? series.map((item, index) => ({
        label: `Total · ${item.label}`,
        value: totals[index] || 0,
        formatter,
      }))
      : []);
  }
  if (!Array.isArray(rows) || !rows.length) {
    const empty = document.createElement("p");
    empty.className = "comparison-bars-empty";
    empty.textContent = "No matching data in these comparison periods.";
    container.append(empty);
    return;
  }

  const seriesTotals = series.map((item, index) => Number(totals?.[index])
    || rows.reduce((sum, row) => sum + Number(row[item.key] || 0), 0));
  const maximum = Math.max(
    1,
    ...rows.flatMap((row) => series.map((item) => Number(row[item.key]) || 0)),
  );
  rows.forEach((row) => {
    const group = document.createElement("div");
    group.className = "comparison-column-group";
    makeComparisonCategoryInteractive(group, row, series, valueKind, seriesTotals);
    const plot = document.createElement("div");
    plot.className = "comparison-column-plot";
    series.forEach((item, seriesIndex) => {
      const value = Math.max(0, Number(row[item.key]) || 0);
      const percentage = percent(value, seriesTotals[seriesIndex]);
      const bar = document.createElement("span");
      bar.className = `comparison-column-bar${item.key === "first" ? "" : ` ${item.key}`}`;
      bar.style.height = `${value / maximum * 78}%`;
      bar.setAttribute("aria-label", `${item.label}: ${formatter(value)}, ${percentLabel(percentage)} of total`);
      const amount = document.createElement("span");
      amount.className = "comparison-column-value";
      const share = document.createElement("span");
      share.className = `comparison-chart-percent ${seriesIndex ? comparisonShareChange(row, series, seriesTotals, seriesIndex) : ""}`;
      share.textContent = percentLabel(percentage);
      const formattedAmount = document.createElement("span");
      formattedAmount.className = "comparison-chart-amount";
      formattedAmount.textContent = formatter(value);
      amount.append(share, formattedAmount);
      amount.style.top = compact
        ? `${-18 - seriesIndex * 14}px`
        : `${-31 - seriesIndex * 30}px`;
      amount.title = `${formatter(value)} · ${percentLabel(percentage)} of total`;
      bar.append(amount);
      plot.append(bar);
    });
    const label = document.createElement("strong");
    label.className = "comparison-column-label";
    label.textContent = row.shortLabel || row.label || "Unknown";
    label.title = row.label || "Unknown";
    group.append(plot, label);
    container.append(group);
  });
}

function renderComparisonHorizontalChart(
  container, rows, series, formatter = number, totalsElement = null, totals = null,
  valueKind = "count",
) {
  container.replaceChildren();
  container.classList.add("comparison-horizontal-chart");
  container.dataset.seriesCount = String(series.length);
  if (totalsElement) {
    renderTotalItems(totalsElement, totals
      ? series.map((item, index) => ({
        label: `Total · ${item.label}`,
        value: totals[index] || 0,
        formatter,
      }))
      : []);
  }
  if (!Array.isArray(rows) || !rows.length) {
    const empty = document.createElement("p");
    empty.className = "comparison-bars-empty";
    empty.textContent = "No matching data in these comparison periods.";
    container.append(empty);
    return;
  }

  const seriesTotals = series.map((item, index) => Number(totals?.[index])
    || rows.reduce((sum, row) => sum + Number(row[item.key] || 0), 0));
  const maximum = Math.max(
    1,
    ...rows.flatMap((row) => series.map((item) => Number(row[item.key]) || 0)),
  );
  rows.forEach((row) => {
    const group = document.createElement("div");
    group.className = "comparison-horizontal-group";
    makeComparisonCategoryInteractive(group, row, series, valueKind, seriesTotals);
    const label = document.createElement("strong");
    label.className = "comparison-horizontal-label";
    label.textContent = row.label || "Unknown";
    label.title = row.label || "Unknown";
    const values = document.createElement("div");
    values.className = "comparison-horizontal-series";
    series.forEach((item, seriesIndex) => {
      const value = Math.max(0, Number(row[item.key]) || 0);
      const percentage = percent(value, seriesTotals[seriesIndex]);
      const line = document.createElement("div");
      line.className = "comparison-horizontal-line";
      const track = document.createElement("span");
      track.className = "comparison-horizontal-track";
      const bar = document.createElement("span");
      bar.className = `comparison-horizontal-bar${item.key === "first" ? "" : ` ${item.key}`}`;
      bar.style.width = `${value / maximum * 100}%`;
      bar.setAttribute("aria-label", `${item.label}: ${formatter(value)}, ${percentLabel(percentage)} of total`);
      track.append(bar);
      const amount = document.createElement("strong");
      amount.className = "comparison-horizontal-value";
      const formattedAmount = document.createElement("span");
      formattedAmount.className = "comparison-chart-amount";
      formattedAmount.textContent = formatter(value);
      const share = document.createElement("span");
      share.className = `comparison-chart-percent ${seriesIndex ? comparisonShareChange(row, series, seriesTotals, seriesIndex) : ""}`;
      share.textContent = percentLabel(percentage);
      amount.append(formattedAmount, share);
      line.append(track, amount);
      values.append(line);
      amount.dataset.barWidth = String(Math.min(100, value / maximum * 100));
    });
    group.append(label, values);
    container.append(group);
    group.querySelectorAll(".comparison-horizontal-value").forEach((amount) => {
      const line = amount.parentElement;
      const track = line.querySelector(".comparison-horizontal-track");
      const desiredLeft = track.clientWidth * Number(amount.dataset.barWidth) / 100 + 5;
      amount.style.left = `${Math.max(
        0,
        Math.min(desiredLeft, track.clientWidth - amount.offsetWidth),
      )}px`;
    });
  });
}

function comparisonMoverChartRows(data, metricKey, dimension, limit = 10) {
  const rows = data.moverData?.[metricKey]?.[dimension];
  if (!Array.isArray(rows)) return [];
  const peak = (row) => Math.max(...COMPARISON_SERIES_KEYS.map((key) => row[key] || 0));
  return rows
    .map((row) => ({
      label: row.name || "Unknown",
      ...Object.fromEntries((row.values || []).map((value, index) => [
        COMPARISON_SERIES_KEYS[index], Number(value) || 0,
      ])),
    }))
    .filter((row) => peak(row) > 0)
    .sort((left, right) => peak(right) - peak(left))
    .slice(0, limit);
}

function formatComparisonDate(value) {
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return String(value || "");
  return dateText(value);
}

function comparisonRangeText(data, index) {
  const range = data.periodRanges?.[index];
  if (!Array.isArray(range) || range.length < 2) return "";
  return `${formatComparisonDate(range[0])} – ${formatComparisonDate(range[1])}`;
}

function renderComparisonTrendTables(data) {
  const holder = $("#comparison-trend-tables");
  if (!holder) return;
  holder.replaceChildren();
  const labels = data.labels || [];
  const lastIndex = labels.length - 1;
  const trends = data.trends || {};
  const periodSummary = $("#comparison-mode")?.value === "Period"
    && labels.length === 3;
  const items = [
    { trend: trends.admissions, fallback: "IPD admission trend", format: number },
    { trend: trends.discharges, fallback: "IPD discharge trend", format: number },
    { trend: trends.revenue || comparisonRevenueTrend(data), fallback: "IPD revenue trend", format: money, revenue: true },
    { trend: trends.opdConsultations, fallback: "OPD consultation trend", format: number },
  ];

  const changeText = (first, last, format) => {
    const diff = last - first;
    const sign = diff > 0 ? "+" : diff < 0 ? "−" : "";
    if (!first) return diff ? `${sign}${format(Math.abs(diff))} (new)` : "—";
    return `${sign}${format(Math.abs(diff))} (${sign}${Math.abs(diff / first * 100).toFixed(1)}%)`;
  };
  const tone = (first, last) => (last > first ? "increase" : last < first ? "decrease" : "neutral");

  const buildTable = (firstHeading, rows, format, totals) => {
    const table = document.createElement("table");
    table.className = "comparison-trend-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    [firstHeading, ...labels, "Change"].forEach((text) => {
      const th = document.createElement("th");
      th.textContent = text;
      headRow.append(th);
    });
    head.append(headRow);
    const body = document.createElement("tbody");
    const addRow = (parent, name, values) => {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.scope = "row";
      th.textContent = name;
      tr.append(th);
      values.forEach((value, index) => {
        const td = document.createElement("td");
        td.className = `period-${Math.min(index, 2)}`;
        td.textContent = format(value);
        tr.append(td);
      });
      const td = document.createElement("td");
      td.className = `change ${tone(values[0], values[lastIndex])}`;
      td.textContent = changeText(values[0], values[lastIndex], format);
      tr.append(td);
      parent.append(tr);
    };
    rows.forEach((row) => {
      addRow(body, row.label, labels.map((_, index) => Number(row[COMPARISON_SERIES_KEYS[index]]) || 0));
    });
    table.append(head, body);
    if (totals) {
      const foot = document.createElement("tfoot");
      addRow(foot, "Total", labels.map((_, index) => Number(totals[index]) || 0));
      table.append(foot);
    }
    return table;
  };

  items.forEach(({ trend, fallback, format }) => {
    const card = document.createElement("article");
    card.className = "panel comparison-trend-table-card";
    const title = document.createElement("h3");
    title.textContent = periodSummary
      ? (trend?.title || fallback).replace(/ trend by (day|month|period)$/i, " totals by billing period")
      : trend?.title || fallback;
    card.append(title);
    const rows = trend?.rows || [];
    if (!trend || !rows.length) {
      const note = document.createElement("p");
      note.className = "comparison-trend-note";
      note.textContent = trend
        ? "No matching data in these comparison periods."
        : "Trend numbers were not sent by the server. Replace dashboard_service.py with the new version and restart the server.";
      card.append(note);
    } else if (periodSummary) {
      const table = document.createElement("table");
      table.className = "comparison-trend-table";
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      ["Billing period", "Total"].forEach((text) => {
        const th = document.createElement("th");
        th.textContent = text;
        headRow.append(th);
      });
      head.append(headRow);
      const body = document.createElement("tbody");
      const totals = trend.totals || labels.map((_, index) =>
        rows.reduce(
          (sum, row) => sum + (Number(row[COMPARISON_SERIES_KEYS[index]]) || 0),
          0,
        ),
      );
      labels.forEach((label, index) => {
        const row = document.createElement("tr");
        const period = document.createElement("th");
        period.scope = "row";
        period.textContent = label;
        const total = document.createElement("td");
        total.className = `period-${Math.min(index, 2)}`;
        total.textContent = data.recordCounts?.[index] === 0
          ? "No data"
          : format(Number(totals[index]) || 0);
        row.append(period, total);
        body.append(row);
      });
      table.append(head, body);
      const tableWrap = document.createElement("div");
      tableWrap.className = "comparison-trend-table-wrap";
      tableWrap.append(table);
      card.append(tableWrap);
    } else {
      const heading = trend.granularity === "day" ? "Day" : trend.granularity === "month" ? "Month" : "Period";
      const tableWrap = document.createElement("div");
      tableWrap.className = "comparison-trend-table-wrap";
      tableWrap.append(buildTable(
        heading,
        rows,
        format,
        trend.totals && trend.totals.length ? trend.totals : null,
      ));
      card.append(tableWrap);
    }
    holder.append(card);
  });
}

function renderComparisonTargetDonuts(data) {
  const holder = $("#comparison-target-donuts");
  if (!holder) return;
  holder.replaceChildren();
  holder.style.setProperty("--donut-columns", String(Math.max(1, (data.labels || []).length)));
  const labels = data.labels || [];
  const revenue = comparisonMetricValues(data, "Revenue");
  const targets = comparisonMetricValues(data, "Revenue target");
  const colors = ["#168f88", "#168f88", "#168f88"];
  const svgNS = "http://www.w3.org/2000/svg";
  const radius = 70;
  const circumference = 2 * Math.PI * radius;
  labels.forEach((label, index) => {
    const actual = revenue[index] || 0;
    const target = targets[index] || 0;
    const achieved = target ? actual / target * 100 : 0;
    const reached = Math.min(achieved, 100);
    const exceeded = achieved > 100;
    const color = exceeded ? "#1b6b4a" : colors[index % colors.length];
    const range = comparisonRangeText(data, index);

    const card = document.createElement("article");
    card.className = "panel comparison-donut-card";
    card.tabIndex = 0;
    const lines = [];
    if (range) lines.push({ label: "Date range", value: range });
    lines.push({ label: "Revenue achieved", value: money(actual) });
    lines.push({ label: "Achieved", value: `${achieved.toFixed(1)}%`, tone: exceeded ? "good" : "" });
    if (exceeded) {
      lines.push({ label: "Above target", value: `${(achieved - 100).toFixed(1)}% (${money(actual - target)})`, tone: "good" });
    } else {
      lines.push({ label: "Left to target", value: `${Math.max(0, 100 - achieved).toFixed(1)}% (${money(Math.max(0, target - actual))})`, tone: "bad" });
    }
    card.dataset.comparisonTip = JSON.stringify({ title: `${label} · revenue target`, lines });

    const heading = document.createElement("h3");
    heading.textContent = label;
    const rangeLine = document.createElement("p");
    rangeLine.className = "comparison-donut-range";
    rangeLine.textContent = range || " ";

    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 180 180");
    svg.setAttribute("class", "comparison-donut-svg");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${label}: ${achieved.toFixed(1)}% of revenue target achieved`);
    const ring = (className) => {
      const circle = document.createElementNS(svgNS, "circle");
      circle.setAttribute("class", className);
      [["cx", 90], ["cy", 90], ["r", radius], ["stroke-width", 26]].forEach(([name, value]) => circle.setAttribute(name, value));
      return circle;
    };
    const track = ring("comparison-donut-track");
    const arc = ring("comparison-donut-value");
    arc.setAttribute("stroke", color);
    arc.setAttribute("stroke-dasharray", `${circumference * reached / 100} ${circumference}`);
    arc.setAttribute("transform", "rotate(-90 90 90)");
    const percentText = document.createElementNS(svgNS, "text");
    percentText.setAttribute("class", "comparison-donut-percent");
    percentText.setAttribute("x", 90);
    percentText.setAttribute("y", 94);
    percentText.setAttribute("text-anchor", "middle");
    percentText.textContent = `${achieved.toFixed(1)}%`;
    const caption = document.createElementNS(svgNS, "text");
    caption.setAttribute("class", "comparison-donut-caption");
    caption.setAttribute("x", 90);
    caption.setAttribute("y", 112);
    caption.setAttribute("text-anchor", "middle");
    caption.textContent = "of target";
    svg.append(track, arc, percentText, caption);

    const key = document.createElement("div");
    key.className = "comparison-donut-key";
    const keyItem = (dotColor, text) => {
      const item = document.createElement("span");
      const dot = document.createElement("i");
      dot.style.background = dotColor;
      item.append(dot, text);
      return item;
    };
    key.append(
      keyItem(color, `Achieved ${achieved.toFixed(1)}%`),
      exceeded
        ? keyItem(COLORS.teal, `Above target ${(achieved - 100).toFixed(1)}%`)
        : keyItem("#dbe5eb", `Left ${Math.max(0, 100 - achieved).toFixed(1)}%`),
    );
    card.append(heading, rangeLine, svg, key);
    holder.append(card);
  });
}

function scheduleComparisonChart() {
  if (state.comparisonView === "numbers") {
    const data = state.comparisonData;
    if (!data || $("#comparison-view").classList.contains("is-hidden")) return;
    const labels = data.labels || ["Period 1", "Period 2"];
    renderComparisonTrendTables(data);
    return;
  }
  if (state.comparisonView === "prediction") return;

  window.requestAnimationFrame(() => {
    const data = state.comparisonData;
    if (!data || $("#comparison-view").classList.contains("is-hidden")) return;
    const labels = data.labels || ["Period 1", "Period 2"];
    const series = labels.map((label, index) => ({
      key: COMPARISON_SERIES_KEYS[index],
      label,
    }));
    const draw = (selector, key, rows, currency = false) => {
      const container = $(selector);
      renderComparisonColumnChart(
        container, rows, series, currency ? compactMoney : number,
        $(`${selector}-total`), comparisonChartTotals(data, key, labels),
        currency ? "currency" : "count",
      );
    };
    draw(
      "#comparison-admission-departments-chart", "admissionDepartments",
      comparisonChartRows(data, "admissionDepartments", "admissionDepartments", "Dept. Name"),
    );
    draw(
      "#comparison-admission-doctors-chart", "admissionDoctors",
      comparisonChartRows(data, "admissionDoctors", "admissionDoctors", "Doctor Name"),
    );
    draw(
      "#comparison-discharge-departments-chart", "dischargeDepartments",
      comparisonChartRows(data, "dischargeDepartments", "dischargeDepartments", "Dept. Name"),
    );
    draw(
      "#comparison-discharge-doctors-chart", "dischargeDoctors",
      comparisonChartRows(data, "dischargeDoctors", "dischargeDoctors", "Doctor Name"),
    );
    draw(
      "#comparison-opd-departments-chart", "opdDoctors",
      comparisonMoverChartRows(data, "opdConsultations", "departments"),
    );
    draw(
      "#comparison-opd-doctors-chart", "opdDoctors",
      comparisonChartRows(data, "opdDoctors", "opdDoctors", "Doctor Name"),
    );
    draw(
      "#comparison-services-chart", "opdServices",
      comparisonChartRows(data, "services", "services", "Service"),
    );
    draw(
      "#comparison-ipd-departments-chart", "ipdRevenueDepartments",
      comparisonChartRows(data, "ipdRevenueDepartments", "departments", "Department"),
      true,
    );
    draw(
      "#comparison-ipd-doctors-chart", "ipdRevenueDoctors",
      comparisonChartRows(data, "ipdRevenueDoctors", "doctors", "Doctor"),
      true,
    );
    renderComparisonTargetDonuts(data);
  });
}

function renderComparisonInsights(data) {
  const labels = data.labels || [];
  const summary = data.summary;
  const list = $("#comparison-insights");
  list.replaceChildren();
  $("#comparison-summary-title").textContent = labels.length > 1
    ? `Summary — what changed (${labels[0]} compared with ${labels[labels.length - 1]})`
    : "Summary — what changed";
  if (!summary) {
    const item = document.createElement("li");
    item.textContent = data.isZeroState
      ? "Run Compare to see a summary."
      : "Summary data is not available for this comparison.";
    list.append(item);
    return;
  }

  const firstIndex = 0;
  const lastIndex = labels.length - 1;
  const pair = (key) => summary[key] || [];
  const first = (key) => Number(pair(key)[firstIndex] || 0);
  const last = (key) => Number(pair(key)[lastIndex] || 0);
  const amountChange = (before, after) => {
    const difference = after - before;
    return `${difference > 0 ? "+" : difference < 0 ? "−" : ""}${compactMoney(Math.abs(difference))}`;
  };
  const percentChange = (before, after) => {
    if (!before) return after ? "new" : "0.0%";
    const difference = (after - before) / before * 100;
    return `${difference > 0 ? "+" : ""}${difference.toFixed(1)}%`;
  };
  const comparisonText = (key) =>
    `${compactMoney(first(key))} in ${labels[firstIndex]} vs ${compactMoney(last(key))} in ${labels[lastIndex]}`;
  const add = (text, tone = "") => {
    const item = document.createElement("li");
    item.textContent = text;
    if (tone) item.className = tone;
    list.append(item);
  };

  const revenuePerDay = pair("totalRevenue").map((value, index) =>
    Number(value || 0) / Math.max(1, Number(pair("periodDays")[index] || 0)),
  );
  const firstDaily = revenuePerDay[firstIndex] || 0;
  const lastDaily = revenuePerDay[lastIndex] || 0;
  add(
    `Total revenue was ${comparisonText("totalRevenue")} `
      + `(${percentChange(first("totalRevenue"), last("totalRevenue"))}). `
      + `Per day that is ${compactMoney(firstDaily)} vs ${compactMoney(lastDaily)} `
      + `(${percentChange(firstDaily, lastDaily)}); the ranges are `
      + `${number(pair("periodDays")[firstIndex])} and ${number(pair("periodDays")[lastIndex])} days long.`,
  );

  const dischargeChange = Number(summary.ipdDischargeEffect || 0);
  const billChange = Number(summary.ipdBillPerDischargeEffect || 0);
  const dischargeEffectText = `${dischargeChange > 0 ? "+" : dischargeChange < 0 ? "−" : ""}${compactMoney(Math.abs(dischargeChange))}`;
  const billEffectText = `${billChange > 0 ? "+" : billChange < 0 ? "−" : ""}${compactMoney(Math.abs(billChange))}`;
  add(
    `IPD revenue changed by ${amountChange(first("ipdRevenue"), last("ipdRevenue"))}: `
      + `${dischargeEffectText} from discharge volume (${number(first("discharges"))} vs ${number(last("discharges"))}) `
      + `and ${billEffectText} from average revenue per discharge `
      + `(${compactMoney(first("averageIpdRevenuePerDischarge"))} vs ${compactMoney(last("averageIpdRevenuePerDischarge"))}).`,
  );

  const opdDifference = last("opdRevenue") - first("opdRevenue");
  add(
    `OPD revenue ${opdDifference >= 0 ? "rose" : "fell"} by `
      + `${money(Math.abs(opdDifference))} (${comparisonText("opdRevenue")}).`,
    opdDifference >= 0 ? "positive" : "negative",
  );

  const moverNames = [
    ["departments", "department"],
    ["doctors", "doctor"],
    ["panels", "panel"],
  ];
  moverNames.forEach(([key, label]) => {
    const mover = summary.biggestIpdRevenueMovers?.[key];
    if (!mover) {
      add(`No IPD revenue change was identified for a ${label} in these periods.`);
      return;
    }
    const change = Number(mover.change || 0);
    const netChange = mover.netChangePercent;
    const netShare = netChange === null || netChange === undefined
      ? "net IPD revenue change was zero"
      : `${netChange > 0 ? "+" : netChange < 0 ? "−" : ""}${Math.abs(netChange).toFixed(0)}% of the net IPD change`;
    add(
      `Biggest ${label} mover in IPD revenue: ${mover.name} `
        + `(${change >= 0 ? "+" : "−"}${compactMoney(Math.abs(change))}, ${netShare}).`,
    );
  });

  add(
    `Credit due is ${comparisonText("creditDue")} `
      + `(${percentChange(first("creditDue"), last("creditDue"))}); `
      + `the collected share of IPD billing was `
      + `${first("collectionRate").toFixed(1)}% vs ${last("collectionRate").toFixed(1)}%.`,
  );
}

function startDevelopmentReload() {
  let currentVersion = null;
  let initialized = false;
  let checking = false;
  let pollTimer = null;

  const checkForChanges = async () => {
    if (checking) return;
    checking = true;
    try {
      const response = await fetch("/api/dev/version", { cache: "no-store" });
      if (response.status === 404) {
        if (pollTimer !== null) window.clearInterval(pollTimer);
        return;
      }
      if (!response.ok) {
        throw new Error(`Development reload check failed (${response.status}).`);
      }
      const payload = await response.json();
      if (!payload.enabled) return;
      if (!initialized) {
        currentVersion = payload.version;
        initialized = true;
      } else if (payload.version !== currentVersion) {
        window.location.reload();
      }
    } catch (error) {
      console.warn("Could not check for local dashboard changes; retrying.", error);
    } finally {
      checking = false;
    }
  };

  void checkForChanges();
  pollTimer = window.setInterval(checkForChanges, 1000);
}

initialize();

function createComparisonFields() {
  const mode = $("#comparison-mode").value;
  const grid = $(".comparison-filter-grid");
  if (grid) grid.dataset.mode = mode;
  const holder = $("#comparison-dynamic-fields");
  holder.replaceChildren();
  const options = state.options?.comparison || { years: [], months: [] };
  const years = (options.years || []).map(String);
  const latestYear = years.length ? years[years.length - 1] : String(new Date().getFullYear());
  const months = MONTH_NAMES;
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
  const addHorizon = (values, unit, fallback) => addField(
    "Forecast ahead", "horizon",
    values.map((value) => ({ value: String(value), label: `Next ${value} ${unit}` })),
    String(fallback),
  );
  const hints = {
    Year: "Shows the selected year against the other years, month by month, and forecasts the coming months. "
      + "The breakdown charts compare it with the previous year over the same dates.",
    Month: "Compares the selected month with the same month of the previous year (same days if the month is still running) "
      + "and forecasts it for the coming years.",
    Recent: "Takes the last 2, 3 or 6 complete months, compares them with the months just before, "
      + "and forecasts the next months from that recent trend.",
    Period: "Compares billing periods inside a month. Choose a single period to also forecast it for the coming months.",
    "From-To": "Compares two custom date ranges. No forecast for custom ranges.",
  };
  const hint = $("#comparison-mode-hint");
  if (hint) hint.textContent = hints[mode] || "";

  if (mode === "Year") {
    addField("Year", "year", years, latestYear);
    addHorizon([3, 6, 12], "months", 6);
  } else if (mode === "Month") {
    addField("Year", "year", years, latestYear);
    addField("Month", "month", months, months[new Date().getMonth()]);
    addHorizon([1, 2, 3], "years", 1);
  } else if (mode === "Recent") {
    addField("Look back", "window", [
      { value: "2", label: "Last 2 months" },
      { value: "3", label: "Last 3 months" },
      { value: "6", label: "Last 6 months" },
    ], "3");
    addHorizon([3, 6], "months", 3);
  } else if (mode === "Period") {
    addField("Year", "year", years, latestYear);
    addField("Month", "month", months, months[0]);
    addField("Select period", "period", ["All Periods", ...PERIODS], "All Periods");
    addHorizon([3, 6, 12], "months", 6);
  } else {
    addField("First start", "start1", [], "", "date");
    addField("First end", "end1", [], "", "date");
    addField("Second start", "start2", [], "", "date");
    addField("Second end", "end2", [], "", "date");
  }
}

function mapOfflineComparisonFilters(filters) {
  // The offline report cannot forecast; it only needs a plain two-period comparison.
  const years = (state.options?.comparison?.years || []).map(Number);
  const otherYear = (year) => {
    const earlier = years.filter((value) => value < year);
    if (earlier.length) return Math.max(...earlier);
    const later = years.filter((value) => value > year);
    return later.length ? Math.min(...later) : year;
  };
  const pad = (value) => String(value).padStart(2, "0");
  const iso = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  if (filters.compareBy === "Year" || filters.compareBy === "Month") {
    const year = Number(filters.year);
    const other = otherYear(year);
    const [first, second] = other <= year ? [other, year] : [year, other];
    return {
      compareBy: "Year", year1: String(first), year2: String(second),
      month: filters.compareBy === "Month" ? filters.month : "All Months",
    };
  }
  if (filters.compareBy === "Recent") {
    const windowSize = Number(filters.window || 3);
    const end = new Date(`${state.options?.dateRange?.end || iso(new Date())}T00:00:00`);
    const monthLast = new Date(end.getFullYear(), end.getMonth() + 1, 0).getDate();
    const lastMonthEnd = end.getDate() === monthLast ? end : new Date(end.getFullYear(), end.getMonth(), 0);
    const secondStart = new Date(lastMonthEnd.getFullYear(), lastMonthEnd.getMonth() - (windowSize - 1), 1);
    const firstEnd = new Date(secondStart.getFullYear(), secondStart.getMonth(), 0);
    const firstStart = new Date(secondStart.getFullYear(), secondStart.getMonth() - windowSize, 1);
    return {
      compareBy: "From-To",
      start1: iso(firstStart), end1: iso(firstEnd),
      start2: iso(secondStart), end2: iso(lastMonthEnd),
    };
  }
  const { horizon, ...rest } = filters;
  return rest;
}

async function loadComparison() {
  const filters = comparisonFiltersFromForm();
  if (Object.values(filters).some((value) => value === "")) {
    toast("Choose a value for each comparison filter.", true);
    return;
  }
  const hospital = $("#comparison-hospital").value || "All Hospitals";
  $("#apply-comparison").disabled = true;
  try {
    let result;
    if (window.__OFFLINE_REPORT__) {
      const query = new URLSearchParams({
        hospital, filters: JSON.stringify(mapOfflineComparisonFilters(filters)),
      });
      result = {
        comparison: await request(`/api/comparison?${query.toString()}`),
        forecast: null,
        forecastMessage: "Forecasts are available in the live dashboard, not in the downloaded report.",
      };
    } else {
      const query = new URLSearchParams({ hospital, filters: JSON.stringify(filters) });
      result = await request(`/api/comparison/forecast?${query.toString()}`);
    }
    const comparison = result.comparison || { hasData: false };
    if (!comparison.hasData) {
      toast(comparison.message || "No records found for this comparison.", true);
    }
    renderComparison(
      comparison.hasData ? comparison : zeroComparisonData(),
      result.forecast || null,
      result.forecastMessage || "",
    );
  } catch (error) {
    toast(error.message, true);
  } finally {
    $("#apply-comparison").disabled = false;
  }
}

function zeroComparisonData() {
  const mode = $("#comparison-mode").value;
  let labels = ["Period 1", "Period 2"];
  if (mode === "Period") {
    const chosen = document.querySelector('#comparison-dynamic-fields [name="period"]')?.value;
    labels = chosen && chosen !== "All Periods" ? [chosen] : PERIODS;
  }
  const keys = COMPARISON_SERIES_KEYS.slice(0, labels.length);
  const metrics = [
    ["Revenue", "currency"], ["IPD revenue", "currency"], ["OPD revenue", "currency"],
    ["Revenue target", "currency"], ["Target achievement", "percent"],
    ["Admissions", "count"], ["Discharges", "count"], ["OPD consultations", "count"],
    ["Occupancy", "percent"], ["Occupied bed days", "count"], ["Average stay", "days"],
    ["Cash due", "currency"], ["Credit due", "currency"],
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
  return {
    hasData: true,
    labels,
    metrics,
    charts,
    chartTotals,
    departments: [], doctors: [], panels: [], services: [],
    managementInsights: {},
    insights: [],
    isZeroState: true,
  };
}

function renderComparison(data, forecast = null, forecastMessage = "") {
  if (!data.hasData) {
    toast(data.message || "No saved hospital records are available to compare.", true);
    return;
  }
  $("#comparison-value-popup")?.classList.add("is-hidden");
  $("#comparison-content").classList.remove("is-hidden");
  state.comparisonData = data;
  state.comparisonForecast = forecast;
  state.comparisonForecastMessage = forecastMessage;
  scheduleComparisonChart();
}

function scheduleComparisonChart() {
  window.requestAnimationFrame(() => {
    const data = state.comparisonData;
    if (!data || $("#comparison-view").classList.contains("is-hidden")) return;
    const labels = data.labels || ["Period 1", "Period 2"];
    const series = labels.map((label, index) => ({
      key: COMPARISON_SERIES_KEYS[index],
      label,
    }));
    renderComparisonLegends(data, labels);
    renderForecastSection(state.comparisonForecast, state.comparisonForecastMessage);

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
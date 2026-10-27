(() => {
  const hospitals = ["Surya Hospital", "NIMMS Hospital (Haldoni)", "Vedansh Hospital (Dadri)"];
  const pharmacyOptions = hospital =>
    hospital === hospitals[0] ? ["IPD", "OPD"] : ["Pharmacy"];
  const pharmacyLabel = pharmacy => pharmacy === "Pharmacy" ? "Pharmacy" : `${pharmacy} Pharmacy`;
  let filePreviews = [];
  let appliedDashboardState = null;
  let dashboardLoadRequest = 0;
  let currentSupplierRows = [];
  let currentHospitalSupplierRows = [];
  let currentHospitalMonthly = [];
  let latestTransactionDate = null;
  let previousMonthRange = null;
  const byId = id => document.getElementById(id);
  const dateInput = date => date.toISOString().slice(0, 10);
  const parseDate = value => {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(Date.UTC(year, month - 1, day));
  };
  const displayDate = value => {
    const [year, month, day] = value.split("-");
    return `${day}-${month}-${year}`;
  };
  const periodCaption = range =>
    `Current period: ${displayDate(range.start)} – ${displayDate(range.end)} · ` +
    `Same dates last year: ${displayDate(shiftYear(range.start))} – ${displayDate(shiftYear(range.end))}`;
  const displayMonth = value => {
    const [year, month] = value.split("-").map(Number);
    return new Intl.DateTimeFormat("en-IN", {month: "long", year: "numeric"})
      .format(new Date(year, month - 1, 1));
  };
  const shiftYear = value => {
    const date = parseDate(value);
    const year = date.getUTCFullYear() - 1;
    const month = date.getUTCMonth();
    const day = Math.min(date.getUTCDate(), new Date(Date.UTC(year, month + 1, 0)).getUTCDate());
    return dateInput(new Date(Date.UTC(year, month, day)));
  };
  const money = value => new Intl.NumberFormat("en-IN", {
    style: "currency", currency: "INR", minimumFractionDigits: 0, maximumFractionDigits: 2,
  }).format(Number(value) || 0);
  const hasPendingAmounts = value => Number(value?.sales_rows) > 0 &&
    Number(value?.cash_due_rows) >= Number(value.sales_rows) &&
    Number(value?.credit_due_rows) >= Number(value.sales_rows);
  const pendingAmount = value =>
    (Number(value?.cash_due) || 0) + (Number(value?.credit_due) || 0);
  const percentChange = value => `${Math.abs(value).toFixed(1)}%`;
  function changeIndicator(current, previous, lowerIsBetter = false) {
    const change = document.createElement("span");
    change.className = "facility-change";
    if (!previous) {
      change.hidden = true;
    } else {
      const difference = current - previous;
      const favorableChange = lowerIsBetter ? -difference : difference;
      change.classList.add(
        favorableChange > 0 ? "up" : favorableChange < 0 ? "down" : "flat",
      );
      change.textContent =
        `${difference > 0 ? "↑" : difference < 0 ? "↓" : "—"} ${percentChange(difference / Math.abs(previous) * 100)}`;
    }
    if (previous) {
      change.title = (lowerIsBetter
        ? "Lower is better: decreases are green; increases are red. "
        : "") +
        "Percent change = (current period − same period last year) ÷ last year's amount × 100";
      change.setAttribute("aria-label",
        `Change versus the same period last year, calculated using last year's amount as the base: ${change.textContent}`);
    }
    return change;
  }
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const reportTypes = [
    ["sales", "Sales revenue"],
    ["collection", "Collection revenue"],
    ["purchase", "Purchase value"],
  ];
  const selectedHospital = () => byId("uploadHospital").value;
  const selectedPharmacy = () => byId("batchPharmacy").value;

  function addOptions(select, values, selected) {
    select.replaceChildren();
    values.forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = value === selected;
      select.appendChild(option);
    });
  }

  function showImportStatus(message, kind = "") {
    const status = byId("importStatus");
    status.textContent = message;
    status.className = `status-message ${kind}`;
  }

  function renderPreviews() {
    const container = byId("batchPreviewList");
    container.replaceChildren(...filePreviews.map(preview => {
      const item = document.createElement("div");
      item.className = "batch-file-row";
      const name = document.createElement("strong");
      name.textContent = preview.file.name;
      const details = document.createElement("span");
      const label = reportTypes.find(([type]) => type === preview.reportType)[1];
      details.textContent = `${label} · ${preview.metadata.row_count.toLocaleString("en-IN")} rows`;
      item.append(name, details);
      return item;
    }));
    byId("previewCount").textContent =
      `${selectedHospital()} · ${pharmacyLabel(selectedPharmacy())} · one file for each report type.`;
    byId("previewArea").classList.remove("hidden");
  }

  function detectReportType(filename) {
    const words = filename.toLowerCase().replace(/[^a-z0-9]+/g, " ");
    const matches = reportTypes.filter(([type]) => {
      if (type === "sales") return /\b(sales|sale|revenue)\b/.test(words);
      if (type === "collection") return /\b(collection|collections)\b/.test(words);
      return /\b(purchase|purchases)\b/.test(words);
    });
    if (matches.length !== 1) {
      throw new Error(`${filename}: file name must clearly contain Sales, Collection, or Purchase.`);
    }
    return matches[0][0];
  }

  function validateSuggestedMapping(filename, reportType, mapping) {
    if (!mapping.date) throw new Error(`${filename}: transaction date column was not detected.`);
    if (reportType !== "purchase" && !mapping.amount) {
      throw new Error(`${filename}: amount column was not detected.`);
    }
    if (reportType === "purchase" && !mapping.amount && !(mapping.quantity && mapping.unit_cost)) {
      throw new Error(`${filename}: amount or both quantity and unit cost columns were not detected.`);
    }
  }

  async function previewFile() {
    const files = [...byId("reportFile").files];
    try {
      if (!files.length) throw new Error("Choose at least one Excel or CSV report file.");
      if (files.length > reportTypes.length) {
        throw new Error("Choose up to three files at a time: one Sales, one Collection, and one Purchase.");
      }
      if (files.reduce((total, file) => total + file.size, 0) > 50 * 1024 * 1024) {
        throw new Error("The combined file size exceeds the 50 MB limit.");
      }
      showImportStatus("Reading selected reports and finding their columns…");
      byId("previewButton").disabled = true;
      const reviewed = [];
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        const response = await fetch("/pharmacy-api/preview", {method: "POST", body: form});
        const metadata = await response.json();
        if (!response.ok) throw new Error(`${file.name}: ${metadata.error || "Preview failed."}`);
        const reportType = detectReportType(file.name);
        validateSuggestedMapping(file.name, reportType, metadata.suggestions);
        reviewed.push({
          file,
          metadata,
          reportType,
          mapping: metadata.suggestions,
        });
      }
      const foundTypes = reviewed.map(preview => preview.reportType);
      if (new Set(foundTypes).size !== foundTypes.length) {
        throw new Error("Choose only one file for each report type in this upload.");
      }
      reviewed.sort((first, second) =>
        reportTypes.findIndex(([type]) => type === first.reportType) -
        reportTypes.findIndex(([type]) => type === second.reportType)
      );
      filePreviews = reviewed;
      renderPreviews();
      showImportStatus(
        `${filePreviews.length} report${filePreviews.length === 1 ? "" : "s"} ready. Click Store and import all.`,
        "success"
      );
    } catch (error) {
      byId("previewArea").classList.add("hidden");
      filePreviews = [];
      showImportStatus(error.message || "The report previews could not be created.", "error");
    } finally {
      byId("previewButton").disabled = false;
    }
  }

  async function importFiles() {
    if (!filePreviews.length) return;
    try {
      const form = new FormData();
      filePreviews.forEach(preview => form.append("file", preview.file));
      form.append("hospital", selectedHospital());
      form.append("reports", JSON.stringify(filePreviews.map(preview => ({
        pharmacy: selectedPharmacy(),
        report_type: preview.reportType,
        mapping: preview.mapping,
      }))));
      showImportStatus(`Validating and storing ${filePreviews.length} files…`);
      byId("importButton").disabled = true;
      const response = await fetch("/pharmacy-api/batch-import", {method: "POST", body: form});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "The reports could not be stored.");
      byId("previewArea").classList.add("hidden");
      byId("reportFile").value = "";
      filePreviews = [];
      await loadUploads();
      const dashboardLoaded = await loadDashboard();
      const skippedNote = result.skipped_count
        ? ` ${result.skipped_count.toLocaleString("en-IN")} rows without a valid Bill Date or amount were skipped.`
        : "";
      if (dashboardLoaded) {
        showImportStatus(
          `${result.file_count} original files stored and ${result.row_count.toLocaleString("en-IN")} rows imported for ${result.hospital}.` +
          skippedNote,
          "success"
        );
        byId("metricGrid").scrollIntoView({behavior: "smooth", block: "start"});
      } else {
        showImportStatus(
          `${result.file_count} files stored, but the dashboard could not refresh. Use Refresh to try again.`,
          "error"
        );
      }
    } catch (error) {
      showImportStatus(error.message || "The reports could not be stored.", "error");
    } finally {
      byId("importButton").disabled = false;
    }
  }

  function setDefaultDates() {
    byId("periodFilter").value = "custom";
    byId("startDate").value = "2025-04-01";
    byId("endDate").value = "2026-03-31";
  }

  function yearRange() {
    const today = new Date();
    const range = {
      start: `${today.getFullYear()}-01-01`,
      end: `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`,
    };
    return clampRangeToAvailableData(range);
  }

  function clampRangeToAvailableData(range) {
    if (!latestTransactionDate) return range;
    const end = range.end > latestTransactionDate ? latestTransactionDate : range.end;
    return {
      start: range.start > end ? end : range.start,
      end,
    };
  }

  function currentRange() {
    const selectedPeriod = byId("periodFilter").value;
    if (selectedPeriod === "custom") {
      return clampRangeToAvailableData({
        start: byId("startDate").value,
        end: byId("endDate").value,
      });
    }
    if (selectedPeriod === "year") {
      return yearRange();
    }
    return yearRange();
  }

  function updateDateControls() {
    const customRange = byId("periodFilter").value === "custom";
    byId("startDateGroup").classList.toggle("hidden", !customRange);
    byId("endDateGroup").classList.toggle("hidden", !customRange);
    if (latestTransactionDate) {
      byId("startDate").max = latestTransactionDate;
      byId("endDate").max = latestTransactionDate;
    } else {
      byId("startDate").removeAttribute("max");
      byId("endDate").removeAttribute("max");
    }
  }

  function metricCard(label, reportType, current, comparison) {
    const now = current[reportType];
    const before = comparison[reportType];
    const isPending = reportType === "pending";
    const lowerIsBetter = isPending || reportType === "cash_due" ||
      reportType === "credit_due";
    const metricClass = isPending ? " pending-metric" : "";
    const pendingValueClass = isPending
      ? ` ${now.amount > 0 ? "pending-outstanding" : "pending-settled"}`
      : "";
    if (now.available === false) {
      const message = isPending
        ? "Sales report must include Cash Due and Credit Due for every row."
        : "Restart the dashboard server to load Cash Due and Credit Due totals.";
      return `<article class="metric-card${metricClass}"><div class="metric-label">${label}</div><div class="metric-value">Unavailable</div><div class="metric-compare">${message}</div></article>`;
    }
    if (!now.rows && !before.rows) {
      return `<article class="metric-card${metricClass}"><div class="metric-label">${label}</div><div class="metric-value">No data</div><div class="metric-compare">No data available for either period.</div></article>`;
    }
    if (!before.rows) {
      return `<article class="metric-card${metricClass}"><div class="metric-label">${label}</div><div class="metric-value${pendingValueClass}">${money(now.amount)}</div><div class="metric-compare">No comparison-period data.</div></article>`;
    }
    const difference = now.amount - before.amount;
    const percent = before.amount === 0 ? null : difference / Math.abs(before.amount) * 100;
    const trendClass = difference === 0
      ? "flat"
      : lowerIsBetter
        ? difference < 0 ? "up" : "down"
        : difference > 0 ? "up" : "down";
    const trendText = difference > 0 ? "↑" : difference < 0 ? "↓" : "→";
    const percentText = percent === null ? "" : ` (${percentChange(percent)})`;
    return `<article class="metric-card${metricClass}">
      <div class="metric-label">${label}</div>
      <div class="metric-value${pendingValueClass}">${money(now.amount)}</div>
      <div class="metric-compare">Same dates last year: ${money(before.amount)}<br>
        <span class="delta ${trendClass}">${trendText} ${money(Math.abs(difference))}${percentText} ${difference > 0 ? "higher" : difference < 0 ? "lower" : "unchanged"}</span>
      </div>
    </article>`;
  }

  function withPendingAmount(totals, dueTotals = {}) {
    const cashDueRows = Number(dueTotals.cash_due?.rows) || 0;
    const creditDueRows = Number(dueTotals.credit_due?.rows) || 0;
    const salesRows = Number(totals.sales?.rows) || 0;
    const pendingAvailable = salesRows > 0 &&
      cashDueRows === salesRows && creditDueRows === salesRows;
    return {
      ...totals,
      cash_due: dueTotals.cash_due
        ? {...dueTotals.cash_due, available: cashDueRows > 0}
        : {amount: 0, rows: 0, available: false},
      credit_due: dueTotals.credit_due
        ? {...dueTotals.credit_due, available: creditDueRows > 0}
        : {amount: 0, rows: 0, available: false},
      pending: {
        amount: pendingAvailable
          ? (Number(dueTotals.cash_due.amount) || 0) +
            (Number(dueTotals.credit_due.amount) || 0)
          : 0,
        rows: pendingAvailable ? salesRows : 0,
        available: pendingAvailable,
      },
    };
  }

  function renderSupplierChart(container, supplierRows, mode) {
    const totals = new Map();
    supplierRows.forEach(row => {
      const supplier = String(row.supplier || "").trim();
      if (!supplier || !Number.isFinite(Number(row.amount))) return;
      const key = supplier.toLocaleLowerCase("en-IN");
      const total = totals.get(key) || {supplier, amount: 0};
      total.amount += Number(row.amount);
      totals.set(key, total);
    });
    const sorted = [...totals.values()]
      .sort((first, second) =>
        second.amount - first.amount || first.supplier.localeCompare(second.supplier));
    let visible = sorted;
    if (mode === "top") {
      visible = sorted.slice(0, 5);
    } else if (mode === "bottom") {
      visible = sorted.slice(-5).reverse();
    } else if (mode === "ends") {
      const top = sorted.slice(0, 5);
      const topNames = new Set(top.map(row => row.supplier));
      visible = top.concat(sorted.slice(-5).reverse().filter(row => !topNames.has(row.supplier)));
    }
    if (!visible.length) {
      container.innerHTML = '<p class="empty-cell">No supplier purchase data for the selected filters.</p>';
      return;
    }
    const maximum = Math.max(...visible.map(row => Math.abs(row.amount)), 1);
    const highestCount = Math.min(5, sorted.length);
    const fragment = document.createDocumentFragment();
    visible.forEach((row, index) => {
      if (index === 0 && mode !== "all") {
        const title = document.createElement("h4");
        title.className = "supplier-group-label";
        title.textContent = mode === "bottom" ? "Lowest purchase amounts" : "Highest purchase amounts";
        fragment.appendChild(title);
      } else if (mode === "ends" && sorted.length > 5 && index === highestCount) {
        const title = document.createElement("h4");
        title.className = "supplier-group-label supplier-group-low";
        title.textContent = "Lowest purchase amounts";
        fragment.appendChild(title);
      }
      const item = document.createElement("div");
      item.className = "supplier-bar-row";
      item.setAttribute("aria-label", `${row.supplier}: ${money(row.amount)}`);
      const name = document.createElement("span");
      name.className = "supplier-bar-name";
      name.title = row.supplier;
      name.textContent = row.supplier;
      const track = document.createElement("span");
      track.className = "supplier-bar-track";
      const bar = document.createElement("span");
      bar.className = `supplier-bar${mode === "bottom" || (mode === "ends" && index >= highestCount) ? " supplier-bar-low" : ""}`;
      bar.style.width = `${Math.max(row.amount ? 2 : 0, Math.abs(row.amount) / maximum * 100)}%`;
      track.appendChild(bar);
      const amount = document.createElement("strong");
      amount.className = "supplier-bar-amount";
      amount.textContent = money(row.amount);
      item.append(name, track, amount);
      fragment.appendChild(item);
    });
    container.replaceChildren(fragment);
  }

  function aggregateFacilitiesByHospital(facilities) {
    const totals = new Map(hospitals.map(hospital => [hospital, {
      sales: 0, collection: 0, purchase: 0, sales_rows: 0,
      collection_rows: 0, purchase_rows: 0, cash_due: 0, credit_due: 0,
      cash_due_rows: 0, credit_due_rows: 0,
    }]));
    facilities.forEach(facility => {
      const total = totals.get(facility.hospital);
      if (!total) return;
      ["sales", "collection", "purchase", "sales_rows", "collection_rows",
        "purchase_rows", "cash_due", "credit_due", "cash_due_rows",
        "credit_due_rows"].forEach(type => {
        total[type] += Number(facility[type]) || 0;
      });
    });
    return totals;
  }

  function appendHospitalComparisonCell(row, amount, available,
    previousAmount, previousAvailable, options = {}) {
    const cell = document.createElement("td");
    if (options.pending) cell.className = "hospital-pending-cell";
    const value = document.createElement("strong");
    value.className = "hospital-comparison-value";
    value.textContent = available ? money(amount) : (options.noData ? "No data" : "Unavailable");
    if (options.pending && available && amount > 0) {
      value.classList.add("pending-outstanding");
    }
    cell.appendChild(value);
    if (available && previousAvailable && previousAmount !== 0) {
      const change = changeIndicator(amount, previousAmount, Boolean(options.lowerIsBetter));
      change.classList.add("hospital-comparison-change");
      change.append(document.createTextNode(" vs last year"));
      cell.appendChild(change);
    } else {
      const note = document.createElement("span");
      note.className = "hospital-comparison-change-neutral";
      note.textContent = previousAvailable ? "No prior value" : "No prior data";
      cell.appendChild(note);
    }
    row.appendChild(cell);
  }

  function hasHospitalReportData(amount, rows) {
    const numericAmount = Number(amount);
    return Number(rows) > 0 || (Number.isFinite(numericAmount) && numericAmount !== 0);
  }

  function renderHospitalComparison(facilities, comparisonFacilities, range) {
    const container = byId("hospitalFacilitySummaries");
    const currentByHospital = aggregateFacilitiesByHospital(facilities);
    const previousByHospital = aggregateFacilitiesByHospital(comparisonFacilities);
    const rows = hospitals.map((hospital, index) => {
      const current = currentByHospital.get(hospital);
      const previous = previousByHospital.get(hospital);
      const row = document.createElement("tr");
      const headingCell = document.createElement("th");
      headingCell.scope = "row";
      headingCell.className = "hospital-comparison-name";
      const heading = document.createElement("span");
      heading.className = "hospital-facility-title hospital-key-" + index;
      heading.textContent = hospital;
      headingCell.appendChild(heading);
      row.appendChild(headingCell);
      const hasSales = current.sales_rows > 0;
      const hadSales = previous.sales_rows > 0;
      const cashAvailable = hasSales && current.cash_due_rows >= current.sales_rows;
      const previousCashAvailable = hadSales &&
        previous.cash_due_rows >= previous.sales_rows;
      const creditAvailable = hasSales && current.credit_due_rows >= current.sales_rows;
      const previousCreditAvailable = hadSales &&
        previous.credit_due_rows >= previous.sales_rows;
      const pendingAvailable = cashAvailable && creditAvailable;
      const previousPendingAvailable = previousCashAvailable && previousCreditAvailable;
      appendHospitalComparisonCell(row, current.sales, hasSales,
        previous.sales, hadSales, {noData: true});
      appendHospitalComparisonCell(row, current.collection,
        hasHospitalReportData(current.collection, current.collection_rows),
        previous.collection,
        hasHospitalReportData(previous.collection, previous.collection_rows),
        {noData: true});
      appendHospitalComparisonCell(row, current.purchase,
        hasHospitalReportData(current.purchase, current.purchase_rows),
        previous.purchase,
        hasHospitalReportData(previous.purchase, previous.purchase_rows),
        {noData: true});
      appendHospitalComparisonCell(row,
        current.cash_due + current.credit_due, pendingAvailable,
        previous.cash_due + previous.credit_due, previousPendingAvailable,
        {pending: true, noData: !hasSales,
          lowerIsBetter: true});
      appendHospitalComparisonCell(row, current.cash_due, cashAvailable,
        previous.cash_due, previousCashAvailable,
        {noData: !hasSales, lowerIsBetter: true});
      appendHospitalComparisonCell(row, current.credit_due, creditAvailable,
        previous.credit_due, previousCreditAvailable,
        {noData: !hasSales, lowerIsBetter: true});
      return row;
    });
    container.replaceChildren(...rows);
  }

  function renderHospitalSupplierCharts(supplierRows, mode, range) {
    const container = byId("hospitalSupplierCharts");
    const cards = hospitals.map((hospital, index) => {
      const card = document.createElement("article");
      card.className = "hospital-supplier-card";
      const heading = document.createElement("h3");
      heading.className = `hospital-supplier-card-title hospital-key-${index}`;
      heading.textContent = hospital;
      const chart = document.createElement("div");
      chart.className = "supplier-chart supplier-chart-compact";
      renderSupplierChart(
        chart,
        supplierRows.filter(row => row.hospital === hospital),
        mode,
      );
      card.append(heading, chart);
      return card;
    });
    container.replaceChildren(...cards);
  }

  function setDashboardModule(module) {
    const isHospitalModule = module === "hospitals";
    const isDataModule = module === "data";
    document.body.dataset.module = isDataModule
      ? "data"
      : isHospitalModule ? "hospitals" : "pharmacy";
    document.querySelectorAll(".module-tab").forEach(button => {
      if (button.dataset.module === document.body.dataset.module) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    byId("comparison-title").textContent =
      isDataModule ? "Data management" :
        isHospitalModule ? "Hospital comparison date range" : "Pharmacy performance";
    byId("filterEyebrow").textContent =
      isDataModule ? "REPORT FILES" :
        isHospitalModule ? "ALL HOSPITALS · SAME DATES LAST YEAR" : "SAME DATES · YEAR-ON-YEAR";
  }

  function renderFacilities(currentFacilities, comparisonFacilities, selectedHospital, selectedPharmacy) {
    const visibleHospitals = selectedHospital === "all"
      ? hospitals
      : hospitals.filter(hospital => hospital === selectedHospital);
    const keys = visibleHospitals.flatMap(hospital =>
      pharmacyOptions(hospital)
        .filter(pharmacy => selectedPharmacy === "all" || pharmacy === selectedPharmacy)
        .map(pharmacy => ({hospital, pharmacy}))
    );
    const toMap = facilities => new Map(facilities.map(item => [`${item.hospital}|${item.pharmacy}`, item]));
    const currentRows = toMap(currentFacilities);
    const comparisonRows = toMap(comparisonFacilities);
    const rows = keys.map(({hospital, pharmacy}) => {
      const key = `${hospital}|${pharmacy}`;
      const current = currentRows.get(key) || {};
      const comparison = comparisonRows.get(key) || {};
      const row = document.createElement("tr");
      const hospitalCell = document.createElement("td");
      const hospitalButton = document.createElement("button");
      hospitalButton.type = "button";
      hospitalButton.className = "facility-filter-button";
      hospitalButton.textContent = hospital;
      hospitalButton.dataset.hospital = hospital;
      hospitalCell.appendChild(hospitalButton);
      row.appendChild(hospitalCell);
      const pharmacyCell = document.createElement("td");
      const pharmacyButton = document.createElement("button");
      pharmacyButton.type = "button";
      pharmacyButton.className = "facility-filter-button";
      pharmacyButton.textContent = pharmacyLabel(pharmacy);
      pharmacyButton.dataset.hospital = hospital;
      pharmacyButton.dataset.pharmacy = pharmacy;
      pharmacyCell.appendChild(pharmacyButton);
      row.appendChild(pharmacyCell);
      reportTypes.forEach(([type]) => {
        const currentAmount = Number(current[type]) || 0;
        const previousAmount = Number(comparison[type]) || 0;
        const currentCell = document.createElement("td");
        currentCell.className = "numeric facility-current";
        const currentValue = document.createElement("strong");
        currentValue.className = "facility-amount";
        currentValue.textContent = money(currentAmount);
        currentCell.appendChild(currentValue);
        const change = changeIndicator(currentAmount, previousAmount);
        currentCell.appendChild(change);
        row.appendChild(currentCell);

        const previousCell = document.createElement("td");
        previousCell.className = "numeric facility-previous";
        previousCell.textContent = money(previousAmount);
        row.appendChild(previousCell);
      });
      [current, comparison].forEach((facility, index, facilities) => {
        const cell = document.createElement("td");
        cell.className = `numeric ${index ? "facility-previous" : "facility-current"}`;
        const available = hasPendingAmounts(facility);
        if (available) {
          const amount = document.createElement("strong");
          amount.className = "facility-amount";
          amount.textContent = money(pendingAmount(facility));
          cell.appendChild(amount);
          if (!index && hasPendingAmounts(facilities[1])) {
            cell.appendChild(changeIndicator(
              pendingAmount(facility),
              pendingAmount(facilities[1]),
              true,
            ));
          }
        } else {
          cell.textContent = Number(facility.sales_rows) > 0 ? "Unavailable" : "No data";
        }
        row.appendChild(cell);
      });
      return row;
    });
    const currentTotals = Object.fromEntries(reportTypes.map(([type]) => [type, 0]));
    const comparisonTotals = Object.fromEntries(reportTypes.map(([type]) => [type, 0]));
    const currentPendingTotals = {
      sales_rows: 0, cash_due_rows: 0, credit_due_rows: 0, cash_due: 0, credit_due: 0,
    };
    const comparisonPendingTotals = {...currentPendingTotals};
    keys.forEach(({hospital, pharmacy}) => {
      const key = `${hospital}|${pharmacy}`;
      const current = currentRows.get(key) || {};
      const comparison = comparisonRows.get(key) || {};
      reportTypes.forEach(([type]) => {
        currentTotals[type] += Number(current[type]) || 0;
        comparisonTotals[type] += Number(comparison[type]) || 0;
      });
      ["sales_rows", "cash_due_rows", "credit_due_rows", "cash_due", "credit_due"].forEach(type => {
        currentPendingTotals[type] += Number(current[type]) || 0;
        comparisonPendingTotals[type] += Number(comparison[type]) || 0;
      });
    });
    const totalRow = document.createElement("tr");
    totalRow.className = "facility-grand-total";
    const totalLabel = document.createElement("td");
    totalLabel.colSpan = 2;
    totalLabel.textContent = "Grand total";
    totalRow.appendChild(totalLabel);
    reportTypes.forEach(([type]) => {
      const currentCell = document.createElement("td");
      currentCell.className = "numeric facility-current";
      const currentValue = document.createElement("strong");
      currentValue.textContent = money(currentTotals[type]);
      currentCell.append(currentValue, changeIndicator(currentTotals[type], comparisonTotals[type]));
      totalRow.appendChild(currentCell);
      const comparisonCell = document.createElement("td");
      comparisonCell.className = "numeric facility-previous";
      comparisonCell.textContent = money(comparisonTotals[type]);
      totalRow.appendChild(comparisonCell);
    });
    const currentPendingCell = document.createElement("td");
    currentPendingCell.className = "numeric facility-current";
    const currentPendingAvailable = hasPendingAmounts(currentPendingTotals);
    const comparisonPendingAvailable = hasPendingAmounts(comparisonPendingTotals);
    if (currentPendingAvailable) {
      const amount = document.createElement("strong");
      amount.className = "facility-amount";
      amount.textContent = money(pendingAmount(currentPendingTotals));
      currentPendingCell.appendChild(amount);
      if (comparisonPendingAvailable) {
        currentPendingCell.appendChild(changeIndicator(
          pendingAmount(currentPendingTotals),
          pendingAmount(comparisonPendingTotals),
          true,
        ));
      }
    } else {
      currentPendingCell.textContent = currentPendingTotals.sales_rows > 0 ? "Unavailable" : "No data";
    }
    totalRow.appendChild(currentPendingCell);
    const comparisonPendingCell = document.createElement("td");
    comparisonPendingCell.className = "numeric facility-previous";
    comparisonPendingCell.textContent = comparisonPendingAvailable
      ? money(pendingAmount(comparisonPendingTotals))
      : comparisonPendingTotals.sales_rows > 0 ? "Unavailable" : "No data";
    totalRow.appendChild(comparisonPendingCell);
    rows.push(totalRow);
    byId("facilityRows").replaceChildren(...rows);
  }

  function renderDailyTotals(points, comparisonPoints) {
    const rows = byId("dailyRows");
    if (!points.length && !comparisonPoints.length) {
      rows.innerHTML = '<tr><td colspan="5" class="empty-cell">No data in this period.</td></tr>';
      return;
    }
    const monthlyTotals = new Map();
    const comparisonMonthlyTotals = new Map();
    comparisonPoints.forEach(point => {
      const month = point.month || point.date?.slice(0, 7);
      if (month) comparisonMonthlyTotals.set(month, point);
    });
    points.forEach(point => {
      const month = point.month || point.date?.slice(0, 7);
      if (!month) return;
      const total = monthlyTotals.get(month) || {
        month, sales: 0, collection: 0, purchase: 0, sales_rows: 0,
        cash_due: 0, credit_due: 0, cash_due_rows: 0, credit_due_rows: 0,
      };
      reportTypes.forEach(([type]) => {
        total[type] += Number(point[type]) || 0;
      });
      ["sales_rows", "cash_due", "credit_due", "cash_due_rows", "credit_due_rows"]
        .forEach(type => {
          total[type] += Number(point[type]) || 0;
        });
      monthlyTotals.set(month, total);
    });
    if (!monthlyTotals.size) {
      rows.innerHTML = '<tr><td colspan="5" class="empty-cell">No monthly totals available.</td></tr>';
      return;
    }
    const monthlyRows = [...monthlyTotals.values()].map(point => {
      const row = document.createElement("tr");
      [
        displayMonth(point.month),
        money(point.sales),
        money(point.collection),
        money(point.purchase),
      ].forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index > 0) {
          cell.className = "numeric";
          const type = reportTypes[index - 1][0];
          const previousMonth = `${Number(point.month.slice(0, 4)) - 1}${point.month.slice(4)}`;
          const previousAmount = Number(comparisonMonthlyTotals.get(previousMonth)?.[type]) || 0;
          cell.appendChild(changeIndicator(Number(point[type]) || 0, previousAmount));
        }
        row.appendChild(cell);
      });
      const previousMonth = `${Number(point.month.slice(0, 4)) - 1}${point.month.slice(4)}`;
      const previousPoint = comparisonMonthlyTotals.get(previousMonth);
      const pendingCell = document.createElement("td");
      pendingCell.className = "numeric";
      pendingCell.textContent = hasPendingAmounts(point)
        ? money(pendingAmount(point))
        : Number(point.sales_rows) > 0 ? "Unavailable" : "No data";
      if (hasPendingAmounts(point) && hasPendingAmounts(previousPoint)) {
        pendingCell.appendChild(changeIndicator(
          pendingAmount(point), pendingAmount(previousPoint),
        ));
      }
      row.appendChild(pendingCell);
      return row;
    });
    const grandTotal = [...monthlyTotals.values()].reduce((total, point) => {
      reportTypes.forEach(([type]) => {
        total[type] += point[type];
      });
      ["sales_rows", "cash_due", "credit_due", "cash_due_rows", "credit_due_rows"]
        .forEach(type => {
          total[type] += Number(point[type]) || 0;
        });
      return total;
    }, {
      sales: 0, collection: 0, purchase: 0, sales_rows: 0,
      cash_due: 0, credit_due: 0, cash_due_rows: 0, credit_due_rows: 0,
    });
    const comparisonGrandTotal = [...comparisonMonthlyTotals.values()].reduce((total, point) => {
      reportTypes.forEach(([type]) => {
        total[type] += Number(point[type]) || 0;
      });
      ["sales_rows", "cash_due", "credit_due", "cash_due_rows", "credit_due_rows"]
        .forEach(type => {
          total[type] += Number(point[type]) || 0;
        });
      return total;
    }, {
      sales: 0, collection: 0, purchase: 0, sales_rows: 0,
      cash_due: 0, credit_due: 0, cash_due_rows: 0, credit_due_rows: 0,
    });
    const totalRow = document.createElement("tr");
    totalRow.className = "monthly-grand-total";
    [
      "Grand total",
      ...reportTypes.map(([type]) => money(grandTotal[type])),
      hasPendingAmounts(grandTotal) ? money(pendingAmount(grandTotal)) : "Unavailable",
    ]
      .forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index > 0) {
          cell.className = "numeric";
          if (index <= reportTypes.length) {
            const type = reportTypes[index - 1][0];
            cell.appendChild(changeIndicator(grandTotal[type], comparisonGrandTotal[type]));
          } else if (hasPendingAmounts(grandTotal) && hasPendingAmounts(comparisonGrandTotal)) {
            cell.appendChild(changeIndicator(
              pendingAmount(grandTotal), pendingAmount(comparisonGrandTotal),
            ));
          }
        }
        totalRow.appendChild(cell);
      });
    monthlyRows.push(totalRow);
    rows.replaceChildren(...monthlyRows);
  }

  function renderMonthlyChart(currentPoints, comparisonPoints, reportType) {
    const chart = byId("monthlyChart");
    const toMonthlyMap = points => {
      const monthly = new Map();
      points.forEach(point => {
        const month = point.month || point.date?.slice(0, 7);
        if (!month) return;
        const total = monthly.get(month) || {sales: 0, collection: 0, purchase: 0};
        ["sales", "collection", "purchase"].forEach(type => {
          total[type] += Number(point[type]) || 0;
        });
        monthly.set(month, total);
      });
      return monthly;
    };
    const currentMonths = toMonthlyMap(currentPoints);
    const previousMonths = toMonthlyMap(comparisonPoints);
    const range = currentRange();
    const months = [...new Set([
      ...currentMonths.keys(),
      ...[...previousMonths.keys()].map(month =>
        `${Number(month.slice(0, 4)) + 1}${month.slice(4)}`),
    ])].filter(Boolean).sort().filter(month => {
      const [year, monthNumber] = month.split("-").map(Number);
      const previousMonth = `${year - 1}-${String(monthNumber).padStart(2, "0")}`;
      return (Number(currentMonths.get(month)?.[reportType]) || 0) !== 0 ||
        (Number(previousMonths.get(previousMonth)?.[reportType]) || 0) !== 0;
    });
    if (!months.length) {
      chart.innerHTML = '<p class="empty-cell">No non-zero monthly data for this report in the selected period.</p>';
      return;
    }
    const values = months.flatMap(month => {
      const [year, monthNumber] = month.split("-").map(Number);
      const previousMonth = `${year - 1}-${String(monthNumber).padStart(2, "0")}`;
      return [
        Number(currentMonths.get(month)?.[reportType]) || 0,
        Number(previousMonths.get(previousMonth)?.[reportType]) || 0,
      ];
    });
    const maximum = Math.max(...values, 1);
    const groups = months.map(month => {
      const [year, monthNumber] = month.split("-").map(Number);
      const previousMonth = `${year - 1}-${String(monthNumber).padStart(2, "0")}`;
      const currentAmount = Number(currentMonths.get(month)?.[reportType]) || 0;
      const previousAmount = Number(previousMonths.get(previousMonth)?.[reportType]) || 0;
      const values = [
        currentAmount ? `Selected period ${money(currentAmount)}` : "Selected period: no data",
        previousAmount ? `Same period last year ${money(previousAmount)}` : "Same period last year: no data",
      ].filter(Boolean);
      const group = document.createElement("button");
      group.type = "button";
      group.className = "month-chart-group";
      group.title = `${displayMonth(month)}: ${values.join("; ")}. Select to view this month.`;
      group.setAttribute("aria-label", group.title);
      group.addEventListener("click", () => selectMonth(month));
      const valueLabels = document.createElement("span");
      valueLabels.className = "month-chart-values";
      [
        ["current", currentAmount],
        ["previous", previousAmount],
      ].forEach(([period, amount]) => {
        if (!amount) return;
        const value = document.createElement("span");
        value.className = `month-chart-value ${period}`;
        value.textContent = money(amount);
        valueLabels.appendChild(value);
      });
      const monthChange = changeIndicator(currentAmount, previousAmount);
      monthChange.classList.add("month-chart-change");
      valueLabels.appendChild(monthChange);
      const bars = document.createElement("span");
      bars.className = "month-chart-bars";
      [
        ["current", currentAmount],
        ["previous", previousAmount],
      ].forEach(([period, amount]) => {
        const bar = document.createElement("span");
        bar.className = `month-chart-bar ${period}`;
        bar.style.height = `${Math.max(amount ? 4 : 0, amount / maximum * 100)}%`;
        bar.setAttribute("aria-hidden", "true");
        bars.appendChild(bar);
      });
      const label = document.createElement("span");
      label.className = "month-chart-label";
      label.textContent = displayMonth(month);
      group.append(valueLabels, bars, label);
      return group;
    });
    const currentTotal = currentPoints.reduce(
      (total, point) => total + (Number(point[reportType]) || 0), 0,
    );
    const previousTotal = comparisonPoints.reduce(
      (total, point) => total + (Number(point[reportType]) || 0), 0,
    );
    const totalGroup = document.createElement("div");
    totalGroup.className = "month-chart-group month-chart-total";
    totalGroup.setAttribute("role", "group");
    totalGroup.setAttribute(
      "aria-label",
      `Grand total: selected period ${money(currentTotal)}; same period last year ${money(previousTotal)}`,
    );
    const totalLabels = document.createElement("span");
    totalLabels.className = "month-chart-values";
    [["current", currentTotal], ["previous", previousTotal]].forEach(([period, amount]) => {
      const value = document.createElement("span");
      value.className = `month-chart-value ${period}`;
      value.textContent = money(amount);
      totalLabels.appendChild(value);
    });
    const totalChange = changeIndicator(currentTotal, previousTotal);
    totalChange.classList.add("month-chart-change");
    totalLabels.appendChild(totalChange);
    const totalBars = document.createElement("span");
    totalBars.className = "month-chart-bars";
    totalBars.setAttribute("aria-hidden", "true");
    const totalLabel = document.createElement("span");
    totalLabel.className = "month-chart-label";
    totalLabel.textContent = "Grand total";
    totalGroup.append(totalLabels, totalBars, totalLabel);
    groups.push(totalGroup);
    chart.replaceChildren(...groups);
  }

  function compactMoney(value) {
    return new Intl.NumberFormat("en-IN", {
      style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1,
    }).format(Number(value) || 0);
  }

  function monthKeys(range) {
    const start = parseDate(range.start);
    const end = parseDate(range.end);
    const month = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
    const finalMonth = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), 1));
    const keys = [];
    while (month <= finalMonth) {
      keys.push(`${month.getUTCFullYear()}-${String(month.getUTCMonth() + 1).padStart(2, "0")}`);
      month.setUTCMonth(month.getUTCMonth() + 1);
    }
    return keys;
  }

  function renderHospitalMonthlyChart(points, reportType, range) {
    const chart = byId("hospitalMonthlyChart");
    const legend = byId("hospitalMonthlyLegend");
    legend.replaceChildren(...hospitals.map((hospital, index) => {
      const item = document.createElement("span");
      const key = document.createElement("i");
      key.className = `hospital-key-${index}`;
      item.append(key, document.createTextNode(hospital));
      return item;
    }));
    if (!points.length) {
      chart.innerHTML = '<p class="empty-cell">No hospital data for this period.</p>';
      return;
    }
    const byMonthHospital = new Map(points.map(point => [
      `${point.month}|${point.hospital}`,
      {
        amount: Number(point[reportType]) || 0,
        rows: Number(point[`${reportType}_rows`]) || 0,
      },
    ]));
    const months = monthKeys(range);
    const maximum = Math.max(
      1,
      ...months.flatMap(month => hospitals.map(hospital =>
        byMonthHospital.get(`${month}|${hospital}`)?.amount || 0)),
    );
    const groups = months.map(month => {
      const group = document.createElement("div");
      group.className = "hospital-month-group";
      group.setAttribute("role", "group");
      group.setAttribute("aria-label", displayMonth(month));
      const bars = document.createElement("div");
      bars.className = "hospital-month-bars";
      hospitals.forEach((hospital, index) => {
        const periodData = byMonthHospital.get(`${month}|${hospital}`);
        const amount = periodData?.amount || 0;
        const series = document.createElement("div");
        series.className = "hospital-month-series";
        const valueLabel = document.createElement("span");
        valueLabel.className = "hospital-month-value";
        valueLabel.textContent = periodData?.rows ? compactMoney(amount) : "—";
        const track = document.createElement("span");
        track.className = "hospital-month-track";
        const barHeight = amount ? Math.max(3, amount / maximum * 82) : 0;
        track.style.setProperty("--bar-height", `${barHeight}%`);
        const bar = document.createElement("span");
        bar.className = `hospital-month-bar hospital-${index}`;
        bar.style.height = `${barHeight}%`;
        bar.title = periodData?.rows
          ? `${hospital} · ${displayMonth(month)} · ${money(amount)}`
          : `${hospital} · ${displayMonth(month)} · No ${reportType} report data`;
        bar.setAttribute("aria-label", bar.title);
        track.append(valueLabel, bar);
        series.appendChild(track);
        bars.appendChild(series);
      });
      const label = document.createElement("span");
      label.className = "hospital-month-label";
      label.textContent = displayMonth(month);
      group.append(bars, label);
      return group;
    });
    chart.replaceChildren(...groups);
  }

  function selectMonth(month) {
    if (!previousMonthRange) {
      previousMonthRange = {
        period: byId("periodFilter").value,
        start: byId("startDate").value,
        end: byId("endDate").value,
      };
    }
    const [year, monthNumber] = month.split("-").map(Number);
    const start = new Date(Date.UTC(year, monthNumber - 1, 1));
    const end = new Date(Date.UTC(year, monthNumber, 0));
    const toInput = value => value.toISOString().slice(0, 10);
    byId("periodFilter").value = "custom";
    byId("startDate").value = toInput(start);
    byId("endDate").value = toInput(end);
    byId("resetMonthFilter").hidden = false;
    updateDateControls();
    loadDashboard();
    byId("comparison-title").scrollIntoView({behavior: "smooth", block: "start"});
  }

  async function loadDashboard() {
    const requestId = ++dashboardLoadRequest;
    const current = currentRange();
    if (byId("periodFilter").value === "custom") {
      byId("startDate").value = current.start;
      byId("endDate").value = current.end;
    }
    if (!current.start || !current.end || current.start > current.end) {
      dashboardLoadRequest += 1;
      byId("comparisonCaption").textContent = "Select a valid date range.";
      byId("hospitalSupplierCharts").innerHTML =
        '<p class="empty-cell">Select a valid date range.</p>';
      byId("hospitalFacilitySummaries").innerHTML =
        '<tr><td colspan="7" class="hospital-summary-empty">Select a valid date range.</td></tr>';
      byId("metricGrid").innerHTML = '<div class="metric-card loading-card">The start date must be on or before the end date.</div>';
      return false;
    }
    const comparison = {start: shiftYear(current.start), end: shiftYear(current.end)};
    const params = new URLSearchParams({
      ...current,
      compare_start: comparison.start,
      compare_end: comparison.end,
      hospital: byId("hospitalFilter").value,
      pharmacy: byId("pharmacyFilter").value,
    });
    byId("comparisonCaption").textContent = periodCaption(current);
    byId("hospitalFacilitySummaries").innerHTML =
      '<tr><td colspan="7" class="hospital-summary-empty">Loading hospital comparison…</td></tr>';
    byId("hospitalSupplierCharts").innerHTML =
      '<p class="empty-cell">Loading hospital supplier purchases…</p>';
    byId("metricGrid").innerHTML = '<div class="metric-card loading-card">Loading comparison…</div>';
    let kpisRendered = false;
    try {
      const response = await fetch(`/pharmacy-api/summary?${params}`);
      const data = await response.json();
      if (requestId !== dashboardLoadRequest) return false;
      if (!response.ok) throw new Error(data.error || "Dashboard load nahi ho saka.");
      if (data.latest_date !== null &&
          (typeof data.latest_date !== "string" ||
           !/^\d{4}-\d{2}-\d{2}$/.test(data.latest_date))) {
        throw new Error("Latest available report date is invalid.");
      }
      if (data.latest_date !== latestTransactionDate) {
        latestTransactionDate = data.latest_date;
        updateDateControls();
        const availableRange = currentRange();
        if (availableRange.start !== current.start || availableRange.end !== current.end) {
          if (byId("periodFilter").value === "custom") {
            byId("startDate").value = availableRange.start;
            byId("endDate").value = availableRange.end;
          }
          return loadDashboard();
        }
      }
      const hospitalComparison = data.hospital_comparison;
      if (!hospitalComparison || !Array.isArray(hospitalComparison.current?.facilities) ||
          !Array.isArray(hospitalComparison.comparison?.facilities) ||
          !Array.isArray(hospitalComparison.current?.supplier_totals)) {
        throw new Error("Hospital comparison data could not be loaded.");
      }
      byId("comparisonCaption").textContent = periodCaption(current);
      currentHospitalMonthly = Array.isArray(data.hospital_monthly)
        ? data.hospital_monthly
        : [];
      currentSupplierRows = Array.isArray(data.current.supplier_totals)
        ? data.current.supplier_totals
        : [];
      currentHospitalSupplierRows = hospitalComparison.current.supplier_totals;
      if (!Array.isArray(currentHospitalSupplierRows)) {
        throw new Error("Hospital supplier data could not be loaded.");
      }
      renderSupplierChart(
        byId("supplierChart"),
        currentSupplierRows,
        byId("supplierChartMode").value,
      );
      renderHospitalComparison(
        hospitalComparison.current.facilities,
        hospitalComparison.comparison.facilities,
        current,
      );
      renderHospitalSupplierCharts(
        currentHospitalSupplierRows,
        byId("hospitalSupplierChartMode").value,
        current,
      );
      let comparisonTrend = data.trend_comparison;
      if (!Array.isArray(comparisonTrend)) {
        const earlierComparison = {
          start: shiftYear(comparison.start),
          end: shiftYear(comparison.end),
        };
        const comparisonParams = new URLSearchParams({
          ...comparison,
          compare_start: earlierComparison.start,
          compare_end: earlierComparison.end,
          hospital: byId("hospitalFilter").value,
          pharmacy: byId("pharmacyFilter").value,
        });
        const comparisonResponse = await fetch(`/pharmacy-api/summary?${comparisonParams}`);
        const comparisonData = await comparisonResponse.json();
        if (requestId !== dashboardLoadRequest) return false;
        if (!comparisonResponse.ok) {
          throw new Error(comparisonData.error || "Comparison trend could not be loaded.");
        }
        comparisonTrend = comparisonData.trend;
      }
      const currentTotals = withPendingAmount(data.current.totals, data.current.due_totals);
      const comparisonTotals = withPendingAmount(
        data.comparison.totals, data.comparison.due_totals,
      );
      byId("metricGrid").innerHTML = [
        ...reportTypes.map(([key, label]) =>
          metricCard(label, key, currentTotals, comparisonTotals)),
        metricCard("Cash due", "cash_due", currentTotals, comparisonTotals),
        metricCard("Credit due", "credit_due", currentTotals, comparisonTotals),
        metricCard("Total pending amount", "pending", currentTotals, comparisonTotals),
      ].join("");
      kpisRendered = true;
      renderFacilities(
        data.facility_comparison.current,
        data.facility_comparison.comparison,
        byId("hospitalFilter").value,
        byId("pharmacyFilter").value,
      );
      renderDailyTotals(data.trend, comparisonTrend);
      renderMonthlyChart(
        data.trend,
        comparisonTrend,
        byId("chartMetric").value,
      );
      renderHospitalMonthlyChart(
        currentHospitalMonthly,
        byId("hospitalMonthlyMetric").value,
        current,
      );
      appliedDashboardState = {
        ...current,
        hospital: byId("hospitalFilter").value,
        pharmacy: byId("pharmacyFilter").value,
        period: byId("periodFilter").value,
        chartMetric: byId("chartMetric").value,
        hospitalMonthlyMetric: byId("hospitalMonthlyMetric").value,
        hospitalSupplierChartMode: byId("hospitalSupplierChartMode").value,
        supplierChartMode: byId("supplierChartMode").value,
      };
      return true;
    } catch (error) {
      if (requestId !== dashboardLoadRequest) return false;
      byId("hospitalFacilitySummaries").innerHTML =
        `<tr><td colspan="7" class="hospital-summary-empty">${escapeHtml(error.message)}</td></tr>`;
      byId("hospitalSupplierCharts").innerHTML =
        `<p class="empty-cell">${escapeHtml(error.message)}</p>`;
      if (!kpisRendered) {
        byId("metricGrid").innerHTML = `<div class="metric-card loading-card">${escapeHtml(error.message)}</div>`;
      } else {
        byId("dailyRows").innerHTML =
          `<tr><td colspan="5" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
        byId("monthlyChart").innerHTML =
          `<p class="empty-cell">${escapeHtml(error.message)}</p>`;
        byId("hospitalMonthlyChart").innerHTML =
          `<p class="empty-cell">${escapeHtml(error.message)}</p>`;
      }
      return false;
    }
  }

  async function downloadInteractiveHtml() {
    const button = byId("downloadHtmlButton");
    button.disabled = true;
    try {
      const [dataResponse, styleResponse] = await Promise.all([
        fetch("/pharmacy-api/export-data"),
        fetch("/styles.css"),
      ]);
      let data;
      if (dataResponse.ok) {
        data = await dataResponse.json();
      } else if (dataResponse.status === 404) {
        const pharmacies = hospitals.flatMap(hospital =>
          pharmacyOptions(hospital).map(pharmacy => ({hospital, pharmacy}))
        );
        const exports = await Promise.all(pharmacies.map(async ({hospital, pharmacy}) => {
          const params = new URLSearchParams({
            start: "1900-01-01",
            end: "2099-12-31",
            compare_start: "1899-01-01",
            compare_end: "2098-12-31",
            hospital,
            pharmacy,
          });
          const summaryResponse = await fetch(`/pharmacy-api/summary?${params}`);
          const summary = await summaryResponse.json();
          if (!summaryResponse.ok) {
            throw new Error(summary.error || "Dashboard export data could not be prepared.");
          }
          return summary.trend.map(point => ({...point, hospital, pharmacy}));
        }));
        const monthly = exports.flat();
        data = {
          hospitals,
          pharmacies: Object.fromEntries(hospitals.map(hospital => [
            hospital,
            pharmacyOptions(hospital),
          ])),
          monthly,
          daily: monthly.map(point => ({
            ...point,
            date: `${point.month}-01`,
            sales_rows: Number(point.sales) ? 1 : 0,
            collection_rows: Number(point.collection) ? 1 : 0,
            purchase_rows: Number(point.purchase) ? 1 : 0,
            cash_due_rows: Number(point.cash_due_rows) || 0,
            credit_due_rows: Number(point.credit_due_rows) || 0,
          })),
          granularity: "month",
        };
      } else {
        const result = await dataResponse.json();
        throw new Error(result.error || "Dashboard export data could not be prepared.");
      }
      if (!Array.isArray(data.daily)) {
        throw new Error("Dashboard export data is missing its daily totals.");
      }
      if (!styleResponse.ok) {
        throw new Error("Dashboard styles could not be loaded for the export.");
      }
      const stylesheet = await styleResponse.text();
      const state = appliedDashboardState || {
        ...currentRange(),
        hospital: byId("hospitalFilter").value,
        pharmacy: byId("pharmacyFilter").value,
        period: byId("periodFilter").value,
        chartMetric: byId("chartMetric").value,
        hospitalMonthlyMetric: byId("hospitalMonthlyMetric").value,
        hospitalSupplierChartMode: byId("hospitalSupplierChartMode").value,
        supplierChartMode: byId("supplierChartMode").value,
      };
      const monthlyExport = data.granularity === "month";
      const dashboard = document.createElement("main");
      dashboard.className = "page";
      const header = document.querySelector(".topbar").cloneNode(true);
      const moduleNav = header.querySelector(".module-nav");
      if (!moduleNav) throw new Error("Dashboard module navigation could not be included in the export.");
      moduleNav.querySelector(".module-home-tab")?.remove();
      moduleNav.querySelector('[data-module="data"]')?.remove();
      moduleNav.querySelectorAll(".module-tab").forEach(tab => {
        if (tab.dataset.module === "pharmacy") tab.setAttribute("aria-current", "page");
        else tab.removeAttribute("aria-current");
      });
      header.querySelector("#downloadHtmlButton")?.remove();
      header.querySelector("#refreshButton")?.remove();
      if (!header.querySelector(".topbar-actions")?.children.length) {
        header.querySelector(".topbar-actions")?.remove();
      }
      header.querySelectorAll(".topbar-actions button").forEach(button => {
        button.disabled = false;
      });
      const exportedFilters = document.querySelector(".filters-panel").cloneNode(true);
      exportedFilters.querySelector("#comparison-title").textContent = "Pharmacy performance";
      exportedFilters.querySelector("#filterEyebrow").textContent = "SAME DATES · YEAR-ON-YEAR";
      dashboard.appendChild(exportedFilters);
      [
        "#metricGrid",
        ".facility-panel",
        ".chart-panel",
        ".supplier-panel",
        ".daily-panel",
        "#hospitalComparisonPanel",
      ].forEach(selector => {
        const section = document.querySelector(selector);
        if (!section) throw new Error("A dashboard section could not be included in the export.");
        dashboard.appendChild(section.cloneNode(true));
      });
      dashboard.querySelector("#resetMonthFilter").hidden = true;
      const footer = document.querySelector("footer")?.cloneNode(true);
      const exportControls = {
        hospitalFilter: state.hospital,
        pharmacyFilter: state.pharmacy,
        periodFilter: state.period,
        startDate: state.start,
        endDate: state.end,
        chartMetric: state.chartMetric,
        hospitalMonthlyMetric: state.hospitalMonthlyMetric || "sales",
        hospitalSupplierChartMode: state.hospitalSupplierChartMode || "ends",
        supplierChartMode: state.supplierChartMode || "ends",
      };
      if (monthlyExport) {
        ["startDate", "endDate"].forEach(id => {
          const control = dashboard.querySelector(`#${id}`);
          const label = control?.closest("label");
          if (!control || !label) return;
          control.type = "month";
          label.firstChild.textContent = id === "startDate" ? "From month" : "To month";
        });
      }
      Object.entries(exportControls).forEach(([id, value]) => {
        const control = dashboard.querySelector(`#${id}`);
        if (!control) return;
        if (control instanceof HTMLSelectElement) {
          [...control.options].forEach(option => {
            if (option.value === value) option.setAttribute("selected", "");
            else option.removeAttribute("selected");
          });
        } else {
          const inputValue = monthlyExport ? value.slice(0, 7) : value;
          control.value = inputValue;
          control.setAttribute("value", inputValue);
        }
      });
      const embeddedData = JSON.stringify(data).replace(/</g, "\\u003c");
      const embeddedState = JSON.stringify(state).replace(/</g, "\\u003c");
      const standaloneScript = String.raw`
(() => {
  const types = ["sales", "collection", "purchase"];
  const labels = {sales: "Sales revenue", collection: "Collection revenue", purchase: "Purchase value"};
  const $ = id => document.getElementById(id);
  const money = value => new Intl.NumberFormat("en-IN", {
    style: "currency", currency: "INR", minimumFractionDigits: 0, maximumFractionDigits: 2,
  }).format(Number(value) || 0);
  const hasPendingAmounts = value => Number(value && value.sales_rows) > 0 &&
    Number(value.cash_due_rows) >= Number(value.sales_rows) &&
    Number(value.credit_due_rows) >= Number(value.sales_rows);
  const pendingAmount = value =>
    (Number(value && value.cash_due) || 0) + (Number(value && value.credit_due) || 0);
  const percentChange = value => Math.abs(value).toFixed(1) + "%";
  function changeIndicator(current, previous, lowerIsBetter = false) {
    const change = document.createElement("span");
    change.className = "facility-change";
    if (!previous) {
      change.hidden = true;
    } else {
      const difference = current - previous;
      const favorableChange = lowerIsBetter ? -difference : difference;
      change.classList.add(
        favorableChange > 0 ? "up" : favorableChange < 0 ? "down" : "flat",
      );
      change.textContent = (difference > 0 ? "↑" : difference < 0 ? "↓" : "—") +
        " " + percentChange(difference / Math.abs(previous) * 100);
    }
    if (previous) {
      change.title = (lowerIsBetter
        ? "Lower is better: decreases are green; increases are red. "
        : "") +
        "Percent change = (current period − same period last year) ÷ last year's amount × 100";
      change.setAttribute("aria-label",
        "Change versus the same period last year, calculated using last year's amount as the base: " +
        change.textContent);
    }
    return change;
  }
  const parseDate = value => {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(Date.UTC(year, month - 1, day));
  };
  const dateInput = value => value.toISOString().slice(0, 10);
  const displayDate = value => {
    const [year, month, day] = value.split("-");
    return day + "-" + month + "-" + year;
  };
  const displayMonth = value => {
    const [year, month] = value.split("-").map(Number);
    return new Intl.DateTimeFormat("en-IN", {month: "long", year: "numeric"})
      .format(new Date(year, month - 1, 1));
  };
  const shiftYear = value => {
    const date = parseDate(value);
    const year = date.getUTCFullYear() - 1;
    const month = date.getUTCMonth();
    return dateInput(new Date(Date.UTC(year, month,
      Math.min(date.getUTCDate(), new Date(Date.UTC(year, month + 1, 0)).getUTCDate()))));
  };
  const pharmacyLabel = value => value === "Pharmacy" ? value : value + " Pharmacy";
  const initialStart = initialState.start;
  const initialEnd = initialState.end;
  const monthGranularity = reportData.granularity === "month";
  let latestDataDate = reportData.latest_date || (reportData.daily || []).reduce(
    (latest, point) => point.date && (!latest || point.date > latest) ? point.date : latest,
    null,
  );
  let previousMonthRange = null;
  function rangeLabel(range) {
    return monthGranularity
      ? displayMonth(range.start.slice(0, 7)) + " – " + displayMonth(range.end.slice(0, 7))
      : displayDate(range.start) + " – " + displayDate(range.end);
  }
  function periodCaption(range) {
    const previous = {start: shiftYear(range.start), end: shiftYear(range.end)};
    return "Current period: " + rangeLabel(range) + " · Same dates last year: " +
      rangeLabel(previous);
  }

  function normalizeMonthRange(value, endOfMonth) {
    if (!monthGranularity) return value;
    const [year, month] = value.split("-").map(Number);
    return dateInput(new Date(Date.UTC(year, month + (endOfMonth ? 0 : -1), endOfMonth ? 0 : 1)));
  }

  function yearRange() {
    const today = new Date();
    const range = {
      start: dateInput(new Date(Date.UTC(today.getFullYear(), 0, 1))),
      end: dateInput(new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()))),
    };
    return clampRangeToAvailableData(range);
  }

  function clampRangeToAvailableData(range) {
    if (!latestDataDate) return range;
    const end = range.end > latestDataDate ? latestDataDate : range.end;
    return {start: range.start > end ? end : range.start, end};
  }

  function currentRange() {
    if ($("periodFilter").value === "custom") {
      return clampRangeToAvailableData({
        start: normalizeMonthRange($("startDate").value, false),
        end: normalizeMonthRange($("endDate").value, true),
      });
    }
    if ($("periodFilter").value === "year") {
      return yearRange();
    }
    return {start: initialStart, end: initialEnd};
  }

  function setDashboardModule(module) {
    const isHospitalModule = module === "hospitals";
    document.body.dataset.module = isHospitalModule ? "hospitals" : "pharmacy";
    document.querySelectorAll(".module-tab").forEach(button => {
      if (button.dataset.module === document.body.dataset.module) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    $("comparison-title").textContent =
      isHospitalModule ? "Hospital comparison date range" : "Pharmacy performance";
    $("filterEyebrow").textContent =
      isHospitalModule ? "ALL HOSPITALS · SAME DATES LAST YEAR" : "SAME DATES · YEAR-ON-YEAR";
  }

  function updateDateControls() {
    const custom = $("periodFilter").value === "custom";
    $("startDateGroup").classList.toggle("hidden", !custom);
    $("endDateGroup").classList.toggle("hidden", !custom);
    if (latestDataDate) {
      const maxDate = monthGranularity ? latestDataDate.slice(0, 7) : latestDataDate;
      $("startDate").max = maxDate;
      $("endDate").max = maxDate;
    }
  }

  function matchingSupplierTotals(start, end, hospital, pharmacy) {
    const totals = new Map();
    (reportData.suppliers || []).forEach(point => {
      const inRange = monthGranularity
        ? point.date.slice(0, 7) >= start.slice(0, 7) && point.date.slice(0, 7) <= end.slice(0, 7)
        : point.date >= start && point.date <= end;
      if (!inRange || (hospital !== "all" && point.hospital !== hospital) ||
          (pharmacy !== "all" && point.pharmacy !== pharmacy)) return;
      const key = point.hospital + "|" + point.pharmacy + "|" + point.supplier;
      const row = totals.get(key) || {
        hospital: point.hospital, pharmacy: point.pharmacy,
        supplier: point.supplier, amount: 0,
      };
      row.amount += Number(point.amount) || 0;
      totals.set(key, row);
    });
    return [...totals.values()];
  }

  function renderSupplierChart(container, supplierRows, mode) {
    const totals = new Map();
    supplierRows.forEach(row => {
      const supplier = String(row.supplier || "").trim();
      if (!supplier || !Number.isFinite(Number(row.amount))) return;
      const key = supplier.toLocaleLowerCase("en-IN");
      const total = totals.get(key) || {supplier, amount: 0};
      total.amount += Number(row.amount);
      totals.set(key, total);
    });
    const sorted = [...totals.values()]
      .sort((first, second) =>
        second.amount - first.amount || first.supplier.localeCompare(second.supplier));
    let visible = sorted;
    if (mode === "top") visible = sorted.slice(0, 5);
    else if (mode === "bottom") visible = sorted.slice(-5).reverse();
    else if (mode === "ends") {
      const top = sorted.slice(0, 5);
      const topNames = new Set(top.map(row => row.supplier));
      visible = top.concat(sorted.slice(-5).reverse().filter(row => !topNames.has(row.supplier)));
    }
    if (!visible.length) {
      container.innerHTML = '<p class="empty-cell">No supplier purchase data for the selected filters.</p>';
      return;
    }
    const maximum = Math.max(...visible.map(row => Math.abs(row.amount)), 1);
    const highestCount = Math.min(5, sorted.length);
    const fragment = document.createDocumentFragment();
    visible.forEach((row, index) => {
      if (index === 0 && mode !== "all") {
        const title = document.createElement("h4");
        title.className = "supplier-group-label";
        title.textContent = mode === "bottom" ? "Lowest purchase amounts" : "Highest purchase amounts";
        fragment.appendChild(title);
      } else if (mode === "ends" && sorted.length > 5 && index === highestCount) {
        const title = document.createElement("h4");
        title.className = "supplier-group-label supplier-group-low";
        title.textContent = "Lowest purchase amounts";
        fragment.appendChild(title);
      }
      const item = document.createElement("div");
      item.className = "supplier-bar-row";
      item.setAttribute("aria-label", row.supplier + ": " + money(row.amount));
      const name = document.createElement("span");
      name.className = "supplier-bar-name";
      name.title = row.supplier;
      name.textContent = row.supplier;
      const track = document.createElement("span");
      track.className = "supplier-bar-track";
      const bar = document.createElement("span");
      bar.className = "supplier-bar" +
        (mode === "bottom" || (mode === "ends" && index >= highestCount)
          ? " supplier-bar-low"
          : "");
      bar.style.width = Math.max(row.amount ? 2 : 0, Math.abs(row.amount) / maximum * 100) + "%";
      track.appendChild(bar);
      const amount = document.createElement("strong");
      amount.className = "supplier-bar-amount";
      amount.textContent = money(row.amount);
      item.append(name, track, amount);
      fragment.appendChild(item);
    });
    container.replaceChildren(fragment);
  }

  function renderHospitalSupplierCharts(range, mode) {
    const container = $("hospitalSupplierCharts");
    const rows = matchingSupplierTotals(range.start, range.end, "all", "all");
    const cards = reportData.hospitals.map((hospital, index) => {
      const card = document.createElement("article");
      card.className = "hospital-supplier-card";
      const heading = document.createElement("h3");
      heading.className = "hospital-supplier-card-title hospital-key-" + index;
      heading.textContent = hospital;
      const chart = document.createElement("div");
      chart.className = "supplier-chart supplier-chart-compact";
      renderSupplierChart(chart, rows.filter(row => row.hospital === hospital), mode);
      card.append(heading, chart);
      return card;
    });
    container.replaceChildren(...cards);
  }

  function aggregateFacilitiesByHospital(facilities) {
    const totals = new Map(reportData.hospitals.map(hospital => [hospital, {
      sales: 0, collection: 0, purchase: 0, sales_rows: 0,
      collection_rows: 0, purchase_rows: 0, cash_due: 0, credit_due: 0,
      cash_due_rows: 0, credit_due_rows: 0,
    }]));
    facilities.forEach(facility => {
      const total = totals.get(facility.hospital);
      if (!total) return;
      ["sales", "collection", "purchase", "sales_rows", "collection_rows",
        "purchase_rows", "cash_due", "credit_due", "cash_due_rows",
        "credit_due_rows"].forEach(type => {
        total[type] += Number(facility[type]) || 0;
      });
    });
    return totals;
  }

  function appendHospitalComparisonCell(row, amount, available,
    previousAmount, previousAvailable, options = {}) {
    const cell = document.createElement("td");
    if (options.pending) cell.className = "hospital-pending-cell";
    const value = document.createElement("strong");
    value.className = "hospital-comparison-value";
    value.textContent = available ? money(amount) : (options.noData ? "No data" : "Unavailable");
    if (options.pending && available && amount > 0) {
      value.classList.add("pending-outstanding");
    }
    cell.appendChild(value);
    if (available && previousAvailable && previousAmount !== 0) {
      const change = changeIndicator(amount, previousAmount, Boolean(options.lowerIsBetter));
      change.classList.add("hospital-comparison-change");
      change.append(document.createTextNode(" vs last year"));
      cell.appendChild(change);
    } else {
      const note = document.createElement("span");
      note.className = "hospital-comparison-change-neutral";
      note.textContent = previousAvailable ? "No prior value" : "No prior data";
      cell.appendChild(note);
    }
    row.appendChild(cell);
  }

  function hasHospitalReportData(amount, rows) {
    const numericAmount = Number(amount);
    return Number(rows) > 0 || (Number.isFinite(numericAmount) && numericAmount !== 0);
  }

  function renderHospitalComparison(facilities, comparisonFacilities, range) {
    const container = $("hospitalFacilitySummaries");
    const currentByHospital = aggregateFacilitiesByHospital(facilities);
    const previousByHospital = aggregateFacilitiesByHospital(comparisonFacilities);
    const rows = reportData.hospitals.map((hospital, index) => {
      const current = currentByHospital.get(hospital);
      const previous = previousByHospital.get(hospital);
      const row = document.createElement("tr");
      const headingCell = document.createElement("th");
      headingCell.scope = "row";
      headingCell.className = "hospital-comparison-name";
      const heading = document.createElement("span");
      heading.className = "hospital-facility-title hospital-key-" + index;
      heading.textContent = hospital;
      headingCell.appendChild(heading);
      row.appendChild(headingCell);
      const hasSales = current.sales_rows > 0;
      const hadSales = previous.sales_rows > 0;
      const cashAvailable = hasSales && current.cash_due_rows >= current.sales_rows;
      const previousCashAvailable = hadSales &&
        previous.cash_due_rows >= previous.sales_rows;
      const creditAvailable = hasSales && current.credit_due_rows >= current.sales_rows;
      const previousCreditAvailable = hadSales &&
        previous.credit_due_rows >= previous.sales_rows;
      const pendingAvailable = cashAvailable && creditAvailable;
      const previousPendingAvailable = previousCashAvailable && previousCreditAvailable;
      appendHospitalComparisonCell(row, current.sales, hasSales,
        previous.sales, hadSales, {prominent: true, noData: true});
      appendHospitalComparisonCell(row, current.collection,
        hasHospitalReportData(current.collection, current.collection_rows),
        previous.collection,
        hasHospitalReportData(previous.collection, previous.collection_rows),
        {noData: true});
      appendHospitalComparisonCell(row, current.purchase,
        hasHospitalReportData(current.purchase, current.purchase_rows),
        previous.purchase,
        hasHospitalReportData(previous.purchase, previous.purchase_rows),
        {noData: true});
      appendHospitalComparisonCell(row,
        current.cash_due + current.credit_due, pendingAvailable,
        previous.cash_due + previous.credit_due, previousPendingAvailable,
        {pending: true, noData: !hasSales,
          lowerIsBetter: true});
      appendHospitalComparisonCell(row, current.cash_due, cashAvailable,
        previous.cash_due, previousCashAvailable,
        {noData: !hasSales, lowerIsBetter: true});
      appendHospitalComparisonCell(row, current.credit_due, creditAvailable,
        previous.credit_due, previousCreditAvailable,
        {noData: !hasSales, lowerIsBetter: true});
      return row;
    });
    container.replaceChildren(...rows);
  }

  function pharmacyOptions() {
    const hospital = $("hospitalFilter").value;
    const values = hospital === "all"
      ? ["IPD", "OPD", "Pharmacy"]
      : reportData.pharmacies[hospital] || [];
    const select = $("pharmacyFilter");
    const previous = select.value;
    select.replaceChildren(new Option("All pharmacies", "all"));
    values.forEach(value => select.add(new Option(pharmacyLabel(value), value)));
    select.value = values.includes(previous) ? previous : "all";
  }

  function matching(start, end, hospital, pharmacy) {
    return reportData.daily.filter(point =>
      (monthGranularity
        ? point.date.slice(0, 7) >= start.slice(0, 7) && point.date.slice(0, 7) <= end.slice(0, 7)
        : point.date >= start && point.date <= end) &&
      (hospital === "all" || point.hospital === hospital) &&
      (pharmacy === "all" || point.pharmacy === pharmacy));
  }

  function matchingDueTotals(start, end, hospital, pharmacy) {
    const points = reportData.daily.filter(point =>
      (monthGranularity
        ? point.date.slice(0, 7) >= start.slice(0, 7) && point.date.slice(0, 7) <= end.slice(0, 7)
        : point.date >= start && point.date <= end) &&
      (hospital === "all" || point.hospital === hospital) &&
      (pharmacy === "all" || point.pharmacy === pharmacy));
    const totals = ["cash_due", "credit_due"].reduce((result, type) => {
      result[type] = points.reduce((total, point) => ({
        amount: total.amount + (Number(point[type]) || 0),
        rows: total.rows + (Number(point[type + "_rows"]) || 0),
      }), {amount: 0, rows: 0});
      return result;
    }, {});
    const salesRows = points.reduce(
      (total, point) => total + (Number(point.sales_rows) || 0), 0,
    );
    totals.cash_due.available = totals.cash_due.rows > 0;
    totals.credit_due.available = totals.credit_due.rows > 0;
    totals.pending_available = salesRows > 0 &&
      totals.cash_due.rows >= salesRows && totals.credit_due.rows >= salesRows;
    return totals;
  }

  function summarize(points) {
    const totals = Object.fromEntries(types.map(type => [type, {amount: 0, rows: 0}]));
    const facilities = new Map();
    const months = new Map();
    points.forEach(point => {
      const key = point.hospital + "|" + point.pharmacy;
      const facility = facilities.get(key) || {
        hospital: point.hospital, pharmacy: point.pharmacy,
        sales: 0, collection: 0, purchase: 0, sales_rows: 0,
        collection_rows: 0, purchase_rows: 0,
        cash_due: 0, credit_due: 0, cash_due_rows: 0, credit_due_rows: 0,
      };
      const month = point.date.slice(0, 7);
      const monthly = months.get(month) || {
        month, sales: 0, collection: 0, purchase: 0, sales_rows: 0,
        cash_due: 0, credit_due: 0, cash_due_rows: 0, credit_due_rows: 0,
      };
      types.forEach(type => {
        const amount = Number(point[type]) || 0;
        facility[type] += amount;
        facility[type + "_rows"] += Number(point[type + "_rows"]) || 0;
        monthly[type] += amount;
        totals[type].amount += amount;
        totals[type].rows += Number(point[type + "_rows"]) || 0;
      });
      facility.cash_due += Number(point.cash_due) || 0;
      facility.credit_due += Number(point.credit_due) || 0;
      facility.cash_due_rows += Number(point.cash_due_rows) || 0;
      facility.credit_due_rows += Number(point.credit_due_rows) || 0;
      monthly.sales_rows += Number(point.sales_rows) || 0;
      monthly.cash_due += Number(point.cash_due) || 0;
      monthly.credit_due += Number(point.credit_due) || 0;
      monthly.cash_due_rows += Number(point.cash_due_rows) || 0;
      monthly.credit_due_rows += Number(point.credit_due_rows) || 0;
      facilities.set(key, facility);
      months.set(month, monthly);
    });
    return {
      totals,
      facilities: [...facilities.values()],
      months: [...months.values()].sort((a, b) => a.month.localeCompare(b.month)),
    };
  }

  function metricCard(label, type, current, comparison) {
    const now = current[type];
    const before = comparison[type];
    const isPending = type === "pending";
    const lowerIsBetter = isPending || type === "cash_due" ||
      type === "credit_due";
    const metricClass = isPending ? " pending-metric" : "";
    const pendingValueClass = isPending
      ? " " + (now.amount > 0 ? "pending-outstanding" : "pending-settled")
      : "";
    if (now.available === false) {
      return '<article class="metric-card' + metricClass + '"><div class="metric-label">' +
        label + '</div><div class="metric-value">Unavailable</div><div class="metric-compare">' +
        (isPending
          ? "Sales report must include Cash Due and Credit Due for every row."
          : "Restart the dashboard server to load Cash Due and Credit Due totals.") +
        '</div></article>';
    }
    if (!now.rows && !before.rows) {
      return '<article class="metric-card' + metricClass + '"><div class="metric-label">' +
        label + '</div><div class="metric-value">No data</div><div class="metric-compare">No data available for either period.</div></article>';
    }
    if (!before.rows) {
      return '<article class="metric-card' + metricClass + '"><div class="metric-label">' + label +
        '</div><div class="metric-value' + pendingValueClass + '">' + money(now.amount) +
        '</div><div class="metric-compare">' +
        'No comparison-period data.</div></article>';
    }
    const difference = now.amount - before.amount;
    const percent = before.amount === 0 ? null : difference / Math.abs(before.amount) * 100;
    const trendClass = difference === 0
      ? "flat"
      : lowerIsBetter
        ? difference < 0 ? "up" : "down"
        : difference > 0 ? "up" : "down";
    const trendText = difference > 0 ? "↑" : difference < 0 ? "↓" : "→";
    const percentText = percent === null ? "" : " (" + percentChange(percent) + ")";
    return '<article class="metric-card' + metricClass + '"><div class="metric-label">' + label +
      '</div><div class="metric-value' + pendingValueClass + '">' + money(now.amount) +
      '</div><div class="metric-compare">' +
      'Same dates last year: ' + money(before.amount) +
      '<br><span class="delta ' + trendClass + '">' + trendText + " " +
      money(Math.abs(difference)) + percentText + " " +
      (difference > 0 ? "higher" : difference < 0 ? "lower" : "unchanged") +
      "</span></div></article>";
  }

  function withPendingAmount(totals, dueTotals = {}) {
    const salesRows = Number(totals.sales && totals.sales.rows) || 0;
    const cashDueRows = Number(dueTotals.cash_due && dueTotals.cash_due.rows) || 0;
    const creditDueRows = Number(dueTotals.credit_due && dueTotals.credit_due.rows) || 0;
    const pendingAvailable = dueTotals.pending_available === undefined
      ? salesRows > 0 && cashDueRows >= salesRows && creditDueRows >= salesRows
      : dueTotals.pending_available;
    return {
      ...totals,
      cash_due: dueTotals.cash_due
        ? {...dueTotals.cash_due, available: cashDueRows > 0}
        : {amount: 0, rows: 0, available: false},
      credit_due: dueTotals.credit_due
        ? {...dueTotals.credit_due, available: creditDueRows > 0}
        : {amount: 0, rows: 0, available: false},
      pending: {
        amount: pendingAvailable
          ? (Number(dueTotals.cash_due && dueTotals.cash_due.amount) || 0) +
            (Number(dueTotals.credit_due && dueTotals.credit_due.amount) || 0)
          : 0,
        rows: pendingAvailable ? salesRows : 0,
        available: pendingAvailable,
      },
    };
  }

  function renderFacilities(current, comparison, hospital, pharmacy) {
    const selectedHospitals = hospital === "all"
      ? reportData.hospitals
      : reportData.hospitals.filter(value => value === hospital);
    const keys = selectedHospitals.flatMap(value =>
      (reportData.pharmacies[value] || [])
        .filter(item => pharmacy === "all" || item === pharmacy)
        .map(item => ({hospital: value, pharmacy: item}))
    );
    const toMap = facilities => new Map(facilities.map(item =>
      [item.hospital + "|" + item.pharmacy, item]));
    const now = toMap(current);
    const before = toMap(comparison);
    const rows = keys.map(({hospital: facilityHospital, pharmacy: facilityPharmacy}) => {
      const key = facilityHospital + "|" + facilityPharmacy;
      const a = now.get(key) || {};
      const b = before.get(key) || {};
      const row = document.createElement("tr");
      const hospitalCell = document.createElement("td");
      const hospitalButton = document.createElement("button");
      hospitalButton.type = "button";
      hospitalButton.className = "facility-filter-button";
      hospitalButton.textContent = facilityHospital;
      hospitalButton.dataset.hospital = facilityHospital;
      hospitalCell.appendChild(hospitalButton);
      row.appendChild(hospitalCell);
      const pharmacyCell = document.createElement("td");
      const pharmacyButton = document.createElement("button");
      pharmacyButton.type = "button";
      pharmacyButton.className = "facility-filter-button";
      pharmacyButton.textContent = pharmacyLabel(facilityPharmacy);
      pharmacyButton.dataset.hospital = facilityHospital;
      pharmacyButton.dataset.pharmacy = facilityPharmacy;
      pharmacyCell.appendChild(pharmacyButton);
      row.appendChild(pharmacyCell);
      types.forEach(type => {
        const amount = Number(a[type]) || 0;
        const previous = Number(b[type]) || 0;
        const currentCell = document.createElement("td");
        currentCell.className = "numeric facility-current";
        const currentValue = document.createElement("strong");
        currentValue.className = "facility-amount";
        currentValue.textContent = money(amount);
        currentCell.appendChild(currentValue);
        currentCell.appendChild(changeIndicator(amount, previous));
        row.appendChild(currentCell);
        const previousCell = document.createElement("td");
        previousCell.className = "numeric facility-previous";
        previousCell.textContent = money(previous);
        row.appendChild(previousCell);
      });
      [a, b].forEach((facility, index, facilities) => {
        const cell = document.createElement("td");
        cell.className = "numeric " + (index ? "facility-previous" : "facility-current");
        const available = hasPendingAmounts(facility);
        if (available) {
          const amount = document.createElement("strong");
          amount.className = "facility-amount";
          amount.textContent = money(pendingAmount(facility));
          cell.appendChild(amount);
          if (!index && hasPendingAmounts(facilities[1])) {
            cell.appendChild(changeIndicator(
              pendingAmount(facility),
              pendingAmount(facilities[1]),
              true,
            ));
          }
        } else {
          cell.textContent = Number(facility.sales_rows) > 0 ? "Unavailable" : "No data";
        }
        row.appendChild(cell);
      });
      return row;
    });
    const currentTotals = Object.fromEntries(types.map(type => [type, 0]));
    const comparisonTotals = Object.fromEntries(types.map(type => [type, 0]));
    const currentPendingTotals = {
      sales_rows: 0, cash_due_rows: 0, credit_due_rows: 0, cash_due: 0, credit_due: 0,
    };
    const comparisonPendingTotals = {...currentPendingTotals};
    keys.forEach(({hospital: facilityHospital, pharmacy: facilityPharmacy}) => {
      const key = facilityHospital + "|" + facilityPharmacy;
      const a = now.get(key) || {};
      const b = before.get(key) || {};
      types.forEach(type => {
        currentTotals[type] += Number(a[type]) || 0;
        comparisonTotals[type] += Number(b[type]) || 0;
      });
      ["sales_rows", "cash_due_rows", "credit_due_rows", "cash_due", "credit_due"].forEach(type => {
        currentPendingTotals[type] += Number(a[type]) || 0;
        comparisonPendingTotals[type] += Number(b[type]) || 0;
      });
    });
    const totalRow = document.createElement("tr");
    totalRow.className = "facility-grand-total";
    const totalLabel = document.createElement("td");
    totalLabel.colSpan = 2;
    totalLabel.textContent = "Grand total";
    totalRow.appendChild(totalLabel);
    types.forEach(type => {
      const currentCell = document.createElement("td");
      currentCell.className = "numeric facility-current";
      const currentValue = document.createElement("strong");
      currentValue.textContent = money(currentTotals[type]);
      currentCell.append(currentValue, changeIndicator(currentTotals[type], comparisonTotals[type]));
      totalRow.appendChild(currentCell);
      const comparisonCell = document.createElement("td");
      comparisonCell.className = "numeric facility-previous";
      comparisonCell.textContent = money(comparisonTotals[type]);
      totalRow.appendChild(comparisonCell);
    });
    const currentPendingCell = document.createElement("td");
    currentPendingCell.className = "numeric facility-current";
    const currentPendingAvailable = hasPendingAmounts(currentPendingTotals);
    const comparisonPendingAvailable = hasPendingAmounts(comparisonPendingTotals);
    if (currentPendingAvailable) {
      const amount = document.createElement("strong");
      amount.className = "facility-amount";
      amount.textContent = money(pendingAmount(currentPendingTotals));
      currentPendingCell.appendChild(amount);
      if (comparisonPendingAvailable) {
        currentPendingCell.appendChild(changeIndicator(
          pendingAmount(currentPendingTotals),
          pendingAmount(comparisonPendingTotals),
          true,
        ));
      }
    } else {
      currentPendingCell.textContent = currentPendingTotals.sales_rows > 0 ? "Unavailable" : "No data";
    }
    totalRow.appendChild(currentPendingCell);
    const comparisonPendingCell = document.createElement("td");
    comparisonPendingCell.className = "numeric facility-previous";
    comparisonPendingCell.textContent = comparisonPendingAvailable
      ? money(pendingAmount(comparisonPendingTotals))
      : comparisonPendingTotals.sales_rows > 0 ? "Unavailable" : "No data";
    totalRow.appendChild(comparisonPendingCell);
    rows.push(totalRow);
    $("facilityRows").replaceChildren(...rows);
  }

  function renderDailyTotals(points, comparisonPoints) {
    const rows = $("dailyRows");
    if (!points.length && !comparisonPoints.length) {
      rows.innerHTML = '<tr><td colspan="5" class="empty-cell">No data in this period.</td></tr>';
      return;
    }
    const comparisonMonths = new Map(comparisonPoints.map(point => [point.month, point]));
    const monthlyRows = points.map(point => {
      const row = document.createElement("tr");
      [displayMonth(point.month), ...types.map(type => money(point[type]))].forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index) {
          cell.className = "numeric";
          const type = types[index - 1];
          const previousMonth = (Number(point.month.slice(0, 4)) - 1) + point.month.slice(4);
          cell.appendChild(changeIndicator(
            Number(point[type]) || 0,
            Number(comparisonMonths.get(previousMonth)?.[type]) || 0,
          ));
        }
        row.appendChild(cell);
      });
      const previousMonth = (Number(point.month.slice(0, 4)) - 1) + point.month.slice(4);
      const previousPoint = comparisonMonths.get(previousMonth);
      const pendingCell = document.createElement("td");
      pendingCell.className = "numeric";
      pendingCell.textContent = hasPendingAmounts(point)
        ? money(pendingAmount(point))
        : Number(point.sales_rows) > 0 ? "Unavailable" : "No data";
      if (hasPendingAmounts(point) && hasPendingAmounts(previousPoint)) {
        pendingCell.appendChild(changeIndicator(
          pendingAmount(point), pendingAmount(previousPoint),
        ));
      }
      row.appendChild(pendingCell);
      return row;
    });
    const grandTotal = points.reduce((total, point) => {
      types.forEach(type => {
        total[type] += Number(point[type]) || 0;
      });
      ["sales_rows", "cash_due", "credit_due", "cash_due_rows", "credit_due_rows"]
        .forEach(type => {
          total[type] += Number(point[type]) || 0;
        });
      return total;
    }, {
      sales: 0, collection: 0, purchase: 0, sales_rows: 0,
      cash_due: 0, credit_due: 0, cash_due_rows: 0, credit_due_rows: 0,
    });
    const comparisonGrandTotal = comparisonPoints.reduce((total, point) => {
      types.forEach(type => {
        total[type] += Number(point[type]) || 0;
      });
      ["sales_rows", "cash_due", "credit_due", "cash_due_rows", "credit_due_rows"]
        .forEach(type => {
          total[type] += Number(point[type]) || 0;
        });
      return total;
    }, {
      sales: 0, collection: 0, purchase: 0, sales_rows: 0,
      cash_due: 0, credit_due: 0, cash_due_rows: 0, credit_due_rows: 0,
    });
    const totalRow = document.createElement("tr");
    totalRow.className = "monthly-grand-total";
    [
      "Grand total",
      ...types.map(type => money(grandTotal[type])),
      hasPendingAmounts(grandTotal) ? money(pendingAmount(grandTotal)) : "Unavailable",
    ].forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index) {
        cell.className = "numeric";
        if (index <= types.length) {
          const type = types[index - 1];
          cell.appendChild(changeIndicator(grandTotal[type], comparisonGrandTotal[type]));
        } else if (hasPendingAmounts(grandTotal) && hasPendingAmounts(comparisonGrandTotal)) {
          cell.appendChild(changeIndicator(
            pendingAmount(grandTotal), pendingAmount(comparisonGrandTotal),
          ));
        }
      }
      totalRow.appendChild(cell);
    });
    monthlyRows.push(totalRow);
    rows.replaceChildren(...monthlyRows);
  }

  function renderMonthlyChart(current, comparison, reportType, range) {
    const currentMonths = new Map(current.map(point => [point.month, point]));
    const previousMonths = new Map(comparison.map(point => [point.month, point]));
    const months = [...new Set([
      ...currentMonths.keys(),
      ...[...previousMonths.keys()].map(month =>
        String(Number(month.slice(0, 4)) + 1) + month.slice(4)),
    ])].filter(month => {
      const previousMonth = (Number(month.slice(0, 4)) - 1) + month.slice(4);
      return (Number(currentMonths.get(month)?.[reportType]) || 0) !== 0 ||
        (Number(previousMonths.get(previousMonth)?.[reportType]) || 0) !== 0;
    }).sort();
    const chart = $("monthlyChart");
    if (!months.length) {
      chart.innerHTML = '<p class="empty-cell">No non-zero monthly data for this report in the selected period.</p>';
      return;
    }
    const values = months.flatMap(month => {
      const prior = (Number(month.slice(0, 4)) - 1) + month.slice(4);
      return [
        Number(currentMonths.get(month)?.[reportType]) || 0,
        Number(previousMonths.get(prior)?.[reportType]) || 0,
      ];
    });
    const maximum = Math.max(...values, 1);
    const groups = months.map(month => {
      const previousMonth = (Number(month.slice(0, 4)) - 1) + month.slice(4);
      const currentAmount = Number(currentMonths.get(month)?.[reportType]) || 0;
      const previousAmount = Number(previousMonths.get(previousMonth)?.[reportType]) || 0;
      const group = document.createElement("button");
      group.type = "button";
      group.className = "month-chart-group";
      group.title = displayMonth(month) + ": " +
        (currentAmount ? "Selected period " + money(currentAmount) : "Selected period: no data") +
        "; " + (previousAmount ? "Same period last year " + money(previousAmount) :
          "Same period last year: no data") + ". Select to view this month.";
      group.setAttribute("aria-label", group.title);
      group.addEventListener("click", () => {
        if (!previousMonthRange) {
          previousMonthRange = {
            period: $("periodFilter").value,
            start: $("startDate").value,
            end: $("endDate").value,
          };
        }
        const [year, monthNumber] = month.split("-").map(Number);
        $("periodFilter").value = "custom";
        if (monthGranularity) {
          $("startDate").value = month;
          $("endDate").value = month;
        } else {
          $("startDate").value = dateInput(new Date(Date.UTC(year, monthNumber - 1, 1)));
          $("endDate").value = dateInput(new Date(Date.UTC(year, monthNumber, 0)));
        }
        $("resetMonthFilter").hidden = false;
        updateDateControls();
        render();
      });
      const valueLabels = document.createElement("span");
      valueLabels.className = "month-chart-values";
      [["current", currentAmount], ["previous", previousAmount]].forEach(([period, amount]) => {
        if (!amount) return;
        const value = document.createElement("span");
        value.className = "month-chart-value " + period;
        value.textContent = money(amount);
        valueLabels.appendChild(value);
      });
      const monthChange = changeIndicator(currentAmount, previousAmount);
      monthChange.classList.add("month-chart-change");
      valueLabels.appendChild(monthChange);
      const bars = document.createElement("span");
      bars.className = "month-chart-bars";
      [["current", currentAmount], ["previous", previousAmount]].forEach(([period, amount]) => {
        const bar = document.createElement("span");
        bar.className = "month-chart-bar " + period;
        bar.style.height = Math.max(amount ? 4 : 0, amount / maximum * 100) + "%";
        bar.setAttribute("aria-hidden", "true");
        bars.appendChild(bar);
      });
      const label = document.createElement("span");
      label.className = "month-chart-label";
      label.textContent = displayMonth(month);
      group.append(valueLabels, bars, label);
      return group;
    });
    const currentTotal = current.reduce(
      (total, point) => total + (Number(point[reportType]) || 0), 0,
    );
    const previousTotal = comparison.reduce(
      (total, point) => total + (Number(point[reportType]) || 0), 0,
    );
    const totalGroup = document.createElement("div");
    totalGroup.className = "month-chart-group month-chart-total";
    totalGroup.setAttribute("role", "group");
    totalGroup.setAttribute(
      "aria-label",
      "Grand total: selected period " + money(currentTotal) +
        "; same period last year " + money(previousTotal),
    );
    const totalLabels = document.createElement("span");
    totalLabels.className = "month-chart-values";
    [["current", currentTotal], ["previous", previousTotal]].forEach(([period, amount]) => {
      const value = document.createElement("span");
      value.className = "month-chart-value " + period;
      value.textContent = money(amount);
      totalLabels.appendChild(value);
    });
    const totalChange = changeIndicator(currentTotal, previousTotal);
    totalChange.classList.add("month-chart-change");
    totalLabels.appendChild(totalChange);
    const totalBars = document.createElement("span");
    totalBars.className = "month-chart-bars";
    totalBars.setAttribute("aria-hidden", "true");
    const totalLabel = document.createElement("span");
    totalLabel.className = "month-chart-label";
    totalLabel.textContent = "Grand total";
    totalGroup.append(totalLabels, totalBars, totalLabel);
    groups.push(totalGroup);
    chart.replaceChildren(...groups);
  }

  function compactMoney(value) {
    return new Intl.NumberFormat("en-IN", {
      style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1,
    }).format(Number(value) || 0);
  }

  function monthKeys(range) {
    const start = parseDate(range.start);
    const end = parseDate(range.end);
    const month = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
    const finalMonth = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), 1));
    const keys = [];
    while (month <= finalMonth) {
      keys.push(month.getUTCFullYear() + "-" +
        String(month.getUTCMonth() + 1).padStart(2, "0"));
      month.setUTCMonth(month.getUTCMonth() + 1);
    }
    return keys;
  }

  function renderHospitalMonthlyChart(range, reportType) {
    const chart = $("hospitalMonthlyChart");
    const legend = $("hospitalMonthlyLegend");
    legend.replaceChildren(...reportData.hospitals.map((hospital, index) => {
      const item = document.createElement("span");
      const key = document.createElement("i");
      key.className = "hospital-key-" + index;
      item.append(key, document.createTextNode(hospital));
      return item;
    }));
    const points = matching(range.start, range.end, "all", "all");
    if (!points.length) {
      chart.innerHTML = '<p class="empty-cell">No hospital data for this period.</p>';
      return;
    }
    const totals = new Map();
    points.forEach(point => {
      const month = point.date.slice(0, 7);
      const key = month + "|" + point.hospital;
      const total = totals.get(key) || {amount: 0, rows: 0};
      total.amount += Number(point[reportType]) || 0;
      total.rows += Number(point[reportType + "_rows"]) || 0;
      totals.set(key, total);
    });
    const months = monthKeys(range);
    const maximum = Math.max(1, ...months.flatMap(month =>
      reportData.hospitals.map(hospital =>
        totals.get(month + "|" + hospital)?.amount || 0)));
    const groups = months.map(month => {
      const group = document.createElement("div");
      group.className = "hospital-month-group";
      group.setAttribute("role", "group");
      group.setAttribute("aria-label", displayMonth(month));
      const bars = document.createElement("div");
      bars.className = "hospital-month-bars";
      reportData.hospitals.forEach((hospital, index) => {
        const periodData = totals.get(month + "|" + hospital);
        const amount = periodData?.amount || 0;
        const series = document.createElement("div");
        series.className = "hospital-month-series";
        const value = document.createElement("span");
        value.className = "hospital-month-value";
        value.textContent = periodData?.rows ? compactMoney(amount) : "—";
        const track = document.createElement("span");
        track.className = "hospital-month-track";
        const barHeight = amount ? Math.max(3, amount / maximum * 82) : 0;
        track.style.setProperty("--bar-height", barHeight + "%");
        const bar = document.createElement("span");
        bar.className = "hospital-month-bar hospital-" + index;
        bar.style.height = barHeight + "%";
        bar.title = periodData?.rows
          ? hospital + " · " + displayMonth(month) + " · " + money(amount)
          : hospital + " · " + displayMonth(month) + " · No " + reportType + " report data";
        bar.setAttribute("aria-label", bar.title);
        track.append(value, bar);
        series.appendChild(track);
        bars.appendChild(series);
      });
      const label = document.createElement("span");
      label.className = "hospital-month-label";
      label.textContent = displayMonth(month);
      group.append(bars, label);
      return group;
    });
    chart.replaceChildren(...groups);
  }

  function render() {
    const range = currentRange();
    if ($("periodFilter").value === "custom") {
      $("startDate").value = monthGranularity ? range.start.slice(0, 7) : range.start;
      $("endDate").value = monthGranularity ? range.end.slice(0, 7) : range.end;
    }
    if (!range.start || !range.end || range.start > range.end) {
      $("comparisonCaption").textContent = "Select a valid date range.";
      $("hospitalSupplierCharts").innerHTML =
        '<p class="empty-cell">Select a valid date range.</p>';
      $("hospitalFacilitySummaries").innerHTML =
        '<tr><td colspan="7" class="hospital-summary-empty">Select a valid date range.</td></tr>';
      $("metricGrid").innerHTML =
        '<div class="metric-card loading-card">The start date must be on or before the end date.</div>';
      return;
    }
    const previousRange = {start: shiftYear(range.start), end: shiftYear(range.end)};
    const hospital = $("hospitalFilter").value;
    const pharmacy = $("pharmacyFilter").value;
    const currentPoints = matching(range.start, range.end, hospital, pharmacy);
    const current = summarize(currentPoints);
    const comparison = summarize(matching(previousRange.start, previousRange.end, hospital, pharmacy));
    const currentFacility = summarize(matching(range.start, range.end, hospital, "all"));
    const previousFacility = summarize(matching(previousRange.start, previousRange.end, hospital, "all"));
    const supplierRows = matchingSupplierTotals(range.start, range.end, hospital, pharmacy);
    $("comparisonCaption").textContent = periodCaption(range);
    const currentTotals = withPendingAmount(
      current.totals, matchingDueTotals(range.start, range.end, hospital, pharmacy),
    );
    const comparisonTotals = withPendingAmount(
      comparison.totals,
      matchingDueTotals(previousRange.start, previousRange.end, hospital, pharmacy),
    );
    $("metricGrid").innerHTML = [
      ...types.map(type =>
        metricCard(labels[type], type, currentTotals, comparisonTotals)),
      metricCard("Cash due", "cash_due", currentTotals, comparisonTotals),
      metricCard("Credit due", "credit_due", currentTotals, comparisonTotals),
      metricCard("Total pending amount", "pending", currentTotals, comparisonTotals),
    ].join("");
    renderSupplierChart($("supplierChart"), supplierRows, $("supplierChartMode").value);
    const hospitalCurrent = summarize(matching(range.start, range.end, "all", "all"));
    const hospitalPrevious = summarize(
      matching(previousRange.start, previousRange.end, "all", "all"),
    );
    renderHospitalComparison(
      hospitalCurrent.facilities,
      hospitalPrevious.facilities,
      range,
    );
    renderFacilities(currentFacility.facilities, previousFacility.facilities, hospital, pharmacy);
    renderDailyTotals(current.months, comparison.months);
    renderMonthlyChart(current.months, comparison.months, $("chartMetric").value, range);
    renderHospitalMonthlyChart(range, $("hospitalMonthlyMetric").value);
    renderHospitalSupplierCharts(range, $("hospitalSupplierChartMode").value);
  }

  $("hospitalFilter").addEventListener("change", () => {
    pharmacyOptions();
    render();
  });
  $("resetMonthFilter").addEventListener("click", () => {
    if (!previousMonthRange) return;
    $("periodFilter").value = previousMonthRange.period;
    $("startDate").value = previousMonthRange.start;
    $("endDate").value = previousMonthRange.end;
    previousMonthRange = null;
    $("resetMonthFilter").hidden = true;
    updateDateControls();
    render();
  });
  $("pharmacyFilter").addEventListener("change", render);
  $("supplierChartMode").addEventListener("change", render);
  $("periodFilter").addEventListener("change", updateDateControls);
  $("applyFilters").addEventListener("click", render);
  $("chartMetric").addEventListener("change", render);
  $("hospitalMonthlyMetric").addEventListener("change", render);
  $("hospitalSupplierChartMode").addEventListener("change", render);
  document.querySelectorAll(".module-tab[data-module]").forEach(button => {
    button.addEventListener("click", () => setDashboardModule(button.dataset.module));
  });
  $("facilityRows").addEventListener("click", event => {
    const button = event.target.closest(".facility-filter-button");
    if (!button) return;
    $("hospitalFilter").value = button.dataset.hospital;
    pharmacyOptions();
    if (button.dataset.pharmacy) $("pharmacyFilter").value = button.dataset.pharmacy;
    render();
  });
  pharmacyOptions();
  $("hospitalFilter").value = initialState.hospital;
  pharmacyOptions();
  $("pharmacyFilter").value = initialState.pharmacy;
  $("periodFilter").value = initialState.period;
  $("startDate").value = monthGranularity ? initialState.start.slice(0, 7) : initialState.start;
  $("endDate").value = monthGranularity ? initialState.end.slice(0, 7) : initialState.end;
  $("chartMetric").value = initialState.chartMetric;
  $("hospitalMonthlyMetric").value = initialState.hospitalMonthlyMetric || "sales";
  $("hospitalSupplierChartMode").value = initialState.hospitalSupplierChartMode || "ends";
  setDashboardModule("pharmacy");
  updateDateControls();
  render();
})();`;
      const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#102a43"><title>Pharmacy Analytics Dashboard</title><style>${stylesheet}
#previewButton { display: none !important; }
html, body { max-width: 100%; overflow-x: hidden; }
@media screen {
  body.standalone-dashboard { width: 125%; zoom: .8; }
  .standalone-dashboard .topbar { padding-right: max(20px, calc((100% - 2000px) / 2)); padding-left: max(20px, calc((100% - 2000px) / 2)); }
  .standalone-dashboard .page { width: min(2000px, calc(100% - 40px)); }
}
.page, .page > * { min-width: 0; }
.table-scroll, .monthly-chart { min-width: 0; max-width: 100%; }
.print-dashboard-button { position: fixed; right: 18px; bottom: 18px; z-index: 10; box-shadow: 0 3px 12px #1d3b5330; }
@page { size: A4 landscape; margin: 6mm; }
@media print {
  body { width: 121.96%; zoom: .82; }
  .topbar { min-height: 42px; padding-top: 5px; padding-bottom: 5px; }
  .page { width: 100%; max-width: none; gap: 5px; }
  .panel { padding: 7px 9px; }
  .section-heading { margin-bottom: 5px; }
  .metric-grid { gap: 6px; }
  .metric-card { min-height: 70px; padding: 7px 9px; }
  .metric-value { margin: 3px 0; font-size: 16px; }
  .monthly-chart { min-height: 132px; padding: 4px 2px 0; }
  .month-chart-group { flex: 1 1 0; min-width: 0; grid-template-rows: 38px 72px 18px; gap: 3px; padding: 0 2px 4px; }
  .month-chart-values { gap: 2px; min-height: 0; font-size: 8px; }
  .month-chart-value { padding: 1px 3px; font-size: 8px; }
  .month-chart-bars { height: 72px; }
  .month-chart-label { min-height: 18px; font-size: 8px; }
  .monthly-totals-table { min-width: 0; }
  th { padding: 4px 5px; font-size: 7px; }
  td { padding: 4px 5px; font-size: 8px; }
  .facility-panel th { padding: 5px 6px; font-size: 9px; }
  .facility-panel td { padding: 5px 6px; font-size: 10px; }
  .facility-panel .facility-amount { font-size: 10px; }
  .facility-change { margin-top: 2px; padding: 2px 5px; font-size: 9px; }
  .print-dashboard-button { display: none !important; }
  .panel, .metric-card { break-inside: avoid; page-break-inside: avoid; }
}
</style></head><body class="standalone-dashboard" data-module="pharmacy">${header.outerHTML}<button class="button button-primary print-dashboard-button" type="button" onclick="window.print()">Print / Save as PDF</button>${dashboard.outerHTML}${footer?.outerHTML || ""}<script>let reportData=${embeddedData};const initialState=${embeddedState};${standaloneScript}</script></body></html>`;
      const blob = new Blob([html], {type: "text/html;charset=utf-8"});
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "pharmacy-analytics-dashboard.html";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      window.alert(error.message || "Interactive HTML download failed.");
    } finally {
      button.disabled = false;
    }
  }

  async function loadUploads() {
    const hospital = selectedHospital();
    byId("archiveHospital").textContent = hospital;
    const rows = byId("uploadRows");
    try {
      const response = await fetch(`/pharmacy-api/uploads?hospital=${encodeURIComponent(hospital)}`);
      const uploads = await response.json();
      if (!response.ok) throw new Error(uploads.error || "Stored reports could not be loaded.");
      if (!uploads.length) {
        rows.innerHTML = '<tr><td colspan="5" class="empty-cell">No reports stored for this hospital yet.</td></tr>';
        return;
      }
      rows.replaceChildren(...uploads.map(upload => {
        const row = document.createElement("tr");
        const rowCount = upload.row_count.toLocaleString("en-IN");
        const skippedCount = Number(upload.skipped_count) || 0;
        const importedRows = skippedCount
          ? `${rowCount} imported · ${skippedCount.toLocaleString("en-IN")} skipped`
          : rowCount;
        const values = [upload.filename, pharmacyLabel(upload.pharmacy), reportTypes.find(([key]) => key === upload.report_type)?.[1] || upload.report_type, importedRows];
        values.forEach(value => {
          const cell = document.createElement("td");
          cell.textContent = value;
          row.appendChild(cell);
        });
        const action = document.createElement("td");
        const actions = document.createElement("div");
        actions.className = "upload-actions";
        if (upload.source_available) {
          const download = document.createElement("a");
          download.className = "button button-secondary download-button";
          download.href = `/pharmacy-api/uploads/${upload.id}/file`;
          download.textContent = "Download";
          actions.appendChild(download);
        } else {
          const unavailable = document.createElement("span");
          unavailable.textContent = "Original unavailable";
          unavailable.className = "subtle";
          actions.appendChild(unavailable);
        }
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "button button-danger remove-upload-button";
        remove.textContent = "Remove";
        remove.addEventListener("click", () => removeUpload(upload, remove));
        actions.appendChild(remove);
        action.appendChild(actions);
        row.appendChild(action);
        return row;
      }));
    } catch (error) {
      rows.innerHTML = `<tr><td colspan="5" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  async function removeUpload(upload, button) {
    if (!window.confirm(
      `Remove "${upload.filename}" from ${upload.hospital}? Its stored original and imported rows will be deleted.`
    )) return;
    button.disabled = true;
    showImportStatus(`Removing ${upload.filename}…`);
    try {
      const response = await fetch(`/pharmacy-api/uploads/${upload.id}`, {method: "DELETE"});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "The stored report could not be removed.");
      await Promise.all([loadUploads(), loadDashboard()]);
      showImportStatus(`${upload.filename} and its imported rows were removed. You can now add a replacement file.`, "success");
    } catch (error) {
      button.disabled = false;
      showImportStatus(error.message || "The stored report could not be removed.", "error");
    }
  }

  function updateFilterPharmacies() {
    const hospital = byId("hospitalFilter").value;
    const pharmacies = hospital === "all" ? ["IPD", "OPD", "Pharmacy"] : pharmacyOptions(hospital);
    const select = byId("pharmacyFilter");
    const selected = pharmacies.includes(select.value) ? select.value : "all";
    addOptions(
      select,
      [["all", "All pharmacies"], ...pharmacies.map(pharmacy => [pharmacy, pharmacyLabel(pharmacy)])],
      selected,
    );
  }

  function updateBatchHospital() {
    const pharmacies = pharmacyOptions(selectedHospital());
    const pharmacySelect = byId("batchPharmacy");
    const currentPharmacy = pharmacies.includes(pharmacySelect.value)
      ? pharmacySelect.value
      : pharmacies[0];
    addOptions(
      pharmacySelect,
      pharmacies.map(pharmacy => [pharmacy, pharmacyLabel(pharmacy)]),
      currentPharmacy,
    );
    filePreviews = [];
    byId("previewArea").classList.add("hidden");
    showImportStatus("");
    loadUploads();
  }

  function clearFilePreview() {
    filePreviews = [];
    byId("previewArea").classList.add("hidden");
    showImportStatus("");
  }

  setDefaultDates();
  setDashboardModule("pharmacy");
  updateDateControls();
  updateFilterPharmacies();
  updateBatchHospital();
  byId("periodFilter").addEventListener("change", updateDateControls);
  byId("applyFilters").addEventListener("click", loadDashboard);
  byId("refreshButton").addEventListener("click", loadDashboard);
  byId("downloadHtmlButton").addEventListener("click", downloadInteractiveHtml);
  byId("supplierChartMode").addEventListener("change", () => {
    renderSupplierChart(
      byId("supplierChart"),
      currentSupplierRows,
      byId("supplierChartMode").value,
    );
  });
  byId("hospitalSupplierChartMode").addEventListener("change", () => {
    const range = appliedDashboardState || currentRange();
    if (!range.start || !range.end || range.start > range.end) return;
    if (appliedDashboardState) {
      appliedDashboardState.hospitalSupplierChartMode =
        byId("hospitalSupplierChartMode").value;
    }
    renderHospitalSupplierCharts(
      currentHospitalSupplierRows,
      byId("hospitalSupplierChartMode").value,
      range,
    );
  });
  document.querySelectorAll(".module-tab[data-module]").forEach(button => {
    button.addEventListener("click", () => setDashboardModule(button.dataset.module));
  });
  byId("previewButton").addEventListener("click", previewFile);
  byId("importButton").addEventListener("click", importFiles);
  byId("hospitalFilter").addEventListener("change", () => {
    updateFilterPharmacies();
    loadDashboard();
  });
  byId("pharmacyFilter").addEventListener("change", loadDashboard);
  byId("chartMetric").addEventListener("change", () => loadDashboard());
  byId("resetMonthFilter").addEventListener("click", () => {
    if (!previousMonthRange) return;
    byId("periodFilter").value = previousMonthRange.period;
    byId("startDate").value = previousMonthRange.start;
    byId("endDate").value = previousMonthRange.end;
    previousMonthRange = null;
    byId("resetMonthFilter").hidden = true;
    updateDateControls();
    loadDashboard();
  });
  byId("hospitalMonthlyMetric").addEventListener("change", () => {
    renderHospitalMonthlyChart(
      currentHospitalMonthly,
      byId("hospitalMonthlyMetric").value,
      currentRange(),
    );
  });
  byId("facilityRows").addEventListener("click", event => {
    const button = event.target.closest(".facility-filter-button");
    if (!button) return;
    byId("hospitalFilter").value = button.dataset.hospital;
    updateFilterPharmacies();
    if (button.dataset.pharmacy) {
      byId("pharmacyFilter").value = button.dataset.pharmacy;
    }
    loadDashboard();
    byId("comparison-title").scrollIntoView({behavior: "smooth", block: "start"});
  });
  byId("uploadHospital").addEventListener("change", updateBatchHospital);
  byId("batchPharmacy").addEventListener("change", clearFilePreview);
  byId("reportFile").addEventListener("change", clearFilePreview);
  loadUploads();
  loadDashboard();
})();

// >>> COMPARISON FORECAST START
const FC_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const FC_YEAR_PALETTE = ["#b9c8d3", "#9db3c2", "#7f9db3", "#5f86a3", "#456f8d"];
const FC_NAVY = "#102f49";
const FC_GREY = "#9fb2bf";

function fcChangeText(pct) {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return "—";
  return `${pct > 0 ? "▲ +" : pct < 0 ? "▼ −" : ""}${Math.abs(pct).toFixed(1)}%`;
}

function fcYearStyles(years, watch) {
  const emphasised = years.includes(watch) ? watch : years[years.length - 1];
  const others = years.filter((year) => year !== emphasised);
  const styles = new Map();
  others.forEach((year, index) => {
    const slot = Math.max(0, Math.min(FC_YEAR_PALETTE.length - 1, FC_YEAR_PALETTE.length - others.length + index));
    styles.set(year, { color: FC_YEAR_PALETTE[slot], width: 1.8, emphasis: false });
  });
  if (emphasised !== undefined) styles.set(emphasised, { color: FC_NAVY, width: 3, emphasis: true });
  return styles;
}

function fcCalendarChart(rows, forecast) {
  const years = new Map();
  rows.forEach((row) => {
    const year = Number(row.start.slice(0, 4));
    const month = Number(row.start.slice(5, 7)) - 1;
    if (!years.has(year)) {
      const blank = () => Array(12).fill(null);
      years.set(year, { actual: blank(), soFar: blank(), forecast: blank(), lower: blank(), upper: blank() });
    }
    const entry = years.get(year);
    if (row.actual !== null && !row.partial) entry.actual[month] = row.actual;
    if (row.actual !== null && row.partial) entry.soFar[month] = row.actual;
    if (row.forecast !== null) {
      entry.forecast[month] = row.forecast;
      entry.lower[month] = row.lower;
      entry.upper[month] = row.upper;
    }
  });
  const ordered = [...years.keys()].sort((a, b) => a - b);
  const actualYears = ordered.filter((year) => years.get(year).actual.some((value) => value !== null));
  const styles = fcYearStyles(actualYears, forecast.watch?.year);
  const series = [];
  ordered.forEach((year) => {
    const entry = years.get(year);
    const style = styles.get(year);
    if (style) {
      series.push({ name: String(year), color: style.color, width: style.width, labels: style.emphasis, values: entry.actual });
    }
    if (entry.soFar.some((value) => value !== null)) {
      series.push({ name: `${year} so far (incomplete)`, color: style ? style.color : COLORS.blue, values: entry.soFar, noLine: true, hollow: true });
    }
    if (entry.forecast.some((value) => value !== null)) {
      const first = entry.forecast.findIndex((value) => value !== null);
      let anchor = -1;
      for (let index = first - 1; index >= 0; index -= 1) {
        if (entry.actual[index] !== null) { anchor = index; break; }
      }
      const values = entry.forecast.slice();
      const markers = values.map((value) => value !== null);
      if (anchor >= 0) { values[anchor] = entry.actual[anchor]; markers[anchor] = false; }
      series.push({
        name: `${year} forecast`, color: COLORS.orange, width: 2.2, dashed: true, labels: true,
        values, markers, band: { lower: entry.lower, upper: entry.upper },
      });
    }
  });
  const targets = rows.map((row) => row.target).filter((value) => Number.isFinite(value));
  return {
    labels: FC_MONTHS, series,
    target: targets.length ? FC_MONTHS.map(() => Math.max(...targets)) : null,
    zoneStart: null,
  };
}

function fcTimelineChart(rows, forecast) {
  const labels = rows.map((row) => row.label);
  const firstForecast = rows.findIndex((row) => row.forecast !== null);
  let anchor = -1;
  rows.forEach((row, index) => {
    if (row.actual !== null && !row.partial && (firstForecast < 0 || index < firstForecast)) anchor = index;
  });
  const actual = rows.map((row) => (row.actual !== null && !row.partial ? row.actual : null));
  const soFar = rows.map((row) => (row.actual !== null && row.partial ? row.actual : null));
  const values = rows.map((row, index) => (
    row.forecast !== null ? row.forecast : (index === anchor && firstForecast >= 0 ? row.actual : null)
  ));
  const markers = rows.map((row) => row.forecast !== null);
  const previous = rows.map((row) => (row.previousYear === undefined ? null : row.previousYear));
  const series = [];
  if (previous.some((value) => value !== null)) {
    series.push({
      name: forecast.mode === "Period" ? "Same period last year" : "Same month last year",
      color: FC_GREY, width: 1.8, labels: false, values: previous,
    });
  }
  series.push({ name: "Actual", color: COLORS.blue, width: 2.4, labels: true, values: actual });
  if (soFar.some((value) => value !== null)) {
    series.push({ name: "So far (incomplete)", color: COLORS.blue, values: soFar, noLine: true, hollow: true });
  }
  series.push({
    name: "Forecast", color: COLORS.orange, width: 2.4, dashed: true, labels: true, values, markers,
    band: { lower: rows.map((row) => row.lower), upper: rows.map((row) => row.upper) },
  });
  const targets = rows.map((row) => (Number.isFinite(row.target) ? row.target : null));
  return {
    labels, series,
    target: targets.some((value) => value !== null) ? targets : null,
    zoneStart: firstForecast >= 0 ? firstForecast : null,
  };
}

function fcBuildChart(metric, forecast) {
  const rows = metric.rows || [];
  return forecast.layout === "calendar" ? fcCalendarChart(rows, forecast) : fcTimelineChart(rows, forecast);
}

function fcDrawChart(canvas, chart, kind) {
  const { context: ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const axisFormat = kind === "currency" ? compactMoney : number;
  const tipFormat = kind === "currency" ? money : number;
  const { labels, series, target, zoneStart } = chart;
  if (!labels.length || !series.length || width < 80 || height < 60) {
    drawEmpty(ctx, width, height);
    attachChartTooltip(canvas, []);
    return;
  }
  const margin = { top: 22, right: 14, bottom: 30, left: 52 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const all = [];
  series.forEach((item) => {
    item.values.forEach((value) => { if (value !== null) all.push(value); });
    if (item.band) item.band.upper.forEach((value) => { if (value !== null) all.push(value); });
  });
  if (target) target.forEach((value) => { if (value !== null) all.push(value); });
  const max = Math.max(...all, 1) * 1.1;
  const step = chartWidth / labels.length;
  const xAt = (index) => margin.left + step * index + step / 2;
  const yAt = (value) => margin.top + chartHeight - chartHeight * value / max;

  if (zoneStart !== null) {
    ctx.fillStyle = "#f4f8fa";
    ctx.fillRect(margin.left + step * zoneStart, margin.top - 6, chartWidth - step * zoneStart, chartHeight + 6);
    ctx.fillStyle = COLORS.muted;
    ctx.font = "bold 9.5px Segoe UI, Arial";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText("FORECAST", margin.left + step * zoneStart + 5, margin.top - 3);
  }

  ctx.font = "10px Segoe UI, Arial";
  ctx.textBaseline = "middle";
  for (let tick = 0; tick <= 3; tick += 1) {
    const y = margin.top + chartHeight - chartHeight * tick / 3;
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(margin.left, y); ctx.lineTo(width - margin.right, y); ctx.stroke();
    ctx.fillStyle = COLORS.muted;
    ctx.textAlign = "right";
    ctx.fillText(axisFormat(max * tick / 3), margin.left - 6, y);
  }

  series.forEach((item) => {
    if (!item.band) return;
    const points = item.values
      .map((_, index) => index)
      .filter((index) => item.band.upper[index] !== null && item.band.lower[index] !== null);
    if (points.length > 1) {
      ctx.fillStyle = "rgba(228, 149, 63, .18)";
      ctx.beginPath();
      points.forEach((index, i) => {
        if (i === 0) ctx.moveTo(xAt(index), yAt(item.band.upper[index]));
        else ctx.lineTo(xAt(index), yAt(item.band.upper[index]));
      });
      [...points].reverse().forEach((index) => ctx.lineTo(xAt(index), yAt(item.band.lower[index])));
      ctx.closePath();
      ctx.fill();
    } else if (points.length === 1) {
      ctx.strokeStyle = "rgba(228, 149, 63, .55)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(xAt(points[0]), yAt(item.band.upper[points[0]]));
      ctx.lineTo(xAt(points[0]), yAt(item.band.lower[points[0]]));
      ctx.stroke();
    }
  });

  if (target) {
    ctx.strokeStyle = FC_GREY;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    let started = false;
    target.forEach((value, index) => {
      if (value === null) return;
      if (!started) { ctx.moveTo(xAt(index), yAt(value)); started = true; }
      else ctx.lineTo(xAt(index), yAt(value));
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  const hitboxes = [];
  const candidates = [];
  const labelEvery = labels.length <= 14 ? 1 : Math.ceil(labels.length / 12);
  series.forEach((item) => {
    if (!item.noLine) {
      ctx.strokeStyle = item.color;
      ctx.lineWidth = item.width || 2;
      ctx.setLineDash(item.dashed ? [6, 4] : []);
      ctx.beginPath();
      let drawing = false;
      item.values.forEach((value, index) => {
        if (value === null) { drawing = false; return; }
        if (!drawing) { ctx.moveTo(xAt(index), yAt(value)); drawing = true; }
        else ctx.lineTo(xAt(index), yAt(value));
      });
      ctx.stroke();
      ctx.setLineDash([]);
    }
    item.values.forEach((value, index) => {
      if (value === null || (item.markers && item.markers[index] === false)) return;
      const x = xAt(index);
      const y = yAt(value);
      ctx.beginPath();
      ctx.arc(x, y, item.hollow ? 4 : 3.4, 0, Math.PI * 2);
      ctx.fillStyle = item.hollow ? "#fff" : item.color;
      ctx.fill();
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = item.color;
      ctx.stroke();
      if (item.labels && index % labelEvery === 0) {
        candidates.push({
          text: axisFormat(value), x, y,
          color: item.dashed ? "#a85f12" : item.color,
          priority: item.dashed ? 0 : 1,
        });
      }
      let text = `${item.name} · ${labels[index]}`;
      if (item.band && item.band.lower[index] !== null) {
        text += ` (likely ${tipFormat(item.band.lower[index])} – ${tipFormat(item.band.upper[index])})`;
      }
      hitboxes.push({ x: x - 9, y: y - 9, width: 18, height: 18, label: text, value, formatter: tipFormat });
    });
  });

  // Value labels: forecast first, then the selected line. A label that would
  // overlap one already placed is tried below the point, otherwise skipped.
  ctx.font = "bold 10px Segoe UI, Arial";
  const placed = [];
  const fits = (box) => box.left >= margin.left - 4 && box.right <= width - 4
    && !placed.some((other) => box.left < other.right && box.right > other.left
      && box.top < other.bottom && box.bottom > other.top);
  candidates.sort((a, b) => a.priority - b.priority || a.x - b.x).forEach((label) => {
    const measured = ctx.measureText(label.text);
    const half = ((measured && measured.width) || label.text.length * 6) / 2 + 3;
    for (const baseline of [label.y - 7, label.y + 19]) {
      const box = { left: label.x - half, right: label.x + half, top: baseline - 12, bottom: baseline + 2 };
      if (!fits(box)) continue;
      placed.push(box);
      drawValueLabel(ctx, label.text, label.x, baseline, label.color);
      break;
    }
  });

  const every = Math.max(1, Math.ceil(46 / step));
  ctx.fillStyle = COLORS.muted;
  ctx.font = "10px Segoe UI, Arial";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  labels.forEach((label, index) => {
    if (index % every === 0) ctx.fillText(String(label), xAt(index), height - 12);
  });
  attachChartTooltip(canvas, hitboxes.reverse());
}

function fcRenderLegend(forecast, holder) {
  holder.replaceChildren();
  const add = (color, text, kind = "") => {
    const item = document.createElement("span");
    item.className = "fc-legend-item";
    const dot = document.createElement("i");
    dot.className = `fc-legend-dot ${kind}`.trim();
    dot.style.setProperty("--c", color);
    item.append(dot, document.createTextNode(text));
    holder.append(item);
  };
  const rows = forecast.metrics[0]?.rows || [];
  if (forecast.layout === "calendar") {
    const years = [...new Set(rows.filter((row) => row.actual !== null && !row.partial)
      .map((row) => Number(row.start.slice(0, 4))))].sort((a, b) => a - b);
    const styles = fcYearStyles(years, forecast.watch?.year);
    years.forEach((year) => {
      const style = styles.get(year);
      add(style.color, style.emphasis ? `${year} (selected)` : String(year));
    });
  } else {
    add(COLORS.blue, "Actual");
    if (rows.some((row) => row.previousYear !== null && row.previousYear !== undefined)) {
      add(FC_GREY, forecast.mode === "Period" ? "Same period last year" : "Same month last year");
    }
  }
  add(COLORS.orange, "Forecast (shaded = likely range)", "is-dashed");
  if (rows.some((row) => row.partial && row.actual !== null)) add(COLORS.blue, "Incomplete so far", "is-hollow");
}

function renderForecastSection(forecast, message = "") {
  const section = $("#comparison-forecast-section");
  if (!section) return;
  const grid = $("#comparison-forecast-grid");
  const notes = $("#comparison-forecast-notes");
  const legend = $("#comparison-forecast-legend");
  [grid, notes, legend].forEach((node) => node.replaceChildren());
  const addNote = (text) => {
    const item = document.createElement("li");
    item.textContent = text;
    notes.append(item);
  };
  if (!forecast || !forecast.hasData) {
    section.classList.toggle("is-hidden", !message);
    if (message) {
      $("#comparison-forecast-title").textContent = "Forecast";
      $("#comparison-forecast-subtitle").textContent = "";
      $("#comparison-forecast-confidence").textContent = "";
      addNote(message);
    }
    return;
  }
  section.classList.remove("is-hidden");
  const titles = {
    Year: "Year-on-year trend",
    Month: `${forecast.watch?.month || "Month"} across the years`,
    Recent: `Recent trend (last ${forecast.window} months)`,
    Period: "Billing-period trend",
  };
  $("#comparison-forecast-title").textContent = `${titles[forecast.mode] || "Trend"} · next ${forecast.horizon} ${forecast.unit}`;
  $("#comparison-forecast-subtitle").textContent = forecast.description;
  $("#comparison-forecast-confidence").textContent = `Confidence: ${forecast.confidence} (${forecast.points} data points)`;
  fcRenderLegend(forecast, legend);
  (forecast.notes || []).forEach(addNote);

  forecast.metrics.forEach((metric) => {
    const format = metric.kind === "currency" ? money : number;
    const summary = metric.summary;
    const hasTarget = metric.rows.some((row) => Number.isFinite(row.target));
    const card = document.createElement("article");
    card.className = "panel chart-card";
    const heading = document.createElement("div");
    heading.className = "chart-heading";
    const title = document.createElement("div");
    const name = document.createElement("h3");
    name.textContent = metric.label;
    const sub = document.createElement("p");
    sub.textContent = hasTarget ? "Dashed grey line = revenue target" : "Actual vs forecast";
    title.append(name, sub);
    heading.append(title);
    const wrap = document.createElement("div");
    wrap.className = "chart-wrap";
    const canvas = document.createElement("canvas");
    canvas.className = "fc-canvas";
    canvas.setAttribute("aria-label", `${metric.label} trend and forecast chart`);
    canvas.fcMetric = metric;
    canvas.fcForecast = forecast;
    wrap.append(canvas);
    const footer = document.createElement("div");
    footer.className = "chart-total";
    const items = [{ label: `Forecast · ${summary.nextLabel}`, value: summary.next, formatter: format }];
    if (forecast.mode !== "Month") {
      items.push({ label: `vs ${summary.previousLabel}`, value: summary.changePct, formatter: fcChangeText });
    }
    if (summary.yoyPct !== null && summary.yoyPct !== undefined) {
      items.push({ label: `vs ${summary.yoyLabel}`, value: summary.yoyPct, formatter: fcChangeText });
    }
    if (Number.isFinite(summary.targetPct)) {
      items.push({ label: "Of target", value: summary.targetPct, formatter: (value) => `${value.toFixed(0)}%` });
    }
    items.push({ label: `Total next ${forecast.horizon} ${forecast.unit}`, value: summary.horizonTotal, formatter: format });
    renderTotalItems(footer, items);
    card.append(heading, wrap, footer);
    grid.append(card);
  });

  if (forecast.services && forecast.services.length) {
    const first = forecast.metrics[0].summary;
    const card = document.createElement("article");
    card.className = "panel chart-card comparison-mini-card";
    const heading = document.createElement("div");
    heading.className = "chart-heading";
    const title = document.createElement("h3");
    title.textContent = `Top OPD services · forecast for ${first.nextLabel}`;
    heading.append(title);
    const container = document.createElement("div");
    container.className = "comparison-column-chart comparison-compact-chart";
    container.setAttribute("role", "img");
    card.append(heading, container);
    grid.append(card);
    renderComparisonColumnChart(
      container,
      forecast.services.map((row) => ({ label: row.name, first: row.previous, second: row.next })),
      [
        { key: "first", label: `Last (${first.previousLabel})` },
        { key: "second", label: `Forecast (${first.nextLabel})` },
      ],
      number, null, null, "count",
    );
  }
  drawForecastCharts();
}

function drawForecastCharts() {
  const view = $("#comparison-view");
  if (!view || view.classList.contains("is-hidden")) return;
  document.querySelectorAll("#comparison-forecast-grid canvas.fc-canvas").forEach((canvas) => {
    if (canvas.fcMetric) fcDrawChart(canvas, fcBuildChart(canvas.fcMetric, canvas.fcForecast), canvas.fcMetric.kind);
  });
}

window.addEventListener("resize", () => window.requestAnimationFrame(drawForecastCharts));
// <<< COMPARISON FORECAST END
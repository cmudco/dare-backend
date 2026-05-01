/* ════════════════════════════════════════════════════════════════════
   Usage Dashboard — Chart.js wiring and small DOM helpers.
   Reads JSON payloads from <script type="application/json"> nodes and
   pulls colors from CSS custom properties so dark mode just works.
   ════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  const dash = document.getElementById("dash");
  if (!dash) return;

  const cv = (name) => getComputedStyle(dash).getPropertyValue(name).trim();

  const blue   = cv("--blue");
  const green  = cv("--green");
  const amber  = cv("--amber");
  const purple = cv("--purple");
  const rose   = cv("--rose");
  const teal   = cv("--teal");
  const indigo = cv("--indigo");
  const chText = cv("--ch-text");
  const chGrid = cv("--ch-grid");
  const cardBg = cv("--card");
  const t1     = cv("--t1");
  const t2     = cv("--t2");

  const palette = [blue, green, amber, purple, rose, teal, indigo, "#ec4899", "#06b6d4", "#84cc16"];

  // Stable provider → color mapping so the donut and per-provider rows
  // share a visual key.
  const PROVIDER_COLORS = {
    OpenAI:  blue,
    Claude:  amber,
    Gemini:  purple,
    Llama:   teal,
    Custom:  rose,
    Unknown: indigo,
  };
  const providerColor = (name) => PROVIDER_COLORS[name] || indigo;

  // Tier color mapping — matches the .bx-tier-* badge colors in CSS.
  const TIER_COLORS = {
    Premium:  purple,
    Advanced: blue,
    Flash:    teal,
  };
  const tierColor = (name) => TIER_COLORS[name] || indigo;

  Chart.defaults.color       = chText;
  Chart.defaults.borderColor = chGrid;
  Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Inter', Roboto, sans-serif";
  Chart.defaults.font.size   = 11;

  const tip = {
    backgroundColor: cardBg,
    titleColor: t1,
    bodyColor: t2,
    borderColor: chGrid,
    borderWidth: 1,
    padding: 10,
    cornerRadius: 6,
  };

  const readJSON = (id, fallback) => {
    const node = document.getElementById(id);
    if (!node) return fallback;
    try {
      return JSON.parse(node.textContent || "");
    } catch (e) {
      console.warn("dashboard.js: failed to parse " + id, e);
      return fallback;
    }
  };

  const trend    = readJSON("dash-trend-json",    []);
  const modelMix = readJSON("dash-model-json",    []);
  const provMix  = readJSON("dash-provider-json", []);
  const tierMix  = readJSON("dash-tier-json",     []);

  /* ── Daily activity (dual-axis line) ────────────────────────── */
  const trendCanvas = document.getElementById("trendChart");
  if (trendCanvas) {
    const legSpend = document.getElementById("leg-spend");
    const legCalls = document.getElementById("leg-calls");
    if (legSpend) legSpend.style.background = blue;
    if (legCalls) legCalls.style.background = green;

    new Chart(trendCanvas, {
      type: "line",
      data: {
        labels: trend.map((d) => d.date),
        datasets: [
          {
            label: "Wallet Spend ($)",
            data: trend.map((d) => d.cost),
            borderColor: blue,
            backgroundColor: blue + "14",
            borderWidth: 2,
            pointRadius: trend.length > 40 ? 0 : 3,
            pointHoverRadius: 5,
            pointBackgroundColor: blue,
            tension: 0.35,
            fill: true,
            yAxisID: "ySpend",
          },
          {
            label: "LLM Calls",
            data: trend.map((d) => d.calls),
            borderColor: green,
            backgroundColor: green + "14",
            borderWidth: 2,
            pointRadius: trend.length > 40 ? 0 : 3,
            pointHoverRadius: 5,
            pointBackgroundColor: green,
            tension: 0.35,
            fill: true,
            yAxisID: "yCalls",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...tip,
            callbacks: {
              label: (ctx) =>
                ctx.datasetIndex === 0
                  ? " $" + ctx.parsed.y.toFixed(6)
                  : " " + ctx.parsed.y + " calls",
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: { maxTicksLimit: 8, maxRotation: 0, color: chText },
          },
          ySpend: {
            position: "left",
            grid: { color: chGrid },
            border: { display: false },
            ticks: {
              color: chText,
              callback: (v) => (v < 0.01 ? "$" + v.toExponential(1) : "$" + v.toFixed(4)),
            },
          },
          yCalls: {
            position: "right",
            grid: { drawOnChartArea: false },
            border: { display: false },
            ticks: { color: chText, stepSize: 1, callback: (v) => (Number.isInteger(v) ? v : "") },
          },
        },
      },
    });
  }

  /* ── Cost by Model (horizontal bar with calls/cost/tokens toggle) */
  const modelCanvas = document.getElementById("modelChart");
  if (modelCanvas && modelMix.length) {
    const labels = modelMix.map((d) => d.name);
    const datasetFor = (key, title, fmt) => ({
      label: title,
      data: modelMix.map((d) => d[key]),
      backgroundColor: modelMix.map((_, i) => palette[i % palette.length] + "bb"),
      borderColor:     modelMix.map((_, i) => palette[i % palette.length]),
      borderWidth: 1,
      borderRadius: 4,
      borderSkipped: false,
      _fmt: fmt,
    });

    const datasetMap = {
      calls:  datasetFor("calls",  "LLM Calls",   (v) => v + " calls"),
      cost:   datasetFor("cost",   "Cost (USD)",  (v) => "$" + v.toFixed(4)),
      tokens: datasetFor("tokens", "Total Tokens", (v) => v.toLocaleString() + " tokens"),
    };

    const chart = new Chart(modelCanvas, {
      type: "bar",
      data: { labels, datasets: [datasetMap.calls] },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            ...tip,
            callbacks: {
              label: (ctx) => " " + (ctx.dataset._fmt ? ctx.dataset._fmt(ctx.parsed.x) : ctx.parsed.x),
            },
          },
        },
        scales: {
          x: { grid: { color: chGrid }, border: { display: false }, ticks: { color: chText } },
          y: { grid: { display: false }, border: { display: false }, ticks: { color: chText } },
        },
      },
    });

    document.querySelectorAll("[data-toggle-model]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.toggleModel;
        if (!datasetMap[key]) return;
        document
          .querySelectorAll("[data-toggle-model]")
          .forEach((b) => b.classList.toggle("on", b === btn));
        chart.data.datasets = [datasetMap[key]];
        chart.update();
      });
    });
  }

  /* ── Cost by Provider (donut) ───────────────────────────────── */
  const provCanvas = document.getElementById("providerChart");
  if (provCanvas && provMix.length) {
    new Chart(provCanvas, {
      type: "doughnut",
      data: {
        labels: provMix.map((d) => d.name),
        datasets: [
          {
            data: provMix.map((d) => d.cost),
            backgroundColor: provMix.map((d) => providerColor(d.name)),
            borderColor: cardBg,
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: {
          legend: {
            position: "bottom",
            labels: { color: chText, boxWidth: 10, boxHeight: 10, padding: 12 },
          },
          tooltip: {
            ...tip,
            callbacks: {
              label: (ctx) => " " + ctx.label + " — $" + ctx.parsed.toFixed(4),
            },
          },
        },
      },
    });

    // Color the swatches in the table to match the donut.
    document.querySelectorAll("[data-provider-swatch]").forEach((el) => {
      el.style.background = providerColor(el.dataset.providerSwatch);
    });
  }

  /* ── Cost by Tier (horizontal bar) ─────────────────────────── */
  const tierCanvas = document.getElementById("tierChart");
  if (tierCanvas && tierMix.length) {
    new Chart(tierCanvas, {
      type: "bar",
      data: {
        labels: tierMix.map((d) => d.name),
        datasets: [
          {
            label: "Cost (USD)",
            data: tierMix.map((d) => d.cost),
            backgroundColor: tierMix.map((d) => tierColor(d.name) + "bb"),
            borderColor: tierMix.map((d) => tierColor(d.name)),
            borderWidth: 1,
            borderRadius: 4,
            borderSkipped: false,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            ...tip,
            callbacks: { label: (ctx) => " $" + ctx.parsed.x.toFixed(4) },
          },
        },
        scales: {
          x: { grid: { color: chGrid }, border: { display: false }, ticks: { color: chText } },
          y: { grid: { display: false }, border: { display: false }, ticks: { color: chText } },
        },
      },
    });
  }

  /* ── Compact large numbers in KPI cards ────────────────────── */
  const compact = (el) => {
    if (!el) return;
    const n = parseFloat(el.textContent.replace(/[^0-9.]/g, ""));
    if (!Number.isFinite(n)) return;
    if (n >= 1e9) el.textContent = (n / 1e9).toFixed(1) + "B";
    else if (n >= 1e6) el.textContent = (n / 1e6).toFixed(1) + "M";
    else if (n >= 1e3) el.textContent = (n / 1e3).toFixed(1) + "K";
  };
  document.querySelectorAll("[data-compact]").forEach(compact);
})();

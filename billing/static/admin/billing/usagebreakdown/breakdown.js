/* Usage Breakdown — clickable rows + daily trend chart on detail view. */
(function () {
  "use strict";

  // ── Clickable table rows → detail link ─────────────────────────
  document.querySelectorAll("#dash tr.row-link[data-href]").forEach(function (tr) {
    tr.addEventListener("click", function () {
      window.location.href = tr.getAttribute("data-href");
    });
  });

  // ── Daily trend chart (detail view only) ───────────────────────
  var canvas = document.getElementById("trendChart");
  var payloadEl = document.getElementById("trend-json");
  if (!canvas || !payloadEl || typeof Chart === "undefined") return;

  var data;
  try {
    data = JSON.parse(payloadEl.textContent || "[]");
  } catch (_) {
    data = [];
  }
  if (!data.length) return;

  // Pull theme colors from CSS vars so dark mode works automatically.
  var styles = getComputedStyle(document.getElementById("dash"));
  var color = function (name, fallback) {
    return (styles.getPropertyValue(name) || "").trim() || fallback;
  };
  var blue = color("--blue", "#3b82f6");
  var teal = color("--teal", "#14b8a6");
  var grid = color("--ch-grid", "rgba(15,23,42,0.06)");
  var text = color("--ch-text", "#64748b");

  // Tag legend dots (re-uses dashboard.css .ch-legend-dot styling).
  var ls = document.getElementById("leg-spend");
  var lc = document.getElementById("leg-calls");
  if (ls) ls.style.background = blue;
  if (lc) lc.style.background = teal;

  new Chart(canvas, {
    type: "line",
    data: {
      labels: data.map(function (r) { return r.date; }),
      datasets: [
        {
          label: "Spend ($)",
          data: data.map(function (r) { return r.cost; }),
          borderColor: blue,
          backgroundColor: blue,
          yAxisID: "y",
          tension: 0.25,
          pointRadius: 2,
          borderWidth: 2,
        },
        {
          label: "LLM Calls",
          data: data.map(function (r) { return r.calls; }),
          borderColor: teal,
          backgroundColor: teal,
          yAxisID: "y1",
          tension: 0.25,
          pointRadius: 2,
          borderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: text, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
          grid: { color: grid },
        },
        y: {
          position: "left",
          ticks: { color: text, callback: function (v) { return "$" + v; } },
          grid: { color: grid },
        },
        y1: {
          position: "right",
          ticks: { color: text },
          grid: { display: false },
        },
      },
    },
  });
})();

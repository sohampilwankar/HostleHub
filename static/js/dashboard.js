// HostelHub admin dashboard charts (Chart.js). Data comes from the server as JSON
// rendered from live database queries; nothing here is hard-coded.
(function () {
  var dataEl = document.getElementById("dashboard-data");
  if (!dataEl || !window.Chart) return;
  var data = JSON.parse(dataEl.textContent);

  var NAVY = "#1e3a8a";
  var NAVY_SOFT = "#c7d2fe";
  var INK = "#1f2937";
  var MUTED = "#6b7280";
  var GRID = "#eef0f3";

  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = MUTED;
  Chart.defaults.animation = false; // keep the dashboard calm
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.plugins.tooltip.backgroundColor = "#111827";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.plugins.tooltip.displayColors = false;

  function showEmpty(canvas, message) {
    var box = canvas.parentElement;
    box.classList.add("chart-empty");
    box.innerHTML = '<div class="empty-state"><i class="bi bi-bar-chart"></i><p>' + message + "</p></div>";
  }

  function sum(values) {
    return values.reduce(function (a, b) { return a + b; }, 0);
  }

  // Draws the count above each bar (skips zero bars to avoid clutter).
  var barValueLabels = {
    id: "barValueLabels",
    afterDatasetsDraw: function (chart) {
      var ctx = chart.ctx;
      ctx.save();
      ctx.fillStyle = INK;
      ctx.font = "600 12px " + Chart.defaults.font.family;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      chart.getDatasetMeta(0).data.forEach(function (bar, i) {
        var value = chart.data.datasets[0].data[i];
        if (value > 0) ctx.fillText(value, bar.x, bar.y - 4);
      });
      ctx.restore();
    }
  };

  function integerScale(max) {
    return {
      beginAtZero: true,
      suggestedMax: Math.max(4, Math.ceil(max * 1.2)),
      ticks: { precision: 0, maxTicksLimit: 5 },
      grid: { color: GRID },
      border: { display: false }
    };
  }

  function statusBarChart(id, chartData, noun) {
    var canvas = document.getElementById(id);
    if (!canvas) return;
    if (sum(chartData.values) === 0) return showEmpty(canvas, "No " + noun + " yet.");
    new Chart(canvas, {
      type: "bar",
      data: {
        labels: chartData.labels,
        datasets: [{
          label: noun.charAt(0).toUpperCase() + noun.slice(1),
          data: chartData.values,
          backgroundColor: NAVY,
          hoverBackgroundColor: "#172554",
          borderRadius: { topLeft: 4, topRight: 4 },
          borderSkipped: "bottom",
          maxBarThickness: 44
        }]
      },
      options: {
        maintainAspectRatio: false,
        layout: { padding: { top: 18 } },
        scales: {
          x: { grid: { display: false }, border: { color: "#d1d5db" }, ticks: { color: INK } },
          y: integerScale(Math.max.apply(null, chartData.values))
        },
        plugins: {
          tooltip: {
            callbacks: {
              label: function (item) {
                var total = sum(chartData.values);
                var pct = total ? Math.round((item.raw / total) * 100) : 0;
                return item.raw + " " + noun + " (" + pct + "%)";
              }
            }
          }
        }
      },
      plugins: [barValueLabels]
    });
  }

  // A. Registrations over time
  (function () {
    var canvas = document.getElementById("chart-registrations");
    var reg = data.registrations;
    if (!canvas) return;
    if (sum(reg.values) === 0) return showEmpty(canvas, "No students registered in the last 30 days.");
    new Chart(canvas, {
      type: "bar",
      data: {
        labels: reg.labels,
        datasets: [{
          label: "New registrations",
          data: reg.values,
          backgroundColor: NAVY,
          hoverBackgroundColor: "#172554",
          borderRadius: { topLeft: 4, topRight: 4 },
          borderSkipped: "bottom",
          maxBarThickness: 18,
          minBarLength: 0
        }]
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            grid: { display: false },
            border: { color: "#d1d5db" },
            ticks: { autoSkip: true, maxTicksLimit: 8, maxRotation: 0 }
          },
          y: integerScale(Math.max.apply(null, reg.values))
        },
        plugins: {
          tooltip: {
            callbacks: {
              label: function (item) {
                return item.raw + (item.raw === 1 ? " new student" : " new students");
              }
            }
          }
        }
      }
    });
  })();

  // B. Room occupancy doughnut
  (function () {
    var canvas = document.getElementById("chart-occupancy");
    var occ = data.occupancy;
    if (!canvas) return;
    var total = occ.occupied + occ.available;
    if (total === 0) return showEmpty(canvas, "No rooms added yet.");
    var pct = Math.round((occ.occupied / total) * 100);

    var centerText = {
      id: "centerText",
      afterDraw: function (chart) {
        var meta = chart.getDatasetMeta(0).data[0];
        if (!meta) return;
        var ctx = chart.ctx;
        ctx.save();
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = INK;
        ctx.font = "600 24px " + Chart.defaults.font.family;
        ctx.fillText(pct + "%", meta.x, meta.y - 8);
        ctx.fillStyle = MUTED;
        ctx.font = "12px " + Chart.defaults.font.family;
        ctx.fillText("occupied", meta.x, meta.y + 14);
        ctx.restore();
      }
    };

    new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: ["Occupied beds", "Available beds"],
        datasets: [{
          data: [occ.occupied, occ.available],
          backgroundColor: [NAVY, NAVY_SOFT],
          hoverBackgroundColor: ["#172554", "#a5b4fc"],
          borderColor: "#ffffff",
          borderWidth: 2,
          hoverOffset: 4
        }]
      },
      options: {
        maintainAspectRatio: false,
        cutout: "70%",
        plugins: {
          tooltip: {
            callbacks: {
              label: function (item) {
                return item.label + ": " + item.raw + " (" + Math.round((item.raw / total) * 100) + "%)";
              }
            }
          }
        }
      },
      plugins: [centerText]
    });
  })();

  // C & D. Status charts
  statusBarChart("chart-applications", data.applications, "applications");
  statusBarChart("chart-complaints", data.complaints, "complaints");
})();

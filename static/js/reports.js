/* Reports charts */

(function () {
  const data = window.REPORT_DATA || {};
  const cashflow = data.cashflow || [];
  const categories = data.categories || [];

  Chart.defaults.color = "#94a3b8";
  Chart.defaults.borderColor = "rgba(255,255,255,0.06)";
  Chart.defaults.font.family = "'DM Sans', system-ui, sans-serif";

  const cashflowEl = document.getElementById("cashflowChart");
  if (cashflowEl && cashflow.length) {
    new Chart(cashflowEl, {
      type: "bar",
      data: {
        labels: cashflow.map((d) => d.label),
        datasets: [
          {
            label: "Income",
            data: cashflow.map((d) => d.income),
            backgroundColor: "rgba(52, 211, 153, 0.55)",
            borderRadius: 6,
            borderSkipped: false,
          },
          {
            label: "Expenses",
            data: cashflow.map((d) => d.expenses),
            backgroundColor: "rgba(251, 113, 133, 0.55)",
            borderRadius: 6,
            borderSkipped: false,
          },
          {
            label: "Investments",
            data: cashflow.map((d) => d.investments),
            backgroundColor: "rgba(56, 189, 248, 0.55)",
            borderRadius: 6,
            borderSkipped: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            position: "top",
            align: "end",
            labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true },
          },
          tooltip: {
            callbacks: {
              label: (ctx) =>
                ` ${ctx.dataset.label}: ${(data.currency || "₹")}${Number(
                  ctx.raw
                ).toLocaleString("en-IN")}`,
            },
          },
        },
        scales: {
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            ticks: {
              callback: (v) =>
                (data.currency || "₹") +
                Number(v).toLocaleString("en-IN", { notation: "compact" }),
            },
          },
        },
      },
    });
  }

  const categoryEl = document.getElementById("categoryChart");
  if (categoryEl && categories.length) {
    new Chart(categoryEl, {
      type: "doughnut",
      data: {
        labels: categories.map((c) => c.name),
        datasets: [
          {
            data: categories.map((c) => c.total),
            backgroundColor: categories.map((c) => c.color || "#64748b"),
            borderWidth: 0,
            hoverOffset: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: {
          legend: {
            position: "right",
            labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, padding: 12 },
          },
          tooltip: {
            callbacks: {
              label: (ctx) =>
                ` ${ctx.label}: ${(data.currency || "₹")}${Number(
                  ctx.raw
                ).toLocaleString("en-IN")}`,
            },
          },
        },
      },
    });
  }
})();

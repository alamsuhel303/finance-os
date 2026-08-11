(function () {
  const data = window.NW_DATA || {};
  const chart = data.chart || [];
  const allocation = data.allocation || [];
  const currency = data.currency || "₹";

  Chart.defaults.color = "#94a3b8";
  Chart.defaults.borderColor = "rgba(255,255,255,0.06)";
  Chart.defaults.font.family = "'DM Sans', system-ui, sans-serif";

  const nwEl = document.getElementById("nwChart");
  if (nwEl && chart.length) {
    new Chart(nwEl, {
      type: "line",
      data: {
        labels: chart.map((d) => d.label),
        datasets: [
          {
            label: "Net Worth",
            data: chart.map((d) => d.net_worth),
            borderColor: "#5eead4",
            backgroundColor: "rgba(94, 234, 212, 0.12)",
            fill: true,
            tension: 0.35,
            pointRadius: 3,
            pointHoverRadius: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) =>
                ` ${currency}${Number(ctx.raw).toLocaleString("en-IN")}`,
            },
          },
        },
        scales: {
          x: { grid: { display: false } },
          y: {
            ticks: {
              callback: (v) =>
                currency +
                Number(v).toLocaleString("en-IN", { notation: "compact" }),
            },
          },
        },
      },
    });
  }

  const allocEl = document.getElementById("allocNwChart");
  if (allocEl && allocation.length) {
    const positive = allocation.filter((a) => a.value > 0);
    new Chart(allocEl, {
      type: "doughnut",
      data: {
        labels: positive.map((a) => a.label),
        datasets: [
          {
            data: positive.map((a) => a.value),
            backgroundColor: positive.map((a, i) => {
              const palette = [
                "#38bdf8",
                "#34d399",
                "#a78bfa",
                "#fbbf24",
                "#2dd4bf",
                "#60a5fa",
                "#fb923c",
                "#f472b6",
              ];
              return a.color && a.color !== "#64748b"
                ? a.color
                : palette[i % palette.length];
            }),
            borderWidth: 0,
            hoverOffset: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "60%",
        plugins: {
          legend: {
            position: "bottom",
            labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, padding: 10 },
          },
        },
      },
    });
  }
})();

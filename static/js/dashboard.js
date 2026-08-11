/* Dashboard charts */

document.addEventListener("DOMContentLoaded", () => {
  Chart.defaults.color = "#94a3b8";
  Chart.defaults.borderColor = "rgba(255,255,255,0.06)";
  Chart.defaults.font.family = "'DM Sans', system-ui, sans-serif";

  const trendCanvas = document.getElementById("expenseTrendChart");
  if (trendCanvas) {
    const labels = JSON.parse(trendCanvas.dataset.labels || "[]");
    const values = JSON.parse(trendCanvas.dataset.values || "[]");

    new Chart(trendCanvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Expenses",
            data: values,
            backgroundColor: "rgba(94, 234, 212, 0.35)",
            borderColor: "#5eead4",
            borderWidth: 1.5,
            borderRadius: 8,
            borderSkipped: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#111827",
            borderColor: "rgba(255,255,255,0.1)",
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: (ctx) =>
                " ₹" + Number(ctx.raw).toLocaleString("en-IN"),
            },
          },
        },
        scales: {
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            ticks: {
              callback: (v) =>
                "₹" + Number(v).toLocaleString("en-IN", { notation: "compact" }),
            },
          },
        },
      },
    });
  }

  const pieCanvas = document.getElementById("categoryPieChart");
  if (pieCanvas) {
    const labels = JSON.parse(pieCanvas.dataset.labels || "[]");
    const values = JSON.parse(pieCanvas.dataset.values || "[]");
    const colors = JSON.parse(pieCanvas.dataset.colors || "[]");

    new Chart(pieCanvas, {
      type: "doughnut",
      data: {
        labels,
        datasets: [
          {
            data: values,
            backgroundColor: colors,
            borderColor: "#0b0f1a",
            borderWidth: 3,
            hoverOffset: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              boxWidth: 10,
              boxHeight: 10,
              usePointStyle: true,
              pointStyle: "circle",
              padding: 14,
              font: { size: 11 },
            },
          },
          tooltip: {
            backgroundColor: "#111827",
            borderColor: "rgba(255,255,255,0.1)",
            borderWidth: 1,
            callbacks: {
              label: (ctx) =>
                ` ${ctx.label}: ₹${Number(ctx.raw).toLocaleString("en-IN")}`,
            },
          },
        },
      },
    });
  }

  /* Privacy eye — hide net worth / income / balances until revealed */
  const privacyBtn = document.getElementById("privacyToggle");
  const privacyIcon = document.getElementById("privacyToggleIcon");
  const privacyLabel = document.getElementById("privacyToggleLabel");
  const KEY = "fos_privacy_hidden";

  function setPrivacy(hidden) {
    document.body.classList.toggle("privacy-on", hidden);
    if (privacyIcon) {
      privacyIcon.className = hidden ? "bi bi-eye-slash" : "bi bi-eye";
    }
    if (privacyLabel) {
      privacyLabel.textContent = hidden ? "Show amounts" : "Hide amounts";
    }
    if (privacyBtn) {
      privacyBtn.setAttribute("aria-pressed", hidden ? "false" : "true");
    }
    try {
      localStorage.setItem(KEY, hidden ? "1" : "0");
    } catch (_) {}
  }

  const startHidden = (() => {
    try {
      const v = localStorage.getItem(KEY);
      return v === null ? true : v === "1"; // default: hidden
    } catch (_) {
      return true;
    }
  })();
  setPrivacy(startHidden);

  if (privacyBtn) {
    privacyBtn.addEventListener("click", () => {
      setPrivacy(!document.body.classList.contains("privacy-on"));
    });
  }
});

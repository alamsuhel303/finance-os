(function () {
  /* —— Allocation chart —— */
  const allocation = window.INVEST_DATA || [];
  const el = document.getElementById("allocChart");
  if (el && allocation.length) {
    Chart.defaults.color = "#94a3b8";
    Chart.defaults.borderColor = "rgba(255,255,255,0.06)";
    Chart.defaults.font.family = "'DM Sans', system-ui, sans-serif";

    const colors = [
      "#34d399",
      "#38bdf8",
      "#a78bfa",
      "#fbbf24",
      "#fb7185",
      "#2dd4bf",
      "#60a5fa",
      "#f472b6",
      "#94a3b8",
    ];

    new Chart(el, {
      type: "doughnut",
      data: {
        labels: allocation.map((a) => a.label),
        datasets: [
          {
            data: allocation.map((a) => a.current),
            backgroundColor: allocation.map((_, i) => colors[i % colors.length]),
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
            labels: {
              boxWidth: 10,
              boxHeight: 10,
              usePointStyle: true,
              padding: 12,
            },
          },
        },
      },
    });
  }

  /* —— Holdings type filter (no page reload) —— */
  const cfg = window.HOLDINGS_FILTER || {};
  const labels = cfg.labels || {};
  const defaultSubtitle = cfg.defaultSubtitle || "";
  const table = document.getElementById("holdingsTable");
  const tableWrap = table?.closest(".table-wrap");
  const emptyState = document.getElementById("holdingsFilterEmpty");
  const subtitle = document.getElementById("holdingsSubtitle");
  const chips = document.querySelectorAll(".holding-filter-chip");
  const typeRows = document.querySelectorAll(".holding-type-row");

  if (!table) return;

  function setFilter(type) {
    const want = (type || "").trim().toLowerCase();
    let visible = 0;

    table.querySelectorAll("tbody tr[data-asset-type]").forEach((row) => {
      const match = !want || row.dataset.assetType === want;
      row.classList.toggle("hidden", !match);
      if (match) visible += 1;
    });

    chips.forEach((chip) => {
      const chipType = (chip.dataset.filterType || "").trim().toLowerCase();
      chip.classList.toggle("is-active", chipType === want);
    });

    typeRows.forEach((row) => {
      const rowType = (row.dataset.filterType || "").trim().toLowerCase();
      row.classList.toggle("is-active-filter", Boolean(want) && rowType === want);
    });

    if (tableWrap) tableWrap.classList.toggle("hidden", visible === 0);
    if (emptyState) emptyState.classList.toggle("hidden", visible > 0);

    if (subtitle) {
      if (!want) {
        subtitle.innerHTML = defaultSubtitle;
      } else {
        const label = labels[want] || want;
        const plural = visible === 1 ? "" : "s";
        subtitle.innerHTML =
          `Showing ${visible} ${label} holding${plural}` +
          ` · <button type="button" class="btn btn-link p-0 align-baseline" data-filter-type="">Clear filter</button>`;
      }
    }

    if (want) {
      document.getElementById("holdings")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }

  document.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-filter-type]");
    if (!btn) return;
    // Ignore real navigation links that also happen to have the attr
    if (btn.tagName === "A" && btn.getAttribute("href")) return;
    event.preventDefault();
    setFilter(btn.dataset.filterType || "");
  });
})();

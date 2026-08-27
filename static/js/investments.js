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

  /* —— Holdings type + live search filters —— */
  const cfg = window.HOLDINGS_FILTER || {};
  const typeLabels = cfg.labels || {};
  const defaultSubtitle = cfg.defaultSubtitle || "";
  const table = document.getElementById("holdingsTable");
  const tableWrap = table?.closest(".table-wrap");
  const emptyState = document.getElementById("holdingsFilterEmpty");
  const subtitle = document.getElementById("holdingsSubtitle");
  const typeRows = document.querySelectorAll(".holding-type-row");
  const searchInput = document.getElementById("holdingsSearch");

  if (!table) return;

  let activeType = "";
  let searchQuery = "";

  function applyFilters({ scroll } = {}) {
    const wantType = (activeType || "").trim().toLowerCase();
    const q = (searchQuery || "").trim().toLowerCase();
    let visible = 0;

    table.querySelectorAll("tbody tr[data-asset-type]").forEach((row) => {
      const typeOk = !wantType || row.dataset.assetType === wantType;
      const hay = (row.dataset.search || row.textContent || "").toLowerCase();
      const searchOk = !q || hay.includes(q);
      const match = typeOk && searchOk;
      row.classList.toggle("hidden", !match);
      if (match) visible += 1;
    });

    document.querySelectorAll(".holding-filter-chip[data-filter-type]").forEach((chip) => {
      const chipType = (chip.dataset.filterType || "").trim().toLowerCase();
      chip.classList.toggle("is-active", chipType === wantType);
    });

    typeRows.forEach((row) => {
      const rowType = (row.dataset.filterType || "").trim().toLowerCase();
      row.classList.toggle("is-active-filter", Boolean(wantType) && rowType === wantType);
    });

    if (tableWrap) tableWrap.classList.toggle("hidden", visible === 0);
    if (emptyState) emptyState.classList.toggle("hidden", visible > 0);

    if (subtitle) {
      if (!wantType && !q) {
        subtitle.innerHTML = defaultSubtitle;
      } else {
        const parts = [];
        if (wantType) parts.push(typeLabels[wantType] || wantType);
        if (q) parts.push(`“${q}”`);
        const plural = visible === 1 ? "" : "s";
        subtitle.innerHTML =
          `Showing ${visible} holding${plural}` +
          (parts.length ? ` · ${parts.join(" · ")}` : "") +
          ` · <button type="button" class="btn btn-link p-0 align-baseline" data-clear-filters="1">Clear filters</button>`;
      }
    }

    if (scroll && wantType) {
      document.getElementById("holdings")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }

  if (searchInput) {
    searchInput.addEventListener("input", () => {
      searchQuery = searchInput.value || "";
      applyFilters();
    });
  }

  document.addEventListener("click", (event) => {
    const clear = event.target.closest("[data-clear-filters]");
    if (clear) {
      event.preventDefault();
      activeType = "";
      searchQuery = "";
      if (searchInput) searchInput.value = "";
      applyFilters();
      return;
    }

    const typeBtn = event.target.closest("[data-filter-type]");
    if (!typeBtn) return;
    if (typeBtn.tagName === "A" && typeBtn.getAttribute("href")) return;
    event.preventDefault();
    activeType = typeBtn.dataset.filterType || "";
    applyFilters({ scroll: true });
  });
})();

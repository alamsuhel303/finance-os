(() => {
  const typeSelect = document.getElementById("asset_type");
  const subtitle = document.getElementById("formSubtitle");

  const SUBTITLES = {
    epf: "EPF balance and monthly salary contribution",
    mutual_fund: "Cost, units, NAV, and SIP schedule",
    sip: "Cost, units, NAV, and SIP schedule",
    nps: "NPS balance and optional monthly contribution",
    fd: "Fixed deposit value and optional schedule",
    stock: "Holdings value",
    rsu: "RSU value",
    gold: "Gold holding value",
    other: "Investment value and optional schedule",
  };

  function typeList(attr) {
    return (attr || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function matchesType(attr, type) {
    const list = typeList(attr);
    return list.length === 0 || list.includes(type);
  }

  function syncFormByType() {
    if (!typeSelect) return;
    const type = typeSelect.value;
    if (subtitle) {
      subtitle.textContent = SUBTITLES[type] || "Track holdings and monthly contributions";
    }

    document.querySelectorAll(".form-section[data-show]").forEach((el) => {
      const show = matchesType(el.dataset.show, type);
      el.classList.toggle("d-none", !show);
      // Disabled fields are omitted from POST — backend treats missing units/scheme/source as empty
      el.querySelectorAll("input, select, textarea").forEach((field) => {
        field.disabled = !show;
      });
    });

    document.querySelectorAll(".type-hint[data-for]").forEach((el) => {
      el.classList.toggle("d-none", !matchesType(el.dataset.for, type));
    });
  }

  if (typeSelect) {
    typeSelect.addEventListener("change", syncFormByType);
    syncFormByType();
  }

  // —— Scheme search (MF / SIP) ——
  const searchInput = document.getElementById("scheme_search");
  const resultsEl = document.getElementById("schemeSearchResults");
  const codeInput = document.getElementById("scheme_code");
  const nameInput = document.getElementById("name");
  if (!searchInput || !resultsEl || !codeInput) {
    return;
  }

  const searchUrl = window.SCHEME_SEARCH_URL;
  let timer = null;

  function hideResults() {
    resultsEl.hidden = true;
    resultsEl.replaceChildren();
  }

  function pick(scheme) {
    codeInput.value = scheme.scheme_code;
    if (nameInput && (!nameInput.value || nameInput.value.trim().length < 3)) {
      nameInput.value = scheme.scheme_name;
    }
    searchInput.value = scheme.scheme_name;
    hideResults();
  }

  function renderResults(rows) {
    resultsEl.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "scheme-search-empty";
      empty.textContent = "No funds found";
      resultsEl.appendChild(empty);
      resultsEl.hidden = false;
      return;
    }
    rows.forEach((row) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "scheme-search-item";
      const name = document.createElement("span");
      name.className = "scheme-search-name";
      name.textContent = row.scheme_name;
      const code = document.createElement("span");
      code.className = "scheme-search-code";
      code.textContent = row.scheme_code;
      btn.append(name, code);
      btn.addEventListener("click", () => pick(row));
      resultsEl.appendChild(btn);
    });
    resultsEl.hidden = false;
  }

  async function runSearch(q) {
    if (!q || q.length < 2) {
      hideResults();
      return;
    }
    try {
      const resp = await fetch(`${searchUrl}?q=${encodeURIComponent(q)}`);
      const data = await resp.json();
      if (!resp.ok || data.error) {
        resultsEl.replaceChildren();
        const empty = document.createElement("div");
        empty.className = "scheme-search-empty";
        empty.textContent = data.error || "Search failed";
        resultsEl.appendChild(empty);
        resultsEl.hidden = false;
        return;
      }
      renderResults(Array.isArray(data) ? data : []);
    } catch (err) {
      resultsEl.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "scheme-search-empty";
      empty.textContent = "Network error";
      resultsEl.appendChild(empty);
      resultsEl.hidden = false;
    }
  }

  searchInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => runSearch(searchInput.value.trim()), 280);
  });

  document.addEventListener("click", (e) => {
    if (!resultsEl.contains(e.target) && e.target !== searchInput) {
      hideResults();
    }
  });
})();

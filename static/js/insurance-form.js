/** Insurance form — show type-specific fields and clarify due-date labels. */
(function () {
  const typeSelect = document.getElementById("policy_type");
  const freqSelect = document.getElementById("premium_frequency");
  const termFields = document.getElementById("termFields");
  const healthFields = document.getElementById("healthFields");
  const nextDueLabel = document.getElementById("nextDueLabel");
  const nextDueHint = document.getElementById("nextDueHint");
  const frequencyHint = document.getElementById("frequencyHint");
  const nextDueInput = document.getElementById("next_renewal_date");
  const coverageEnd = document.getElementById("coverage_end_date");
  const pptInput = document.getElementById("premium_paying_term_years");

  if (!typeSelect) return;

  function syncTypeFields() {
    const type = typeSelect.value;
    const isTerm = type === "term" || type === "life";
    const isHealth = type === "health";

    if (termFields) {
      termFields.classList.toggle("hidden", !isTerm);
      termFields.querySelectorAll("input").forEach((el) => {
        el.disabled = !isTerm;
      });
    }
    if (healthFields) {
      healthFields.classList.toggle("hidden", !isHealth);
      healthFields.querySelectorAll("input").forEach((el) => {
        el.disabled = !isHealth;
      });
    }
    if (pptInput) {
      pptInput.required = type === "term";
    }
    syncFrequencyCopy();
  }

  function syncFrequencyCopy() {
    const type = typeSelect.value;
    const freq = freqSelect ? freqSelect.value : "yearly";

    if (freq === "monthly") {
      if (nextDueLabel) nextDueLabel.textContent = "Next premium due";
      if (nextDueHint) nextDueHint.textContent = "Date of the next monthly premium.";
      if (frequencyHint) {
        frequencyHint.textContent = "Monthly premium — annual cost = premium × 12.";
      }
    } else if (freq === "quarterly") {
      if (nextDueLabel) nextDueLabel.textContent = "Next premium due";
      if (nextDueHint) nextDueHint.textContent = "Date of the next quarterly premium.";
      if (frequencyHint) {
        frequencyHint.textContent = "Quarterly premium — annual cost = premium × 4.";
      }
    } else if (freq === "one_time") {
      if (nextDueLabel) nextDueLabel.textContent = "Next renewal / repayment";
      if (nextDueHint) {
        nextDueHint.textContent =
          type === "health"
            ? "Usually the coverage end date, when you renew the multi-year policy."
            : "When you need to pay again (if ever).";
      }
      if (frequencyHint) {
        frequencyHint.textContent =
          "One lump-sum payment (e.g. 3-year health prepaid). Annual cost is spread over the cover years.";
      }
    } else {
      if (nextDueLabel) nextDueLabel.textContent = "Next renewal";
      if (nextDueHint) nextDueHint.textContent = "When the next yearly premium / renewal is due.";
      if (frequencyHint) {
        frequencyHint.textContent = "Yearly premium renewal.";
      }
    }
  }

  function maybeFillNextDueFromCoverageEnd() {
    if (!coverageEnd || !nextDueInput) return;
    if (typeSelect.value !== "health") return;
    if (nextDueInput.value) return;
    if (coverageEnd.value) {
      nextDueInput.value = coverageEnd.value;
    }
  }

  typeSelect.addEventListener("change", syncTypeFields);
  if (freqSelect) freqSelect.addEventListener("change", syncFrequencyCopy);
  if (coverageEnd) {
    coverageEnd.addEventListener("change", maybeFillNextDueFromCoverageEnd);
  }

  syncTypeFields();
})();

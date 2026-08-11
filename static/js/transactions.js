/* Transaction form — type-aware fields, transfer defaults, balance hints, amount in words */

document.addEventListener("DOMContentLoaded", () => {
  const defaults = window.TXN_DEFAULTS || {};
  const typeSelect = document.getElementById("transaction_type");
  const accountSelect = document.getElementById("account_id");
  const toAccountSelect = document.getElementById("to_account_id");
  const accountLabel = document.getElementById("accountLabel");
  const transferFields = document.querySelectorAll(".transfer-fields");
  const expenseFields = document.getElementById("expenseFields");
  const categoryFields = document.querySelectorAll(".category-fields");
  const needWantFields = document.getElementById("needWantFields");
  const paidByFields = document.getElementById("paidByFields");
  const paymentModeFields = document.getElementById("paymentModeFields");
  const categorySelect = document.getElementById("category_id");
  const envelopeSelect = document.getElementById("envelope_id");
  const envelopeMismatchHint = document.getElementById("envelopeMismatchHint");
  const paidBySelect = document.getElementById("paid_by");
  const amountInput = document.getElementById("amount");
  const amountWords = document.getElementById("amountWords");
  const balanceHint = document.getElementById("balanceHint");
  const dateInput = document.getElementById("date");
  const splitRows = document.getElementById("splitRows");
  const addSplitBtn = document.getElementById("addSplitRow");
  const splitTemplate = document.getElementById("splitRowTemplate");
  const splitSummary = document.getElementById("splitSummary");

  // Track whether user manually picked an account (don't overwrite on type flip)
  let accountTouched = Boolean(defaults.isEdit || accountSelect?.value);
  // After category sync, user may intentionally pick another pot
  let envelopeManual = Boolean(
    defaults.isEdit && envelopeSelect?.value
  );

  if (dateInput && !dateInput.value) {
    dateInput.value = new Date().toISOString().slice(0, 10);
  }

  /* ── Amount in words (shared helper) ── */
  function syncAmountWords() {
    if (!amountWords) return;
    amountWords.textContent = window.fosAmountToWords
      ? window.fosAmountToWords(amountInput?.value)
      : "";
  }

  function selectedAccountOption() {
    if (!accountSelect) return null;
    return accountSelect.options[accountSelect.selectedIndex] || null;
  }

  function syncPaidByVisibility() {
    const type = typeSelect?.value || "expense";
    const opt = selectedAccountOption();
    const owner = (opt?.dataset.owner || "").toLowerCase();
    // Only ask "paid by" for expenses from joint-owned accounts
    const show =
      type === "expense" && owner === "joint" && Boolean(accountSelect?.value);
    if (paidByFields) paidByFields.classList.toggle("hidden", !show);
    if (paidBySelect && opt?.dataset.owner) {
      if (owner === "self" || owner === "wife") {
        paidBySelect.value = owner;
      }
    }
  }

  function syncBalanceHint() {
    if (!balanceHint || !amountInput) return;
    const type = typeSelect?.value || "expense";
    const opt = selectedAccountOption();
    if (!opt || !opt.value || !["expense", "transfer", "investment"].includes(type)) {
      balanceHint.textContent = "";
      balanceHint.style.color = "";
      return;
    }
    const bal = parseFloat(opt.dataset.balance || "0") || 0;
    const amt = parseFloat(amountInput.value || "0") || 0;
    const name = opt.dataset.name || "account";
    if (amt > 0 && amt > bal) {
      balanceHint.textContent = `Insufficient — ${name} has ${bal.toLocaleString("en-IN")}`;
      balanceHint.style.color = "var(--danger)";
    } else {
      balanceHint.textContent = `Available in ${name}: ${bal.toLocaleString("en-IN")}`;
      balanceHint.style.color = "var(--text-muted)";
    }
  }

  function filterCategoryOptions(txnType) {
    if (!categorySelect) return;
    const want =
      txnType === "income" ? "income" : txnType === "refund" ? "expense" : "expense";
    const current = categorySelect.value;
    [...categorySelect.options].forEach((opt) => {
      if (!opt.value) {
        opt.hidden = false;
        return;
      }
      const catType = (opt.dataset.type || "expense").toLowerCase();
      const match =
        txnType === "income"
          ? catType === "income"
          : catType === "expense" || catType === "refund";
      opt.hidden = !match;
    });
    const selected = categorySelect.options[categorySelect.selectedIndex];
    if (selected && selected.hidden) {
      categorySelect.value = "";
    } else if (current) {
      categorySelect.value = current;
    }
  }

  function applyTransferDefaults() {
    if (defaults.isEdit) return;
    const type = typeSelect?.value;
    if (type !== "transfer") return;
    if (accountSelect && defaults.fromId && !accountTouched) {
      accountSelect.value = String(defaults.fromId);
    }
    if (toAccountSelect && defaults.jointId) {
      // Always prefer Joint as destination for new transfers
      if (!toAccountSelect.value || toAccountSelect.value === accountSelect?.value) {
        toAccountSelect.value = String(defaults.jointId);
      }
    }
    // Avoid same from/to
    if (
      accountSelect?.value &&
      toAccountSelect?.value &&
      accountSelect.value === toAccountSelect.value &&
      defaults.jointId
    ) {
      toAccountSelect.value = String(defaults.jointId);
    }
  }

  function applyExpenseDefaults() {
    if (defaults.isEdit) return;
    if (typeSelect?.value !== "expense") return;
    // Expenses default to Joint (household spend)
    if (accountSelect && defaults.jointId && !accountTouched) {
      accountSelect.value = String(defaults.jointId);
    }
  }

  function categoryDefaultEnvelopeId() {
    if (!categorySelect) return "";
    const opt = categorySelect.options[categorySelect.selectedIndex];
    return (opt?.dataset.envelope || "").trim();
  }

  function envelopeOptionLabel(envId) {
    if (!envelopeSelect || !envId) return "";
    const opt = [...envelopeSelect.options].find(
      (o) => String(o.value) === String(envId)
    );
    if (!opt) return "";
    // Strip trailing balance "(1,234.00)"
    return (opt.textContent || "").replace(/\s*\([^)]*\)\s*$/, "").trim();
  }

  function syncEnvelopeFromCategory({ force = false } = {}) {
    if (!envelopeSelect || typeSelect?.value !== "expense") return;
    const defaultEnv = categoryDefaultEnvelopeId();
    if (force || !envelopeManual) {
      envelopeSelect.value = defaultEnv || "";
      envelopeManual = false;
    }
    updateEnvelopeMismatchHint();
  }

  function updateEnvelopeMismatchHint() {
    if (!envelopeMismatchHint) return;
    if (typeSelect?.value !== "expense") {
      envelopeMismatchHint.textContent = "";
      return;
    }
    const defaultEnv = categoryDefaultEnvelopeId();
    const chosen = (envelopeSelect?.value || "").trim();
    if (!defaultEnv || !chosen || String(defaultEnv) === String(chosen)) {
      envelopeMismatchHint.textContent = "";
      return;
    }
    const catOpt = categorySelect?.options[categorySelect.selectedIndex];
    const catName = (catOpt?.textContent || "This category")
      .split("·")[0]
      .trim();
    const defaultName = envelopeOptionLabel(defaultEnv) || "its default pot";
    const chosenName = envelopeOptionLabel(chosen) || "another pot";
    envelopeMismatchHint.textContent =
      `“${catName}” normally uses ${defaultName}, but you selected ${chosenName}. ` +
      `Budget follows the category; the pot follows ${chosenName}.`;
  }

  function syncTypeVisibility() {
    const type = typeSelect ? typeSelect.value : "expense";
    const isTransfer = type === "transfer";
    const isExpense = type === "expense";
    const isIncome = type === "income";
    const isInvestment = type === "investment";
    const showCategory = type === "expense" || type === "refund" || type === "income";
    const showNeedWant = type === "expense" || type === "refund";

    transferFields.forEach((el) => el.classList.toggle("hidden", !isTransfer));
    if (expenseFields) expenseFields.classList.toggle("hidden", !isExpense);
    categoryFields.forEach((el) => el.classList.toggle("hidden", !showCategory));
    if (needWantFields) needWantFields.classList.toggle("hidden", !showNeedWant);
    if (paymentModeFields) paymentModeFields.classList.toggle("hidden", isIncome);
    document.querySelectorAll(".investment-fields").forEach((el) => {
      el.classList.toggle("hidden", !isInvestment);
    });

    const investmentSelect = document.getElementById("investment_id");
    if (investmentSelect) {
      investmentSelect.disabled = !isInvestment;
      if (!isInvestment) investmentSelect.value = "";
    }

    if (accountLabel) {
      accountLabel.textContent = isTransfer
        ? "From Account"
        : isIncome
          ? "Credit Account"
          : isInvestment
            ? "Debit from"
            : "Account";
    }

    if (categorySelect) {
      categorySelect.disabled = !showCategory;
      if (!showCategory) categorySelect.value = "";
      else filterCategoryOptions(type);
    }

    if (isTransfer) {
      applyTransferDefaults();
      ensureDefaultEssentialsSplit();
    }
    if (isExpense) {
      applyExpenseDefaults();
      syncEnvelopeFromCategory({ force: !defaults.isEdit });
    } else if (envelopeMismatchHint) {
      envelopeMismatchHint.textContent = "";
    }
    if (isInvestment) applyInvestmentDefaults();
    syncPaidByVisibility();
    syncBalanceHint();
    syncAmountWords();
    updateSplitSummary();
  }

  function applyInvestmentDefaults() {
    if (defaults.isEdit) return;
    if (typeSelect?.value !== "investment") return;
    // Default debit from self bank (Suhel / My Account)
    if (accountSelect && defaults.fromId && !accountTouched) {
      accountSelect.value = String(defaults.fromId);
    }
    const mode = document.getElementById("payment_mode");
    if (mode && !defaults.isEdit) {
      const hasAuto = [...mode.options].some((o) => o.value === "auto_debit");
      if (hasAuto) mode.value = "auto_debit";
    }
  }

  function isTransferIntoJoint() {
    return (
      typeSelect?.value === "transfer" &&
      defaults.jointId &&
      String(toAccountSelect?.value || "") === String(defaults.jointId)
    );
  }

  function ensureDefaultEssentialsSplit() {
    if (defaults.isEdit || !splitRows || !splitTemplate) return;
    if (!isTransferIntoJoint()) return;
    if (splitRows.children.length > 0) return;

    const node = splitTemplate.content.cloneNode(true);
    splitRows.appendChild(node);
    const row = splitRows.lastElementChild;
    const envSelect = row?.querySelector('select[name="split_envelope_id"]');
    const amtInput = row?.querySelector('input[name="split_amount"]');
    if (envSelect && defaults.essentialsId) {
      envSelect.value = String(defaults.essentialsId);
    }
    if (amtInput && amountInput?.value) {
      amtInput.value = amountInput.value;
    }
    bindSplitRowEvents();
    updateSplitSummary();
  }

  function addSplitRow() {
    if (!splitRows || !splitTemplate) return;
    const node = splitTemplate.content.cloneNode(true);
    splitRows.appendChild(node);
    const row = splitRows.lastElementChild;
    // First row on Joint transfer → Essentials + full amount
    if (
      splitRows.children.length === 1 &&
      isTransferIntoJoint() &&
      defaults.essentialsId
    ) {
      const envSelect = row?.querySelector('select[name="split_envelope_id"]');
      const amtInput = row?.querySelector('input[name="split_amount"]');
      if (envSelect) envSelect.value = String(defaults.essentialsId);
      if (amtInput && amountInput?.value) amtInput.value = amountInput.value;
    }
    bindSplitRowEvents();
    updateSplitSummary();
  }

  function bindSplitRowEvents() {
    if (!splitRows) return;
    splitRows.querySelectorAll(".remove-split").forEach((btn) => {
      btn.onclick = () => {
        btn.closest(".split-row")?.remove();
        updateSplitSummary();
      };
    });
    splitRows.querySelectorAll("input, select").forEach((el) => {
      el.removeEventListener("input", updateSplitSummary);
      el.removeEventListener("change", updateSplitSummary);
      el.addEventListener("input", updateSplitSummary);
      el.addEventListener("change", updateSplitSummary);
    });
  }

  function updateSplitSummary() {
    if (!splitSummary || !splitRows) return;
    let sum = 0;
    splitRows.querySelectorAll('input[name="split_amount"]').forEach((input) => {
      const v = parseFloat(input.value);
      if (!Number.isNaN(v)) sum += v;
    });
    const total = parseFloat(amountInput?.value || "0") || 0;
    if (sum === 0 && splitRows.children.length === 0) {
      splitSummary.textContent = "";
      return;
    }
    const diff = total - sum;
    const ok = Math.abs(diff) < 0.005 && sum > 0;
    splitSummary.textContent = ok
      ? `Split totals ${sum.toLocaleString("en-IN")} — matches transfer ✓`
      : `Split totals ${sum.toLocaleString("en-IN")} · transfer ${total.toLocaleString("en-IN")} · diff ${diff.toLocaleString("en-IN")}`;
    splitSummary.style.color = ok ? "var(--success)" : "var(--warning)";
  }

  if (typeSelect) {
    typeSelect.addEventListener("change", () => {
      // Changing type resets account default unless user already chose one
      if (!defaults.isEdit) accountTouched = false;
      syncTypeVisibility();
    });
  }
  if (accountSelect) {
    accountSelect.addEventListener("change", () => {
      accountTouched = true;
      syncPaidByVisibility();
      syncBalanceHint();
    });
  }
  if (amountInput) {
    amountInput.addEventListener("input", () => {
      syncBalanceHint();
      syncAmountWords();
      // Keep single Essentials row in sync with transfer amount
      if (
        isTransferIntoJoint() &&
        splitRows?.children.length === 1
      ) {
        const envSelect = splitRows.querySelector('select[name="split_envelope_id"]');
        const amtInput = splitRows.querySelector('input[name="split_amount"]');
        if (
          envSelect &&
          String(envSelect.value) === String(defaults.essentialsId) &&
          amtInput
        ) {
          amtInput.value = amountInput.value || "";
        }
      }
      updateSplitSummary();
    });
  }
  if (toAccountSelect) {
    toAccountSelect.addEventListener("change", () => {
      if (typeSelect?.value === "transfer") ensureDefaultEssentialsSplit();
      updateSplitSummary();
    });
  }
  if (addSplitBtn) addSplitBtn.addEventListener("click", addSplitRow);

  if (categorySelect) {
    categorySelect.addEventListener("change", () => {
      // Category change always resets pot to the mapped default (haircut fix)
      syncEnvelopeFromCategory({ force: true });
    });
  }
  if (envelopeSelect) {
    envelopeSelect.addEventListener("change", () => {
      envelopeManual = Boolean(envelopeSelect.value);
      updateEnvelopeMismatchHint();
    });
  }

  syncTypeVisibility();
  if (defaults.isEdit) updateEnvelopeMismatchHint();
  bindSplitRowEvents();
  updateSplitSummary();
  syncAmountWords();
});

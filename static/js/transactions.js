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
    const type = typeSelect?.value || "expense";
    if (type !== "expense" && type !== "refund") return;
    // Expenses/refunds default to Joint (household cash)
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
    const type = typeSelect?.value || "expense";
    if (!envelopeSelect || (type !== "expense" && type !== "refund")) return;
    const defaultEnv = categoryDefaultEnvelopeId();
    if (force || !envelopeManual) {
      envelopeSelect.value = defaultEnv || "";
      envelopeManual = false;
    }
    updateEnvelopeMismatchHint();
  }

  function updateEnvelopeMismatchHint() {
    if (!envelopeMismatchHint) return;
    const type = typeSelect?.value || "expense";
    if (type !== "expense" && type !== "refund") {
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
    const usesEnvelope = type === "expense" || type === "refund";
    const isIncome = type === "income";
    const isInvestment = type === "investment";
    const showCategory = type === "expense" || type === "refund" || type === "income";
    const showNeedWant = type === "expense" || type === "refund";

    transferFields.forEach((el) => el.classList.toggle("hidden", !isTransfer));
    if (expenseFields) expenseFields.classList.toggle("hidden", !usesEnvelope);
    document.querySelectorAll(".expense-only-fields").forEach((el) => {
      el.classList.toggle("hidden", !isExpense);
    });
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
            : type === "refund"
              ? "Credit Account"
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
    if (usesEnvelope) {
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
    syncFriendSplitVisibility();
    updateFriendSplitSummary();
  }

  function applyInvestmentDefaults() {
    if (defaults.isEdit) return;
    if (typeSelect?.value !== "investment") return;
    // Default debit from self bank (primary account)
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
      if (
        householdPartsInput?.value &&
        friendSplitRows?.querySelector('input[name="split_friend_parts"]')?.value
      ) {
        applyPartsFriendSplit({ quiet: true });
      }
      updateFriendSplitSummary();
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

  /* —— Friend expense splits —— */
  const splitEnabled = document.getElementById("split_enabled");
  const friendSplitFields = document.getElementById("friendSplitFields");
  const friendSplitRows = document.getElementById("friendSplitRows");
  const friendSplitTemplate = document.getElementById("friendSplitRowTemplate");
  const friendSplitSummary = document.getElementById("friendSplitSummary");
  const addFriendSplitBtn = document.getElementById("addFriendSplitRow");
  const equalSplitBtn = document.getElementById("equalSplitBtn");
  const applyPartsBtn = document.getElementById("applyPartsBtn");
  const householdShareInput = document.getElementById("split_household_share");
  const householdPartsInput = document.getElementById("split_household_parts");

  function syncFriendSplitVisibility() {
    const isExpense = (typeSelect?.value || "expense") === "expense";
    const on = Boolean(splitEnabled?.checked) && isExpense;
    if (friendSplitFields) friendSplitFields.classList.toggle("hidden", !on);
  }

  function addFriendSplitRow() {
    if (!friendSplitRows || !friendSplitTemplate) return;
    const node = friendSplitTemplate.content.cloneNode(true);
    friendSplitRows.appendChild(node);
    bindFriendSplitRowEvents();
    updateFriendSplitSummary();
  }

  function bindFriendSplitRowEvents() {
    if (!friendSplitRows) return;
    friendSplitRows.querySelectorAll(".remove-friend-split").forEach((btn) => {
      btn.onclick = () => {
        btn.closest(".friend-split-row")?.remove();
        updateFriendSplitSummary();
      };
    });
    friendSplitRows.querySelectorAll("input, select").forEach((el) => {
      el.oninput = () => {
        if (el.name === "split_friend_parts") applyPartsFriendSplit({ quiet: true });
        updateFriendSplitSummary();
      };
      el.onchange = updateFriendSplitSummary;
    });
  }

  function updateFriendSplitSummary() {
    if (!friendSplitSummary) return;
    if (!splitEnabled?.checked || (typeSelect?.value || "") !== "expense") {
      friendSplitSummary.textContent = "";
      return;
    }
    let friendSum = 0;
    friendSplitRows?.querySelectorAll('input[name="split_friend_amount"]').forEach((input) => {
      const v = parseFloat(input.value);
      if (!Number.isNaN(v)) friendSum += v;
    });
    const hh = parseFloat(householdShareInput?.value || "0") || 0;
    const total = parseFloat(amountInput?.value || "0") || 0;
    const sum = hh + friendSum;
    const diff = total - sum;
    const ok = Math.abs(diff) < 0.005 && total > 0;
    friendSplitSummary.textContent = ok
      ? `Shares total ${sum.toLocaleString("en-IN")} — matches bill ✓`
      : `Shares ${sum.toLocaleString("en-IN")} · bill ${total.toLocaleString("en-IN")} · diff ${diff.toLocaleString("en-IN")}`;
    friendSplitSummary.style.color = ok ? "var(--success)" : "var(--warning)";
  }

  function divideByWeights(total, weights) {
    const sumW = weights.reduce((a, b) => a + b, 0);
    if (sumW <= 0) return null;
    const amounts = weights.slice(0, -1).map((w) => Math.round((total * w) / sumW * 100) / 100);
    const used = amounts.reduce((a, b) => a + b, 0);
    amounts.push(Math.round((total - used) * 100) / 100);
    return amounts;
  }

  function applyEqualFriendSplit() {
    const total = parseFloat(amountInput?.value || "0") || 0;
    if (total <= 0) return;
    if (!friendSplitRows) return;
    if (friendSplitRows.children.length === 0) addFriendSplitRow();
    const nFriends = friendSplitRows.children.length;
    const weights = Array(nFriends + 1).fill(1);
    const amounts = divideByWeights(total, weights);
    if (!amounts) return;
    if (householdShareInput) householdShareInput.value = String(amounts[0]);
    if (householdPartsInput) householdPartsInput.value = "1";
    [...friendSplitRows.querySelectorAll(".friend-split-row")].forEach((row, i) => {
      const amt = row.querySelector('input[name="split_friend_amount"]');
      const parts = row.querySelector('input[name="split_friend_parts"]');
      if (amt) amt.value = String(amounts[i + 1] ?? amounts[amounts.length - 1]);
      if (parts) parts.value = "1";
    });
    updateFriendSplitSummary();
  }

  function applyPartsFriendSplit({ quiet = false } = {}) {
    const total = parseFloat(amountInput?.value || "0") || 0;
    if (total <= 0) {
      if (!quiet && friendSplitSummary) {
        friendSplitSummary.textContent = "Enter the bill amount first.";
        friendSplitSummary.style.color = "var(--warning)";
      }
      return;
    }
    if (!friendSplitRows) return;
    if (friendSplitRows.children.length === 0) {
      if (!quiet) addFriendSplitRow();
      else return;
    }
    const hhParts = parseFloat(householdPartsInput?.value || "");
    const friendPartInputs = [
      ...friendSplitRows.querySelectorAll('input[name="split_friend_parts"]'),
    ];
    const friendWeights = friendPartInputs.map((input) => parseFloat(input.value || ""));
    const weights = [hhParts, ...friendWeights];
    if (weights.some((w) => Number.isNaN(w) || w <= 0)) {
      if (!quiet && friendSplitSummary) {
        friendSplitSummary.textContent =
          "Enter parts for you and each friend (e.g. 2, 1, 1).";
        friendSplitSummary.style.color = "var(--warning)";
      }
      return;
    }
    const amounts = divideByWeights(total, weights);
    if (!amounts) return;
    if (householdShareInput) householdShareInput.value = String(amounts[0]);
    [...friendSplitRows.querySelectorAll('input[name="split_friend_amount"]')].forEach(
      (input, i) => {
        input.value = String(amounts[i + 1] ?? amounts[amounts.length - 1]);
      }
    );
    updateFriendSplitSummary();
  }

  if (splitEnabled) {
    splitEnabled.addEventListener("change", () => {
      syncFriendSplitVisibility();
      if (splitEnabled.checked && friendSplitRows && friendSplitRows.children.length === 0) {
        addFriendSplitRow();
      }
      updateFriendSplitSummary();
    });
  }
  if (addFriendSplitBtn) addFriendSplitBtn.addEventListener("click", addFriendSplitRow);
  if (equalSplitBtn) equalSplitBtn.addEventListener("click", applyEqualFriendSplit);
  if (applyPartsBtn) applyPartsBtn.addEventListener("click", () => applyPartsFriendSplit());
  if (householdShareInput) {
    householdShareInput.addEventListener("input", updateFriendSplitSummary);
  }
  if (householdPartsInput) {
    householdPartsInput.addEventListener("input", () => applyPartsFriendSplit({ quiet: true }));
  }

  syncTypeVisibility();
  if (defaults.isEdit) updateEnvelopeMismatchHint();
  bindSplitRowEvents();
  bindFriendSplitRowEvents();
  updateSplitSummary();
  updateFriendSplitSummary();
  syncAmountWords();
});

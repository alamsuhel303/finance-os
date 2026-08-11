/* Indian amount → words (shared across forms) */

const FOS_ONES = [
  "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
  "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
  "Seventeen", "Eighteen", "Nineteen",
];
const FOS_TENS = [
  "", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety",
];

function fosTwoDigits(n) {
  if (n < 20) return FOS_ONES[n];
  const t = Math.floor(n / 10);
  const o = n % 10;
  return `${FOS_TENS[t]}${o ? ` ${FOS_ONES[o]}` : ""}`.trim();
}

function fosThreeDigits(n) {
  const h = Math.floor(n / 100);
  const rest = n % 100;
  const parts = [];
  if (h) parts.push(`${FOS_ONES[h]} Hundred`);
  if (rest) parts.push(fosTwoDigits(rest));
  return parts.join(" ");
}

function fosIntegerToWords(n) {
  if (n === 0) return "Zero";
  const crore = Math.floor(n / 10000000);
  n %= 10000000;
  const lakh = Math.floor(n / 100000);
  n %= 100000;
  const thousand = Math.floor(n / 1000);
  n %= 1000;
  const parts = [];
  if (crore) parts.push(`${fosThreeDigits(crore)} Crore`);
  if (lakh) parts.push(`${fosTwoDigits(lakh)} Lakh`);
  if (thousand) parts.push(`${fosTwoDigits(thousand)} Thousand`);
  if (n) parts.push(fosThreeDigits(n));
  return parts.join(" ");
}

window.fosNormalizeAmountInput = function fosNormalizeAmountInput(raw) {
  // Strip currency / Indian commas / spaces so "₹1,50,000.50" works
  return String(raw || "")
    .trim()
    .replace(/₹/g, "")
    .replace(/\brs\.?\b/gi, "")
    .replace(/,/g, "")
    .replace(/\s+/g, "");
};

window.fosAmountToWords = function fosAmountToWords(raw) {
  const text = window.fosNormalizeAmountInput(raw);
  if (!text || text === ".") return "";
  const num = Number(text);
  if (!Number.isFinite(num) || num < 0) return "";
  const [intPart, decPart] = text.split(".");
  const whole = parseInt(intPart || "0", 10);
  if (!Number.isFinite(whole)) return "";
  let words = `${fosIntegerToWords(whole)} Rupees`;
  if (decPart != null && decPart.length) {
    const paise = parseInt((decPart + "00").slice(0, 2), 10);
    if (paise > 0) words += ` and ${fosTwoDigits(paise)} Paise`;
  }
  return `${words} Only`;
};

window.fosBindAmountWords = function fosBindAmountWords(inputEl, wordsEl) {
  if (!inputEl || !wordsEl) return;
  const sync = () => {
    wordsEl.textContent = window.fosAmountToWords(inputEl.value);
  };
  inputEl.addEventListener("input", sync);
  sync();
};

/** Auto-bind: <input data-amount-words="wordsElId"> */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-amount-words]").forEach((input) => {
    const wordsEl = document.getElementById(input.getAttribute("data-amount-words"));
    window.fosBindAmountWords(input, wordsEl);
  });
});

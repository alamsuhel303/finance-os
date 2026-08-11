/* Finance OS — shared UI behaviour */

document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.getElementById("sidebar");
  const toggle = document.getElementById("sidebarToggle");

  if (toggle && sidebar) {
    toggle.addEventListener("click", () => {
      sidebar.classList.toggle("open");
    });

    document.addEventListener("click", (event) => {
      if (
        window.innerWidth <= 992 &&
        sidebar.classList.contains("open") &&
        !sidebar.contains(event.target) &&
        !toggle.contains(event.target)
      ) {
        sidebar.classList.remove("open");
      }
    });
  }

  initNavReorder();
});

const NAV_ORDER_KEY = "fos-nav-order";

function initNavReorder() {
  const nav = document.getElementById("navSection");
  if (!nav) return;

  const items = () => [...nav.querySelectorAll("[data-nav-id]")];

  // Restore saved order
  try {
    const saved = JSON.parse(localStorage.getItem(NAV_ORDER_KEY) || "[]");
    if (Array.isArray(saved) && saved.length) {
      const byId = Object.fromEntries(items().map((el) => [el.dataset.navId, el]));
      saved.forEach((id) => {
        if (byId[id]) nav.appendChild(byId[id]);
      });
      // Append any new items not in saved order
      items().forEach((el) => {
        if (!saved.includes(el.dataset.navId)) nav.appendChild(el);
      });
    }
  } catch (_) {
    /* ignore bad localStorage */
  }

  let dragEl = null;

  items().forEach((el) => {
    el.addEventListener("dragstart", (e) => {
      dragEl = el;
      el.classList.add("nav-dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", el.dataset.navId || "");
    });

    el.addEventListener("dragend", () => {
      el.classList.remove("nav-dragging");
      items().forEach((n) => n.classList.remove("nav-drag-over"));
      dragEl = null;
      persistNavOrder(nav);
    });

    el.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      if (!dragEl || dragEl === el) return;
      const rect = el.getBoundingClientRect();
      const before = e.clientY < rect.top + rect.height / 2;
      nav.insertBefore(dragEl, before ? el : el.nextSibling);
      items().forEach((n) => n.classList.toggle("nav-drag-over", n === el));
    });

    el.addEventListener("dragleave", () => {
      el.classList.remove("nav-drag-over");
    });

    el.addEventListener("drop", (e) => {
      e.preventDefault();
      el.classList.remove("nav-drag-over");
    });

    // Prevent accidental navigation when starting a drag from the grip
    const grip = el.querySelector(".nav-grip");
    if (grip) {
      grip.addEventListener("click", (e) => e.preventDefault());
    }
  });
}

function persistNavOrder(nav) {
  const order = [...nav.querySelectorAll("[data-nav-id]")].map((el) => el.dataset.navId);
  localStorage.setItem(NAV_ORDER_KEY, JSON.stringify(order));
}

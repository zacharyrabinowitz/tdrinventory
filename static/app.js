(function () {
  function closeAll() {
    document.querySelectorAll(".dropdown.open").forEach(d => d.classList.remove("open"));
  }

  function setDropdownValue(dd, value, label) {
    const hidden = dd.querySelector("input[type='hidden']");
    const valueSpan = dd.querySelector("[data-dd-value]");

    if (hidden) hidden.value = value;

    if (valueSpan) {
      valueSpan.textContent = label;
      valueSpan.classList.remove("placeholder");
    }

    dd.querySelectorAll(".dropdown-item").forEach(x => x.classList.remove("active"));
    const match = dd.querySelector(`.dropdown-item[data-value="${CSS.escape(value)}"]`);
    if (match) match.classList.add("active");
  }

  // Delegated click handling (works for dynamically added dropdowns)
  document.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-dd-toggle]");
    const item = e.target.closest("[data-dd-item]");
    const dd = e.target.closest(".dropdown");

    // Click outside any dropdown -> close all
    if (!dd) {
      closeAll();
      return;
    }

    // Toggle open/close
    if (toggle) {
      e.preventDefault();
      const isOpen = dd.classList.contains("open");
      closeAll();
      if (!isOpen) dd.classList.add("open");
      return;
    }

    // Select an item
    if (item) {
      const value = item.getAttribute("data-value");
      const label = item.getAttribute("data-label") || item.textContent.trim();
      setDropdownValue(dd, value, label);
      dd.classList.remove("open");
      return;
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAll();
  });

  // Search filter (delegated)
  document.addEventListener("input", (e) => {
    const inp = e.target.closest("[data-dd-search]");
    if (!inp) return;

    const dd = inp.closest(".dropdown");
    const q = (inp.value || "").toLowerCase().trim();

    dd.querySelectorAll(".dropdown-item").forEach(it => {
      const t = (it.getAttribute("data-label") || it.textContent || "").toLowerCase();
      it.style.display = t.includes(q) ? "" : "none";
    });
  });

  // Init any dropdowns that have a hidden input already set
  function initAllDropdowns() {
    document.querySelectorAll(".dropdown").forEach(dd => {
      const hidden = dd.querySelector("input[type='hidden']");
      const valueSpan = dd.querySelector("[data-dd-value]");
      if (!hidden || !valueSpan) return;

      // If the UI is still placeholder but hidden has a value, set display label from matching item
      if (valueSpan.classList.contains("placeholder") && hidden.value) {
        const match = dd.querySelector(`.dropdown-item[data-value="${CSS.escape(hidden.value)}"]`);
        if (match) {
          const label = match.getAttribute("data-label") || match.textContent.trim();
          setDropdownValue(dd, hidden.value, label);
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", initAllDropdowns);

  // If you dynamically add dropdown HTML, call: window.DRDropdownInit()
  window.DRDropdownInit = initAllDropdowns;
})();

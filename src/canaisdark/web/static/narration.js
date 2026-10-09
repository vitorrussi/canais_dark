// Atualiza a estimativa de créditos do card de Narração (teste curto: N cenas | completa).
(function () {
  if (window.__narrBound) return;
  window.__narrBound = true;
  function update(form) {
    const cum = JSON.parse(form.dataset.cum || "[]"), mult = parseFloat(form.dataset.mult || "1");
    const mode = form.querySelector('[name=mode]:checked').value;
    const n = Math.max(1, Math.min(cum.length, parseInt(form.querySelector('[name=test_scenes]').value || "1", 10)));
    const chars = !cum.length ? 0 : mode === "full" ? cum[cum.length - 1] : cum[n - 1];
    const credits = Math.ceil(chars * mult);
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set("est-chars", chars); set("est-credits", credits);
    const bal = parseInt(form.dataset.balance || "", 10);
    const box = document.getElementById("narr-estimate");
    if (box) box.style.borderColor = !isNaN(bal) && credits > bal ? "var(--danger)" : "";
  }
  document.addEventListener("input", (e) => { const f = e.target.closest && e.target.closest("[data-narr-form]"); if (f) update(f); });
  document.addEventListener("change", (e) => { const f = e.target.closest && e.target.closest("[data-narr-form]"); if (f) update(f); });
})();

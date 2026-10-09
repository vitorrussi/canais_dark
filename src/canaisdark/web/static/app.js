// JS global leve: tema, menu, copiar, confirmar, toasts. Usa delegação (funciona com hx-boost).
(function () {
  const root = document.documentElement;
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} } };

  function toast(msg, kind) {
    const box = document.getElementById("toasts"); if (!box) return;
    const t = document.createElement("div"); t.className = "toast " + (kind || "info"); t.textContent = msg;
    box.appendChild(t); setTimeout(() => t.remove(), kind === "error" ? 8000 : 4000);
  }
  window.studioToast = toast;

  function flushFlash() { const f = document.getElementById("flash-data"); if (f) { toast(f.dataset.msg, f.dataset.kind); f.remove(); } }
  document.addEventListener("DOMContentLoaded", flushFlash);
  document.addEventListener("htmx:afterSettle", flushFlash);
  document.addEventListener("htmx:afterSwap", () => document.body.classList.remove("menu-open"));

  // Atalhos de teclado (ignorados ao digitar em campos)
  let gPending = 0;
  document.addEventListener("keydown", (e) => {
    const t = e.target, typing = t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable);
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (gPending && Date.now() - gPending < 1000) {
      const dest = { c: "/", p: "/tasks", k: "/connect" }[e.key];
      gPending = 0; if (dest) { e.preventDefault(); window.location.href = dest; } return;
    }
    gPending = 0;
    if (e.key === "g") gPending = Date.now();
    else if (e.key === "/") { const el = document.querySelector("[data-search]"); if (el) { e.preventDefault(); el.focus(); el.select(); } }
    else if (e.key === "n") { const el = document.querySelector("[data-new]"); if (el) { e.preventDefault(); if (/^(INPUT|TEXTAREA)$/.test(el.tagName)) el.focus(); else el.click(); } }
    else if (e.key === "?") toast("Atalhos: g c (canais) · g p (pendências) · g k (conectar Claude) · / (buscar) · n (novo)", "info");
  });

  document.addEventListener("click", async (e) => {
    const conf = e.target.closest("[data-confirm]");
    if (conf && !window.confirm(conf.dataset.confirm)) { e.preventDefault(); e.stopPropagation(); return; }

    if (e.target.closest("[data-theme-toggle]")) {
      const next = root.dataset.theme === "light" ? "dark" : "light";
      root.dataset.theme = next; store.set("theme", next); return;
    }
    if (e.target.closest("[data-menu-toggle]")) { document.body.classList.toggle("menu-open"); return; }

    const cp = e.target.closest("[data-copy]");
    if (cp) {
      const src = cp.dataset.copy.startsWith("#") ? document.querySelector(cp.dataset.copy) : null;
      const text = src ? (src.value !== undefined && src.tagName !== "PRE" ? src.value : src.innerText) : cp.dataset.copy;
      try {
        if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
        else { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
        toast("Copiado para a área de transferência", "success");
      } catch (err) { toast("Não foi possível copiar", "error"); }
      return;
    }
    const fill = e.target.closest("[data-fill]");
    if (fill) {
      const data = JSON.parse(fill.dataset.fill);
      for (const [name, value] of Object.entries(data)) { const el = document.querySelector('[name="' + name + '"]'); if (el) el.value = value; }
      toast("Preset aplicado — ajuste como quiser", "success");
    }
  }, true);
})();

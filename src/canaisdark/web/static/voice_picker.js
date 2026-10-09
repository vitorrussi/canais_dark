// Teste de amostra de voz (gasta créditos; mostra o custo estimado e pede confirmação).
(function () {
  if (window.__voiceBound) return;
  window.__voiceBound = true;
  const mult = () => parseFloat((document.querySelector('[name=model_id]:checked') || {}).dataset?.mult || "1");
  const cost = () => { const t = document.getElementById("sample-text"); return t ? Math.ceil(t.value.length * mult()) : 0; };
  document.addEventListener("input", (e) => { if (e.target.id === "sample-text") { const el = document.getElementById("sample-cost"); if (el) el.textContent = "~" + cost(); } });
  document.addEventListener("change", (e) => { if (e.target.name === "model_id") { const el = document.getElementById("sample-cost"); if (el) el.textContent = "~" + cost(); } });
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-sample]"); if (!btn) return;
    const root = document.getElementById("voice-root"), text = document.getElementById("sample-text").value.trim();
    if (!text) { window.studioToast("Escreva o texto de teste.", "error"); return; }
    const model = (document.querySelector("[name=model_id]:checked") || {}).value;
    if (!window.confirm("Gerar uma amostra de voz? Isso gasta cerca de " + cost() + " créditos do ElevenLabs.")) return;
    btn.disabled = true; const old = btn.textContent; btn.textContent = "Gerando…";
    const out = btn.closest(".card").querySelector(".sample-out");
    try {
      const r = await fetch("/channels/" + root.dataset.slug + "/voice/sample", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice_id: btn.dataset.voice, model_id: model, text }) });
      if (!r.ok) { const j = await r.json().catch(() => ({})); window.studioToast(j.detail || "Falha ao gerar a amostra", "error"); return; }
      const url = URL.createObjectURL(await r.blob());
      out.innerHTML = ""; const a = document.createElement("audio"); a.controls = true; a.src = url; a.style.width = "100%"; out.append(a); a.play().catch(() => {});
    } catch (err) { window.studioToast("Falha de rede ao gerar a amostra.", "error"); }
    finally { btn.disabled = false; btn.textContent = old; }
  });
})();

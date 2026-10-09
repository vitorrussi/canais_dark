// Storyboard: autosave de prompts/flags, upload por cena (1 arquivo/requisição, com progresso), lote e copiar tudo.
(function () {
  const root = document.getElementById("scenes-root");
  if (!root) return;
  const videoId = root.dataset.video, maxBytes = parseFloat(root.dataset.maxMb) * 1024 * 1024;
  const toast = (m, k) => window.studioToast(m, k);
  const $ = (el, sel) => el.querySelector(sel);

  // ---------- autosave ----------
  async function save(row, patch) {
    const r = await fetch(`/videos/${videoId}/scenes/${row.dataset.sceneId}/update`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { toast(j.detail || "Não foi possível salvar", "error"); return; }
    const full = $(row, "[data-full]"), vert = $(row, "[data-full-vertical]");
    full.dataset.copy = j.full; full.disabled = !j.full; vert.dataset.copy = j.full_vertical; vert.disabled = !j.full;
    vert.hidden = !j.wants_vertical; $(row, '.slot[data-variant="vertical"]').hidden = !j.wants_vertical; $(row, ".video-prompt").hidden = !j.is_key_scene;
    refreshStatus(row);
  }
  root.addEventListener("focusout", (e) => {
    const ta = e.target.closest(".prompt-input"); if (!ta) return;
    if (ta.dataset.saved === ta.value) return;
    ta.dataset.saved = ta.value; save(ta.closest(".scene-row"), { [ta.dataset.field]: ta.value });
  });
  root.addEventListener("focusin", (e) => { const ta = e.target.closest(".prompt-input"); if (ta && ta.dataset.saved === undefined) ta.dataset.saved = ta.value; });
  root.addEventListener("change", (e) => {
    const f = e.target.closest(".flag"); if (f) save(f.closest(".scene-row"), { [f.dataset.field]: f.checked });
  });

  // ---------- slots ----------
  function refreshStatus(row) {
    const main = $(row, '.slot[data-variant="main"] .slot-body img, .slot[data-variant="main"] .slot-body video');
    const vslot = $(row, '.slot[data-variant="vertical"]');
    const vOk = vslot.hidden || $(vslot, ".slot-body img, .slot-body video");
    const done = !!main && !!vOk, b = $(row, "[data-status]");
    b.textContent = done ? "completa" : "pendente"; b.classList.toggle("done", done);
  }
  function setSlot(slot, asset) {
    const body = $(slot, ".slot-body"), info = $(slot, ".slot-info"), btns = $(slot, ".stack-inline");
    const label = slot.dataset.variant === "main" ? "16:9" : "9:16";
    body.innerHTML = "";
    if (asset) {
      const el = document.createElement(asset.thumb_url ? "img" : "video");
      el.src = asset.thumb_url || asset.file_url; if (!asset.thumb_url) { el.muted = true; el.preload = "metadata"; } body.append(el);
    } else body.innerHTML = `<span class="muted">${label}<br><small>soltar aqui</small></span>`;
    info.innerHTML = "";
    if (asset) {
      const s = document.createElement("small"); s.textContent = `${asset.width || "?"}×${asset.height || "?"}${asset.duration ? " · " + asset.duration + "s" : ""}`; info.append(s);
      (asset.warnings || []).forEach((w) => { const x = document.createElement("small"); x.className = "warn-text"; x.textContent = "⚠ " + w; info.append(x); });
    }
    btns.innerHTML = `<button type="button" class="secondary sm" data-pick>${asset ? "Trocar" : "Enviar"} ${label}</button>` + (asset ? `<button type="button" class="ghost sm" data-remove data-asset="${asset.id}">Remover</button>` : "");
    refreshStatus(slot.closest(".scene-row"));
  }
  function upload(file, index, variant, slot) {
    return new Promise((resolve) => {
      if (file.size > maxBytes) { const m = `${file.name}: ${(file.size / 1048576).toFixed(0)} MB passa do limite de ${root.dataset.maxMb} MB.`; toast(m, "error"); resolve({ ok: false, msg: m }); return; }
      const bar = slot ? $(slot, ".progress") : null, fill = bar ? bar.firstElementChild : null;
      if (bar) { bar.hidden = false; fill.style.width = "0%"; }
      const fd = new FormData(); fd.append("file", file); fd.append("variant", variant);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/videos/${videoId}/scenes/${index}/upload`);
      xhr.upload.onprogress = (e) => { if (fill && e.lengthComputable) fill.style.width = Math.round((e.loaded / e.total) * 100) + "%"; };
      xhr.onload = () => {
        if (bar) bar.hidden = true;
        let j = {}; try { j = JSON.parse(xhr.responseText); } catch (e) {}
        if (xhr.status === 200 && j.ok) { if (slot) setSlot(slot, j.asset); (j.asset.warnings || []).forEach((w) => toast(`${file.name}: ${w}`, "info")); resolve({ ok: true, replaced: j.replaced, asset: j.asset }); }
        else { const m = j.detail || (xhr.status === 413 ? "Arquivo grande demais para o servidor." : "Falha no envio"); toast(`${file.name}: ${m}`, "error"); resolve({ ok: false, msg: m }); }
      };
      xhr.onerror = () => { if (bar) bar.hidden = true; toast(`${file.name}: falha de rede`, "error"); resolve({ ok: false, msg: "falha de rede" }); };
      xhr.send(fd);
    });
  }
  const slotFor = (index, variant) => root.querySelector(`.scene-row[data-index="${index}"] .slot[data-variant="${variant}"]`);

  root.addEventListener("click", async (e) => {
    const pick = e.target.closest("[data-pick]"), body = e.target.closest(".slot-body");
    if (pick || body) { const slot = (pick || body).closest(".slot"); $(slot, 'input[type="file"]').click(); return; }
    const rm = e.target.closest("[data-remove]");
    if (rm) {
      if (!window.confirm("Remover este arquivo?")) return;
      const r = await fetch(`/videos/${videoId}/assets/${rm.dataset.asset}/delete`, { method: "POST" });
      if (r.ok) setSlot(rm.closest(".slot"), null); else toast("Não foi possível remover", "error");
    }
  });
  root.addEventListener("change", (e) => {
    if (e.target.matches('.slot input[type="file"]') && e.target.files[0]) {
      const slot = e.target.closest(".slot"), row = slot.closest(".scene-row");
      upload(e.target.files[0], row.dataset.index, slot.dataset.variant, slot); e.target.value = "";
    }
  });
  ["dragover", "dragleave", "drop"].forEach((ev) => root.addEventListener(ev, (e) => {
    const body = e.target.closest && e.target.closest(".slot-body");
    if (!body) return;
    e.preventDefault(); body.classList.toggle("drag", ev === "dragover");
    if (ev === "drop" && e.dataTransfer.files[0]) { const slot = body.closest(".slot"); upload(e.dataTransfer.files[0], slot.closest(".scene-row").dataset.index, slot.dataset.variant, slot); }
  }));

  // ---------- lote ----------
  const batch = document.getElementById("batch-drop"), report = document.getElementById("batch-report");
  async function runBatch(files) {
    files = Array.from(files); if (!files.length) return;
    const variant = document.getElementById("batch-variant").value;
    report.textContent = "Planejando…";
    const r = await fetch(`/videos/${videoId}/assets/batch-plan`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ names: files.map((f) => f.name), variant }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { toast(j.detail || "Falha ao planejar o lote", "error"); report.textContent = ""; return; }
    report.innerHTML = ""; let ok = 0;
    for (let i = 0; i < files.length; i++) {
      const p = j.plan[i], line = document.createElement("div"); report.append(line);
      if (p.scene === null) { line.textContent = `✗ ${files[i].name}: ${p.reason}`; continue; }
      line.textContent = `… ${files[i].name} → cena ${p.scene + 1} (${p.reason})`;
      const res = await upload(files[i], p.scene, variant, slotFor(p.scene, variant));
      line.textContent = res.ok ? `✓ ${files[i].name} → cena ${p.scene + 1}${res.replaced ? " (substituiu)" : ""}` : `✗ ${files[i].name}: ${res.msg}`;
      if (res.ok) ok++;
    }
    toast(`Lote concluído: ${ok}/${files.length} enviado(s).`, ok === files.length ? "success" : "info");
  }
  if (batch) {
    ["dragenter", "dragover"].forEach((ev) => batch.addEventListener(ev, (e) => { e.preventDefault(); batch.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => batch.addEventListener(ev, (e) => { e.preventDefault(); batch.classList.remove("drag"); }));
    batch.addEventListener("drop", (e) => runBatch(e.dataTransfer.files));
    document.getElementById("batch-input").addEventListener("change", (e) => { runBatch(e.target.files); e.target.value = ""; });
  }

  // ---------- copiar todos ----------
  const all = document.getElementById("copy-all");
  if (all) all.addEventListener("click", () => {
    const lines = Array.from(root.querySelectorAll(".scene-row")).map((row) => { const b = $(row, "[data-full]"); return b && b.dataset.copy ? `[Cena ${Number(row.dataset.index) + 1}] ${b.dataset.copy}` : ""; }).filter(Boolean);
    if (!lines.length) { toast("Ainda não há prompts para copiar.", "error"); return; }
    window.studioCopyText(lines.join("\n\n"));
  });
})();

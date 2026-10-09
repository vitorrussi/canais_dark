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

  // ---------- lote (com pré-visualização e ordenação pelo horário do arquivo) ----------
  const batch = document.getElementById("batch-drop"), report = document.getElementById("batch-report");
  const esc = (t) => { const d = document.createElement("div"); d.textContent = t; return d.innerHTML; };
  async function runBatch(files) {
    files = Array.from(files); if (!files.length) return;
    const variant = document.getElementById("batch-variant").value;
    report.textContent = "Planejando…";
    const r = await fetch(`/videos/${videoId}/assets/batch-plan`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names: files.map((f) => f.name), variant, mtimes: files.map((f) => f.lastModified) }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { toast(j.detail || "Falha ao planejar o lote", "error"); report.textContent = ""; return; }
    const n = j.scene_count, urls = [];
    const sceneOptions = (sel) => `<option value="">— não enviar —</option>` + Array.from({ length: n }, (_, i) => `<option value="${i}" ${sel === i ? "selected" : ""}>Cena ${i + 1}</option>`).join("");
    report.innerHTML = `<div class="help"><strong>Confira antes de enviar.</strong> Arquivos sem número no nome foram ordenados pelo horário em que foram criados/baixados. Ajuste a cena de cada um se precisar.</div>
      <div class="batch-grid">${files.map((f, i) => { const p = j.plan[i]; const isImg = f.type.startsWith("image/"); const u = isImg ? URL.createObjectURL(f) : ""; if (u) urls.push(u);
        return `<div class="batch-item" data-i="${i}">${isImg ? `<img src="${u}" alt="">` : `<div class="batch-vid muted">vídeo</div>`}<small class="batch-name">${esc(f.name)}</small>
          <select class="batch-scene">${sceneOptions(p.scene)}</select><small class="muted">${esc(p.reason || "")}</small></div>`; }).join("")}</div>
      <div class="stack-inline" style="margin-top:.6rem"><button type="button" id="batch-go">Enviar tudo</button><button type="button" class="secondary" id="batch-cancel">Cancelar</button><span id="batch-msg" class="muted"></span></div>`;
    const items = () => Array.from(report.querySelectorAll(".batch-item"));
    const check = () => { const seen = {}; let dup = 0; items().forEach((el) => { const v = el.querySelector("select").value; el.classList.remove("conflict");
      if (v !== "") { if (seen[v] !== undefined) { dup++; el.classList.add("conflict"); items()[seen[v]].classList.add("conflict"); } else seen[v] = Number(el.dataset.i); } });
      const ready = items().filter((el) => el.querySelector("select").value !== "").length;
      document.getElementById("batch-msg").textContent = dup ? `⚠ ${dup} cena(s) com mais de uma imagem — ajuste antes de enviar.` : `${ready} de ${files.length} serão enviados.`;
      document.getElementById("batch-go").disabled = !!dup || !ready; };
    report.addEventListener("change", (e) => { if (e.target.matches(".batch-scene")) check(); }); check();
    document.getElementById("batch-cancel").onclick = () => { urls.forEach(URL.revokeObjectURL); report.innerHTML = ""; };
    document.getElementById("batch-go").onclick = async () => {
      const plan = items().map((el) => ({ file: files[Number(el.dataset.i)], scene: el.querySelector("select").value })).filter((x) => x.scene !== "");
      report.innerHTML = ""; let ok = 0;
      for (const x of plan) {
        const line = document.createElement("div"); report.append(line); line.textContent = `… ${x.file.name} → cena ${Number(x.scene) + 1}`;
        const res = await upload(x.file, Number(x.scene), variant, slotFor(Number(x.scene), variant));
        line.textContent = res.ok ? `✓ ${x.file.name} → cena ${Number(x.scene) + 1}${res.replaced ? " (substituiu)" : ""}` : `✗ ${x.file.name}: ${res.msg}`;
        if (res.ok) ok++;
      }
      urls.forEach(URL.revokeObjectURL);
      toast(`Lote concluído: ${ok}/${plan.length} enviado(s).`, ok === plan.length ? "success" : "info");
    };
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

// Editor de roteiro: cenas + texto corrido, ferramentas de texto, desfazer/refazer, rascunho local.
// Fonte da verdade: `scenes` ([{text, notes}]). Salvar sempre cria uma nova versão no servidor.
(function () {
  if (window.__editorCleanup) window.__editorCleanup();
  const dataEl = document.getElementById("editor-data");
  if (!dataEl) return;
  const cfg = JSON.parse(dataEl.textContent);
  const $ = (sel) => document.querySelector(sel);
  const toast = (m, k) => window.studioToast && window.studioToast(m, k);

  const scenesView = $("#scenes-view"), textView = $("#text-view"), fullText = $("#full-text");
  const statsEl = $("#stats"), btnSave = $("#btn-save");
  const draftKey = "studio:draft:" + cfg.videoId + ":" + cfg.baseVersion;

  let mode = "scenes";
  let scenes = cfg.scenes.map((s) => ({ text: s.text || "", notes: s.notes || "" }));
  const initialSnap = JSON.stringify(scenes);
  let lastSnap = initialSnap, dirty = false, timer = null;
  const undoStack = [], redoStack = [];

  // ---------- utilidades de texto ----------
  const words = (t) => (t.trim() ? t.trim().split(/\s+/).length : 0);
  const secs = (w) => Math.round((w / cfg.wpm) * 60);
  const fmt = (s) => Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  const splitBlocks = (t) => t.replace(/\r\n?/g, "\n").split(/\n\s*\n/)
    .map((b) => b.split("\n").map((l) => l.trim()).filter(Boolean).join(" ")).filter(Boolean);
  const splitSentences = (t) => t.trim().split(/(?<=[.!?…])\s+(?=["'“‘(\[]?[A-ZÀ-ÖØ-Þ0-9])/).map((s) => s.trim()).filter(Boolean);

  const TOOLS = {
    tidy: (t) => t.replace(/[ ​  ]/g, " ").replace(/\r\n?/g, "\n").replace(/[ \t]+/g, " ").replace(/ *\n */g, "\n").replace(/\n{3,}/g, "\n\n").trim(),
    punct: (t) => t.replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/…/g, "..."),
    marks: (t) => t.replace(/\[[^\]]*\]/g, "").replace(/\([^)]{0,40}\)/g, "").replace(/ {2,}/g, " ").trim(),
    emoji: (t) => t.replace(/[\p{Extended_Pictographic}️‍]/gu, "").replace(/ {2,}/g, " ").trim(),
    markdown: (t) => t.replace(/^#{1,6}\s*/gm, "").replace(/(\*\*|__|`)/g, "").replace(/(^|\s)[*_]([^*_\n]+)[*_](?=\s|[.,!?]|$)/g, "$1$2"),
    upper: (t) => t.toLocaleUpperCase("pt-BR"),
    lower: (t) => t.toLocaleLowerCase("pt-BR"),
    sentence: (t) => t.toLocaleLowerCase("pt-BR").replace(/(^\s*|[.!?…]\s+)(\p{L})/gu, (m, a, b) => a + b.toLocaleUpperCase("pt-BR")),
  };
  const TOOL_LABEL = { tidy: "Espaços arrumados", punct: "Aspas e reticências normalizadas", marks: "Marcações removidas", emoji: "Emojis removidos", markdown: "Markdown removido", upper: "Convertido para maiúsculas", lower: "Convertido para minúsculas", sentence: "Capitalização aplicada" };

  // ---------- histórico / rascunho ----------
  const snapshot = () => JSON.stringify(scenes);
  function updateButtons() {
    $("#btn-undo").disabled = !undoStack.length; $("#btn-redo").disabled = !redoStack.length;
  }
  function saveDraft() {
    try { if (snapshot() === initialSnap) localStorage.removeItem(draftKey); else localStorage.setItem(draftKey, JSON.stringify({ scenes, ts: Date.now() })); } catch (e) {}
  }
  function commit() {
    const cur = snapshot();
    if (cur === lastSnap) return;
    undoStack.push(lastSnap); if (undoStack.length > 100) undoStack.shift();
    redoStack.length = 0; lastSnap = cur; dirty = cur !== initialSnap; saveDraft(); updateButtons();
  }
  function commitSoon() { clearTimeout(timer); timer = setTimeout(commit, 700); }
  function restore(snap) { scenes = JSON.parse(snap); lastSnap = snap; dirty = snap !== initialSnap; saveDraft(); renderAll(); updateButtons(); }
  function undo() { clearTimeout(timer); commit(); if (!undoStack.length) return; redoStack.push(lastSnap); restore(undoStack.pop()); }
  function redo() { if (!redoStack.length) return; undoStack.push(lastSnap); restore(redoStack.pop()); }

  // ---------- estatísticas ----------
  function updateStats() {
    const w = scenes.reduce((a, s) => a + words(s.text), 0), s = secs(w);
    const empty = scenes.filter((x) => !x.text.trim()).length;
    const over = scenes.filter((x) => x.text.length > cfg.maxSceneChars).length;
    const diff = cfg.targetSeconds ? (s - cfg.targetSeconds) / cfg.targetSeconds : 0;
    const cls = !w ? "" : Math.abs(diff) <= 0.15 ? "ok" : Math.abs(diff) <= 0.4 ? "warn" : "bad";
    const parts = [`<span>${scenes.length} cena${scenes.length === 1 ? "" : "s"}</span>`, `<span>${w} palavras</span>`,
      `<span class="${cls}">≈ ${fmt(s)} de narração (alvo ${fmt(cfg.targetSeconds)})</span>`];
    if (empty) parts.push(`<span class="warn">${empty} cena(s) vazia(s) serão ignoradas</span>`);
    if (over) parts.push(`<span class="bad">${over} cena(s) acima de ${cfg.maxSceneChars} caracteres</span>`);
    if (scenes.length > cfg.maxScenes) parts.push(`<span class="bad">máximo de ${cfg.maxScenes} cenas</span>`);
    if (dirty) parts.push('<span class="warn">● alterações não salvas</span>');
    statsEl.innerHTML = parts.join("");
  }
  function updateCard(i) {
    const card = scenesView.children[i]; if (!card) return;
    const t = scenes[i].text, w = words(t), over = t.length > cfg.maxSceneChars;
    const m = card.querySelector(".metrics");
    m.textContent = `${w} palavras · ~${secs(w)}s${over ? " · muito longa" : ""}`; m.classList.toggle("warn", over);
    card.classList.toggle("over", over); card.classList.toggle("empty", !t.trim());
  }

  // ---------- render ----------
  function autoGrow(ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + 2 + "px"; }
  function btn(act, label, title) {
    const b = document.createElement("button"); b.type = "button"; b.className = "ghost sm"; b.dataset.act = act; b.textContent = label; b.title = title; return b;
  }
  function renderScenes(focus) {
    scenesView.replaceChildren();
    scenes.forEach((s, i) => {
      const card = document.createElement("div"); card.className = "scene-card"; card.dataset.i = i;
      const head = document.createElement("div"); head.className = "scene-head";
      const n = document.createElement("span"); n.className = "n"; n.textContent = i + 1;
      const m = document.createElement("span"); m.className = "metrics";
      const tools = document.createElement("div"); tools.className = "scene-tools";
      tools.append(btn("up", "↑", "Mover para cima (Alt+↑)"), btn("down", "↓", "Mover para baixo (Alt+↓)"),
        btn("split", "Dividir", "Divide no cursor (Ctrl+Enter)"), btn("sentences", "Frases", "Uma cena por frase"),
        btn("merge", "Mesclar ↓", "Junta com a cena seguinte"), btn("dup", "Duplicar", "Duplica a cena"),
        btn("add", "+ Abaixo", "Insere cena vazia abaixo"), btn("del", "Excluir", "Remove a cena"));
      head.append(n, m, tools);
      const ta = document.createElement("textarea"); ta.className = "scene-text"; ta.value = s.text; ta.rows = 2;
      ta.placeholder = "Texto exato da narração desta cena…"; ta.spellcheck = true;
      const note = document.createElement("input"); note.className = "scene-note"; note.value = s.notes; note.placeholder = "Nota (opcional) — ex.: close em objetos antigos";
      card.append(head, ta, note); scenesView.append(card);
      autoGrow(ta); updateCard(i);
    });
    if (focus) {
      const ta = scenesView.children[focus.i]?.querySelector(".scene-text");
      if (ta) { ta.focus(); const p = focus.pos ?? ta.value.length; ta.setSelectionRange(p, p); ta.scrollIntoView({ block: "nearest" }); }
    }
  }
  const textValue = () => scenes.map((s) => s.text).join("\n\n");
  function renderAll(focus) {
    if (mode === "scenes") renderScenes(focus); else fullText.value = textValue();
    updateStats(); updateFindCount();
  }
  function syncFromText() {
    const blocks = splitBlocks(fullText.value);
    scenes = (blocks.length ? blocks : [""]).map((t, i) => ({ text: t, notes: scenes[i]?.notes || "" }));
  }
  function setMode(next) {
    if (next === mode) return;
    if (mode === "text") syncFromText();
    mode = next; commit();
    scenesView.hidden = mode !== "scenes"; textView.hidden = mode !== "text"; $("#add-scene-end").hidden = mode !== "scenes";
    document.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    renderAll();
  }

  // ---------- ações de cena ----------
  function act(i, a) {
    const s = scenes[i], ta = scenesView.children[i]?.querySelector(".scene-text");
    if (a === "up" && i > 0) { [scenes[i - 1], scenes[i]] = [scenes[i], scenes[i - 1]]; commit(); renderScenes({ i: i - 1 }); }
    else if (a === "down" && i < scenes.length - 1) { [scenes[i + 1], scenes[i]] = [scenes[i], scenes[i + 1]]; commit(); renderScenes({ i: i + 1 }); }
    else if (a === "split") {
      const pos = ta ? ta.selectionStart : 0, before = s.text.slice(0, pos).trimEnd(), after = s.text.slice(pos).trimStart();
      if (!before || !after) { toast("Posicione o cursor no meio do texto, onde quer dividir a cena.", "error"); return; }
      scenes.splice(i, 1, { text: before, notes: s.notes }, { text: after, notes: "" }); commit(); renderScenes({ i: i + 1, pos: 0 });
    } else if (a === "sentences") {
      const parts = splitSentences(s.text);
      if (parts.length < 2) { toast("Esta cena tem só uma frase.", "error"); return; }
      scenes.splice(i, 1, ...parts.map((t, k) => ({ text: t, notes: k === 0 ? s.notes : "" }))); commit(); renderScenes({ i });
      toast(parts.length + " cenas criadas", "success");
    } else if (a === "merge") {
      if (i >= scenes.length - 1) { toast("Não há cena seguinte para mesclar.", "error"); return; }
      const nx = scenes[i + 1];
      scenes.splice(i, 2, { text: (s.text.trimEnd() + " " + nx.text.trimStart()).trim(), notes: [s.notes, nx.notes].filter(Boolean).join(" / ") });
      commit(); renderScenes({ i, pos: s.text.length });
    } else if (a === "dup") { scenes.splice(i + 1, 0, { text: s.text, notes: s.notes }); commit(); renderScenes({ i: i + 1 }); }
    else if (a === "add") { scenes.splice(i + 1, 0, { text: "", notes: "" }); commit(); renderScenes({ i: i + 1, pos: 0 }); }
    else if (a === "del") {
      if (s.text.trim() && !window.confirm("Excluir esta cena?")) return;
      if (scenes.length === 1) scenes[0] = { text: "", notes: "" }; else scenes.splice(i, 1);
      commit(); renderScenes({ i: Math.min(i, scenes.length - 1) });
    }
  }

  scenesView.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    act(Number(b.closest(".scene-card").dataset.i), b.dataset.act);
  });
  scenesView.addEventListener("input", (e) => {
    const card = e.target.closest(".scene-card"); if (!card) return;
    const i = Number(card.dataset.i);
    if (e.target.classList.contains("scene-text")) { scenes[i].text = e.target.value; autoGrow(e.target); updateCard(i); }
    else scenes[i].notes = e.target.value;
    dirty = snapshot() !== initialSnap; updateStats(); updateFindCount(); commitSoon();
  });
  scenesView.addEventListener("keydown", (e) => {
    const card = e.target.closest(".scene-card"); if (!card) return;
    const i = Number(card.dataset.i);
    if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); act(i, "split"); }
    else if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); act(i, "up"); }
    else if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); act(i, "down"); }
  });
  fullText.addEventListener("input", () => { syncFromText(); dirty = snapshot() !== initialSnap; updateStats(); updateFindCount(); commitSoon(); });
  $("#add-scene-end").addEventListener("click", () => { scenes.push({ text: "", notes: "" }); commit(); renderScenes({ i: scenes.length - 1, pos: 0 }); updateStats(); });
  document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  $("#btn-undo").addEventListener("click", undo); $("#btn-redo").addEventListener("click", redo);

  // ---------- ferramentas de limpeza ----------
  function transformAll(fn, label) {
    if (mode === "text") syncFromText();
    let changed = 0;
    scenes.forEach((s) => { const t = fn(s.text); if (t !== s.text) { s.text = t; changed++; } });
    if (!changed) { toast("Nada para alterar.", "info"); return 0; }
    commit(); renderAll(); toast(label + " em " + changed + " cena(s).", "success"); return changed;
  }
  document.querySelectorAll("[data-tool]").forEach((b) => b.addEventListener("click", () => {
    transformAll(TOOLS[b.dataset.tool], TOOL_LABEL[b.dataset.tool]); b.closest("details")?.removeAttribute("open");
  }));

  // ---------- localizar / substituir ----------
  const findPanel = $("#find-panel"), findInput = $("#find-input"), replInput = $("#replace-input");
  function buildRegex() {
    const q = findInput.value; $("#find-error").hidden = true;
    if (!q) return null;
    try {
      const src = $("#find-regex").checked ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      return new RegExp(src, "g" + ($("#find-case").checked ? "" : "i"));
    } catch (err) { const el = $("#find-error"); el.textContent = "Expressão inválida: " + err.message; el.hidden = false; return null; }
  }
  function updateFindCount() {
    if (findPanel.hidden) return;
    const re = buildRegex(); if (!re) { $("#find-count").textContent = ""; return; }
    if (mode === "text") syncFromText();
    const n = scenes.reduce((a, s) => a + (s.text.match(re) || []).length, 0);
    $("#find-count").textContent = n + " ocorrência(s)";
  }
  function toggleFind(force) {
    findPanel.hidden = force === undefined ? !findPanel.hidden : !force;
    if (!findPanel.hidden) { findInput.focus(); findInput.select(); updateFindCount(); }
  }
  $("#btn-find").addEventListener("click", () => toggleFind());
  [findInput, $("#find-regex"), $("#find-case")].forEach((el) => el.addEventListener("input", updateFindCount));
  $("#replace-all").addEventListener("click", () => {
    const re = buildRegex(); if (!re) return;
    let total = 0; const repl = replInput.value;
    const n = transformAll((t) => t.replace(re, (...m) => { total++; return repl.replace(/\$(\d+|&)/g, (_, g) => (g === "&" ? m[0] : typeof m[Number(g)] === "string" ? m[Number(g)] : "")); }), "Substituído");
    if (n) toast(total + " substituição(ões) feita(s).", "success"); updateFindCount();
  });
  $("#find-next").addEventListener("click", () => {
    const re = buildRegex(); if (!re) return;
    if (mode === "text") {
      re.lastIndex = fullText.selectionEnd; let m = re.exec(fullText.value); if (!m) { re.lastIndex = 0; m = re.exec(fullText.value); }
      if (m) { fullText.focus(); fullText.setSelectionRange(m.index, m.index + m[0].length); } else toast("Nada encontrado.", "info"); return;
    }
    const active = document.activeElement, startI = active?.classList.contains("scene-text") ? Number(active.closest(".scene-card").dataset.i) : 0;
    const startPos = active?.classList.contains("scene-text") ? active.selectionEnd : 0;
    for (let k = 0; k <= scenes.length; k++) {
      const i = (startI + k) % scenes.length, from = k === 0 ? startPos : 0;
      re.lastIndex = from; const m = re.exec(scenes[i].text);
      if (m && (k < scenes.length || m.index < startPos)) {
        const ta = scenesView.children[i].querySelector(".scene-text"); ta.focus(); ta.setSelectionRange(m.index, m.index + m[0].length); ta.scrollIntoView({ block: "center" }); return;
      }
    }
    toast("Nada encontrado.", "info");
  });

  // ---------- salvar ----------
  async function save() {
    clearTimeout(timer); if (mode === "text") syncFromText(); commit();
    const payload = scenes.filter((s) => s.text.trim()).map((s) => ({ text: s.text.trim(), notes: s.notes.trim() }));
    if (!payload.length) { toast("O roteiro está vazio.", "error"); return; }
    if (payload.length > cfg.maxScenes) { toast("Máximo de " + cfg.maxScenes + " cenas.", "error"); return; }
    if (payload.some((s) => s.text.length > cfg.maxSceneChars)) { toast("Há cenas acima de " + cfg.maxSceneChars + " caracteres. Divida-as.", "error"); return; }
    btnSave.disabled = true;
    try {
      const r = await fetch("/videos/" + cfg.videoId + "/script/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenes: payload, approve: $("#approve-on-save").checked }) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) { const d = body.detail; toast(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || "Erro ao salvar", "error"); btnSave.disabled = false; return; }
      dirty = false; try { localStorage.removeItem(draftKey); } catch (e) {}
      window.location.href = body.url;
    } catch (err) { toast("Falha de rede ao salvar.", "error"); btnSave.disabled = false; }
  }
  btnSave.addEventListener("click", save);

  // ---------- atalhos globais, saída e rascunho ----------
  function onKey(e) {
    if (!document.getElementById("editor")) return;
    const k = e.key.toLowerCase();
    if (e.ctrlKey && k === "s") { e.preventDefault(); save(); }
    else if (e.ctrlKey && k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (e.ctrlKey && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); redo(); }
    else if (e.ctrlKey && k === "h") { e.preventDefault(); toggleFind(true); }
  }
  const onBeforeUnload = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  const onHtmx = (e) => { if (dirty && !window.confirm("Há alterações não salvas. Sair mesmo assim?")) e.preventDefault(); };
  document.addEventListener("keydown", onKey);
  window.addEventListener("beforeunload", onBeforeUnload);
  document.body.addEventListener("htmx:beforeRequest", onHtmx);
  window.__editorCleanup = () => {
    document.removeEventListener("keydown", onKey); window.removeEventListener("beforeunload", onBeforeUnload);
    document.body.removeEventListener("htmx:beforeRequest", onHtmx); window.__editorCleanup = null; window.__editor = null;
  };

  try {
    const raw = localStorage.getItem(draftKey);
    if (raw) {
      const d = JSON.parse(raw);
      if (JSON.stringify(d.scenes) !== initialSnap) {
        $("#draft-banner").hidden = false; $("#draft-when").textContent = "(salvo em " + new Date(d.ts).toLocaleString("pt-BR") + ")";
        $("#draft-restore").onclick = () => { scenes = d.scenes; commit(); dirty = true; renderAll(); $("#draft-banner").hidden = true; };
        $("#draft-discard").onclick = () => { localStorage.removeItem(draftKey); $("#draft-banner").hidden = true; };
      }
    }
  } catch (e) {}

  window.__editor = { get scenes() { return scenes; }, setMode, act, undo, redo, save, transformAll, TOOLS };
  renderAll(); updateButtons();
})();

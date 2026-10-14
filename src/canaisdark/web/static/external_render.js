/* Envia o MP4 editado fora do sistema, em partes de 90 MB (limite de 100 MB do Cloudflare). */
(function () {
  var form = document.getElementById("external-render-form");
  if (!form || form.dataset.bound) return;
  form.dataset.bound = "1";
  var status = document.getElementById("external-render-status");
  var PART = 90 * 1024 * 1024;
  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var file = form.querySelector("input[type=file]").files[0];
    if (!file) return;
    var btn = form.querySelector("button");
    btn.disabled = true;
    try {
      var link = await (await fetch("/videos/" + form.dataset.video + "/external-render/link")).json();
      var total = Math.max(1, Math.ceil(file.size / PART));
      var last = null;
      for (var i = 0; i < total; i++) {
        status.textContent = "Enviando parte " + (i + 1) + " de " + total + "…";
        var res = await fetch(link.url + "?part=" + (i + 1) + "&total=" + total, {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file.slice(i * PART, Math.min(file.size, (i + 1) * PART)),
        });
        last = await res.json();
        if (!res.ok) throw new Error(last.detail || "Falha no envio");
      }
      status.textContent = last && last.status === "ok" ? "Pronto! Recarregando…" : "Envio incompleto.";
      if (last && last.status === "ok") setTimeout(function () { location.reload(); }, 800);
    } catch (e) {
      status.textContent = "Erro: " + e.message;
      btn.disabled = false;
    }
  });
})();

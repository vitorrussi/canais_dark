// Carregado no <head> (sem defer) para evitar "flash" de tema.
try { document.documentElement.dataset.theme = localStorage.getItem("theme") || "dark"; } catch (e) { document.documentElement.dataset.theme = "dark"; }

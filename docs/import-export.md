# Importar, exportar e baixar

## Baixar roteiro
Página do vídeo → **Baixar**: `.txt` (cenas separadas por linha em branco), `.md` (`# Título`, `## Cena N`, notas em `> ...`),
`.json` (`canaisdark.script`). Nome: `<canal>-<id-do-vídeo>-v<versão>.<ext>`. REST: `GET /api/scripts/{id}/download?format=`.

## Importar texto como roteiro
Vídeo → **Importar texto**, ou **Já tenho o roteiro** no canal (cria vídeo sem acionar o Claude; opcionalmente já aprova e/ou pede revisão).
Fonte: texto colado ou arquivo `.txt/.md/.json` (até 2 MB; o arquivo tem prioridade). Regras de divisão:
| Regra | Como divide |
|---|---|
| Detectar | títulos “Cena N” → linhas em branco → frases |
| Linha em branco | blocos separados por linha em branco |
| Uma por linha | cada linha vira uma cena |
| Títulos | `## Cena 1`, `Cena 2:`… (linhas `> nota` viram nota) |
| Uma por frase | uma cena por frase |
| Agrupar até N | junta frases até N caracteres |
Sempre há **prévia** (nº de cenas, palavras, tempo). Importar cria uma **nova versão** (autor `user:<id>`). REST: `POST /api/videos/{id}/scripts/import-text`.

## Exportar / importar vídeo e canal
- Exportar vídeo: todas as versões de roteiro + metadados (`canaisdark.video`). Exportar canal: configurações (+ vídeos, opcional) (`canaisdark.channel`). Sem mídia.
- Importar (`/import`): escolha o `.json`; informe um **nome específico** (opcional). Canal: sem nome, usa o do arquivo (com “ (2)” se existir); com nome já existente → erro.
  Vídeo: escolha o canal de destino; o título pode ser trocado. **Nunca sobrescreve**; importar não aciona o Claude.
- Compatibilidade: `version` do arquivo maior que a suportada é recusada com mensagem clara.

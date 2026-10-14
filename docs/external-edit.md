# Edição livre pelo Claude (sandbox do claude.ai)

O Claude edita o vídeo **no ambiente dele** (Python/ffmpeg do claude.ai) com liberdade total de efeitos
(faixas de capítulo, selos de data, textos animados, movimentos de câmera…) e devolve o MP4 pronto.
O render nativo (worker + ffmpeg) continua existindo; esta é uma opção ao lado dele.

## Como funciona
1. A pessoa clica em **Edição livre pelo Claude** no cartão ④ de `/videos/{id}`, descreve o que quer e copia o
   prompt para o chat do claude.ai (com o conector do Canais Dark).
2. O Claude chama a tool MCP `get_edit_package(video_id, quality="720p")`: manifesto com cenas, tempos por palavra,
   alvo de render e **links assinados** (3 h) para narração, imagens, trilha, legendas e um `.zip`.
3. No sandbox, o Claude baixa (`curl`), edita e envia o MP4 em partes (`split -b 90m`) para `upload.url`
   (`POST ?part=N&total=M`, corpo bruto). A resposta da última parte confirma o registro.
4. O servidor monta, valida com ffprobe (MP4, vídeo+áudio, 16:9, ≥1280 px, duração = narração ±3 s) e registra como
   **render final** (`meta.source = "claude_external"`). Aprovação, Shorts, YouTube e Drive seguem como hoje e
   continuam **humanos**: nenhuma tool MCP envia a YouTube/Drive.

## Requisitos
- Execução de código ligada no claude.ai **e rede liberada para `canaisdark.russi.app`** (Configurações →
  Recursos → execução de código e criação de arquivos). Sem isso o sandbox recebe 403 do proxy dele.
  Abra uma conversa nova depois de mudar.
- Cloudflare: a regra WAF de geo-bloqueio precisa deixar passar `/x/` (o sandbox vem de IPs dos EUA), no mesmo
  padrão da regra Skip de `/mcp`, `/.well-known/`, `/register`, `/token`, `/revoke`:
  `(starts_with(http.request.uri.path, "/x/"))`. Com Cloudflare Access, `/x/*` também fica fora do login por e-mail
  (a assinatura é a proteção).

## Fallback manual (sem rede no sandbox)
No mesmo cartão: **Baixar pacote (.zip)** → anexar no chat do claude.ai → baixar o MP4 que ele devolver →
**Usar vídeo editado externamente** (envia em partes de 90 MB e passa pelas mesmas validações).

## Segurança e limites
- Links assinados com `SECRET_KEY` (itsdangerous, salt `edit-package`): 3 h, um vídeo, amarrados ao token da pessoa
  (rotacionar token ou desativar a pessoa invalida). São credenciais: aparecem na conversa do claude.ai.
- Nada recebido é executado; só ffprobe lê o arquivo. Limite: 95 MB por requisição (Cloudflare), 2 GB no total,
  40 partes.
- O envio substitui o render final anterior (como qualquer render novo). Quando o roteiro/imagens mudam, o render
  externo fica "desatualizado" como os demais.
- Vídeos longos: o sandbox tem tempo/CPU limitados; prefira 720p/30 fps e renderizar por trechos.

Código: `services/edit_package.py`, `web/edit_package.py`, `static/external_render.js`, tool `get_edit_package`,
tarefa `edit_video`. Testes: `tests/test_edit_package.py`.

## Thumbnail pelo Claude
Mesmo mecanismo (links assinados, sandbox do claude.ai), sem narração e sem MP4:
1. Cartão do YouTube → **Criar a thumbnail com o Claude** (instruções opcionais) → copie o prompt para o chat.
2. O Claude chama `get_thumbnail_package(video_id)`: título e `thumbnail_ideas` dos metadados, imagens das cenas
   (links de 3 h), especificação (1280×720, JPG/PNG, ≤ 2 MB), estilo do canal e `upload.url`.
3. No sandbox ele compõe a imagem com Pillow e envia: `POST /x/<assinado>/thumbnail` (corpo bruto, ≤ 10 MB).
   O servidor valida pelo conteúdo e recomprime para ≤ 2 MB (`services/thumbnail.save_upload`); a última vale.
4. A pessoa revisa no cartão e envia ao YouTube como hoje (junto do vídeo). Nada vai ao YouTube pelo MCP.
Tarefa `make_thumbnail` (não existe no modo copiar/colar). Sem rede no sandbox: peça a imagem ao Claude no chat e
use "Enviar thumbnail" normalmente.

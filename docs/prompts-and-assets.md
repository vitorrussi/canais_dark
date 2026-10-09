# Prompts de imagem e assets por cena (Fase 4)

## Fluxo
1. Roteiro **aprovado** → na página do vídeo, “Abrir storyboard” (`/videos/<id>/scenes`).
2. **Pedir prompts ao Claude**: cria a tarefa `generate_prompts`; cole o prompt copiável no Claude Code. Ele chama `save_scene_prompts` (pode ser em lotes) e `complete_task`. Também dá para escrever/editar à mão (autosave ao sair do campo).
3. **Copiar prompt completo** (por cena, com o prefixo de estilo do canal) ou **Copiar todos** / **Baixar pacote** (`.txt`, `.md`, `.json`) e gerar as imagens no ChatGPT/Gemini.
4. **Enviar** as imagens: por cena (clicar/arrastar no slot), ou **em lote** (arrastar vários arquivos). Marque *9:16 (Shorts)* nas cenas que precisam de versão vertical e *cena-chave* nas que terão clipe de vídeo de IA.

## Prompt final
`visual_style_prompt do canal` + `, ` + `image_prompt da cena` + `, ` + `wide 16:9 landscape composition` (ou `vertical 9:16 portrait composition`).
O Claude escreve só o trecho da cena (em inglês). Mudar o estilo do canal atualiza todos os prompts copiados.

## Upload
- **Um arquivo por requisição** (o Cloudflare limita o corpo a 100 MB). `MAX_UPLOAD_MB` (padrão 100) no `.env`.
- Formato verificado pelo **conteúdo** (PNG, JPEG, WebP; MP4, WebM, MOV), nunca pela extensão.
- Erros: arquivo corrompido, resolução < 640 px no maior lado, versão principal vertical / versão 9:16 horizontal, tamanho acima do limite.
- Avisos (aceita e avisa): proporção diferente de 16:9 / 9:16 (≥ 8% — será ajustada/cortada no render), abaixo de 1920×1080 (1080×1920 no vertical). Imagens 3:2 do ChatGPT passam com aviso.
- Enviar de novo **substitui** (apaga o arquivo e a miniatura antigos). Miniatura JPEG gerada no upload.
- **Lote**: o número no nome define a cena (`03.png`, `cena-3.jpg`, `scene_03_final.webp`); sem número, ocupa as cenas ainda sem imagem, em ordem natural; duas imagens para a mesma cena → a segunda é ignorada. Relatório por arquivo.
- Clipes: resolução/duração só são lidas se o `ffprobe` (ffmpeg) estiver instalado; sem ele o clipe é aceito com aviso.

## Ao mudar o roteiro
Ao **aprovar uma nova versão**, cenas com **texto idêntico** à versão aprovada anterior herdam prompts, flags e arquivos (re-vinculados). Cenas alteradas ficam pendentes.

## Etapas do pipeline
`Prompts de imagem`: pendente/em andamento (tarefa) → parcial → concluída. `Imagens/clipes`: parcial → concluída (todas as cenas com versão 16:9 e a vertical onde pedida).

## API / MCP
MCP: `save_scene_prompts(video_id, prompts, task_id?)` (`scene_index` 0-based; devolve `missing_scene_indexes`); `get_script` inclui prompts e flags.
REST: `GET /api/videos/{id}/scenes`, `PUT /api/videos/{id}/scene-prompts`, `POST /api/videos/{id}/prompts/request`, `GET /api/videos/{id}/prompts?format=`, `POST /api/videos/{id}/scenes/{i}/upload`, `DELETE /api/videos/{id}/assets/{asset_id}`.

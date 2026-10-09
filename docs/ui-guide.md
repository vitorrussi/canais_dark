# Guia da interface

| Onde | O que faz |
|---|---|
| **Canais** (`/`) | Cards dos canais, painel “Precisa da sua atenção” (roteiros para aprovar, tarefas que falharam), atividade recente, mostrar arquivados, importar |
| **Novo canal** (`/channels/new`) | Formulário com ajuda e exemplo em cada campo + modelos (Mistério, História sombria, Curiosidades, True crime) |
| **Canal** | Aba Vídeos (busca por título/tema, filtro de status, novo vídeo com instruções extras, “Já tenho o roteiro”, importar/exportar) e aba Configurações (inclui palavras por cena, regras e instruções padrão) |
| **Vídeo** | Tema, cartão do Claude (prompt copiável), roteiro com versões (editar, baixar, comparar, aprovar, pedir revisão), pipeline de etapas, detalhes editáveis, duplicar, importar/exportar |
| **Editor de roteiro** | Cenas ou texto corrido, dividir/mesclar/frases, localizar-substituir (regex), limpeza, desfazer/refazer, rascunho local; salvar = nova versão |
| **Storyboard** (`/videos/<id>/scenes`) | Prompts por cena (editáveis, copiar completo/9:16, pacote .txt/.md/.json), upload por cena e em lote com miniaturas e avisos |
| **Timeline** (`/videos/<id>/timeline`) | Movimento, transição e texto na tela por cena, efeitos, JSON avançado; cada salvamento é uma versão nova |
| **Vídeo** — passos | A página do vídeo segue a ordem do trabalho: Tema → ① Roteiro (com o Claude dentro) → ② Narração → ③ Cenas → ④ Timeline e render (player, aprovar) → ⑤ Shorts 9:16 (opcional, recolhido; dá para dispensar) |
| **Jobs** (`/jobs`) | Histórico dos jobs de narração/alinhamento/render/Shorts, cancelar e tentar de novo |
| **Pendências** (`/tasks`) | Tarefas abertas e recentes, prompt “processar tudo” |
| **Conectar Claude** (`/connect`) | Comando do MCP pronto, gerar novo token (mostrado uma vez), solução de problemas |

| **Usuários** (`/admin/users`, só admin) | Criar pessoas, gerar novo token (mostrado uma vez), desativar/reativar, tornar admin |

## Atalhos
`g c` canais · `g p` pendências · `g k` conectar Claude · `/` buscar · `n` novo (canal ou vídeo) · `?` ajuda. No editor: `Ctrl+S` salvar, `Ctrl+Enter` dividir, `Alt+↑/↓` mover, `Ctrl+Z/Y`, `Ctrl+H` localizar.

## Tema
Botão de sol/lua no topo alterna escuro/claro (salvo no navegador).

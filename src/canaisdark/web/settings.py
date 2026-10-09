"""Conectar o Claude (comando do MCP, token pela UI)."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from canaisdark.services import users
from canaisdark.web.common import SessionDep, WebUser, render

router = APIRouter(include_in_schema=False)

PROCESS_ALL_PROMPT = (
    "Use o MCP `canais` para processar todas as pendências do Estúdio de Canais Dark: "
    "repita claim_next_task → executar o how_to → salvar (ex.: save_script) → complete_task "
    "até não haver mais tarefas. Se algo falhar, use fail_task com o motivo. "
    "Não aprove nada: a aprovação é feita no site."
)


def public_base_url(request: Request) -> str:
    """URL pública (considera proxy/Cloudflare via X-Forwarded-*)."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}"


def _page(request: Request, user: WebUser, token: str | None = None, status: int = 200) -> Response:
    base = public_base_url(request)
    shown = token or "<SEU_TOKEN>"
    return render(
        request,
        "connect.html",
        user,
        status=status,
        nav="connect",
        mcp_url=f"{base}/mcp",
        token=token,
        command=f'claude mcp add --transport http canais {base}/mcp --header "Authorization: Bearer {shown}"',
        process_all_prompt=PROCESS_ALL_PROMPT,
    )


@router.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request, user: WebUser) -> Response:
    return _page(request, user)


@router.post("/connect/token", response_class=HTMLResponse)
def new_token(request: Request, user: WebUser, session: SessionDep) -> Response:
    """Gera um novo token (o anterior deixa de valer). O valor só é exibido nesta resposta."""
    return _page(request, user, token=users.issue_token(session, user))

"""Tela "Permitir" do conector (OAuth do /mcp) e gestão dos conectores autorizados."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from mcp.server.auth.provider import construct_redirect_uri

from canaisdark.services import oauth
from canaisdark.web.common import SessionDep, WebUser, back, render

router = APIRouter(include_in_schema=False)


def _expired(request: Request, user: WebUser) -> Response:
    return render(
        request,
        "error.html",
        user,
        status=410,
        message="Este pedido de autorização expirou. Volte ao Claude e tente conectar de novo.",
        code=410,
    )


@router.get("/oauth/consent", response_class=HTMLResponse)
def consent_page(request: Request, user: WebUser, session: SessionDep, req: str = "") -> Response:
    pending = oauth.get_pending(session, req)
    if pending is None:
        return _expired(request, user)
    return render(request, "oauth_consent.html", user, pending=pending, req=req)


@router.post("/oauth/consent")
def consent_decide(
    request: Request,
    user: WebUser,
    session: SessionDep,
    req: str = Form(""),
    decision: str = Form("deny"),
) -> Response:
    if decision == "allow":
        try:
            redirect_uri, code, state = oauth.approve(session, req, user)
        except LookupError:
            return _expired(request, user)
        return RedirectResponse(
            construct_redirect_uri(redirect_uri, code=code, state=state), status_code=303
        )
    denied = oauth.deny(session, req)
    if denied is None:
        return _expired(request, user)
    redirect_uri, state = denied
    return RedirectResponse(
        construct_redirect_uri(
            redirect_uri,
            error="access_denied",
            error_description="A pessoa não autorizou o acesso",
            state=state,
        ),
        status_code=303,
    )


@router.post("/connectors/{grant_id}/revoke")
def revoke_connector(
    request: Request, user: WebUser, session: SessionDep, grant_id: str
) -> Response:
    assert user.id is not None
    done = oauth.revoke_grant(session, user.id, grant_id)
    return back(
        request,
        "/connect",
        ok="Conector desconectado." if done else "Esse conector já estava desconectado.",
    )

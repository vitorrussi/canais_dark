"""Adaptador do SDK `mcp` (OAuthAuthorizationServerProvider) sobre `services/oauth.py`.

O SDK valida cliente, `redirect_uri`, PKCE (S256) e formato das requisições; aqui só persistimos.
A tela de consentimento é do site (`/oauth/consent`), pois exige a sessão da pessoa.
"""

from urllib.parse import quote

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlmodel import Session

from canaisdark.db import get_engine
from canaisdark.models import OAuthToken as OAuthTokenRow
from canaisdark.services import oauth


def _epoch(row: OAuthTokenRow) -> int:
    return int(oauth._aware(row.expires_at).timestamp())


class StudioOAuthProvider:
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with Session(get_engine()) as s:
            info = oauth.get_client(s, client_id)
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise RegistrationError("invalid_client_metadata", "client_id ausente")
        with Session(get_engine()) as s:
            oauth.save_client(s, client_info.client_id, client_info.model_dump(mode="json"))

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        with Session(get_engine()) as s:
            req = oauth.create_pending(
                s,
                client.client_id or "",
                params.scopes or [],
                {
                    "redirect_uri": str(params.redirect_uri),
                    "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                    "code_challenge": params.code_challenge,
                    "state": params.state,
                    "resource": params.resource,
                },
            )
        return f"/oauth/consent?req={quote(req)}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with Session(get_engine()) as s:
            row = oauth.load_code(s, client.client_id or "", authorization_code)
            if row is None:
                return None
            return AuthorizationCode(
                code=authorization_code,
                scopes=row.scopes,
                expires_at=float(_epoch(row)),
                client_id=row.client_id,
                code_challenge=row.data["code_challenge"],
                redirect_uri=AnyUrl(row.data["redirect_uri"]),
                redirect_uri_provided_explicitly=bool(
                    row.data.get("redirect_uri_provided_explicitly", True)
                ),
                resource=row.data.get("resource"),
                subject=str(row.user_id),
            )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with Session(get_engine()) as s:
            row = oauth.load_code(s, client.client_id or "", authorization_code.code)
            if row is None:
                raise TokenError("invalid_grant", "Código inválido, usado ou expirado")
            access, refresh, expires_in = oauth.exchange_code(s, row)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=refresh,
            scope=" ".join(authorization_code.scopes) or None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        with Session(get_engine()) as s:
            row = oauth.load_refresh(s, client.client_id or "", refresh_token)
            if row is None:
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=row.client_id,
                scopes=row.scopes,
                expires_at=_epoch(row),
                subject=str(row.user_id),
            )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        with Session(get_engine()) as s:
            row = oauth.load_refresh(s, client.client_id or "", refresh_token.token)
            if row is None:
                raise TokenError("invalid_grant", "Refresh token inválido, usado ou expirado")
            access, refresh, expires_in = oauth.exchange_refresh(s, row, scopes or None)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=refresh,
            scope=" ".join(scopes or refresh_token.scopes) or None,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        with Session(get_engine()) as s:
            row = oauth.load_access(s, token)
            if row is None:
                return None
            return AccessToken(
                token=token,
                client_id=row.client_id,
                scopes=row.scopes,
                expires_at=_epoch(row),
                subject=str(row.user_id),
            )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with Session(get_engine()) as s:
            oauth.revoke_by_raw(s, token.token)

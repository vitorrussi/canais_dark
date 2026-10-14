"""Rotas OAuth do servidor MCP (metadados, registro, authorize, token, revoke) via SDK."""

from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.routing import Route

from canaisdark.config import get_settings, oauth_issuer
from canaisdark.mcp.oauth_provider import StudioOAuthProvider


def oauth_routes() -> list[Route]:
    issuer = AnyHttpUrl(oauth_issuer(get_settings()))
    resource = AnyHttpUrl(f"{oauth_issuer(get_settings())}/mcp")
    return [
        *create_auth_routes(
            provider=StudioOAuthProvider(),
            issuer_url=issuer,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
        *create_protected_resource_routes(
            resource_url=resource,
            authorization_servers=[issuer],
            resource_name="Estúdio de Canais Dark",
        ),
    ]

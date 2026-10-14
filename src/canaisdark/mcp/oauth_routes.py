"""Rotas OAuth do servidor MCP (metadados, registro, authorize, token, revoke) via SDK."""

from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl, ConfigDict, TypeAdapter
from starlette.routing import Route

from canaisdark.config import get_settings, oauth_issuer
from canaisdark.mcp.oauth_provider import StudioOAuthProvider

# RFC 8414: o `issuer` é comparado como texto; sem isto o pydantic acrescentaria "/" ao domínio.
_URL = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))


def oauth_routes() -> list[Route]:
    base = oauth_issuer(get_settings())
    issuer = _URL.validate_python(base)
    resource = _URL.validate_python(f"{base}/mcp")
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

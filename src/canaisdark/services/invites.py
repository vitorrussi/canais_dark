"""Link de acesso: entra no site sem colar token (para quem não é técnico).

URL assinada com `SECRET_KEY`, 7 dias, amarrada ao token atual da pessoa (rotacionar o token ou
desativar a pessoa invalida o link). É uma credencial: enviar por canal privado.
"""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.services.errors import InvalidState

SALT = "access-link"
MAX_AGE_SECONDS = 7 * 24 * 3600


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=SALT)


def _fingerprint(user: User) -> str:
    return (user.token_hash or "")[:16]


def make_link_token(user: User) -> str:
    if not user.is_active:
        raise InvalidState("Reative a pessoa antes de gerar um link de acesso")
    return _serializer().dumps({"u": user.id, "f": _fingerprint(user)})


def user_from_link_token(session: Session, signed: str) -> User | None:
    try:
        data = _serializer().loads(signed, max_age=MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    user = session.get(User, data.get("u")) if isinstance(data, dict) else None
    if user is None or not user.is_active or data.get("f") != _fingerprint(user):
        return None
    return user

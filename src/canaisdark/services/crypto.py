"""Criptografia de segredos guardados no banco (refresh tokens). Chave: FERNET_KEY no .env."""

from cryptography.fernet import Fernet, InvalidToken

from canaisdark.config import get_settings
from canaisdark.services.errors import InvalidState

KEY_HELP = (
    'Gere uma chave com: python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())" e coloque em FERNET_KEY no .env.'
)


def _fernet() -> Fernet:
    key = get_settings().fernet_key.strip()
    if not key:
        raise InvalidState(f"FERNET_KEY não está configurada. {KEY_HELP}")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise InvalidState(f"FERNET_KEY inválida. {KEY_HELP}") from e


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise InvalidState(
            "Não foi possível ler o token salvo (a FERNET_KEY mudou?). Reconecte o YouTube do canal."
        ) from e


def configured() -> bool:
    try:
        _fernet()
    except InvalidState:
        return False
    return True

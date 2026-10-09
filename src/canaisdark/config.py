from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/canaisdark.db"
    data_dir: Path = Path("./data/files")
    fernet_key: str = ""
    secret_key: str = "dev-insecure-change-me"
    # Hosts extras do /mcp (DNS rebinding), separados por vírgula
    mcp_allowed_hosts: str = ""
    # URL pública (ex.: https://canaisdark.russi.app). Quando definida: SECRET_KEY real é obrigatório,
    # o cookie de sessão vira Secure (se https) e o host entra automaticamente em MCP_ALLOWED_HOSTS.
    public_url: str = ""
    # ElevenLabs (Fase 3). A chave fica só no .env; nunca é exibida nem gravada no banco.
    elevenlabs_api_key: str = ""
    # Modo demonstração: usa um ElevenLabs FALSO (sem rede, sem créditos; o áudio é sintético/mudo)
    elevenlabs_fake: bool = False
    elevenlabs_max_chars_per_job: int = 2500
    # Tamanho máximo de cada requisição de TTS (trechos cortados em fronteiras de cena)
    tts_chunk_chars: int = 2500
    # Interruptor geral do Forced Alignment (false = nunca chama; tempos das cenas são estimados)
    forced_alignment_enabled: bool = True
    worker_poll_seconds: float = 2.0

    @property
    def elevenlabs_ready(self) -> bool:
        return bool(self.elevenlabs_api_key) or self.elevenlabs_fake


@lru_cache
def get_settings() -> Settings:
    return Settings()


DEFAULT_SECRET = "dev-insecure-change-me"


def public_host(settings: Settings) -> str:
    """Host (com porta, se houver) de PUBLIC_URL, ou string vazia."""
    from urllib.parse import urlparse

    return urlparse(settings.public_url).netloc if settings.public_url else ""


def check_production_settings(settings: Settings) -> None:
    exposed = settings.env != "dev" or bool(settings.public_url)
    if exposed and settings.secret_key == DEFAULT_SECRET:
        raise RuntimeError(
            "Defina SECRET_KEY no .env (aleatório, longo) antes de expor o sistema: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )

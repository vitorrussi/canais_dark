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


@lru_cache
def get_settings() -> Settings:
    return Settings()

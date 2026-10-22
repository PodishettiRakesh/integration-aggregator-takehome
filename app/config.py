from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openbao_addr: str = Field(default="http://127.0.0.1:8200")
    openbao_token: SecretStr = Field(default=SecretStr(""))
    openbao_mount: str = Field(default="oauth2")
    redirect_url: str = Field(default="http://localhost:8000/callback")
    log_level: str = Field(default="INFO")
    http_timeout_seconds: float = Field(default=5.0, ge=0.1)
    oauth_state_ttl_seconds: int = Field(default=600, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()

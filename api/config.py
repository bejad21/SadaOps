"""Typed configuration for the SadaOps FastAPI control plane."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed control-plane settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_http_url: str = "http://127.0.0.1:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secretsecretsecretsecretsecret32"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    database_url: str = "postgresql://sadaops:sadaops_dev_pass@127.0.0.1:5432/sadaops"
    qdrant_url: str = "http://127.0.0.1:6333"
    mailpit_api_url: str = "http://127.0.0.1:8025"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a cleaned list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()

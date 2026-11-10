"""Runtime configuration, loaded from environment variables / `.env`."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    openai_api_key: SecretStr
    openai_model: str = Field(min_length=1)
    openai_max_output_tokens: int = 32_000
    openai_timeout_seconds: float = 600.0

    # GitHub
    github_token: SecretStr
    github_repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    github_api_url: str = "https://api.github.com"
    http_timeout_seconds: float = 30.0

    # Agent behaviour
    trigger_label: str = "autoforge"
    branch_prefix: str = "autoforge/"
    max_context_bytes: int = 60_000
    max_file_bytes: int = 20_000

    log_level: str = "INFO"

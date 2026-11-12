"""Runtime configuration, loaded from environment variables or a ``.env`` file.

Each field below maps to an upper-case environment variable of the same name,
e.g. ``openai_model`` <- ``OPENAI_MODEL``. Real environment variables take
precedence over ``.env``. See ``.env.example`` for a template.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All AutoForge settings, validated on load.

    Missing required values (API key, model, token, repo) raise a
    ``ValidationError``, which the CLI turns into a clear configuration error.
    Secrets are ``SecretStr``: they print as ``**********`` and must be read
    explicitly with ``.get_secret_value()``.

    Attributes:
        openai_api_key: OpenAI API key (``OPENAI_API_KEY``). Required, secret.
        openai_model: Model ID for the coder agent, e.g. ``"gpt-6-luna"``. Required.
        openai_max_output_tokens: Upper bound on tokens the model may generate.
        openai_timeout_seconds: Timeout for one OpenAI call.
        github_token: Fine-grained GitHub token for the target repo. Required, secret.
        github_repo: Target repository as ``"owner/repo"``. Required.
        github_api_url: GitHub API root (change only for GitHub Enterprise).
        http_timeout_seconds: Timeout for one GitHub API call.
        trigger_label: Label that marks issues for AutoForge; also the status-label prefix.
        branch_prefix: Prefix of agent branches (``autoforge/issue-N``).
        max_context_bytes: Total file-content budget sent to the LLM per run.
        max_file_bytes: Largest single file sent to the LLM.
        log_level: Minimum log level (``DEBUG``, ``INFO``, ``WARNING`` ...).
    """

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

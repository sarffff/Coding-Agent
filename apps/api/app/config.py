from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime limits and local workspace policy for the Phase 1 API."""

    workspace_root: Path = Path.cwd()
    state_dir: Path = Path(".forge/state")
    max_file_size_bytes: int = 1_000_000
    max_tree_entries: int = 5_000
    max_search_results: int = 200
    git_timeout_seconds: int = 10
    command_timeout_seconds: int = 30
    github_token: str | None = None
    allowed_remote_hosts: list[str] = ["github.com"]
    allowed_repositories: list[str] = []
    remote_provider: str = "github"
    remote_timeout_seconds: int = 30
    idempotency_ttl_hours: int = 24
    llm_provider: str = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_api_url: str = "https://api.anthropic.com"
    openai_api_key: str | None = None
    openai_api_url: str = "https://api.openai.com/v1"
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = Field(default=4_096, ge=256, le=131_072)
    llm_timeout_seconds: int = Field(default=60, ge=1)
    llm_max_context_files: int = Field(default=12, ge=1)
    llm_max_context_bytes: int = Field(default=120_000, ge=1_000)
    task_max_iterations: int = Field(default=8, ge=1)
    task_max_wall_time_seconds: int = Field(default=3_600, ge=1)
    task_max_remote_calls: int = Field(default=50, ge=1)
    task_max_consecutive_failures: int = Field(default=3, ge=1)
    audit_retention_days: int = 30
    audit_max_events: int = 5_000
    test_output_retention_days: int = 7
    test_output_max_runs: int = 20
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Pin the env file to apps/api/.env so it loads regardless of the process CWD
    # (`pnpm api:dev` runs from the repo root; the Electron shell sets vars directly).
    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=str(Path(__file__).resolve().parents[1] / ".env"),
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

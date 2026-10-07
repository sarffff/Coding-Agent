from functools import lru_cache
from pathlib import Path

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
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

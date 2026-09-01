"""Runtime configuration. Everything is env-driven so nothing is baked into code."""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RT_",
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://rt_claude:rt_claude@127.0.0.1:55447/rt_claude"
    github_token: str = ""
    github_api_url: str = "https://api.github.com"
    # Third-party clones live outside the project tree (AI_STORAGE_RULES.md).
    clone_root: Path = Path("D:/Dev/Repos/_rt_mirrors")
    profiles_dir: Path = PROJECT_ROOT / "repo_profiles"

    discussion_page_size: int = Field(default=50, ge=1, le=100)
    # 0 means "no cap": walk until the incremental watermark is reached.
    max_discussions_per_run: int = 0
    http_timeout_seconds: float = 60.0
    http_max_retries: int = 5

    def resolved_github_token(self) -> str:
        """Env token wins; otherwise borrow the token from an authenticated `gh`."""
        if self.github_token:
            return self.github_token
        gh = shutil.which("gh")
        if not gh:
            return ""
        try:
            out = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [gh, "auth", "token"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return out.stdout.strip() if out.returncode == 0 else ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

"""Gateway settings.

Provider keys deliberately use a COMPANION_ prefix instead of the vendors'
usual names (ANTHROPIC_API_KEY etc.). Claude Code switches from the signed-in
subscription to API billing when ANTHROPIC_API_KEY is in its environment, so
reusing that name for the gateway could silently change how the Desktop app
itself authenticates.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_HOME = Path(os.environ.get("COMPANION_HOME", Path.home() / ".claude-companion"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COMPANION_",
        env_file=(DEFAULT_HOME / ".env", ".env"),
        extra="ignore",
    )

    home: Path = DEFAULT_HOME
    host: str = "127.0.0.1"
    port: int = 8765
    database_url: str | None = None
    allow_non_local_bind: bool = False

    # Permission switches. They are enforced by the gateway, so the MCP server
    # and the dashboard are bound by the same rules.
    allow_write: bool = True
    allow_execute: bool = True
    allow_paid_calls: bool = True
    max_run_cost_usd: float = Field(default=1.0, ge=0)
    mcp_can_approve: bool = False
    max_dataset_items: int = 2000
    # How long an agent's model call waits for the Claude session to answer.
    bridge_timeout_s: float = Field(default=900, gt=0)

    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    ollama_url: str = "http://127.0.0.1:11434"
    custom_base_url: str | None = None
    custom_api_key: SecretStr | None = None
    pricing_file: Path | None = None

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.home / 'companion.db').as_posix()}"

    @property
    def token_path(self) -> Path:
        return self.home / "gateway.token"

    @property
    def checkpoint_path(self) -> Path:
        return self.home / "checkpoints.db"

    def ensure_home(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)

    def gateway_token(self) -> str:
        """Shared secret between gateway, MCP server and dashboard.

        Binding to localhost alone does not stop a web page in the browser from
        posting to the gateway, so every API call also needs this token.
        """
        self.ensure_home()
        if not self.token_path.exists():
            self.token_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        return self.token_path.read_text(encoding="utf-8").strip()

    def public_view(self) -> dict:
        return {
            "home": str(self.home),
            "bind": f"{self.host}:{self.port}",
            "database": "postgresql" if self.db_url.startswith("postgresql") else "sqlite",
            "permissions": {
                "allow_write": self.allow_write,
                "allow_execute": self.allow_execute,
                "allow_paid_calls": self.allow_paid_calls,
                "max_run_cost_usd": self.max_run_cost_usd,
                "mcp_can_approve": self.mcp_can_approve,
            },
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

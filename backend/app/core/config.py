from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global application settings loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_prefix="AGAN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AGAN-CTF"
    debug: bool = False

    # Paths
    data_dir: Path = Field(default=Path("data"))
    workspace_root: Path | None = Field(default=None)
    skills_dir: Path | None = Field(default=None)
    memories_dir: Path | None = Field(default=None)
    database_url: str = "sqlite+aiosqlite:///./data/agan.db"

    # Sandbox defaults
    sandbox_image: str = "agan-ctf-sandbox:latest"
    sandbox_platform: str = "linux/amd64"
    sandbox_cpu_count: float = 2.0
    sandbox_mem_limit: str = "2g"
    sandbox_memswap_limit: str = "2g"
    sandbox_pids_limit: int = 256
    sandbox_network_mode: str = "bridge"
    sandbox_default_timeout: int = 30
    sandbox_max_timeout: int = 180
    sandbox_output_max_chars: int = 8000
    sandbox_output_max_lines: int = 120
    sandbox_output_head_lines: int = 50
    sandbox_output_tail_lines: int = 50

    # Default Guardrails
    default_max_steps: int = 30
    default_step_timeout_sec: int = 120
    default_token_budget: int = 200_000
    default_cost_budget_usd: float = 5.0
    default_repeat_threshold: int = 3
    default_compact_ratio: float = 0.75

    # Optional default LLM settings for MVP CLI usage
    default_llm_base_url: str = "https://api.openai.com/v1"
    default_llm_api_key: str = ""
    default_llm_model: str = "gpt-4o"

    # Optional Web Search provider defaults (overridable per-tool in DB)
    tavily_api_key: str = ""
    brave_api_key: str = ""
    searxng_base_url: str = ""

    @property
    def workspaces_dir(self) -> Path:
        base = self.workspace_root if self.workspace_root is not None else (self.data_dir / "workspaces")
        path = base.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def resolved_skills_dir(self) -> Path:
        base = (
            self.skills_dir
            if self.skills_dir is not None
            else (Path(__file__).resolve().parents[2] / "skills")
        )
        path = base.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def resolved_memories_dir(self) -> Path:
        base = (
            self.memories_dir
            if self.memories_dir is not None
            else (self.data_dir / "memories")
        )
        path = base.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()

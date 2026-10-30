"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _find_config_dir() -> Path:
    env = os.environ.get("TAA_CONFIG_DIR")
    if env:
        return Path(env)
    cwd = Path.cwd() / "config"
    if cwd.is_dir():
        return cwd
    return Path(__file__).resolve().parents[2] / "config"


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    config_dir: Path
    llm_provider: str  # "anthropic", "bedrock" or "off"
    scoring_model: str
    drafting_model: str
    scoring_effort: str
    drafting_effort: str
    fallbacks: bool
    bedrock_region: str | None
    max_llm_candidates: int
    llm_concurrency: int
    pdl_api_key: str | None
    pdl_max_results: int
    app_username: str | None
    app_password: str | None
    trusted_user_header: str | None
    stale_after_days: int

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider in {"anthropic", "bedrock"}

    @property
    def signals_path(self) -> Path:
        return self.config_dir / "signals"

    @property
    def rubrics_path(self) -> Path:
        return self.config_dir / "rubrics"

    @property
    def do_not_target_path(self) -> Path:
        return self.config_dir / "do_not_target.yaml"


def _provider() -> str:
    val = os.environ.get("TAA_LLM_PROVIDER", "auto").strip().lower()
    if val != "auto":
        return val
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_PROFILE"):
        return "anthropic"
    return "off"


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("TAA_DATABASE_URL", "sqlite:///./data/taa.db"),
        config_dir=_find_config_dir(),
        llm_provider=_provider(),
        scoring_model=os.environ.get("TAA_SCORING_MODEL", "claude-opus-5-5"),
        drafting_model=os.environ.get("TAA_DRAFTING_MODEL", "claude-opus-5-5"),
        scoring_effort=os.environ.get("TAA_SCORING_EFFORT", "high"),
        drafting_effort=os.environ.get("TAA_DRAFTING_EFFORT", "medium"),
        fallbacks=_bool("TAA_LLM_FALLBACKS", True),
        bedrock_region=os.environ.get("TAA_BEDROCK_REGION") or os.environ.get("AWS_REGION"),
        max_llm_candidates=int(os.environ.get("TAA_MAX_LLM_CANDIDATES", "200")),
        llm_concurrency=int(os.environ.get("TAA_LLM_CONCURRENCY", "4")),
        pdl_api_key=os.environ.get("PDL_API_KEY") or None,
        pdl_max_results=int(os.environ.get("TAA_PDL_MAX_RESULTS", "25")),
        app_username=os.environ.get("TAA_USERNAME") or None,
        app_password=os.environ.get("TAA_PASSWORD") or None,
        trusted_user_header=os.environ.get("TAA_TRUSTED_USER_HEADER") or None,
        stale_after_days=int(os.environ.get("TAA_STALE_AFTER_DAYS", "180")),
    )

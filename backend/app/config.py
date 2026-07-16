"""Configuration: config.yaml for tunables, .env for secrets.

Secrets never live in config.yaml; tunables never live in .env (except MODE,
which is handy to flip per-run). Loaded once and cached — import `get_settings`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"
ENV_PATH = REPO_ROOT / ".env"


class OpenRouterConfig(BaseModel):
    base_url: str = "https://openrouter.ai/api/v1"
    white_model: str = "openai/gpt-oss-120b:free"
    black_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    analyst_model: str = "qwen/qwen3-coder:free"
    fallback_model: str = "openrouter/free"
    request_timeout_s: float = 60
    max_tokens_move: int = 300
    max_tokens_analysis: int = 1500


class ThrottleConfig(BaseModel):
    min_seconds_between_requests: float = 4
    max_requests_per_game: int = 250


class GameConfig(BaseModel):
    max_moves: int = 120
    illegal_move_retries: int = 3
    live_commentary_every_n_moves: int = 0
    move_delay_ui_ms: int = 800


class Secrets(BaseSettings):
    """Values from .env / process env. Never logged, never sent to the frontend."""

    model_config = SettingsConfigDict(
        env_file=ENV_PATH, env_file_encoding="utf-8", extra="ignore"
    )

    openrouter_api_key: str = ""
    openrouter_site_url: str = "http://localhost:5173"
    openrouter_site_name: str = "LLM Chess Arena"
    mode: Literal["live", "mock", ""] = ""


class Settings(BaseModel):
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    throttle: ThrottleConfig = Field(default_factory=ThrottleConfig)
    game: GameConfig = Field(default_factory=GameConfig)
    mode: Literal["live", "mock"] = "mock"
    secrets: Secrets = Field(default_factory=Secrets)

    @property
    def is_mock(self) -> bool:
        return self.mode == "mock"

    @property
    def has_api_key(self) -> bool:
        return bool(self.secrets.openrouter_api_key.strip())


def load_settings(config_path: Path | None = None) -> Settings:
    path = config_path or CONFIG_PATH
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    secrets = Secrets()
    # MODE in the environment wins over config.yaml, so a run can be flipped
    # without editing tracked files.
    if secrets.mode:
        raw["mode"] = secrets.mode

    settings = Settings(**raw, secrets=secrets)

    # Fail safe, not open: live mode without a key would 401 on every call.
    if settings.mode == "live" and not settings.has_api_key:
        raise RuntimeError(
            "mode is 'live' but OPENROUTER_API_KEY is empty. "
            "Set it in .env or switch config.yaml to mode: mock."
        )
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()

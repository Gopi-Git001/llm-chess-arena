"""Free-model catalogue for the model pickers (PLAN.md §10 GET /api/models).

Two sources, one shape:
  * mock mode → a bundled static list, zero network (mock must stay offline).
  * live mode → proxied from OpenRouter's /models, filtered to free, cached 1h.

The bundled list doubles as the live fallback: if OpenRouter is unreachable the
picker still has something to show rather than an empty dropdown.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import Settings
from app.llm_client import OpenRouterClient

log = logging.getLogger(__name__)

CACHE_TTL_S = 3600

# A hand-kept set of commonly-available free IDs. The lineup rotates, so this is
# a starting point for the picker, never a guarantee — verify_models.py checks
# reality. openrouter/free is the always-works auto-router.
BUNDLED_FREE_MODELS: list[dict[str, Any]] = [
    {"id": "openrouter/free", "name": "Auto-router (free)", "context_length": 200000},
    {"id": "openai/gpt-oss-20b:free", "name": "GPT-OSS 20B", "context_length": 131072},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama 3.3 70B", "context_length": 131072},
    {"id": "meta-llama/llama-3.2-3b-instruct:free", "name": "Llama 3.2 3B", "context_length": 131072},
    {"id": "qwen/qwen3-coder:free", "name": "Qwen3 Coder", "context_length": 1048576},
    {"id": "qwen/qwen3-next-80b-a3b-instruct:free", "name": "Qwen3 Next 80B", "context_length": 262144},
    {"id": "google/gemma-4-31b-it:free", "name": "Gemma 4 31B", "context_length": 131072},
    {"id": "nvidia/nemotron-nano-9b-v2:free", "name": "Nemotron Nano 9B", "context_length": 131072},
    {"id": "nousresearch/hermes-3-llama-3.1-405b:free", "name": "Hermes 3 405B", "context_length": 131072},
]


def _is_free(model: dict[str, Any]) -> bool:
    pricing = model.get("pricing") or {}
    try:
        return float(pricing["prompt"]) == 0 and float(pricing["completion"]) == 0
    except (KeyError, TypeError, ValueError):
        return False


def _slim(model: dict[str, Any]) -> dict[str, Any]:
    """Only what the picker needs — the raw /models entries are large."""
    return {
        "id": model.get("id"),
        "name": model.get("name") or model.get("id"),
        "context_length": model.get("context_length"),
    }


class ModelCatalog:
    """Serves the free-model list, cached, honouring mock vs live."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: list[dict[str, Any]] | None = None
        self._fetched_at: float = 0.0

    def _fresh(self) -> bool:
        return self._cache is not None and (time.monotonic() - self._fetched_at) < CACHE_TTL_S

    async def list_models(self, force: bool = False) -> dict[str, Any]:
        # Mock mode never touches the network — the bundled list is the answer.
        if self.settings.is_mock:
            return {"models": BUNDLED_FREE_MODELS, "source": "bundled", "mode": "mock"}

        if not force and self._fresh():
            return {"models": self._cache, "source": "cache", "mode": "live"}

        try:
            async with OpenRouterClient(self.settings) as client:
                raw = await client.list_models()
            free = sorted(
                (_slim(m) for m in raw if _is_free(m)),
                key=lambda m: m["id"],
            )
            if free:
                self._cache = free
                self._fetched_at = time.monotonic()
                return {"models": free, "source": "openrouter", "mode": "live"}
            log.warning("OpenRouter returned no free models; using bundled list")
        except Exception as exc:
            log.warning("Could not fetch models from OpenRouter: %s", exc)

        # Reachability failure or empty list — the picker still needs options.
        return {"models": BUNDLED_FREE_MODELS, "source": "bundled-fallback", "mode": "live"}

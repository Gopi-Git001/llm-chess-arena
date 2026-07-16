"""OpenRouter client (PLAN.md §8).

Rate limits are per *account*, not per model, so the throttle is global and
shared by all three agents. Every request is counted; the quota meter and the
per-game kill-switch both depend on that number being honest.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

# 429 backoff ladder from §8.
RATE_LIMIT_BACKOFF_S = (10, 30, 60)
SERVER_ERROR_RETRIES = 2
SERVER_ERROR_BACKOFF_S = (2, 5)

EventHook = Callable[[str, dict[str, Any]], None]
SleepFn = Callable[[float], Awaitable[None]]
ClockFn = Callable[[], float]


class LLMError(RuntimeError):
    """Base class for anything that stops a completion from coming back."""


class RateLimitError(LLMError):
    """429s survived the whole backoff ladder."""


class ModelNotFoundError(LLMError):
    """The model 404'd and the fallback didn't save us."""


class EmptyResponseError(LLMError):
    """A 200 with no usable content — common on free tiers under load (§8)."""


class AuthError(LLMError):
    """401/403 — a bad or missing key. Retrying cannot help."""


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict[str, Any]


class Throttle:
    """Global minimum-interval gate.

    Serialises callers so requests are spaced by at least `min_interval_s`
    (default 4s ≈ 15/min, safely under the 20/min cap). `clock` and `sleep` are
    injectable so timing can be tested without real waits.
    """

    def __init__(
        self,
        min_interval_s: float,
        *,
        clock: ClockFn = time.monotonic,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_allowed: float | None = None

    async def acquire(self) -> float:
        """Block until the next request is allowed. Returns seconds waited."""
        # The lock makes the gate global: concurrent agents queue rather than
        # each independently deciding it's their turn.
        async with self._lock:
            now = self._clock()
            if self._next_allowed is None:
                self._next_allowed = now + self.min_interval_s
                return 0.0

            waited = 0.0
            delay = self._next_allowed - now
            if delay > 0:
                await self._sleep(delay)
                waited = delay
                now = self._clock()

            self._next_allowed = max(now, self._next_allowed) + self.min_interval_s
            return waited


class OpenRouterClient:
    """Async OpenAI-compatible client for OpenRouter's /chat/completions."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        throttle: Throttle | None = None,
        on_event: EventHook | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._on_event = on_event
        self._sleep = sleep
        self._owns_client = client is None
        self._requests_used = 0
        self.fallback_model = settings.openrouter.fallback_model

        self.throttle = throttle or Throttle(settings.throttle.min_seconds_between_requests)
        self._client = client or httpx.AsyncClient(
            base_url=settings.openrouter.base_url,
            timeout=settings.openrouter.request_timeout_s,
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        secrets = self.settings.secrets
        return {
            "Authorization": f"Bearer {secrets.openrouter_api_key}",
            # OpenRouter attribution best practice (§8).
            "HTTP-Referer": secrets.openrouter_site_url,
            "X-Title": secrets.openrouter_site_name,
            "Content-Type": "application/json",
        }

    @property
    def requests_used(self) -> int:
        """Every HTTP request made, retries included. Never lie about this."""
        return self._requests_used

    def _emit(self, type: str, data: dict[str, Any]) -> None:
        if self._on_event:
            self._on_event(type, data)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> OpenRouterClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 300,
        temperature: float = 0.7,
        json_object: bool = True,
    ) -> LLMResponse:
        """One completion, with the §8 retry ladder applied.

        Raises LLMError subclasses; callers decide whether that costs a retry.
        """
        active_model = model
        tried_fallback = False
        rate_limit_attempt = 0
        server_error_attempt = 0

        while True:
            payload: dict[str, Any] = {
                "model": active_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if json_object:
                # Honoured by models that support it; harmless on those that
                # don't, which is why parsing stays defensive regardless (§7).
                payload["response_format"] = {"type": "json_object"}

            await self.throttle.acquire()
            self._requests_used += 1

            try:
                response = await self._client.post("/chat/completions", json=payload)
            except httpx.TimeoutException as exc:
                raise LLMError(f"request to {active_model} timed out") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"request to {active_model} failed: {exc}") from exc

            status = response.status_code

            if status == 429:
                if rate_limit_attempt >= len(RATE_LIMIT_BACKOFF_S):
                    raise RateLimitError(
                        f"{active_model} rate limited after "
                        f"{len(RATE_LIMIT_BACKOFF_S)} backoffs"
                    )
                delay = RATE_LIMIT_BACKOFF_S[rate_limit_attempt]
                rate_limit_attempt += 1
                # Tell the UI why nothing is happening (§8).
                self._emit(
                    "RATE_LIMITED",
                    {
                        "model": active_model,
                        "retry_in_s": delay,
                        "attempt": rate_limit_attempt,
                        "requests_used": self._requests_used,
                    },
                )
                log.warning("429 from %s — backing off %ss", active_model, delay)
                await self._sleep(delay)
                continue

            if status in (401, 403):
                raise AuthError(f"OpenRouter rejected the API key ({status})")

            if status == 404:
                # The free lineup rotates; swap to the auto-router once (§8).
                if tried_fallback or active_model == self.fallback_model:
                    raise ModelNotFoundError(f"model {active_model!r} not found")
                log.warning("%s 404'd — falling back to %s", active_model, self.fallback_model)
                self._emit(
                    "ERROR",
                    {
                        "message": (
                            f"Model {active_model} is unavailable — "
                            f"falling back to {self.fallback_model}"
                        )
                    },
                )
                active_model = self.fallback_model
                tried_fallback = True
                continue

            if status >= 500:
                if server_error_attempt >= SERVER_ERROR_RETRIES:
                    raise LLMError(f"{active_model} returned {status} after retries")
                delay = SERVER_ERROR_BACKOFF_S[
                    min(server_error_attempt, len(SERVER_ERROR_BACKOFF_S) - 1)
                ]
                server_error_attempt += 1
                log.warning("%s from %s — retry %s", status, active_model, server_error_attempt)
                await self._sleep(delay)
                continue

            if status != 200:
                raise LLMError(f"{active_model} returned {status}: {response.text[:200]}")

            return self._parse_response(response, active_model)

    def _parse_response(self, response: httpx.Response, model: str) -> LLMResponse:
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError(f"{model} returned non-JSON body") from exc

        # OpenRouter surfaces upstream provider failures in a 200 body.
        if isinstance(body.get("error"), dict):
            message = body["error"].get("message", "unknown provider error")
            raise LLMError(f"{model} provider error: {message}")

        choices = body.get("choices") or []
        if not choices:
            raise EmptyResponseError(f"{model} returned no choices")

        content = (choices[0].get("message") or {}).get("content")
        if not content or not content.strip():
            # §8 empty-content guard: a failed attempt, not a crash.
            raise EmptyResponseError(f"{model} returned empty content")

        return LLMResponse(
            content=content,
            model=body.get("model", model),
            usage=body.get("usage") or {},
        )

    async def list_models(self) -> list[dict[str, Any]]:
        """GET /models. Used by verify_models.py and Phase 5's model picker."""
        await self.throttle.acquire()
        self._requests_used += 1
        response = await self._client.get("/models")
        response.raise_for_status()
        return response.json().get("data", [])

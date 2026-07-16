"""OpenRouterClient + Throttle tests (PLAN.md §8, §11).

Everything here is offline: httpx.MockTransport fakes OpenRouter, and the clock
and sleep are injected so timing is asserted exactly without real waiting. No
test in this file may make a network call or consume quota.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import load_settings
from app.llm_client import (
    AuthError,
    EmptyResponseError,
    LLMError,
    ModelNotFoundError,
    OpenRouterClient,
    RateLimitError,
    Throttle,
)


class FakeClock:
    """A clock that only advances when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def completion(content: str = '{"move": "e2e4", "reasoning": "ok"}', model: str = "test/model"):
    return {
        "id": "gen-1",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.fixture
def settings():
    settings = load_settings()
    # Never let a test depend on a real key being present.
    settings.secrets.openrouter_api_key = "sk-or-test-not-a-real-key"
    return settings


def build_client(settings, handler, **kwargs) -> OpenRouterClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://openrouter.test/api/v1")
    clock = kwargs.pop("clock", None)
    return OpenRouterClient(
        settings,
        client=http,
        throttle=kwargs.pop("throttle", Throttle(0)),
        sleep=(clock.sleep if clock else _no_sleep),
        **kwargs,
    )


async def _no_sleep(_seconds: float) -> None:
    return None


async def ask(client: OpenRouterClient, model: str = "test/model"):
    return await client.complete(model=model, messages=[{"role": "user", "content": "hi"}])


class TestThrottleTiming:
    async def test_first_request_never_waits(self):
        clock = FakeClock()
        throttle = Throttle(4, clock=clock.time, sleep=clock.sleep)

        assert await throttle.acquire() == 0.0
        assert clock.sleeps == []

    async def test_second_request_waits_the_full_interval(self):
        clock = FakeClock()
        throttle = Throttle(4, clock=clock.time, sleep=clock.sleep)

        await throttle.acquire()
        waited = await throttle.acquire()

        assert waited == pytest.approx(4)
        assert clock.sleeps == [pytest.approx(4)]

    async def test_time_already_spent_is_deducted_from_the_wait(self):
        clock = FakeClock()
        throttle = Throttle(4, clock=clock.time, sleep=clock.sleep)

        await throttle.acquire()
        clock.now += 3  # a slow request already burned 3s
        waited = await throttle.acquire()

        assert waited == pytest.approx(1), "should only wait the remaining second"

    async def test_no_wait_when_the_interval_has_already_passed(self):
        clock = FakeClock()
        throttle = Throttle(4, clock=clock.time, sleep=clock.sleep)

        await throttle.acquire()
        clock.now += 60
        waited = await throttle.acquire()

        assert waited == 0.0

    async def test_spacing_holds_across_many_requests(self):
        clock = FakeClock()
        throttle = Throttle(4, clock=clock.time, sleep=clock.sleep)

        stamps = []
        for _ in range(5):
            await throttle.acquire()
            stamps.append(clock.now)

        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert all(gap == pytest.approx(4) for gap in gaps)

    async def test_throttle_is_global_across_concurrent_callers(self):
        """Rate limits are per account, so all agents share one gate (§8)."""
        clock = FakeClock()
        throttle = Throttle(4, clock=clock.time, sleep=clock.sleep)

        await asyncio.gather(*(throttle.acquire() for _ in range(4)))

        # Four callers, three gaps of 4s each — not four simultaneous requests.
        assert clock.now == pytest.approx(12)

    async def test_zero_interval_disables_waiting(self):
        clock = FakeClock()
        throttle = Throttle(0, clock=clock.time, sleep=clock.sleep)

        for _ in range(3):
            await throttle.acquire()
        assert clock.sleeps == []


class TestRequestCounting:
    async def test_every_request_is_counted(self, settings):
        client = build_client(settings, lambda request: httpx.Response(200, json=completion()))

        assert client.requests_used == 0
        await ask(client)
        await ask(client)
        assert client.requests_used == 2

    async def test_retries_count_too(self, settings):
        """The quota meter must reflect reality, not intent."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429)
            return httpx.Response(200, json=completion())

        clock = FakeClock()
        client = build_client(settings, handler, clock=clock)
        await ask(client)

        assert client.requests_used == 3, "two 429s plus the success all cost quota"


class TestRateLimits:
    async def test_429_backs_off_then_succeeds(self, settings):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429) if calls["n"] == 1 else httpx.Response(200, json=completion())

        clock = FakeClock()
        client = build_client(settings, handler, clock=clock)
        response = await ask(client)

        assert response.content
        assert clock.sleeps == [10], "first backoff is 10s per §8"

    async def test_backoff_ladder_is_10_30_60(self, settings):
        clock = FakeClock()
        client = build_client(settings, lambda r: httpx.Response(429), clock=clock)

        with pytest.raises(RateLimitError):
            await ask(client)

        assert clock.sleeps == [10, 30, 60]

    async def test_rate_limit_emits_an_event_for_the_ui(self, settings):
        events = []
        clock = FakeClock()
        client = build_client(
            settings,
            lambda r: httpx.Response(429),
            clock=clock,
            on_event=lambda type, data: events.append((type, data)),
        )

        with pytest.raises(RateLimitError):
            await ask(client)

        assert [e[0] for e in events] == ["RATE_LIMITED"] * 3
        assert events[0][1]["retry_in_s"] == 10
        assert events[0][1]["model"] == "test/model"


class TestModelFallback:
    async def test_404_falls_back_to_the_auto_router(self, settings):
        """The free lineup rotates; a 404 must not end the game (§8)."""
        seen = []

        def handler(request):
            import json

            model = json.loads(request.content)["model"]
            seen.append(model)
            if model == "gone/model:free":
                return httpx.Response(404, json={"error": {"message": "no such model"}})
            return httpx.Response(200, json=completion(model=model))

        client = build_client(settings, handler)
        response = await ask(client, model="gone/model:free")

        assert seen == ["gone/model:free", settings.openrouter.fallback_model]
        assert response.model == settings.openrouter.fallback_model

    async def test_404_fallback_is_announced(self, settings):
        events = []
        client = build_client(
            settings,
            lambda r: httpx.Response(404),
            on_event=lambda type, data: events.append((type, data)),
        )

        with pytest.raises(ModelNotFoundError):
            await ask(client, model="gone/model:free")

        assert events[0][0] == "ERROR"
        assert "falling back" in events[0][1]["message"]

    async def test_404_on_the_fallback_itself_raises(self, settings):
        client = build_client(settings, lambda r: httpx.Response(404))

        with pytest.raises(ModelNotFoundError):
            await ask(client, model="gone/model:free")

    async def test_fallback_is_not_retried_forever(self, settings):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(404)

        client = build_client(settings, handler)
        with pytest.raises(ModelNotFoundError):
            await ask(client, model="gone/model:free")

        assert calls["n"] == 2, "original model, then the fallback, then give up"


class TestServerErrors:
    async def test_500_is_retried_then_succeeds(self, settings):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(500) if calls["n"] == 1 else httpx.Response(200, json=completion())

        clock = FakeClock()
        client = build_client(settings, handler, clock=clock)

        assert (await ask(client)).content
        assert calls["n"] == 2

    async def test_persistent_5xx_raises_after_two_retries(self, settings):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(503)

        clock = FakeClock()
        client = build_client(settings, handler, clock=clock)

        with pytest.raises(LLMError):
            await ask(client)
        assert calls["n"] == 3, "initial attempt + 2 retries"


class TestBadResponses:
    async def test_empty_content_is_flagged(self, settings):
        """Free models return empty completions under load (§8)."""
        client = build_client(settings, lambda r: httpx.Response(200, json=completion(content="")))

        with pytest.raises(EmptyResponseError):
            await ask(client)

    async def test_whitespace_only_content_is_flagged(self, settings):
        client = build_client(
            settings, lambda r: httpx.Response(200, json=completion(content="   \n  "))
        )

        with pytest.raises(EmptyResponseError):
            await ask(client)

    async def test_no_choices_is_flagged(self, settings):
        client = build_client(settings, lambda r: httpx.Response(200, json={"choices": []}))

        with pytest.raises(EmptyResponseError):
            await ask(client)

    async def test_provider_error_inside_a_200_is_raised(self, settings):
        client = build_client(
            settings,
            lambda r: httpx.Response(200, json={"error": {"message": "upstream is down"}}),
        )

        with pytest.raises(LLMError, match="upstream is down"):
            await ask(client)

    async def test_non_json_body_is_raised(self, settings):
        client = build_client(settings, lambda r: httpx.Response(200, text="<html>oops</html>"))

        with pytest.raises(LLMError):
            await ask(client)

    async def test_401_raises_auth_error_without_retrying(self, settings):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(401)

        client = build_client(settings, handler)
        with pytest.raises(AuthError):
            await ask(client)

        assert calls["n"] == 1, "retrying a bad key is pointless"

    async def test_timeout_becomes_an_llm_error(self, settings):
        def handler(request):
            raise httpx.TimeoutException("too slow")

        client = build_client(settings, handler)
        with pytest.raises(LLMError, match="timed out"):
            await ask(client)


class TestRequestShape:
    async def test_request_carries_attribution_headers_and_payload(self, settings):
        captured = {}

        def handler(request):
            import json

            captured["headers"] = request.headers
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=completion())

        transport = httpx.MockTransport(handler)
        http = httpx.AsyncClient(
            transport=transport,
            base_url="https://openrouter.test/api/v1",
            headers={
                "Authorization": "Bearer sk-or-test-not-a-real-key",
                "HTTP-Referer": settings.secrets.openrouter_site_url,
                "X-Title": settings.secrets.openrouter_site_name,
            },
        )
        client = OpenRouterClient(settings, client=http, throttle=Throttle(0), sleep=_no_sleep)
        await client.complete(
            model="test/model",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=123,
            temperature=0.7,
        )

        assert captured["headers"]["authorization"] == "Bearer sk-or-test-not-a-real-key"
        assert captured["headers"]["x-title"] == settings.secrets.openrouter_site_name
        assert captured["body"]["model"] == "test/model"
        assert captured["body"]["max_tokens"] == 123
        assert captured["body"]["temperature"] == 0.7
        assert captured["body"]["response_format"] == {"type": "json_object"}

    async def test_json_object_can_be_disabled(self, settings):
        captured = {}

        def handler(request):
            import json

            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=completion())

        client = build_client(settings, handler)
        await client.complete(
            model="test/model", messages=[{"role": "user", "content": "hi"}], json_object=False
        )

        assert "response_format" not in captured["body"]

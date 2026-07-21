"""Streaming completions for "Too Slow" live thinking (Feature 1).

The SSE assembler is tested purely, then end-to-end through a MockTransport that
serves a fake event stream. No network, no quota — the streamed path returns the
same LLMResponse shape as an ordinary completion so move handling is unchanged.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import load_settings
from app.llm_client import EmptyResponseError, OpenRouterClient, Throttle, sse_delta


def sse(*chunks: str) -> str:
    """Build an OpenAI-style SSE body from content deltas, then [DONE]."""
    lines = []
    for chunk in chunks:
        lines.append('data: {"choices":[{"delta":{"content":%s}}]}' % _json(chunk))
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def _json(text: str) -> str:
    import json

    return json.dumps(text)


@pytest.fixture
def settings():
    settings = load_settings()
    settings.secrets.openrouter_api_key = "sk-or-test-not-a-real-key"
    return settings


def build_client(settings, handler) -> OpenRouterClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://openrouter.test/api/v1")
    return OpenRouterClient(settings, client=http, throttle=Throttle(0), sleep=_no_sleep)


async def _no_sleep(_seconds: float) -> None:
    return None


class TestSseDelta:
    def test_pulls_content_out_of_a_data_line(self):
        assert sse_delta('data: {"choices":[{"delta":{"content":"hi"}}]}') == "hi"

    def test_ignores_done_keepalives_and_junk(self):
        assert sse_delta("data: [DONE]") is None
        assert sse_delta(": keep-alive comment") is None
        assert sse_delta("") is None
        assert sse_delta("data: not json") is None

    def test_ignores_a_delta_with_no_content(self):
        assert sse_delta('data: {"choices":[{"delta":{"role":"assistant"}}]}') is None


@pytest.mark.asyncio
class TestStreamingComplete:
    async def test_assembles_deltas_and_forwards_each_chunk(self, settings):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=sse("Let me ", "think about ", "e4."),
                headers={"content-type": "text/event-stream"},
            )

        client = build_client(settings, handler)
        chunks: list[str] = []
        response = await client.complete(
            model="test/model",
            messages=[{"role": "user", "content": "hi"}],
            token_sink=chunks.append,
        )

        assert response.content == "Let me think about e4."
        assert chunks == ["Let me ", "think about ", "e4."]
        # A streamed request is still exactly one request against the budget.
        assert client.requests_used == 1

    async def test_streamed_request_sets_the_stream_flag(self, settings):
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen.update(json.loads(request.content))
            return httpx.Response(200, text=sse("ok "), headers={"content-type": "text/event-stream"})

        client = build_client(settings, handler)
        await client.complete(
            model="test/model",
            messages=[{"role": "user", "content": "hi"}],
            token_sink=lambda _c: None,
        )
        assert seen["stream"] is True

    async def test_empty_stream_is_an_empty_response_error(self, settings):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

        client = build_client(settings, handler)
        with pytest.raises(EmptyResponseError):
            await client.complete(
                model="test/model",
                messages=[{"role": "user", "content": "hi"}],
                token_sink=lambda _c: None,
            )

    async def test_a_token_sink_that_raises_never_aborts_the_stream(self, settings):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text=sse("a ", "b ", "c"), headers={"content-type": "text/event-stream"}
            )

        client = build_client(settings, handler)

        def angry_sink(_chunk: str) -> None:
            raise RuntimeError("UI blew up")

        response = await client.complete(
            model="test/model",
            messages=[{"role": "user", "content": "hi"}],
            token_sink=angry_sink,
        )
        # The content is still fully assembled despite the sink throwing.
        assert response.content == "a b c"

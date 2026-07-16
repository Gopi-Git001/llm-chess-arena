"""ModelCatalog tests (PLAN.md §10). Mock mode must stay fully offline."""

from __future__ import annotations

import httpx
import pytest

from app.config import load_settings
from app.models_catalog import BUNDLED_FREE_MODELS, ModelCatalog


@pytest.fixture
def mock_settings():
    settings = load_settings()
    settings.mode = "mock"
    return settings


@pytest.fixture
def live_settings():
    settings = load_settings()
    settings.mode = "live"
    settings.secrets.openrouter_api_key = "sk-or-test-not-a-real-key"
    return settings


class TestMockMode:
    async def test_returns_the_bundled_list_without_any_network(self, mock_settings):
        # block_network (conftest) would raise on any real request; if this
        # passes, the mock path made no call.
        result = await ModelCatalog(mock_settings).list_models()

        assert result["source"] == "bundled"
        assert result["mode"] == "mock"
        assert result["models"] == BUNDLED_FREE_MODELS
        assert any(m["id"] == "openrouter/free" for m in result["models"])

    async def test_every_bundled_model_has_the_picker_shape(self, mock_settings):
        for model in (await ModelCatalog(mock_settings).list_models())["models"]:
            assert set(model) == {"id", "name", "context_length"}
            assert model["id"] and model["name"]


class TestLiveMode:
    def _catalog(self, settings, handler):
        catalog = ModelCatalog(settings)
        # Patch the client factory used inside list_models.
        import app.models_catalog as mod

        real_client = mod.OpenRouterClient

        def make_client(s):
            http = httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="https://openrouter.test/api/v1",
            )
            return real_client(s, client=http)

        mod.OpenRouterClient = make_client
        catalog._restore = lambda: setattr(mod, "OpenRouterClient", real_client)
        return catalog

    async def test_proxies_and_filters_to_free(self, live_settings, monkeypatch):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "free/a", "name": "A", "context_length": 100,
                         "pricing": {"prompt": "0", "completion": "0"}},
                        {"id": "paid/b", "name": "B", "context_length": 100,
                         "pricing": {"prompt": "0.001", "completion": "0"}},
                        {"id": "free/c", "name": "C", "context_length": 100,
                         "pricing": {"prompt": "0", "completion": "0"}},
                    ]
                },
            )

        catalog = self._catalog(live_settings, handler)
        try:
            result = await catalog.list_models()
        finally:
            catalog._restore()

        ids = [m["id"] for m in result["models"]]
        assert result["source"] == "openrouter"
        assert ids == ["free/a", "free/c"], "paid model filtered out, sorted by id"

    async def test_second_call_is_cached(self, live_settings):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(
                200,
                json={"data": [{"id": "free/a", "pricing": {"prompt": "0", "completion": "0"}}]},
            )

        catalog = self._catalog(live_settings, handler)
        try:
            await catalog.list_models()
            second = await catalog.list_models()
        finally:
            catalog._restore()

        assert calls["n"] == 1, "the 1h cache should prevent a second fetch"
        assert second["source"] == "cache"

    async def test_falls_back_to_bundled_when_openrouter_is_unreachable(self, live_settings):
        def handler(request):
            raise httpx.ConnectError("no route to host")

        catalog = self._catalog(live_settings, handler)
        try:
            result = await catalog.list_models()
        finally:
            catalog._restore()

        assert result["source"] == "bundled-fallback"
        assert result["models"] == BUNDLED_FREE_MODELS

    async def test_empty_free_list_falls_back(self, live_settings):
        def handler(request):
            return httpx.Response(
                200,
                json={"data": [{"id": "paid/only", "pricing": {"prompt": "1", "completion": "1"}}]},
            )

        catalog = self._catalog(live_settings, handler)
        try:
            result = await catalog.list_models()
        finally:
            catalog._restore()

        assert result["source"] == "bundled-fallback"

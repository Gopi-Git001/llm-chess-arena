"""The live code path, exercised offline.

PLAN.md's Phase 3 verify ends with one real game. That costs quota and needs
explicit approval, so these tests run the *same* wiring — PlayerAgent →
OpenRouterClient → orchestrator → store → events — against a fake transport
standing in for OpenRouter. They prove the plumbing works; only the model's
actual chess ability is left unverified.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest

from app.agents.player import PlayerAgent
from app.config import load_settings
from app.engine import ChessEngine
from app.events import EventType
from app.llm_client import OpenRouterClient, Throttle
from app.orchestrator import GameOrchestrator
from app.store import GameStore

LEGAL_LINE_RE = re.compile(r"choose EXACTLY one from this list:\n([a-h1-8qrbn ]+)", re.I)


@pytest.fixture
def store():
    store = GameStore(":memory:")
    yield store
    store.close()


@pytest.fixture
def settings():
    settings = load_settings()
    settings.secrets.openrouter_api_key = "sk-or-test-not-a-real-key"
    return settings


def legal_moves_from_prompt(prompt: str) -> list[str]:
    """The fake model reads the legal list out of the prompt, like a good model
    would. If the prompt didn't contain one, that itself is the bug."""
    match = LEGAL_LINE_RE.search(prompt)
    assert match, f"prompt is missing the legal move list:\n{prompt}"
    return match.group(1).split()


async def _no_sleep(_s: float) -> None:
    return None


def build_client(settings, handler, on_event=None) -> OpenRouterClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.test/api/v1"
    )
    return OpenRouterClient(
        settings, client=http, throttle=Throttle(0), sleep=_no_sleep, on_event=on_event
    )


def make_game(store, settings, handler, on_event=None, analyst=None, **kwargs) -> GameOrchestrator:
    client = build_client(settings, handler, on_event=on_event)
    game_id = store.create_game("fake/white:free", "fake/black:free", "fake/analyst", "live")
    orchestrator = GameOrchestrator(
        game_id=game_id,
        white=PlayerAgent("fake/white:free", "white", client),
        black=PlayerAgent("fake/black:free", "black", client),
        analyst=analyst,
        store=store,
        seed=1,
        request_counter=lambda: client.requests_used,
        **kwargs,
    )
    return orchestrator


def obedient_analyst(request: httpx.Request) -> httpx.Response:
    """Plays a legal move when asked for one, and returns a valid verdict when
    asked to review — so one handler serves both players and the analyst."""
    body = json.loads(request.content)
    prompt = body["messages"][-1]["content"]
    if "Legal moves" in prompt:  # a player turn
        return obedient_model(request)
    verdict = {
        "winner": "white",
        "result_explanation": "White prevailed.",
        "key_moments": ["1. move", "2. move", "3. move"],
        "white_grade": "B",
        "black_grade": "C",
        "verdict_paragraph": "A game happened.",
    }
    return httpx.Response(
        200, json={"model": body["model"], "choices": [{"message": {"content": json.dumps(verdict)}}]}
    )


def obedient_model(request: httpx.Request) -> httpx.Response:
    """Always answers with a legal move in the required JSON."""
    body = json.loads(request.content)
    legal = legal_moves_from_prompt(body["messages"][-1]["content"])
    return httpx.Response(
        200,
        json={
            "model": body["model"],
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"move": legal[0], "reasoning": "By the book."})
                    }
                }
            ],
        },
    )


class TestFullLiveGameOffline:
    async def test_a_game_completes_through_the_live_path(self, store, settings):
        orch = make_game(store, settings, obedient_model, max_moves=20)
        summary = await orch.run()

        assert summary["status"] == "finished"
        assert summary["result"] in ("1-0", "0-1", "1/2-1/2")
        assert ChessEngine.is_valid_pgn(summary["pgn"])
        assert summary["ply_count"] > 0

    async def test_requests_used_is_reported_honestly(self, store, settings):
        orch = make_game(store, settings, obedient_model, max_moves=10)
        summary = await orch.run()

        # One clean request per move — this is the number the quota meter and
        # the kill-switch both trust.
        assert summary["requests_used"] == 10
        assert store.get_game(orch.game_id)["requests_used"] == 10

    async def test_quota_appears_in_the_event_stream(self, store, settings):
        orch = make_game(store, settings, obedient_model, max_moves=6)
        await orch.run()

        moves = [e for e in orch.bus.history if e.type == EventType.MOVE_MADE]
        assert [m.data["requests_used"] for m in moves] == [1, 2, 3, 4, 5, 6]

    async def test_model_reasoning_reaches_the_database(self, store, settings):
        orch = make_game(store, settings, obedient_model, max_moves=4)
        await orch.run()

        assert all(m["reasoning"] == "By the book." for m in store.get_moves(orch.game_id))

    async def test_persisted_requests_used_includes_the_analysts_calls(self, store, settings):
        """Regression from the live run: finish_game wrote the count before the
        analyst ran, so the persisted total under-reported by the analyst's
        request(s). The DB tally must match the live counter after the verdict."""
        from app.agents.analyst import AnalystAgent

        client = build_client(settings, obedient_analyst)
        game_id = store.create_game("fake/white:free", "fake/black:free", "fake/analyst", "live")
        orch = GameOrchestrator(
            game_id=game_id,
            white=PlayerAgent("fake/white:free", "white", client),
            black=PlayerAgent("fake/black:free", "black", client),
            analyst=AnalystAgent("fake/analyst", client),
            store=store,
            seed=1,
            request_counter=lambda: client.requests_used,
            max_moves=4,
        )
        summary = await orch.run()

        persisted = store.get_game(game_id)["requests_used"]
        assert persisted == client.requests_used, "DB tally must match the live counter"
        assert persisted > summary["requests_used"], "analyst calls land after the summary"
        assert persisted > 4, "4 moves + at least one analyst call"


class TestMisbehavingModelEndToEnd:
    async def test_a_model_that_only_proposes_illegal_moves_still_finishes(self, store, settings):
        """The §2.3 promise: 3 strikes → random legal move, game always ends."""

        def stubborn(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "choices": [{"message": {"content": '{"move": "a1a1"}'}}],
                },
            )

        orch = make_game(store, settings, stubborn, max_moves=6)
        summary = await orch.run()

        assert summary["status"] == "finished"
        assert ChessEngine.is_valid_pgn(summary["pgn"])

        moves = store.get_moves(orch.game_id)
        assert all(m["forfeited"] == 1 for m in moves), "every turn should forfeit"
        forfeit_events = [e for e in orch.bus.history if e.type == EventType.MOVE_FORFEITED]
        assert len(forfeit_events) == len(moves)

        # Every substituted move must still be legal.
        replay = ChessEngine()
        for move in moves:
            replay.push_uci(move["uci"])

    async def test_illegal_attempts_are_logged_per_model(self, store, settings):
        """§11 Phase 3: 'how many illegal attempts per model?' is the tuning
        signal, so the data has to be there to inspect."""

        def flaky(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            prompt = body["messages"][-1]["content"]
            # Obey only after being told off once.
            if "ILLEGAL" in prompt:
                move = legal_moves_from_prompt(prompt)[0]
                content = json.dumps({"move": move, "reasoning": "Sorry."})
            else:
                content = '{"move": "e2e5"}'
            return httpx.Response(
                200, json={"model": body["model"], "choices": [{"message": {"content": content}}]}
            )

        orch = make_game(store, settings, flaky, max_moves=6)
        await orch.run()

        moves = store.get_moves(orch.game_id)
        assert all(m["attempts"] == 2 for m in moves)
        assert all(m["forfeited"] == 0 for m in moves)

        attempts = [e for e in orch.bus.history if e.type == EventType.ILLEGAL_ATTEMPT]
        assert len(attempts) == len(moves)
        assert all(e.data["uci"] == "e2e5" for e in attempts)

    async def test_rate_limiting_invokes_the_clients_event_hook(self, store, settings):
        calls = {"n": 0}

        def rate_limited_once(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429)
            return obedient_model(request)

        events: list[tuple[str, dict]] = []
        orch = make_game(
            store,
            settings,
            rate_limited_once,
            on_event=lambda t, d: events.append((t, d)),
            max_moves=2,
        )
        await orch.run()

        assert events[0][0] == "RATE_LIMITED"
        assert events[0][1]["retry_in_s"] == 10

    async def test_kill_switch_stops_a_runaway_game(self, store, settings):
        orch = make_game(store, settings, obedient_model, max_moves=100, max_requests_per_game=5)
        summary = await orch.run()

        assert summary["status"] == "aborted"
        assert summary["requests_used"] <= 6, "must stop near the budget, not blow past it"
        assert any(e.type == EventType.ERROR for e in orch.bus.history)


class TestManagerLiveWiring:
    """GameManager is what connects the client's hook to the event stream *and*
    the database. An event that reaches the WS but not SQLite disappears on
    refresh — the Phase 2 bug, in a new costume."""

    @pytest.fixture
    def live_settings(self, settings):
        live = settings.model_copy(deep=True)
        live.mode = "live"
        live.secrets.openrouter_api_key = "sk-or-test-not-a-real-key"
        return live

    async def test_live_mode_builds_real_player_agents(self, store, live_settings, monkeypatch):
        from app.manager import GameManager

        monkeypatch.setattr(
            "app.manager.OpenRouterClient",
            lambda cfg, on_event=None: build_client(cfg, obedient_model, on_event=on_event),
        )
        manager = GameManager(store, live_settings)
        session = manager.create(game_settings=None)

        assert isinstance(session.orchestrator.white, PlayerAgent)
        assert isinstance(session.orchestrator.black, PlayerAgent)
        # One client, one throttle — limits are per account, not per model (§8).
        assert session.orchestrator.white.client is session.orchestrator.black.client

    async def test_rate_limit_event_is_streamed_and_persisted(
        self, store, live_settings, monkeypatch
    ):
        calls = {"n": 0}

        def rate_limited_once(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429) if calls["n"] == 1 else obedient_model(request)

        from app.manager import GameManager, GameSettings

        monkeypatch.setattr(
            "app.manager.OpenRouterClient",
            lambda cfg, on_event=None: build_client(cfg, rate_limited_once, on_event=on_event),
        )
        manager = GameManager(store, live_settings)
        session = manager.create(game_settings=GameSettings(max_moves=2, move_delay_ms=0))
        await session.orchestrator.run()

        streamed = [e for e in session.bus.history if e.type == EventType.RATE_LIMITED]
        persisted = [e for e in store.get_events(session.game_id) if e["type"] == "RATE_LIMITED"]

        assert len(streamed) == 1
        assert persisted == [e.model_dump(mode="json") for e in streamed], (
            "a rate-limit event must survive a refresh, not just flash on the WS"
        )

    async def test_live_game_reports_real_quota_through_the_api_shape(
        self, store, live_settings, monkeypatch
    ):
        from app.manager import GameManager, GameSettings

        monkeypatch.setattr(
            "app.manager.OpenRouterClient",
            lambda cfg, on_event=None: build_client(cfg, obedient_model, on_event=on_event),
        )
        manager = GameManager(store, live_settings)
        session = manager.create(game_settings=GameSettings(max_moves=4, move_delay_ms=0))
        summary = await session.orchestrator.run()

        assert summary["requests_used"] == 4
        assert store.get_game(session.game_id)["mode"] == "live"

"""API tests — REST surface (§10) + the WS event stream, all in mock mode.

Rehydration is the headline requirement (§13: "reload mid-game restores exact
state"), so it gets tested directly rather than only through the browser.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.engine import ChessEngine


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh app instance per test, with its own on-disk DB."""
    import app.main as main_module
    from app.config import load_settings
    from app.manager import GameManager
    from app.store import GameStore

    settings = load_settings()
    assert settings.mode == "mock", "tests must never hit the live API"

    store = GameStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "manager", GameManager(store, settings))

    with TestClient(main_module.app) as client:
        yield client
    store.close()


def new_game(client, **settings):
    body = {"settings": {"move_delay_ms": 0, "seed": 1, **settings}}
    response = client.post("/api/games", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def wait_for_finish(client, game_id, timeout=15.0):
    """Poll until the background game task completes."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        game = client.get(f"/api/games/{game_id}").json()
        if game["status"] in ("finished", "aborted", "error"):
            return game
        time.sleep(0.05)
    raise AssertionError(f"game {game_id} did not finish within {timeout}s")


class TestHealth:
    def test_health_reports_mode_without_leaking_the_key(self, client):
        body = client.get("/health").json()

        assert body["status"] == "ok"
        assert body["mode"] == "mock"
        assert isinstance(body["api_key_configured"], bool)
        assert body["max_requests_per_game"] > 0
        # The key itself must never appear anywhere in the payload.
        assert "sk-or" not in str(body)

    def test_health_is_also_served_under_the_api_prefix(self, client):
        assert client.get("/api/health").status_code == 200


class TestModels:
    def test_models_endpoint_returns_a_free_list(self, client):
        body = client.get("/api/models").json()

        assert body["mode"] == "mock"
        assert body["source"] == "bundled"
        assert len(body["models"]) > 0
        assert all({"id", "name", "context_length"} == set(m) for m in body["models"])
        assert any(m["id"] == "openrouter/free" for m in body["models"])


class TestCreateGame:
    def test_create_returns_a_game_id_and_starts_playing(self, client):
        created = new_game(client)

        assert created["mode"] == "mock"
        game = wait_for_finish(client, created["game_id"])
        assert game["status"] == "finished"
        assert game["result"] in ("1-0", "0-1", "1/2-1/2")

    def test_requested_model_names_are_used_as_labels(self, client):
        response = client.post(
            "/api/games",
            json={
                "white_model": "custom/white:free",
                "black_model": "custom/black:free",
                "settings": {"move_delay_ms": 0},
            },
        )
        body = response.json()

        assert body["white_model"] == "custom/white:free"
        assert body["black_model"] == "custom/black:free"

    def test_models_default_to_config(self, client):
        from app.config import get_settings

        body = new_game(client)
        assert body["white_model"] == get_settings().openrouter.white_model

    @pytest.mark.parametrize(
        "bad_settings",
        [
            {"max_moves": 0},
            {"max_moves": 5000},
            {"illegal_rate": 1.5},
            {"move_delay_ms": -1},
            {"illegal_move_retries": 0},
        ],
    )
    def test_invalid_settings_are_rejected(self, client, bad_settings):
        response = client.post("/api/games", json={"settings": bad_settings})
        assert response.status_code == 422


class TestGetGame:
    def test_unknown_game_is_404(self, client):
        assert client.get("/api/games/nope").status_code == 404

    def test_full_game_carries_moves_events_and_verdict_slot(self, client):
        created = new_game(client, max_moves=10)
        wait_for_finish(client, created["game_id"])

        game = client.get(f"/api/games/{created['game_id']}").json()
        assert len(game["moves"]) == 10
        assert game["events"][0]["type"] == "GAME_STARTED"
        # Since Phase 4 the verdict is the last word, after GAME_OVER.
        assert game["events"][-1]["type"] == "VERDICT"
        assert game["verdict"] is not None
        assert game["is_running"] is False

    def test_rehydrated_moves_replay_to_the_stored_final_position(self, client):
        """§13: a reload must restore the exact state, not an approximation."""
        created = new_game(client, max_moves=20)
        game = wait_for_finish(client, created["game_id"])

        replay = ChessEngine()
        for move in client.get(f"/api/games/{created['game_id']}").json()["moves"]:
            replay.push_uci(move["uci"])
        assert replay.fen == game["final_fen"]

    def test_list_games_includes_the_new_game(self, client):
        created = new_game(client, max_moves=4)
        wait_for_finish(client, created["game_id"])

        games = client.get("/api/games").json()["games"]
        assert created["game_id"] in [g["id"] for g in games]


class TestAbort:
    def test_abort_unknown_game_is_404(self, client):
        assert client.post("/api/games/nope/abort").status_code == 404

    def test_abort_stops_a_running_game(self, client):
        # A slow game is still running when the abort lands.
        created = new_game(client, move_delay_ms=200, max_moves=120)
        response = client.post(f"/api/games/{created['game_id']}/abort")

        assert response.status_code == 200
        assert response.json()["status"] == "aborted"

    def test_aborting_a_finished_game_is_harmless(self, client):
        created = new_game(client, max_moves=4)
        wait_for_finish(client, created["game_id"])

        response = client.post(f"/api/games/{created['game_id']}/abort")
        assert response.status_code == 200

    def test_abort_emits_game_over_so_the_ui_can_update(self, client):
        """Regression: abort sent no terminal event, so the live UI froze until
        a refresh. The event stream must carry GAME_OVER(status=aborted)."""
        created = new_game(client, move_delay_ms=200, max_moves=120)
        wait_for_moves(client, created["game_id"], 1)
        client.post(f"/api/games/{created['game_id']}/abort")
        wait_for_finish(client, created["game_id"])

        events = client.get(f"/api/games/{created['game_id']}").json()["events"]
        overs = [e for e in events if e["type"] == "GAME_OVER"]
        assert len(overs) == 1
        assert overs[0]["data"]["status"] == "aborted"


class TestEventStream:
    def test_ws_streams_a_game_from_start_to_finish(self, client):
        created = new_game(client, max_moves=10)

        with client.websocket_connect(f"/ws/games/{created['game_id']}") as ws:
            types = []
            while True:
                event = ws.receive_json()
                types.append(event["type"])
                if event["type"] == "GAME_OVER":
                    break

        assert types[0] == "GAME_STARTED"
        assert types.count("MOVE_MADE") == 10

    def test_ws_replays_history_to_a_late_subscriber(self, client):
        """Connecting mid-game must not lose the moves already played."""
        created = new_game(client, move_delay_ms=60, max_moves=120)
        wait_for_moves(client, created["game_id"], 3)

        with client.websocket_connect(f"/ws/games/{created['game_id']}") as ws:
            first = ws.receive_json()

        assert first["type"] == "GAME_STARTED", "late subscriber must get the backlog"
        assert first["seq"] == 1

    def test_ws_since_parameter_skips_replayed_events(self, client):
        created = new_game(client, move_delay_ms=60, max_moves=120)
        wait_for_moves(client, created["game_id"], 3)

        with client.websocket_connect(f"/ws/games/{created['game_id']}?since=2") as ws:
            first = ws.receive_json()

        assert first["seq"] == 3

    def test_ws_seq_is_gap_free_across_replay_and_live(self, client):
        created = new_game(client, move_delay_ms=20, max_moves=8)
        wait_for_moves(client, created["game_id"], 2)

        with client.websocket_connect(f"/ws/games/{created['game_id']}") as ws:
            seqs = []
            while True:
                event = ws.receive_json()
                seqs.append(event["seq"])
                if event["type"] == "GAME_OVER":
                    break

        assert seqs == list(range(1, len(seqs) + 1)), "no gaps, no duplicates"

    def test_ws_serves_a_finished_game_from_the_database(self, client):
        """Games from a previous process have no session — SQLite must serve."""
        created = new_game(client, max_moves=6)
        wait_for_finish(client, created["game_id"])

        import app.main as main_module

        main_module.manager._sessions.clear()  # simulate a backend restart

        with client.websocket_connect(f"/ws/games/{created['game_id']}") as ws:
            types = []
            try:
                while True:
                    types.append(ws.receive_json()["type"])
            except Exception:
                pass

        assert types[0] == "GAME_STARTED"
        assert "GAME_OVER" in types

    def test_ws_rejects_an_unknown_game(self, client):
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/games/ghost") as ws:
                ws.receive_json()
        assert exc.value.code == 4404

    def test_events_endpoint_mirrors_the_stream(self, client):
        created = new_game(client, max_moves=6)
        wait_for_finish(client, created["game_id"])

        events = client.get(f"/api/games/{created['game_id']}/events").json()["events"]
        since = client.get(f"/api/games/{created['game_id']}/events?since=2").json()["events"]

        assert events[0]["seq"] == 1
        assert since[0]["seq"] == 3
        assert len(since) == len(events) - 2


class TestIllegalMoveVisibility:
    def test_illegal_attempts_reach_the_api(self, client):
        """§13: every illegal attempt visible in the UI and stored in the DB."""
        created = new_game(client, max_moves=12, illegal_rate=0.9, forfeit_rate=0.3)
        wait_for_finish(client, created["game_id"])

        game = client.get(f"/api/games/{created['game_id']}").json()
        types = [e["type"] for e in game["events"]]

        assert "ILLEGAL_ATTEMPT" in types
        assert any(m["attempts"] > 1 for m in game["moves"])

    def test_forfeits_are_visible_in_rehydrated_events(self, client):
        created = new_game(client, max_moves=12, illegal_rate=0.9, forfeit_rate=1.0)
        wait_for_finish(client, created["game_id"])

        game = client.get(f"/api/games/{created['game_id']}").json()
        forfeits = [e for e in game["events"] if e["type"] == "MOVE_FORFEITED"]

        assert forfeits, "a forfeited game must expose MOVE_FORFEITED on rehydration"
        assert forfeits[0]["data"]["color"] in ("white", "black")
        assert len(forfeits) == len([m for m in game["moves"] if m["forfeited"]])


class TestVerdict:
    def test_finished_game_carries_a_verdict(self, client):
        """§11 Phase 4 verify: mock mode produces a template verdict."""
        created = new_game(client, max_moves=10)
        wait_for_finish(client, created["game_id"])

        verdict = client.get(f"/api/games/{created['game_id']}").json()["verdict"]

        assert verdict is not None
        assert verdict["source"] == "template"
        assert verdict["engine_result"] in ("1-0", "0-1", "1/2-1/2")
        assert verdict["verdict_paragraph"]
        assert verdict["white_grade"] and verdict["black_grade"]
        assert len(verdict["key_moments"]) >= 1

    def test_verdict_matches_the_engine_result(self, client):
        created = new_game(client, max_moves=10)
        game = wait_for_finish(client, created["game_id"])
        verdict = client.get(f"/api/games/{created['game_id']}").json()["verdict"]

        assert verdict["engine_result"] == game["result"]
        assert verdict["engine_termination"] == game["termination"]

    def test_verdict_arrives_over_the_websocket_last(self, client):
        created = new_game(client, max_moves=8)

        with client.websocket_connect(f"/ws/games/{created['game_id']}") as ws:
            types = []
            while True:
                event = ws.receive_json()
                types.append(event["type"])
                if event["type"] == "VERDICT":
                    break

        assert types[-1] == "VERDICT"
        assert types[-2] == "GAME_OVER"

    def test_verdict_survives_a_refresh(self, client):
        created = new_game(client, max_moves=8)
        wait_for_finish(client, created["game_id"])

        first = client.get(f"/api/games/{created['game_id']}").json()["verdict"]
        second = client.get(f"/api/games/{created['game_id']}").json()["verdict"]
        assert first == second is not None

    def test_commentary_is_off_by_default(self, client):
        created = new_game(client, max_moves=10)
        wait_for_finish(client, created["game_id"])

        events = client.get(f"/api/games/{created['game_id']}").json()["events"]
        assert not [e for e in events if e["type"] == "COMMENTARY"]

    def test_commentary_can_be_requested(self, client):
        created = new_game(client, max_moves=10, commentary_every_n_moves=5)
        wait_for_finish(client, created["game_id"])

        events = client.get(f"/api/games/{created['game_id']}").json()["events"]
        comments = [e for e in events if e["type"] == "COMMENTARY"]

        assert len(comments) == 2
        assert comments[0]["data"]["text"]

    @pytest.mark.parametrize("bad", [{"commentary_every_n_moves": -1}, {"commentary_every_n_moves": 99}])
    def test_invalid_commentary_settings_are_rejected(self, client, bad):
        assert client.post("/api/games", json={"settings": bad}).status_code == 422


class TestRestAndWebsocketAgree:
    """Rehydration and the live stream must be interchangeable (§9)."""

    def test_rest_events_match_the_websocket_events_exactly(self, client):
        """Regression: REST returned `payload`, the WS sent `data`. The client
        reads `data`, so rehydrating a game that had any ILLEGAL_ATTEMPT or
        MOVE_FORFEITED event threw and wedged the reconnect loop."""
        created = new_game(client, max_moves=8, illegal_rate=0.9, forfeit_rate=0.5)

        with client.websocket_connect(f"/ws/games/{created['game_id']}") as ws:
            streamed = []
            while True:
                event = ws.receive_json()
                streamed.append(event)
                # VERDICT, not GAME_OVER, is the final event since Phase 4.
                if event["type"] == "VERDICT":
                    break

        rehydrated = client.get(f"/api/games/{created['game_id']}").json()["events"]
        assert rehydrated == streamed

    def test_every_event_exposes_a_data_object(self, client):
        created = new_game(client, max_moves=8, illegal_rate=0.9, forfeit_rate=0.5)
        wait_for_finish(client, created["game_id"])

        for event in client.get(f"/api/games/{created['game_id']}").json()["events"]:
            assert set(event) == {"type", "game_id", "seq", "ts", "data"}
            assert isinstance(event["data"], dict)


def wait_for_moves(client, game_id, count, timeout=15.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        game = client.get(f"/api/games/{game_id}").json()
        if len(game["moves"]) >= count:
            return game
        time.sleep(0.02)
    raise AssertionError(f"game {game_id} did not reach {count} moves within {timeout}s")

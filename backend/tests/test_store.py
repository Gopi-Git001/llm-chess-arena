"""GameStore tests — persistence is what makes refresh-proof rehydration work."""

from __future__ import annotations

import pytest

from app.engine import ChessEngine
from app.events import Event, EventType
from app.store import GameStore


@pytest.fixture
def store() -> GameStore:
    store = GameStore(":memory:")
    yield store
    store.close()


@pytest.fixture
def game_id(store: GameStore) -> str:
    return store.create_game("white-model", "black-model", "analyst-model", "mock")


class TestGames:
    def test_new_game_starts_pending(self, store, game_id):
        game = store.get_game(game_id)
        assert game["status"] == "pending"
        assert game["white_model"] == "white-model"
        assert game["result"] is None
        assert game["requests_used"] == 0

    def test_missing_game_returns_none(self, store):
        assert store.get_game("nope") is None
        assert store.get_full_game("nope") is None

    def test_ids_are_unique(self, store):
        ids = {store.create_game("w", "b", "a", "mock") for _ in range(20)}
        assert len(ids) == 20

    def test_finish_game_records_the_result(self, store, game_id):
        store.finish_game(game_id, "1-0", "checkmate", "fen-here", "pgn-here", requests_used=42)
        game = store.get_game(game_id)

        assert game["status"] == "finished"
        assert game["result"] == "1-0"
        assert game["termination"] == "checkmate"
        assert game["requests_used"] == 42

    def test_list_games_is_newest_first(self, store):
        first = store.create_game("w1", "b1", "a", "mock")
        second = store.create_game("w2", "b2", "a", "mock")
        assert [g["id"] for g in store.list_games()][:2] == [second, first]


class TestMoves:
    def test_move_round_trips_with_metadata(self, store, game_id):
        engine = ChessEngine()
        engine.push_uci("e2e4")
        engine.push_uci("d7d5")
        record = engine.push_uci("e4d5")

        store.add_move(game_id, record, reasoning="grabbing a pawn", attempts=2, forfeited=False)
        row = store.get_moves(game_id)[0]

        assert row["san"] == "exd5"
        assert row["reasoning"] == "grabbing a pawn"
        assert row["attempts"] == 2
        assert row["forfeited"] == 0
        assert row["is_capture"] == 1
        assert row["captured_piece"] == "p"

    def test_moves_come_back_in_ply_order(self, store, game_id):
        engine = ChessEngine()
        for uci in ["e2e4", "e7e5", "g1f3"]:
            store.add_move(game_id, engine.push_uci(uci))

        assert [m["ply"] for m in store.get_moves(game_id)] == [1, 2, 3]
        assert [m["san"] for m in store.get_moves(game_id)] == ["e4", "e5", "Nf3"]

    def test_duplicate_ply_is_rejected(self, store, game_id):
        import sqlite3

        engine = ChessEngine()
        record = engine.push_uci("e2e4")
        store.add_move(game_id, record)
        with pytest.raises(sqlite3.IntegrityError):
            store.add_move(game_id, record)

    def test_move_for_unknown_game_is_rejected(self, store):
        import sqlite3

        engine = ChessEngine()
        with pytest.raises(sqlite3.IntegrityError):
            store.add_move("ghost-game", engine.push_uci("e2e4"))


class TestEvents:
    def test_event_payload_round_trips_as_json(self, store, game_id):
        event = Event(
            type=EventType.MOVE_MADE,
            game_id=game_id,
            seq=1,
            data={"uci": "e2e4", "forfeited": False, "attempts": 1},
        )
        store.add_event(event)
        row = store.get_events(game_id)[0]

        assert row["type"] == "MOVE_MADE"
        assert row["data"] == {"uci": "e2e4", "forfeited": False, "attempts": 1}

    def test_stored_events_come_back_in_the_websocket_shape(self, store, game_id):
        """Regression: the store used to return the DB's `payload` column while
        the WS sent `data`, so REST rehydration and the live stream disagreed
        about the same event and the client blew up reading event.data."""
        event = Event(
            type=EventType.ILLEGAL_ATTEMPT,
            game_id=game_id,
            seq=1,
            data={"color": "white", "uci": "e2e5"},
        )
        store.add_event(event)

        assert store.get_events(game_id)[0] == event.model_dump(mode="json")

    def test_events_come_back_in_seq_order(self, store, game_id):
        for seq in (3, 1, 2):
            store.add_event(Event(type=EventType.COMMENTARY, game_id=game_id, seq=seq))
        assert [e["seq"] for e in store.get_events(game_id)] == [1, 2, 3]

    def test_duplicate_seq_is_rejected(self, store, game_id):
        import sqlite3

        store.add_event(Event(type=EventType.COMMENTARY, game_id=game_id, seq=1))
        with pytest.raises(sqlite3.IntegrityError):
            store.add_event(Event(type=EventType.COMMENTARY, game_id=game_id, seq=1))


class TestVerdicts:
    def test_verdict_round_trips(self, store, game_id):
        verdict = {"winner": "white", "white_grade": "B+", "key_moments": ["Qh4#"]}
        store.save_verdict(game_id, verdict)
        assert store.get_verdict(game_id) == verdict

    def test_saving_twice_replaces_rather_than_duplicates(self, store, game_id):
        store.save_verdict(game_id, {"winner": "white"})
        store.save_verdict(game_id, {"winner": "black"})
        assert store.get_verdict(game_id) == {"winner": "black"}

    def test_missing_verdict_is_none(self, store, game_id):
        assert store.get_verdict(game_id) is None


class TestFullGameRehydration:
    def test_full_game_bundles_everything(self, store, game_id):
        engine = ChessEngine()
        store.add_move(game_id, engine.push_uci("e2e4"), reasoning="king's pawn")
        store.add_event(Event(type=EventType.GAME_STARTED, game_id=game_id, seq=1))
        store.save_verdict(game_id, {"winner": "white"})
        store.finish_game(game_id, "1-0", "checkmate", engine.fen, engine.pgn())

        full = store.get_full_game(game_id)
        assert full["result"] == "1-0"
        assert len(full["moves"]) == 1
        assert len(full["events"]) == 1
        assert full["verdict"] == {"winner": "white"}

    def test_data_survives_a_reconnect_to_the_same_file(self, tmp_path):
        db = tmp_path / "arena.db"
        store = GameStore(db)
        game_id = store.create_game("w", "b", "a", "mock")
        store.add_move(game_id, ChessEngine().push_uci("e2e4"))
        store.close()

        reopened = GameStore(db)
        try:
            assert len(reopened.get_moves(game_id)) == 1
            assert reopened.get_game(game_id)["white_model"] == "w"
        finally:
            reopened.close()

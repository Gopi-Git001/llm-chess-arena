"""Mock-mode orchestrator tests — a full game with zero API calls (PLAN.md §11)."""

from __future__ import annotations

import time

import pytest

from app.agents.analyst import MockAnalyst
from app.agents.base import BaseAnalyst, BasePlayer, MoveProposal
from app.agents.commentator import MockCommentator
from app.engine import ChessEngine
from app.events import EventType
from app.orchestrator import GameOrchestrator
from app.agents.player import MockPlayer
from app.store import GameStore
from app.verdict import Verdict

pytestmark = pytest.mark.asyncio


@pytest.fixture
def store() -> GameStore:
    store = GameStore(":memory:")
    yield store
    store.close()


def build(store: GameStore, white: BasePlayer, black: BasePlayer, **kwargs) -> GameOrchestrator:
    game_id = store.create_game(white.model, black.model, "mock-analyst", "mock")
    kwargs.setdefault("max_moves", 120)
    kwargs.setdefault("seed", 7)
    return GameOrchestrator(game_id=game_id, white=white, black=black, store=store, **kwargs)


class ScriptedPlayer(BasePlayer):
    """Plays a fixed list of UCI moves, then falls back to the first legal one."""

    def __init__(self, moves: list[str], model: str = "scripted", color: str = "white"):
        super().__init__(model=model, color=color)
        self.moves = list(moves)
        self.calls = 0

    async def get_move(self, fen, legal_moves, move_history_san, move_number, retry_budget=3):
        self.calls += 1
        uci = self.moves.pop(0) if self.moves else legal_moves[0]
        return MoveProposal(uci=uci, reasoning="scripted")


class TestFullMockGame:
    async def test_mock_game_runs_to_completion_quickly(self, store):
        """The ✅ Verify step: start→finish in <5s, valid PGN, real result."""
        orch = build(store, MockPlayer(seed=1, color="white"), MockPlayer(seed=2, color="black"))

        started = time.perf_counter()
        summary = await orch.run()
        elapsed = time.perf_counter() - started

        assert elapsed < 5, f"mock game took {elapsed:.2f}s, budget is 5s"
        assert summary["status"] == "finished"
        assert summary["result"] in ("1-0", "0-1", "1/2-1/2")
        assert summary["termination"] is not None
        assert summary["ply_count"] > 0
        assert ChessEngine.is_valid_pgn(summary["pgn"])
        assert summary["requests_used"] == 0, "mock mode must cost zero requests"

    async def test_game_is_persisted_and_rehydratable(self, store):
        orch = build(store, MockPlayer(seed=3, color="white"), MockPlayer(seed=4, color="black"))
        summary = await orch.run()

        full = store.get_full_game(orch.game_id)
        assert full["status"] == "finished"
        assert full["result"] == summary["result"]
        assert full["pgn"] == summary["pgn"]
        assert full["final_fen"] == summary["final_fen"]
        assert len(full["moves"]) == summary["ply_count"]
        assert full["moves"][0]["ply"] == 1

    async def test_stored_moves_replay_to_the_final_position(self, store):
        """The DB must hold a real game, not a plausible-looking one."""
        orch = build(store, MockPlayer(seed=5, color="white"), MockPlayer(seed=6, color="black"))
        summary = await orch.run()

        replay = ChessEngine()
        for row in store.get_moves(orch.game_id):
            record = replay.push_uci(row["uci"])
            assert record.san == row["san"]
            assert record.fen_after == row["fen_after"]
        assert replay.fen == summary["final_fen"]

    async def test_seeded_games_are_reproducible(self, store):
        first = build(store, MockPlayer(seed=11, color="white"), MockPlayer(seed=12, color="black"))
        second = build(store, MockPlayer(seed=11, color="white"), MockPlayer(seed=12, color="black"))

        assert (await first.run())["pgn"].split("\n\n")[1] == (await second.run())["pgn"].split("\n\n")[1]


class TestTooSlowThinkingMode:
    """Feature 1: a live-thinking window per move, then MOVE_MADE."""

    def _build(self, store, **kwargs):
        # A short window and a tiny game keep this fast while still exercising
        # several ticks of the pacer.
        kwargs.setdefault("thinking_window_ms", 200)
        kwargs.setdefault("max_moves", 4)
        return build(
            store,
            MockPlayer(seed=1, color="white"),
            MockPlayer(seed=2, color="black"),
            **kwargs,
        )

    async def test_thinking_tokens_stream_then_the_move_lands(self, store):
        orch = self._build(store)
        await orch.run()

        history = orch.bus.history
        tokens = [e for e in history if e.type == EventType.AGENT_THINKING_TOKEN]
        moves = [e for e in history if e.type == EventType.MOVE_MADE]

        assert tokens, "the thinking panel must receive streamed reasoning tokens"
        assert moves, "the game must still make moves"

        # For the first move, every one of its thinking tokens is emitted BEFORE
        # the move commits (the window closes first).
        first_move = moves[0]
        first_move_tokens = [
            e for e in tokens if e.data["move_number"] == 1 and e.data["color"] == "white"
        ]
        assert first_move_tokens
        assert max(e.seq for e in first_move_tokens) < first_move.seq

        # Reassembled tokens form readable reasoning.
        text = "".join(e.data["text_chunk"] for e in first_move_tokens)
        assert len(text.split()) > 3

    async def test_full_thinking_text_is_stored_and_on_the_move_event(self, store):
        orch = self._build(store)
        await orch.run()

        moves = store.get_moves(orch.game_id)
        assert all(m["thinking"] for m in moves), "each move keeps its full reasoning"

        move_events = [e for e in orch.bus.history if e.type == EventType.MOVE_MADE]
        assert all(e.data["thinking"] for e in move_events)

    async def test_window_paces_the_move_no_extra_delay_stacked(self, store):
        # Two plies at a 200ms window ≈ 0.4s; assert we're in that ballpark, i.e.
        # the plain move_delay isn't stacked on top of the window.
        orch = self._build(store, max_moves=2, move_delay_ms=800)
        started = time.perf_counter()
        await orch.run()
        elapsed = time.perf_counter() - started
        assert elapsed < 1.2, f"window should pace it (~0.4s), got {elapsed:.2f}s"


class TestMoveCommentary:
    """Feature 2: one commentary item per move, paired by ply."""

    async def test_commentary_is_emitted_for_every_move(self, store):
        orch = build(
            store,
            MockPlayer(seed=3, color="white"),
            MockPlayer(seed=4, color="black"),
            commentator=MockCommentator(),
            move_commentary_enabled=True,
            move_commentary_every_n_moves=1,
            max_moves=6,
        )
        await orch.run()

        moves = [e for e in orch.bus.history if e.type == EventType.MOVE_MADE]
        comments = [e for e in orch.bus.history if e.type == EventType.MOVE_COMMENTARY]

        assert len(comments) == len(moves)
        # Each commentary is paired to its move by ply and carries spoken text.
        assert [c.data["ply"] for c in comments] == [m.data["ply"] for m in moves]
        assert all(c.data["text"] for c in comments)
        assert all(c.data["source"] == "template" for c in comments)
        # The commentary for a move never precedes that move on the wire.
        for move, comment in zip(moves, comments):
            assert comment.seq > move.seq

    async def test_commentary_off_by_default_setting_emits_nothing(self, store):
        orch = build(
            store,
            MockPlayer(seed=3, color="white"),
            MockPlayer(seed=4, color="black"),
            commentator=MockCommentator(),
            move_commentary_enabled=False,
            max_moves=4,
        )
        await orch.run()
        assert not [e for e in orch.bus.history if e.type == EventType.MOVE_COMMENTARY]

    async def test_every_n_moves_throttles_commentary(self, store):
        orch = build(
            store,
            MockPlayer(seed=3, color="white"),
            MockPlayer(seed=4, color="black"),
            commentator=MockCommentator(),
            move_commentary_enabled=True,
            move_commentary_every_n_moves=2,
            max_moves=6,
        )
        await orch.run()
        comments = [e for e in orch.bus.history if e.type == EventType.MOVE_COMMENTARY]
        # Only even plies get commented.
        assert all(c.data["ply"] % 2 == 0 for c in comments)
        assert len(comments) == 3


class TestEventStream:
    async def test_event_sequence_brackets_the_game(self, store):
        orch = build(store, MockPlayer(seed=8, color="white"), MockPlayer(seed=9, color="black"))
        await orch.run()

        events = orch.bus.history
        assert events[0].type == EventType.GAME_STARTED
        assert events[-1].type == EventType.GAME_OVER
        assert [e.seq for e in events] == list(range(1, len(events) + 1)), "seq must be gap-free"

    async def test_every_move_is_preceded_by_a_thinking_event(self, store):
        orch = build(store, MockPlayer(seed=13, color="white"), MockPlayer(seed=14, color="black"))
        await orch.run()

        types = [e.type for e in orch.bus.history]
        moves = [e for e in orch.bus.history if e.type == EventType.MOVE_MADE]
        assert len(moves) == orch.engine.ply_count
        for move_event in moves:
            index = orch.bus.history.index(move_event)
            earlier = [t for t in types[:index] if t == EventType.AGENT_THINKING]
            assert earlier, "a move must follow an AGENT_THINKING event"

    async def test_events_are_persisted_with_the_stream(self, store):
        orch = build(store, MockPlayer(seed=15, color="white"), MockPlayer(seed=16, color="black"))
        await orch.run()

        stored = store.get_events(orch.game_id)
        assert len(stored) == len(orch.bus.history)
        assert [e["seq"] for e in stored] == [e.seq for e in orch.bus.history]
        assert stored[-1]["type"] == "GAME_OVER"
        assert stored[-1]["data"]["result"] in ("1-0", "0-1", "1/2-1/2")

    async def test_game_over_payload_matches_the_engine(self, store):
        orch = build(store, MockPlayer(seed=17, color="white"), MockPlayer(seed=18, color="black"))
        summary = await orch.run()

        payload = orch.bus.history[-1].data
        assert payload["result"] == summary["result"]
        assert payload["termination"] == summary["termination"]
        assert payload["winner"] == summary["winner"]

    async def test_live_subscriber_receives_events(self, store):
        orch = build(store, MockPlayer(seed=19, color="white"), MockPlayer(seed=20, color="black"))
        queue = orch.bus.subscribe()
        await orch.run()

        received = []
        while not queue.empty():
            received.append(queue.get_nowait())
        assert received[0].type == EventType.GAME_STARTED
        assert received[-1].type == EventType.GAME_OVER


class TestIllegalMovesAndForfeits:
    async def test_illegal_proposal_is_overridden_by_a_legal_move(self, store):
        """An agent proposing an illegal move must not corrupt the game (§2.1)."""
        white = ScriptedPlayer(["e2e5"], color="white")  # well-formed, not legal
        orch = build(store, white, MockPlayer(seed=21, color="black"), max_moves=2)
        await orch.run()

        moves = store.get_moves(orch.game_id)
        assert moves[0]["uci"] != "e2e5"
        assert moves[0]["forfeited"] == 1
        assert ChessEngine().is_legal_uci(moves[0]["uci"])

    async def test_forfeit_emits_an_event_naming_the_attempt(self, store):
        white = ScriptedPlayer(["a1a8"], color="white")
        orch = build(store, white, MockPlayer(seed=22, color="black"), max_moves=2)
        await orch.run()

        forfeits = [e for e in orch.bus.history if e.type == EventType.MOVE_FORFEITED]
        assert len(forfeits) == 1
        assert forfeits[0].data["attempted"] == "a1a8"
        assert forfeits[0].data["color"] == "white"

    async def test_malformed_uci_is_forfeited_not_crashed(self, store):
        white = ScriptedPlayer(["banana"], color="white")
        orch = build(store, white, MockPlayer(seed=23, color="black"), max_moves=2)
        summary = await orch.run()

        assert summary["status"] == "finished"
        assert store.get_moves(orch.game_id)[0]["forfeited"] == 1

    async def test_illegal_attempts_are_stored_and_evented(self, store):
        """§13: every illegal attempt visible in the UI and stored in the DB."""
        orch = build(
            store,
            MockPlayer(seed=24, color="white", illegal_rate=0.9),
            MockPlayer(seed=25, color="black"),
            max_moves=6,
        )
        await orch.run()

        attempts = [e for e in orch.bus.history if e.type == EventType.ILLEGAL_ATTEMPT]
        assert attempts, "expected the mock to produce illegal attempts"
        white_moves = [m for m in store.get_moves(orch.game_id) if m["color"] == "white"]
        assert any(m["attempts"] > 1 for m in white_moves)

    async def test_forfeiting_mock_marks_moves_forfeited(self, store):
        orch = build(
            store,
            MockPlayer(seed=26, color="white", forfeit_rate=1.0),
            MockPlayer(seed=27, color="black"),
            max_moves=4,
        )
        summary = await orch.run()

        white_moves = [m for m in store.get_moves(orch.game_id) if m["color"] == "white"]
        assert all(m["forfeited"] == 1 for m in white_moves)
        assert summary["status"] == "finished"
        assert ChessEngine.is_valid_pgn(summary["pgn"])

    async def test_every_forfeited_move_has_a_matching_event(self, store):
        """Regression: MockPlayer used to pre-pick a *legal* move when it
        forfeited, so the orchestrator's legality check passed and no
        MOVE_FORFEITED was emitted — the DB said forfeited, the event stream
        said nothing, and the UI badge never lit up. Deciding to forfeit is the
        orchestrator's job; an agent only ever proposes.
        """
        orch = build(
            store,
            MockPlayer(seed=40, color="white", forfeit_rate=1.0),
            MockPlayer(seed=41, color="black", forfeit_rate=1.0),
            max_moves=6,
        )
        await orch.run()

        forfeited_moves = [m for m in store.get_moves(orch.game_id) if m["forfeited"]]
        forfeit_events = [e for e in orch.bus.history if e.type == EventType.MOVE_FORFEITED]

        assert forfeited_moves, "expected the mock to forfeit"
        assert len(forfeit_events) == len(forfeited_moves), (
            "every forfeited move needs a MOVE_FORFEITED event or the UI can't show it"
        )
        for event in forfeit_events:
            assert event.data["attempted"] not in ("", None)
            assert event.data["replacement"] != event.data["attempted"]

    async def test_a_forfeited_move_is_still_a_legal_move(self, store):
        orch = build(
            store,
            MockPlayer(seed=42, color="white", forfeit_rate=1.0),
            MockPlayer(seed=43, color="black", forfeit_rate=1.0),
            max_moves=8,
        )
        summary = await orch.run()

        replay = ChessEngine()
        for row in store.get_moves(orch.game_id):
            replay.push_uci(row["uci"])  # raises if the substitute was illegal
        assert replay.fen == summary["final_fen"]


class TestTerminationPaths:
    async def test_fools_mate_is_reported_as_checkmate(self, store):
        white = ScriptedPlayer(["f2f3", "g2g4"], model="foolish", color="white")
        black = ScriptedPlayer(["e7e5", "d8h4"], model="opportunist", color="black")
        orch = build(store, white, black)
        summary = await orch.run()

        assert summary["result"] == "0-1"
        assert summary["termination"] == "checkmate"
        assert summary["winner"] == "black"
        assert summary["ply_count"] == 4
        assert "Qh4#" in summary["pgn"]

    async def test_max_moves_stops_the_loop_and_adjudicates(self, store):
        orch = build(
            store,
            MockPlayer(seed=28, color="white"),
            MockPlayer(seed=29, color="black"),
            max_moves=10,
        )
        summary = await orch.run()

        assert summary["ply_count"] == 10
        assert summary["termination"] == "max_moves"
        assert summary["result"] in ("1-0", "0-1", "1/2-1/2")

    async def test_agents_are_never_asked_to_move_after_game_over(self, store):
        white = ScriptedPlayer(["f2f3", "g2g4"], color="white")
        black = ScriptedPlayer(["e7e5", "d8h4"], color="black")
        orch = build(store, white, black)
        await orch.run()

        # Fool's mate is 4 plies: White moved twice, Black twice. No extra calls.
        assert white.calls == 2
        assert black.calls == 2


class TestControlPaths:
    async def test_request_budget_kill_switch_stops_the_game(self, store):
        orch = build(
            store,
            MockPlayer(seed=30, color="white"),
            MockPlayer(seed=31, color="black"),
            max_requests_per_game=0,
        )
        summary = await orch.run()

        assert summary["status"] == "aborted"
        assert summary["ply_count"] == 0
        assert store.get_game(orch.game_id)["status"] == "aborted"
        assert any(e.type == EventType.ERROR for e in orch.bus.history)

    async def test_abort_stops_the_game_and_persists_status(self, store):
        orch = build(store, MockPlayer(seed=32, color="white"), MockPlayer(seed=33, color="black"))
        orch.abort()
        summary = await orch.run()

        assert summary["status"] == "aborted"
        assert store.get_game(orch.game_id)["status"] == "aborted"

    async def test_abort_emits_a_terminal_event_so_the_ui_updates(self, store):
        """Regression: abort emitted no event, so the live UI froze with a stale
        Abort button until a manual refresh. It must send a terminal GAME_OVER
        tagged status=aborted."""
        orch = build(store, MockPlayer(seed=70, color="white"), MockPlayer(seed=71, color="black"))
        orch.abort()
        await orch.run()

        overs = [e for e in orch.bus.history if e.type == EventType.GAME_OVER]
        assert len(overs) == 1
        assert overs[0].data["status"] == "aborted"
        # Aborted games get no verdict.
        assert not any(e.type == EventType.VERDICT for e in orch.bus.history)

    async def test_finished_game_over_event_is_tagged_finished(self, store):
        white = ScriptedPlayer(["f2f3", "g2g4"], color="white")
        black = ScriptedPlayer(["e7e5", "d8h4"], color="black")
        orch = build(store, white, black)
        await orch.run()

        over = [e for e in orch.bus.history if e.type == EventType.GAME_OVER][0]
        assert over.data["status"] == "finished"

    async def test_crash_marks_the_game_errored_rather_than_stuck(self, store):
        class ExplodingPlayer(BasePlayer):
            async def get_move(self, fen, legal_moves, move_history_san, move_number, retry_budget=3):
                raise RuntimeError("model went up in smoke")

        orch = build(store, ExplodingPlayer("boom", "white"), MockPlayer(seed=34, color="black"))

        with pytest.raises(RuntimeError, match="up in smoke"):
            await orch.run()

        assert store.get_game(orch.game_id)["status"] == "error"
        errors = [e for e in orch.bus.history if e.type == EventType.ERROR]
        assert "RuntimeError" in errors[0].data["message"]


class TestAnalystIntegration:
    async def test_verdict_is_emitted_and_persisted_after_game_over(self, store):
        orch = build(
            store,
            MockPlayer(seed=50, color="white"),
            MockPlayer(seed=51, color="black"),
            analyst=MockAnalyst(seed=1),
            max_moves=10,
        )
        await orch.run()

        types = [e.type for e in orch.bus.history]
        assert types[-1] == EventType.VERDICT
        assert types[-2] == EventType.GAME_OVER, "verdict comes after the result, not before"

        stored = store.get_verdict(orch.game_id)
        assert stored is not None
        assert stored == orch.bus.history[-1].data

    async def test_verdict_agrees_with_the_engine_result(self, store):
        white = ScriptedPlayer(["f2f3", "g2g4"], model="foolish", color="white")
        black = ScriptedPlayer(["e7e5", "d8h4"], model="sharp", color="black")
        orch = build(store, white, black, analyst=MockAnalyst(seed=2))
        summary = await orch.run()

        verdict = store.get_verdict(orch.game_id)
        assert verdict["engine_result"] == summary["result"] == "0-1"
        assert verdict["engine_termination"] == "checkmate"
        assert verdict["winner"] == "black"

    async def test_verdict_sees_forfeits_that_actually_happened(self, store):
        orch = build(
            store,
            MockPlayer(seed=52, color="white", forfeit_rate=1.0),
            MockPlayer(seed=53, color="black"),
            analyst=MockAnalyst(seed=3),
            max_moves=8,
        )
        await orch.run()

        verdict = store.get_verdict(orch.game_id)
        assert "forfeited" in verdict["illegal_move_summary"]
        assert verdict["white_grade"] == "F", "a model that forfeits every turn earns it"

    async def test_no_analyst_means_no_verdict_not_a_crash(self, store):
        orch = build(store, MockPlayer(seed=54, color="white"), MockPlayer(seed=55, color="black"), max_moves=6)
        summary = await orch.run()

        assert summary["status"] == "finished"
        assert store.get_verdict(orch.game_id) is None
        assert not any(e.type == EventType.VERDICT for e in orch.bus.history)

    async def test_aborted_game_gets_no_verdict(self, store):
        orch = build(
            store,
            MockPlayer(seed=56, color="white"),
            MockPlayer(seed=57, color="black"),
            analyst=MockAnalyst(seed=4),
        )
        orch.abort()
        await orch.run()

        assert store.get_verdict(orch.game_id) is None

    async def test_a_broken_analyst_cannot_ruin_a_finished_game(self, store):
        class ExplodingAnalyst(BaseAnalyst):
            async def review(self, facts):
                raise RuntimeError("analyst melted")

        orch = build(
            store,
            MockPlayer(seed=58, color="white"),
            MockPlayer(seed=59, color="black"),
            analyst=ExplodingAnalyst("boom"),
            max_moves=6,
        )
        summary = await orch.run()

        assert summary["status"] == "finished", "the game happened; the review is extra"
        assert store.get_game(orch.game_id)["status"] == "finished"
        assert any(e.type == EventType.ERROR for e in orch.bus.history)

    async def test_verdict_payload_is_json_serialisable_for_the_wire(self, store):
        orch = build(
            store,
            MockPlayer(seed=60, color="white"),
            MockPlayer(seed=61, color="black"),
            analyst=MockAnalyst(seed=5),
            max_moves=6,
        )
        await orch.run()

        import json

        payload = [e for e in orch.bus.history if e.type == EventType.VERDICT][0].data
        assert json.loads(json.dumps(payload)) == payload


class TestLiveCommentary:
    async def test_commentary_is_off_by_default(self, store):
        """§5: live_commentary_every_n_moves defaults to 0 to save quota."""
        orch = build(
            store,
            MockPlayer(seed=62, color="white"),
            MockPlayer(seed=63, color="black"),
            analyst=MockAnalyst(seed=6),
            max_moves=10,
        )
        await orch.run()

        assert not any(e.type == EventType.COMMENTARY for e in orch.bus.history)

    async def test_commentary_fires_every_n_plies(self, store):
        orch = build(
            store,
            MockPlayer(seed=64, color="white"),
            MockPlayer(seed=65, color="black"),
            analyst=MockAnalyst(seed=7),
            max_moves=10,
            commentary_every_n_moves=5,
        )
        await orch.run()

        comments = [e for e in orch.bus.history if e.type == EventType.COMMENTARY]
        assert len(comments) == 2  # at ply 5 and ply 10
        assert comments[0].data["ply"] == 5
        assert comments[0].data["text"]

    async def test_commentary_is_persisted_like_every_other_event(self, store):
        orch = build(
            store,
            MockPlayer(seed=66, color="white"),
            MockPlayer(seed=67, color="black"),
            analyst=MockAnalyst(seed=8),
            max_moves=6,
            commentary_every_n_moves=3,
        )
        await orch.run()

        stored = [e for e in store.get_events(orch.game_id) if e["type"] == "COMMENTARY"]
        assert len(stored) == 2
        assert stored[0]["data"]["text"]

    async def test_broken_commentary_does_not_stop_the_game(self, store):
        class ExplodingCommentator(MockAnalyst):
            async def comment(self, facts):
                raise RuntimeError("no words")

        orch = build(
            store,
            MockPlayer(seed=68, color="white"),
            MockPlayer(seed=69, color="black"),
            analyst=ExplodingCommentator(),
            max_moves=6,
            commentary_every_n_moves=2,
        )
        summary = await orch.run()

        assert summary["status"] == "finished"
        assert not any(e.type == EventType.COMMENTARY for e in orch.bus.history)

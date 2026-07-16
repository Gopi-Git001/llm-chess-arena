"""GameOrchestrator — the game loop from PLAN.md §6.

The narrator of the whole system: it drives the engine, asks agents for moves,
persists everything, and emits the event stream the frontend renders.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any

from app.agents.base import BaseAnalyst, BasePlayer, MoveProposal
from app.engine import ChessEngine, IllegalMoveError
from app.events import EventBus, EventType
from app.store import GameStore
from app.verdict import GameFacts

log = logging.getLogger(__name__)


class GameOrchestrator:
    """Runs one game start to finish.

    Move validation lives here, not in the agents: an agent proposes, the
    engine disposes. An agent that can't produce a legal move within its retry
    budget forfeits the turn to a random legal move (§2.3) — the game always
    reaches a real conclusion.
    """

    def __init__(
        self,
        game_id: str,
        white: BasePlayer,
        black: BasePlayer,
        store: GameStore,
        bus: EventBus | None = None,
        analyst: BaseAnalyst | None = None,
        max_moves: int = 120,
        illegal_move_retries: int = 3,
        move_delay_ms: int = 0,
        max_requests_per_game: int = 250,
        commentary_every_n_moves: int = 0,
        seed: int | None = None,
        request_counter: Callable[[], int] | None = None,
    ) -> None:
        self.game_id = game_id
        self.white = white
        self.black = black
        self.analyst = analyst
        self.commentary_every_n_moves = commentary_every_n_moves
        self.store = store
        self.bus = bus or EventBus(game_id)
        self.engine = ChessEngine(max_moves=max_moves)
        self.illegal_move_retries = illegal_move_retries
        self.move_delay_ms = move_delay_ms
        self.max_requests_per_game = max_requests_per_game
        # Live games read the real count off the LLM client; mock games have no
        # requests to count, so the default is honestly zero.
        self._request_counter = request_counter or (lambda: 0)
        self._rng = random.Random(seed)
        self._abort = asyncio.Event()

    @property
    def requests_used(self) -> int:
        return self._request_counter()

    # --- event plumbing ---------------------------------------------------

    def _emit(self, type: EventType, data: dict[str, Any] | None = None) -> None:
        """Publish and persist in one step, so the WS stream and the DB can
        never disagree about what happened."""
        event = self.bus.publish(type, data)
        self.store.add_event(event)

    def abort(self) -> None:
        """Ask the loop to stop after the current move."""
        self._abort.set()

    @property
    def aborted(self) -> bool:
        return self._abort.is_set()

    def _player_for_turn(self) -> BasePlayer:
        return self.white if self.engine.turn == "white" else self.black

    # --- the loop ---------------------------------------------------------

    async def run(self) -> dict[str, Any]:
        self.store.set_status(self.game_id, "in_progress")
        self._emit(
            EventType.GAME_STARTED,
            {
                "game_id": self.game_id,
                "white_model": self.white.model,
                "black_model": self.black.model,
                "fen": self.engine.fen,
                "max_moves": self.engine.max_moves,
                "requests_used": self.requests_used,
            },
        )

        try:
            while not self.engine.is_game_over:
                if self._abort.is_set():
                    return self._finish(status="aborted")
                if self.requests_used >= self.max_requests_per_game:
                    # Kill-switch (§5): stop before burning more quota.
                    self._emit(
                        EventType.ERROR,
                        {
                            "message": "Request budget exhausted for this game.",
                            "requests_used": self.requests_used,
                        },
                    )
                    return self._finish(status="aborted")

                await self._play_one_turn()
                await self._maybe_comment()

                if self.move_delay_ms:
                    await asyncio.sleep(self.move_delay_ms / 1000)

        except Exception as exc:  # a crash must not leave the game "in_progress"
            log.exception("Game %s crashed", self.game_id)
            self._emit(EventType.ERROR, {"message": f"{type(exc).__name__}: {exc}"})
            self._finish(status="error")
            raise

        summary = self._finish()
        await self._run_analyst()
        return summary

    async def _play_one_turn(self) -> None:
        engine = self.engine
        player = self._player_for_turn()
        color = engine.turn
        legal = engine.legal_moves_uci()

        self._emit(
            EventType.AGENT_THINKING,
            {"color": color, "model": player.model, "move_number": engine.move_number},
        )

        proposal = await player.get_move(
            fen=engine.fen,
            legal_moves=legal,
            move_history_san=engine.san_history(last_n=10),
            move_number=engine.move_number,
            retry_budget=self.illegal_move_retries,
        )

        # Report each rejected attempt individually — the UI badges them and the
        # Analyst grades on them (§13: every illegal attempt visible and stored).
        for attempt_uci in proposal.illegal_attempts:
            self._emit(
                EventType.ILLEGAL_ATTEMPT,
                {
                    "color": color,
                    "model": player.model,
                    "uci": attempt_uci,
                    "move_number": engine.move_number,
                },
            )

        proposal = self._enforce_legality(proposal, legal, color, player.model)

        try:
            record = engine.push_uci(proposal.uci)
        except IllegalMoveError:
            # The engine is the last word. Rather than fail the game, forfeit.
            log.warning("Game %s: proposal %r rejected at push", self.game_id, proposal.uci)
            proposal = self._forfeit(proposal, legal, color, player.model)
            record = engine.push_uci(proposal.uci)

        self.store.add_move(
            self.game_id,
            record,
            reasoning=proposal.reasoning,
            attempts=proposal.attempts,
            forfeited=proposal.forfeited,
        )

        self._emit(
            EventType.MOVE_MADE,
            {
                "ply": record.ply,
                "move_number": record.move_number,
                "color": record.color,
                "uci": record.uci,
                "san": record.san,
                "fen": record.fen_after,
                "reasoning": proposal.reasoning,
                "attempts": proposal.attempts,
                "forfeited": proposal.forfeited,
                "is_check": record.is_check,
                "is_checkmate": record.is_checkmate,
                "is_capture": record.is_capture,
                "captured_piece": record.captured_piece,
                "requests_used": self.requests_used,
            },
        )

    def _enforce_legality(
        self, proposal: MoveProposal, legal: list[str], color: str, model: str
    ) -> MoveProposal:
        """Never trust the proposal — verify it against the legal list (§2.1)."""
        if proposal.uci in legal:
            return proposal
        return self._forfeit(proposal, legal, color, model)

    def _forfeit(
        self, proposal: MoveProposal, legal: list[str], color: str, model: str
    ) -> MoveProposal:
        """Play a random legal move on the agent's behalf and say so loudly."""
        replacement = self._rng.choice(legal)
        self._emit(
            EventType.MOVE_FORFEITED,
            {
                "color": color,
                "model": model,
                "attempted": proposal.uci,
                "replacement": replacement,
                "attempts": proposal.attempts,
                "illegal_attempts": proposal.illegal_attempts,
                "move_number": self.engine.move_number,
            },
        )
        return MoveProposal(
            uci=replacement,
            reasoning=proposal.reasoning or "(no legal move produced — random move played)",
            attempts=proposal.attempts,
            forfeited=True,
            illegal_attempts=proposal.illegal_attempts,
        )

    # --- analyst ----------------------------------------------------------

    def _facts(self) -> GameFacts:
        """The objective record handed to the Analyst. Straight from the engine
        and the database — never from a model."""
        engine = self.engine
        return GameFacts(
            game_id=self.game_id,
            white_model=self.white.model,
            black_model=self.black.model,
            result=engine.result,
            termination=engine.termination,
            winner=engine.winner,
            ply_count=engine.ply_count,
            pgn=engine.pgn(white_model=self.white.model, black_model=self.black.model),
            moves=self.store.get_moves(self.game_id),
            requests_used=self.requests_used,
        )

    async def _maybe_comment(self) -> None:
        """Live commentary every N moves. Off by default to save quota (§5)."""
        every = self.commentary_every_n_moves
        if not self.analyst or not every or self.engine.ply_count == 0:
            return
        if self.engine.ply_count % every != 0:
            return

        try:
            text = await self.analyst.comment(self._facts())
        except Exception:
            # Commentary is decoration. It must never take the game down.
            log.exception("Commentary failed for game %s", self.game_id)
            return

        if text:
            self._emit(
                EventType.COMMENTARY,
                {
                    "text": text,
                    "model": self.analyst.model,
                    "ply": self.engine.ply_count,
                    "move_number": self.engine.move_number,
                },
            )

    async def _run_analyst(self) -> None:
        """Post-game review (§6 step 6-7). Only for games that actually ended."""
        if not self.analyst:
            return

        try:
            verdict = await self.analyst.review(self._facts())
        except Exception as exc:
            # The analyst is supposed to fall back internally; if it somehow
            # throws anyway, a finished game must still not end in an error.
            log.exception("Analyst failed for game %s", self.game_id)
            self._emit(EventType.ERROR, {"message": f"Analyst failed: {exc}"})
            return

        payload = verdict.model_dump()
        self.store.save_verdict(self.game_id, payload)
        self._emit(EventType.VERDICT, payload)

        # _finish wrote the count before the analyst spent its requests; fold
        # them in now so the persisted total matches reality.
        self.store.set_requests_used(self.game_id, self.requests_used)

    # --- finishing --------------------------------------------------------

    def _finish(self, status: str = "finished") -> dict[str, Any]:
        engine = self.engine
        result = engine.result
        termination = engine.termination

        if status != "finished":
            # Aborted/errored games have no sporting result.
            result = engine.result if engine.is_game_over else "*"
            termination = termination or status

        pgn = engine.pgn(white_model=self.white.model, black_model=self.black.model)

        self.store.finish_game(
            self.game_id,
            result=result,
            termination=termination,
            final_fen=engine.fen,
            pgn=pgn,
            requests_used=self.requests_used,
            status=status,
        )

        # Emit a terminal event for EVERY ending — finished, aborted, or error —
        # so the live UI always resolves instead of freezing with a stale
        # "Abort" button. (For finished games the verdict follows separately.)
        self._emit(
            EventType.GAME_OVER,
            {
                "result": result,
                "termination": termination,
                "winner": engine.winner,
                "fen": engine.fen,
                "ply_count": engine.ply_count,
                "pgn": pgn,
                "requests_used": self.requests_used,
                "status": status,  # finished | aborted | error
            },
        )

        return {
            "game_id": self.game_id,
            "status": status,
            "result": result,
            "termination": termination,
            "winner": engine.winner,
            "ply_count": engine.ply_count,
            "pgn": pgn,
            "final_fen": engine.fen,
            "requests_used": self.requests_used,
        }

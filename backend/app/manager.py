"""GameManager — owns running games and the tasks driving them.

Keeps the FastAPI layer thin: routes create/lookup/abort sessions here, and the
WS endpoint subscribes to a session's bus. Live agents land in Phase 3; today
every session is mock.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.agents.analyst import AnalystAgent, MockAnalyst
from app.agents.base import BaseAnalyst, BaseCommentator, BasePlayer
from app.agents.commentator import CommentatorAgent, MockCommentator
from app.agents.player import MockPlayer, PlayerAgent
from app.config import Settings
from app.events import EventBus, EventType
from app.llm_client import OpenRouterClient
from app.orchestrator import GameOrchestrator
from app.store import GameStore

log = logging.getLogger(__name__)


@dataclass
class GameSession:
    """A game in flight: its orchestrator, its bus, and the task running it."""

    game_id: str
    orchestrator: GameOrchestrator
    task: asyncio.Task | None = None
    summary: dict[str, Any] | None = None
    # Live games own an httpx client that must be closed when the game ends.
    llm_client: OpenRouterClient | None = None

    @property
    def bus(self) -> EventBus:
        return self.orchestrator.bus

    @property
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()


@dataclass
class GameSettings:
    """Per-game overrides. Anything omitted falls back to config.yaml."""

    max_moves: int | None = None
    move_delay_ms: int | None = None
    illegal_move_retries: int | None = None
    seed: int | None = None
    commentary_every_n_moves: int | None = None
    # > 0 opts this game into "Too Slow" mode (F1); None/0 uses config default off.
    thinking_window_ms: int | None = None
    # Move/voice commentary (F2). None falls back to config.
    move_commentary_enabled: bool | None = None
    move_commentary_every_n_moves: int | None = None
    # Mock-only knobs, so the illegal/forfeit UI states can be demoed on demand.
    illegal_rate: float = 0.0
    forfeit_rate: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)


class GameManager:
    def __init__(self, store: GameStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._sessions: dict[str, GameSession] = {}

    def get(self, game_id: str) -> GameSession | None:
        return self._sessions.get(game_id)

    def create(
        self,
        white_model: str | None = None,
        black_model: str | None = None,
        analyst_model: str | None = None,
        game_settings: GameSettings | None = None,
    ) -> GameSession:
        cfg = self.settings
        opts = game_settings or GameSettings()

        white_model = white_model or cfg.openrouter.white_model
        black_model = black_model or cfg.openrouter.black_model
        analyst_model = analyst_model or cfg.openrouter.analyst_model

        game_id = self.store.create_game(
            white_model=white_model,
            black_model=black_model,
            analyst_model=analyst_model,
            mode=cfg.mode,
        )

        bus = EventBus(game_id)
        white, black, llm_client = self._build_players(white_model, black_model, opts, bus)
        analyst = self._build_analyst(analyst_model, llm_client, opts)
        commentator = self._build_commentator(cfg.openrouter.commentator_model, llm_client)

        thinking_window_ms = opts.thinking_window_ms or 0
        move_commentary_enabled = (
            cfg.commentary.enabled
            if opts.move_commentary_enabled is None
            else opts.move_commentary_enabled
        )
        move_commentary_every_n_moves = (
            cfg.commentary.every_n_moves
            if opts.move_commentary_every_n_moves is None
            else opts.move_commentary_every_n_moves
        )

        orchestrator = GameOrchestrator(
            game_id=game_id,
            white=white,
            black=black,
            analyst=analyst,
            commentator=commentator,
            store=self.store,
            bus=bus,
            commentary_every_n_moves=(
                cfg.game.live_commentary_every_n_moves
                if opts.commentary_every_n_moves is None
                else opts.commentary_every_n_moves
            ),
            move_commentary_enabled=move_commentary_enabled,
            move_commentary_every_n_moves=move_commentary_every_n_moves,
            thinking_window_ms=thinking_window_ms,
            max_moves=opts.max_moves or cfg.game.max_moves,
            illegal_move_retries=opts.illegal_move_retries or cfg.game.illegal_move_retries,
            move_delay_ms=(
                cfg.game.move_delay_ui_ms if opts.move_delay_ms is None else opts.move_delay_ms
            ),
            max_requests_per_game=cfg.throttle.max_requests_per_game,
            seed=opts.seed,
            request_counter=(lambda: llm_client.requests_used) if llm_client else None,
        )

        session = GameSession(game_id=game_id, orchestrator=orchestrator, llm_client=llm_client)
        self._sessions[game_id] = session
        return session

    def _build_analyst(
        self, analyst_model: str, llm_client: OpenRouterClient | None, opts: GameSettings
    ) -> BaseAnalyst:
        if llm_client is None:  # mock mode
            return MockAnalyst(model=analyst_model, seed=opts.seed)
        # Shares the players' client, so its requests go through the same global
        # throttle and count against the same per-game budget (§8).
        return AnalystAgent(
            model=analyst_model,
            client=llm_client,
            max_tokens=self.settings.openrouter.max_tokens_analysis,
        )

    def _build_commentator(
        self, commentator_model: str, llm_client: OpenRouterClient | None
    ) -> BaseCommentator:
        if llm_client is None:  # mock mode → template commentary, zero calls
            return MockCommentator(model=commentator_model)
        # Reuses the shared client/throttle/budget, like the analyst (§F2a).
        return CommentatorAgent(
            model=commentator_model,
            client=llm_client,
            max_tokens=self.settings.openrouter.max_tokens_commentary,
        )

    def _build_players(
        self, white_model: str, black_model: str, opts: GameSettings, bus: EventBus
    ) -> tuple[BasePlayer, BasePlayer, OpenRouterClient | None]:
        cfg = self.settings

        if cfg.is_mock:
            # Carry the requested model names as labels so the UI looks real
            # while costing nothing.
            return (
                MockPlayer(
                    model=white_model,
                    color="white",
                    seed=opts.seed,
                    illegal_rate=opts.illegal_rate,
                    forfeit_rate=opts.forfeit_rate,
                ),
                MockPlayer(
                    model=black_model,
                    color="black",
                    seed=None if opts.seed is None else opts.seed + 1,
                    illegal_rate=opts.illegal_rate,
                    forfeit_rate=opts.forfeit_rate,
                ),
                None,
            )

        def on_client_event(type: str, data: dict[str, Any]) -> None:
            # Surface throttling and model fallbacks to the UI (§8). Publish
            # *and* persist, like GameOrchestrator._emit: an event that reaches
            # the WS but not the DB would vanish on refresh.
            self.store.add_event(bus.publish(EventType(type), data))

        # One client per game: the throttle is global across both players
        # because OpenRouter's limits are per account, not per model (§8).
        client = OpenRouterClient(cfg, on_event=on_client_event)
        return (
            PlayerAgent(
                model=white_model,
                color="white",
                client=client,
                max_tokens=cfg.openrouter.max_tokens_move,
            ),
            PlayerAgent(
                model=black_model,
                color="black",
                client=client,
                max_tokens=cfg.openrouter.max_tokens_move,
            ),
            client,
        )

    def start(self, session: GameSession) -> GameSession:
        """Kick the game off in the background; the WS stream narrates it."""

        async def runner() -> None:
            try:
                session.summary = await session.orchestrator.run()
            except Exception:
                # The orchestrator already emitted ERROR and marked the row.
                log.exception("Game %s failed", session.game_id)
            finally:
                # Don't leak the httpx connection pool once the game is done.
                if session.llm_client:
                    await session.llm_client.aclose()

        session.task = asyncio.create_task(runner(), name=f"game-{session.game_id}")
        return session

    async def abort(self, game_id: str) -> bool:
        session = self._sessions.get(game_id)
        if session is None:
            return False
        session.orchestrator.abort()
        if session.task:
            await asyncio.wait([session.task], timeout=10)
        return True

    async def shutdown(self) -> None:
        """Stop every running game so the process can exit cleanly."""
        for session in list(self._sessions.values()):
            session.orchestrator.abort()
        tasks = [s.task for s in self._sessions.values() if s.task and not s.task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=10)

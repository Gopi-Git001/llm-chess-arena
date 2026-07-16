"""FastAPI app: REST surface (PLAN.md §10) + the WebSocket event stream.

The backend is the single narrator (§2.7): the frontend renders what arrives
here and computes no chess logic of its own for a live game.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import get_settings
from app.manager import GameManager, GameSettings
from app.models_catalog import ModelCatalog
from app.store import GameStore

log = logging.getLogger(__name__)

settings = get_settings()
store = GameStore()
manager = GameManager(store, settings)
catalog = ModelCatalog(settings)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Stop any game still in flight so the process can exit cleanly.
    await manager.shutdown()


app = FastAPI(title="LLM Chess Arena", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Vite picks the next free port if 5173 is taken, so allow the usual range.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):(517[0-9]|4173)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- schemas -------------------------------------------------------------


class GameSettingsIn(BaseModel):
    max_moves: int | None = Field(default=None, ge=2, le=1000)
    move_delay_ms: int | None = Field(default=None, ge=0, le=10_000)
    illegal_move_retries: int | None = Field(default=None, ge=1, le=10)
    seed: int | None = None
    # 0 = off. Each comment is an extra request in live mode (§5).
    commentary_every_n_moves: int | None = Field(default=None, ge=0, le=50)
    illegal_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    forfeit_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class CreateGameIn(BaseModel):
    white_model: str | None = None
    black_model: str | None = None
    analyst_model: str | None = None
    settings: GameSettingsIn = Field(default_factory=GameSettingsIn)


class CreateGameOut(BaseModel):
    game_id: str
    mode: str
    white_model: str
    black_model: str


# --- routes --------------------------------------------------------------


@app.get("/health")
@app.get("/api/health")
def health() -> dict:
    """Liveness + a safe view of config. Never exposes the API key itself."""
    return {
        "status": "ok",
        "version": app.version,
        "mode": settings.mode,
        "api_key_configured": settings.has_api_key,
        "models": {
            "white": settings.openrouter.white_model,
            "black": settings.openrouter.black_model,
            "analyst": settings.openrouter.analyst_model,
        },
        # The quota meter shows requests-this-game against this kill-switch (§5).
        "max_requests_per_game": settings.throttle.max_requests_per_game,
    }


@app.post("/api/games", response_model=CreateGameOut, status_code=201)
async def create_game(body: CreateGameIn) -> CreateGameOut:
    """Create a game and start playing it. Events stream over /ws/games/{id}."""
    session = manager.create(
        white_model=body.white_model,
        black_model=body.black_model,
        analyst_model=body.analyst_model,
        game_settings=GameSettings(**body.settings.model_dump()),
    )
    manager.start(session)

    game = store.get_game(session.game_id)
    return CreateGameOut(
        game_id=session.game_id,
        mode=game["mode"],
        white_model=game["white_model"],
        black_model=game["black_model"],
    )


@app.get("/api/models")
async def list_models(refresh: bool = False) -> dict[str, Any]:
    """Free-model list for the pickers. Mock mode is offline (bundled list);
    live mode proxies OpenRouter, cached 1h (§10)."""
    return await catalog.list_models(force=refresh)


@app.get("/api/games")
def list_games(limit: int = 50) -> dict[str, Any]:
    return {"games": store.list_games(limit=limit)}


@app.get("/api/games/{game_id}")
def get_game(game_id: str) -> dict[str, Any]:
    """Full state for rehydration after a reload or WS drop (§9)."""
    game = store.get_full_game(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")
    session = manager.get(game_id)
    return {**game, "is_running": bool(session and session.is_running)}


@app.post("/api/games/{game_id}/abort")
async def abort_game(game_id: str) -> dict[str, Any]:
    if store.get_game(game_id) is None:
        raise HTTPException(status_code=404, detail="game not found")
    aborted = await manager.abort(game_id)
    if not aborted:
        # Already finished, or from a previous process — nothing to stop.
        return {"game_id": game_id, "aborted": False, "status": store.get_game(game_id)["status"]}
    return {"game_id": game_id, "aborted": True, "status": store.get_game(game_id)["status"]}


# --- websocket -----------------------------------------------------------


@app.websocket("/ws/games/{game_id}")
async def game_stream(websocket: WebSocket, game_id: str, since: int = 0) -> None:
    """Stream a game's events.

    Replays everything after `since` first, then follows live. A client can
    therefore reconnect mid-game and miss nothing, and `seq` lets it tell.
    """
    await websocket.accept()

    session = manager.get(game_id)
    if session is None:
        # Finished games (or games from a previous process) live only in SQLite.
        if store.get_game(game_id) is None:
            await websocket.close(code=4404, reason="game not found")
            return
        for row in store.get_events(game_id):
            if row["seq"] > since:
                await websocket.send_json(row)
        await websocket.close(code=1000, reason="game already finished")
        return

    bus = session.bus
    queue = bus.subscribe()
    try:
        # Backlog first, then live. Subscribing before the replay means events
        # published mid-replay queue up rather than being lost.
        replayed = 0
        for event in bus.history:
            if event.seq > since:
                await websocket.send_json(event.model_dump(mode="json"))
                replayed = event.seq

        while True:
            event = await queue.get()
            if event.seq <= replayed:
                continue  # already sent during replay
            await websocket.send_json(event.model_dump(mode="json"))

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WS stream failed for game %s", game_id)
    finally:
        bus.unsubscribe(queue)
        with contextlib.suppress(RuntimeError):
            await websocket.close()


@app.get("/api/games/{game_id}/events")
def get_events(game_id: str, since: int = 0) -> dict[str, Any]:
    """REST fallback for the event stream, and handy for debugging."""
    if store.get_game(game_id) is None:
        raise HTTPException(status_code=404, detail="game not found")
    events = [e for e in store.get_events(game_id) if e["seq"] > since]
    return {"game_id": game_id, "events": events}

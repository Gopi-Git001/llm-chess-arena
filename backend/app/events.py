"""Event models + EventBus — the backend is the single narrator (PLAN.md §2.7).

Everything the frontend renders arrives as one of these events.
`frontend/src/types/events.ts` mirrors this schema.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """The complete event vocabulary (PLAN.md §6)."""

    GAME_STARTED = "GAME_STARTED"
    AGENT_THINKING = "AGENT_THINKING"
    # A single reasoning token streamed live during the "Too Slow" window (F1).
    AGENT_THINKING_TOKEN = "AGENT_THINKING_TOKEN"
    MOVE_MADE = "MOVE_MADE"
    ILLEGAL_ATTEMPT = "ILLEGAL_ATTEMPT"
    MOVE_FORFEITED = "MOVE_FORFEITED"
    COMMENTARY = "COMMENTARY"
    # One presentation item's spoken commentary, paired to a move by ply (F2).
    MOVE_COMMENTARY = "MOVE_COMMENTARY"
    GAME_OVER = "GAME_OVER"
    VERDICT = "VERDICT"
    ERROR = "ERROR"
    RATE_LIMITED = "RATE_LIMITED"


class Event(BaseModel):
    """One narration step. `seq` is per-game and gap-free, so a reconnecting
    client can tell whether it missed anything."""

    type: EventType
    game_id: str
    seq: int
    ts: float = Field(default_factory=time.time)
    data: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    """In-process async pub/sub, one instance per game.

    Subscribers each get their own queue and read at their own pace. Publishing
    never blocks on a slow consumer and never fails a game because a browser
    tab stopped reading.
    """

    def __init__(self, game_id: str, queue_maxsize: int = 1000) -> None:
        self.game_id = game_id
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._history: list[Event] = []
        self._seq = 0
        self._queue_maxsize = queue_maxsize

    @property
    def history(self) -> list[Event]:
        """Every event so far, in order. Used to replay to late subscribers."""
        return list(self._history)

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, type: EventType, data: dict[str, Any] | None = None) -> Event:
        """Stamp the next seq, record it, and fan out to subscribers."""
        self._seq += 1
        event = Event(type=type, game_id=self.game_id, seq=self._seq, data=data or {})
        self._history.append(event)

        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A consumer this far behind has lost the plot; it will
                # rehydrate over REST on reconnect (PLAN.md §9).
                self.unsubscribe(queue)
        return event

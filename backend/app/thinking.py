"""The "Too Slow" thinking window (Feature 1).

Each move gets a fixed presentation window. The model streams its reasoning at
its own rate; the viewer watches for exactly `window_s`. Two forces to
reconcile:

  * the model finishes early → spread the remaining buffered reasoning over the
    leftover time so the panel never goes dead;
  * the model is still talking at the deadline → cut the display.

The move is committed (MOVE_MADE) only after the window closes. This module is
pure timing/pacing — it never parses moves or touches game rules.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable

ClockFn = Callable[[], float]
SleepFn = Callable[[float], Awaitable[None]]
EmitFn = Callable[[str], None]

# A "word" is a run of non-whitespace plus the whitespace that follows it, so
# chunks reassemble into the original text (newlines and spacing preserved).
_WORD_RE = re.compile(r"\S+\s*")


def _last_ws_boundary(text: str) -> int:
    """Index just past the last whitespace char, i.e. the start of a possibly
    incomplete trailing word. Everything before it is whole words."""
    for i in range(len(text) - 1, -1, -1):
        if text[i].isspace():
            return i + 1
    return 0


class ReasoningBuffer:
    """Words produced by the model, drained by the pacer.

    `feed` accepts arbitrary streamed chunks (a real delta can split mid-word),
    buffers an incomplete trailing fragment, and only exposes whole words until
    `finish` flushes the tail.
    """

    def __init__(self) -> None:
        self._words: deque[str] = deque()
        self._pending = ""
        self._done = False

    def feed(self, text: str) -> None:
        if not text:
            return
        self._pending += text
        boundary = _last_ws_boundary(self._pending)
        if boundary <= 0:
            return  # no complete word yet — keep buffering
        head, self._pending = self._pending[:boundary], self._pending[boundary:]
        for token in _WORD_RE.findall(head):
            self._words.append(token)

    def finish(self) -> None:
        """No more tokens coming — flush whatever fragment is left."""
        if self._pending.strip():
            for token in _WORD_RE.findall(self._pending):
                self._words.append(token)
        self._pending = ""
        self._done = True

    @property
    def done(self) -> bool:
        return self._done

    def __len__(self) -> int:
        return len(self._words)

    def popleft(self) -> str:
        return self._words.popleft()


def release_count(buffered: int, remaining_s: float, done: bool, tick_s: float) -> int:
    """How many buffered words to reveal on this tick.

    Pure and deterministic so the pacing is unit-testable. The goal: keep the
    panel moving across the whole window without dumping everything at once.

      * nothing buffered → 0;
      * producer done → spread the remainder evenly across the ticks that are
        left, so the buffer empties right at the deadline;
      * producer still going → trickle (at least one word) so there's motion
        without outrunning a stream that may still deliver more.
    """
    if buffered <= 0:
        return 0
    ticks_left = max(1, math.ceil(remaining_s / tick_s))
    if done:
        per_tick = math.ceil(buffered / ticks_left)
    else:
        per_tick = max(1, buffered // ticks_left)
    return min(buffered, per_tick)


async def pace_window(
    buffer: ReasoningBuffer,
    emit: EmitFn,
    window_s: float,
    *,
    tick_s: float = 0.1,
    clock: ClockFn = time.monotonic,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    """Reveal `buffer`'s words to `emit` over exactly `window_s`.

    Returns when the window closes. Whatever is still buffered (because the
    model was slower than the window) is simply left unrevealed — the display is
    cut, the move commits afterward. `clock`/`sleep` are injectable so the
    timing can be tested without real waits.
    """
    if window_s <= 0:
        return
    start = clock()
    while True:
        remaining = window_s - (clock() - start)
        if remaining <= 0:
            return
        count = release_count(len(buffer), remaining, buffer.done, tick_s)
        for _ in range(count):
            emit(buffer.popleft())
        await sleep(min(tick_s, remaining))

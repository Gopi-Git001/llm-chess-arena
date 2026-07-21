"""The "Too Slow" thinking window (Feature 1): buffer + pacing logic.

Everything here is deterministic — the pacer's clock and sleep are injected so
timing is asserted exactly, with no real waits and no concurrency.
"""

from __future__ import annotations

import pytest

from app.thinking import ReasoningBuffer, pace_window, release_count


class FakeClock:
    """Advances only when something sleeps (same pattern as the throttle tests)."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class TestReasoningBuffer:
    def test_whole_words_become_tokens_incomplete_tail_waits(self):
        buf = ReasoningBuffer()
        # A streamed chunk split mid-word: "hel" is incomplete, held back.
        buf.feed("The knight jumps to f")
        drained = _drain(buf)
        assert "".join(drained) == "The knight jumps to "
        assert drained == ["The ", "knight ", "jumps ", "to "]

    def test_finish_flushes_the_trailing_fragment(self):
        buf = ReasoningBuffer()
        buf.feed("plays e4")  # no trailing space → "e4" is pending
        assert _drain(buf) == ["plays "]
        buf.finish()
        assert _drain(buf) == ["e4"]
        assert buf.done

    def test_reassembles_to_the_original_text(self):
        buf = ReasoningBuffer()
        original = "First I consider the centre.\nThen I develop a piece."
        # Fed in awkward, word-splitting chunks like a real stream.
        for chunk in ["First I ", "consi", "der the cent", "re.\nThen I dev", "elop a piece."]:
            buf.feed(chunk)
        buf.finish()
        assert "".join(_drain(buf)) == original


class TestReleaseCount:
    def test_nothing_buffered_releases_nothing(self):
        assert release_count(0, 5.0, done=True, tick_s=0.1) == 0

    def test_done_spreads_remaining_across_the_ticks_left(self):
        # 10 words, 1s left, 0.1s ticks → 10 ticks → ~1 per tick, never all at once.
        assert release_count(10, 1.0, done=True, tick_s=0.1) == 1

    def test_done_at_the_deadline_flushes_everything(self):
        # Less than a tick remains: emit the whole remainder so nothing is lost.
        assert release_count(7, 0.05, done=True, tick_s=0.1) == 7

    def test_still_producing_trickles_at_least_one(self):
        # A couple of words buffered mid-stream still move (no dead panel).
        assert release_count(2, 5.0, done=False, tick_s=0.1) == 1

    def test_never_releases_more_than_buffered(self):
        assert release_count(3, 0.01, done=True, tick_s=0.1) == 3


@pytest.mark.asyncio
class TestPaceWindow:
    async def test_finished_early_everything_is_revealed_by_the_deadline(self):
        clock = FakeClock()
        buf = ReasoningBuffer()
        buf.feed("one two three four five six seven eight nine ten ")
        buf.finish()  # model finished immediately

        emitted: list[str] = []
        await pace_window(
            buf, emitted.append, window_s=1.0, tick_s=0.1,
            clock=clock.time, sleep=clock.sleep,
        )

        assert "".join(emitted).split() == (
            "one two three four five six seven eight nine ten".split()
        )
        assert len(buf) == 0
        # The window was actually held open ~1s, not collapsed instantly.
        assert clock.now == pytest.approx(1.0, abs=1e-9)

    async def test_reveal_is_paced_not_dumped_in_the_first_tick(self):
        clock = FakeClock()
        buf = ReasoningBuffer()
        buf.feed(" ".join(str(i) for i in range(20)) + " ")
        buf.finish()

        first_tick: list[int] = []

        def emit(_word: str) -> None:
            first_tick.append(len(first_tick))

        # Wrap so we can snapshot how many landed before the first sleep.
        emitted: list[str] = []
        original_sleep = clock.sleep
        snapshot: dict[str, int] = {}

        async def sleep(seconds: float) -> None:
            snapshot.setdefault("after_first", len(emitted))
            await original_sleep(seconds)

        await pace_window(
            buf, emitted.append, window_s=2.0, tick_s=0.1,
            clock=clock.time, sleep=sleep,
        )

        assert snapshot["after_first"] < 20, "must not dump the whole buffer at once"
        assert len(emitted) == 20, "but everything is revealed by the end"

    async def test_still_going_at_the_deadline_cuts_the_display(self):
        clock = FakeClock()
        buf = ReasoningBuffer()
        buf.feed("start ")
        fed = {"n": 0}

        # A model that keeps producing faster than the window can show, and never
        # finishes — the pacer must still stop at the deadline, not wait forever.
        async def sleep(seconds: float) -> None:
            for _ in range(50):
                buf.feed(f"w{fed['n']} ")
                fed["n"] += 1
            await clock.sleep(seconds)

        emitted: list[str] = []
        await pace_window(
            buf, emitted.append, window_s=1.0, tick_s=0.1,
            clock=clock.time, sleep=sleep,
        )

        assert clock.now == pytest.approx(1.0, abs=1e-9), "stopped exactly at the deadline"
        assert len(buf) > 0, "reasoning produced after the window is left unshown (display cut)"

    async def test_zero_window_emits_nothing(self):
        buf = ReasoningBuffer()
        buf.feed("never shown ")
        buf.finish()
        emitted: list[str] = []
        await pace_window(buf, emitted.append, window_s=0.0)
        assert emitted == []


def _drain(buf: ReasoningBuffer) -> list[str]:
    out = []
    while len(buf):
        out.append(buf.popleft())
    return out

"""Run one mock game and print the event stream to stdout. Zero API calls.

    python scripts/run_mock_game.py [--seed 42] [--max-moves 120] [--illegal-rate 0.2]

This is the Phase 1 manual verify: watch a full game narrate itself, then paste
the PGN into lichess.org/paste to confirm it's a real game.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agents.player import MockPlayer  # noqa: E402
from app.events import EventType  # noqa: E402
from app.orchestrator import GameOrchestrator  # noqa: E402
from app.store import GameStore  # noqa: E402

def _use_utf8_stdout() -> bool:
    """Windows consoles default to cp1252, which can't encode emoji."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        return True
    except (AttributeError, OSError):
        return False


EMOJI_ICONS = {
    EventType.GAME_STARTED: "🏁",
    EventType.AGENT_THINKING: "🤔",
    EventType.MOVE_MADE: "♟️ ",
    EventType.ILLEGAL_ATTEMPT: "🚫",
    EventType.MOVE_FORFEITED: "⚠️ ",
    EventType.COMMENTARY: "🎙️ ",
    EventType.GAME_OVER: "🏆",
    EventType.VERDICT: "⚖️ ",
    EventType.ERROR: "💥",
    EventType.RATE_LIMITED: "⏳",
}

ASCII_ICONS = {
    EventType.GAME_STARTED: "[*]",
    EventType.AGENT_THINKING: "[.]",
    EventType.MOVE_MADE: "[>]",
    EventType.ILLEGAL_ATTEMPT: "[x]",
    EventType.MOVE_FORFEITED: "[!]",
    EventType.COMMENTARY: "[\"]",
    EventType.GAME_OVER: "[#]",
    EventType.VERDICT: "[=]",
    EventType.ERROR: "[E]",
    EventType.RATE_LIMITED: "[~]",
}

ICONS = EMOJI_ICONS if _use_utf8_stdout() else ASCII_ICONS


def describe(event) -> str:
    d = event.data
    match event.type:
        case EventType.GAME_STARTED:
            return f"{d['white_model']} (white) vs {d['black_model']} (black)"
        case EventType.AGENT_THINKING:
            return f"{d['color']} thinking… ({d['model']})"
        case EventType.MOVE_MADE:
            bits = [f"{d['move_number']}{'.' if d['color'] == 'white' else '...'} {d['san']}"]
            if d["forfeited"]:
                bits.append("[FORFEIT → random legal move]")
            if d["is_checkmate"]:
                bits.append("[checkmate]")
            elif d["is_check"]:
                bits.append("[check]")
            if d["is_capture"]:
                bits.append(f"[captured {d['captured_piece']}]")
            bits.append(f"— {d['reasoning']}")
            return " ".join(bits)
        case EventType.ILLEGAL_ATTEMPT:
            return f"{d['color']} proposed {d['uci']} — illegal, retrying"
        case EventType.MOVE_FORFEITED:
            return f"{d['color']} burned its budget ({d['attempted']}) → played {d['replacement']}"
        case EventType.GAME_OVER:
            return f"{d['result']} by {d['termination']} after {d['ply_count']} plies"
        case EventType.ERROR:
            return d.get("message", "")
        case _:
            return str(d)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run a mock LLM chess game.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-moves", type=int, default=120)
    parser.add_argument("--illegal-rate", type=float, default=0.15,
                        help="chance a mock player proposes illegal moves first")
    parser.add_argument("--forfeit-rate", type=float, default=0.05,
                        help="chance a mock player burns its whole retry budget")
    parser.add_argument("--db", default=":memory:")
    args = parser.parse_args()

    store = GameStore(args.db)
    white = MockPlayer("mock-white", "white", seed=args.seed,
                       illegal_rate=args.illegal_rate, forfeit_rate=args.forfeit_rate)
    black = MockPlayer("mock-black", "black", seed=args.seed + 1,
                       illegal_rate=args.illegal_rate, forfeit_rate=args.forfeit_rate)

    game_id = store.create_game(white.model, black.model, "mock-analyst", "mock")
    orch = GameOrchestrator(
        game_id=game_id, white=white, black=black, store=store,
        max_moves=args.max_moves, seed=args.seed,
    )

    queue = orch.bus.subscribe()

    async def printer() -> None:
        while True:
            event = await queue.get()
            print(f"{ICONS.get(event.type, '  ')} [{event.seq:>3}] {event.type.value:<16} "
                  f"{describe(event)}", flush=True)
            if event.type in (EventType.GAME_OVER, EventType.ERROR):
                break

    printer_task = asyncio.create_task(printer())
    summary = await orch.run()
    await asyncio.wait_for(printer_task, timeout=5)

    print("\n" + "=" * 70)
    print(f"Result: {summary['result']} by {summary['termination']} "
          f"({summary['ply_count']} plies, {summary['requests_used']} API requests)")
    forfeits = sum(1 for m in store.get_moves(game_id) if m["forfeited"])
    print(f"Forfeited moves: {forfeits}")
    print("=" * 70)
    print(summary["pgn"])

    from app.engine import ChessEngine

    print("=" * 70)
    print(f"PGN round-trips as a legal game: {ChessEngine.is_valid_pgn(summary['pgn'])}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

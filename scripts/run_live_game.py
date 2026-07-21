"""Run ONE small LIVE game and print the event stream, incl. the new
streamed-thinking tokens (F1) and model-authored commentary (F2).

    python scripts/run_live_game.py [--max-moves 4] [--thinking-window-ms 8000]
                                    [--no-commentary] [--no-thinking]

Makes REAL OpenRouter calls. Run scripts/verify_models.py first. Keep --max-moves
small: the free tier's per-minute limit throttles hard (a 30-move game took ~35m).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# Scope this run to live WITHOUT editing config.yaml (which stays mock).
os.environ["MODE"] = "live"

from app.config import load_settings  # noqa: E402
from app.events import EventType  # noqa: E402
from app.manager import GameManager, GameSettings  # noqa: E402
from app.store import GameStore  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run one small LIVE game.")
    parser.add_argument("--max-moves", type=int, default=4)
    parser.add_argument("--thinking-window-ms", type=int, default=8000)
    parser.add_argument("--no-commentary", action="store_true")
    parser.add_argument("--no-thinking", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    if settings.mode != "live":
        print("[!!] settings.mode is not live — aborting to avoid confusion.")
        return 2
    if not settings.has_api_key:
        print("[!!] OPENROUTER_API_KEY is not set.")
        return 2

    store = GameStore(":memory:")
    manager = GameManager(store, settings)
    session = manager.create(
        game_settings=GameSettings(
            max_moves=args.max_moves,
            thinking_window_ms=0 if args.no_thinking else args.thinking_window_ms,
            move_commentary_enabled=not args.no_commentary,
            move_commentary_every_n_moves=1,
        )
    )

    print(f"LIVE game {session.game_id}  (max_moves={args.max_moves}, "
          f"thinking_window_ms={0 if args.no_thinking else args.thinking_window_ms}, "
          f"commentary={not args.no_commentary})\n")

    queue = session.bus.subscribe()
    token_counts: dict[int, int] = {}

    async def printer() -> None:
        while True:
            e = await queue.get()
            d = e.data
            t = e.type
            if t == EventType.GAME_STARTED:
                print(f"[*] {d['white_model']} (W) vs {d['black_model']} (B)\n")
            elif t == EventType.AGENT_THINKING:
                print(f"[.] {d['color']} thinking…  ({d['model']})", flush=True)
            elif t == EventType.AGENT_THINKING_TOKEN:
                # Prove the reasoning is STREAMING: print it live, inline.
                token_counts[d['move_number']] = token_counts.get(d['move_number'], 0) + 1
                print(d['text_chunk'], end='', flush=True)
            elif t == EventType.MOVE_MADE:
                bits = [f"\n[>] {d['move_number']}{'.' if d['color']=='white' else '...'} {d['san']}"]
                if d['forfeited']:
                    bits.append("[FORFEIT→random]")
                if d['is_checkmate']:
                    bits.append("[mate]")
                elif d['is_check']:
                    bits.append("[check]")
                if d['is_capture']:
                    bits.append(f"[x{d['captured_piece']}]")
                bits.append(f"— {d['reasoning']}")
                if d.get('thinking'):
                    bits.append(f"(streamed {len(d['thinking'])} chars of reasoning)")
                print(" ".join(bits), flush=True)
            elif t == EventType.MOVE_COMMENTARY:
                print(f"[\U0001f399] COMMENTARY ({d['source']}): {d['text']}\n", flush=True)
            elif t == EventType.ILLEGAL_ATTEMPT:
                print(f"[x] {d['color']} illegal {d['uci']} — retrying", flush=True)
            elif t == EventType.MOVE_FORFEITED:
                print(f"[!] {d['color']} burned budget ({d['attempted']}) → {d['replacement']}", flush=True)
            elif t == EventType.RATE_LIMITED:
                print(f"[~] rate limited by {d['model']} — waiting {d['retry_in_s']}s "
                      f"(attempt {d['attempt']})", flush=True)
            elif t == EventType.ERROR:
                print(f"[E] {d.get('message','')}", flush=True)
            elif t == EventType.GAME_OVER:
                print(f"\n[#] {d['result']} by {d['termination']} "
                      f"({d['ply_count']} plies, {d['requests_used']} requests)", flush=True)
            elif t == EventType.VERDICT:
                print(f"[=] verdict ({d['source']}): {d['verdict_paragraph']}", flush=True)
                break

    printer_task = asyncio.create_task(printer())
    manager.start(session)
    if session.task:
        await session.task
    try:
        await asyncio.wait_for(printer_task, timeout=10)
    except asyncio.TimeoutError:
        printer_task.cancel()

    summary = session.summary or {}
    print("\n" + "=" * 70)
    print(f"Result: {summary.get('result')} by {summary.get('termination')} "
          f"— {summary.get('requests_used')} requests")
    print("PGN:\n" + (summary.get('pgn') or ''))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

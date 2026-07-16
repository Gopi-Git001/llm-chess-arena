"""Preflight: are the models in config.yaml live and free today?

    python scripts/verify_models.py            # check configured models
    python scripts/verify_models.py --list     # also list every free model

OpenRouter's free lineup rotates monthly (PLAN.md §12), so a config that worked
last month can 404 mid-game. Run this before any live run.

This makes ONE real request to OpenRouter's /models endpoint. That endpoint is
free and read-only — it costs no completion quota — but it is a live network
call, so this script is never run automatically as part of the test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import load_settings  # noqa: E402
from app.llm_client import OpenRouterClient  # noqa: E402

OK = "[OK]"
BAD = "[!!]"


def is_free(model: dict) -> bool:
    """Free = both prompt and completion cost exactly zero.

    `:free` in the id is a convention, not a guarantee, so check real pricing.
    Missing or unparseable pricing counts as NOT free: the whole point of this
    check is to prevent accidental spend, so it must fail toward caution.
    """
    pricing = model.get("pricing") or {}
    try:
        return float(pricing["prompt"]) == 0 and float(pricing["completion"]) == 0
    except (KeyError, TypeError, ValueError):
        return False


async def main() -> int:
    parser = argparse.ArgumentParser(description="Verify configured OpenRouter models.")
    parser.add_argument("--list", action="store_true", help="list all free models")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.has_api_key:
        print(f"{BAD} OPENROUTER_API_KEY is not set. Add it to .env first.")
        return 2

    cfg = settings.openrouter
    configured = {
        "white": cfg.white_model,
        "black": cfg.black_model,
        "analyst": cfg.analyst_model,
        "fallback": cfg.fallback_model,
    }

    print(f"Fetching model list from {cfg.base_url}/models …\n")
    async with OpenRouterClient(settings) as client:
        try:
            models = await client.list_models()
        except Exception as exc:
            print(f"{BAD} Could not reach OpenRouter: {exc}")
            return 2

    by_id = {m.get("id"): m for m in models}
    free_ids = sorted(mid for mid, m in by_id.items() if is_free(m))
    print(f"OpenRouter lists {len(by_id)} models, {len(free_ids)} of them free.\n")

    problems = 0
    for role, model_id in configured.items():
        model = by_id.get(model_id)
        if model is None:
            # The auto-router isn't always enumerated in /models; it still works.
            note = " (auto-router — not listed, used as fallback)" if model_id == cfg.fallback_model else ""
            if note:
                print(f"{OK}  {role:<9} {model_id}{note}")
            else:
                print(f"{BAD} {role:<9} {model_id} — NOT FOUND. The id may have rotated.")
                problems += 1
            continue

        if is_free(model):
            ctx = model.get("context_length", "?")
            print(f"{OK}  {role:<9} {model_id} — free, context {ctx}")
        else:
            pricing = model.get("pricing", {})
            print(f"{BAD} {role:<9} {model_id} — EXISTS BUT IS NOT FREE: {pricing}")
            problems += 1

    if args.list:
        print("\nFree models available today:")
        for model_id in free_ids:
            print(f"  {model_id}")

    if problems:
        print(
            f"\n{BAD} {problems} problem(s). Fix config.yaml before running live "
            f"(or rely on {cfg.fallback_model})."
        )
        return 1

    print("\nAll configured models are live and free. Safe to run live.")
    print(
        "Reminder: one game is ~60–120 requests. The free tier allows ~50/day "
        "(1,000/day with $10 of credits ever purchased) — lower game.max_moves "
        "for a smoke test if you're on the 50/day tier."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Move commentator (Feature 2): short, spoken-style play-by-play per move.

The commentary is what the browser reads aloud in sync with the board, so it
must always exist — a silent broadcast is worse than a plain one. `MockCommentator`
and every failure path fall back to `template_move_commentary`, built purely from
engine facts (SAN + capture/check flags), which is also what mock mode always
uses (§2.6).
"""

from __future__ import annotations

import logging
import re
import zlib

from app.agents.base import BaseCommentator, CommentaryResult, MoveContext
from app.llm_client import LLMError, OpenRouterClient
from app.prompts import build_commentator_system, build_commentator_user

log = logging.getLogger(__name__)

PIECE_WORDS = {"N": "knight", "B": "bishop", "R": "rook", "Q": "queen", "K": "king"}
CAPTURED_NAMES = {
    "p": "pawn",
    "n": "knight",
    "b": "bishop",
    "r": "rook",
    "q": "queen",
    "k": "king",
}

_SQUARE_RE = re.compile(r"[a-h][1-8]")


def _dest_square(san_core: str) -> str:
    """The destination square from a SAN move with check/mate/promo stripped."""
    squares = _SQUARE_RE.findall(san_core)
    return squares[-1] if squares else san_core[-2:]


def _variant(options: list[str], ctx: MoveContext) -> str:
    """Pick one phrasing, deterministically from the move+position.

    Same move → same line (keeps the fallback testable and replay-stable), but
    different moves spread across the bank, so a game reads with real variety
    instead of the same sentence every time.
    """
    key = f"{ctx.san}|{ctx.fen}".encode()
    return options[zlib.crc32(key) % len(options)]


def template_move_commentary(ctx: MoveContext) -> str:
    """Commentary from engine facts alone — the never-silent fallback (§F2e).

    Broadcaster-flavoured and varied, e.g. "Oh, knight gone! The bishop takes on
    f6! Check — the king's in trouble!". Still 100% fact-driven (piece, square,
    capture/check/mate/castle/promotion) and deterministic per position.
    """
    san = ctx.san
    mover = ctx.mover

    if san.startswith("O-O-O"):
        return _finish(ctx, _variant([
            f"{mover} castles queenside — the king dives for cover!",
            f"Queenside castle for {mover}! Rook swung into the game.",
            f"{mover} goes long, castling queenside and connecting the rooks!",
        ], ctx))
    if san.startswith("O-O"):
        return _finish(ctx, _variant([
            f"{mover} castles kingside — king bolts for the corner!",
            f"Kingside castle for {mover}! The king tucks away safely.",
            f"{mover} castles kingside and the rook springs to life!",
        ], ctx))

    core = san.rstrip("+#")
    promo = None
    if "=" in core:
        core, promo_tail = core.split("=", 1)
        promo = PIECE_WORDS.get(promo_tail[:1])
    dest = _dest_square(core)
    piece = PIECE_WORDS.get(san[0], "pawn")

    if promo:
        return _finish(ctx, _variant([
            f"Promotion on {dest}! {mover} brings a brand-new {promo} to the board!",
            f"{mover} promotes on {dest} — say hello to a fresh {promo}!",
            f"All the way home! {mover}'s pawn is promoted to a {promo} on {dest}!",
        ], ctx))

    captured = CAPTURED_NAMES.get((ctx.captured_piece or "").lower())
    if ctx.is_capture and captured and captured != "king":
        return _finish(ctx, _variant([
            f"{mover}'s {piece} crashes into {dest} and grabs the {captured}!",
            f"Oh, the {captured} is gone! {mover}'s {piece} takes on {dest}!",
            f"{mover} strikes — the {piece} snaps up the {captured} on {dest}!",
            f"Material down! {mover}'s {piece} takes the {captured} on {dest}!",
        ], ctx))
    if ctx.is_capture:
        return _finish(ctx, _variant([
            f"{mover}'s {piece} takes on {dest}!",
            f"Capture! {mover}'s {piece} strikes on {dest}!",
            f"{mover} swipes it — the {piece} takes on {dest}!",
        ], ctx))
    if piece == "pawn":
        return _finish(ctx, _variant([
            f"{mover} pushes a pawn to {dest}.",
            f"{mover} nudges a pawn up to {dest}, grabbing space.",
            f"A pawn jab to {dest} from {mover}.",
            f"{mover} plants a pawn on {dest}.",
        ], ctx))
    return _finish(ctx, _variant([
        f"{mover} swings the {piece} to {dest}.",
        f"{mover} develops the {piece} to {dest}, eyeing the fight.",
        f"The {piece} slides to {dest} for {mover}.",
        f"{mover} lifts the {piece} into {dest}.",
    ], ctx))


def _finish(ctx: MoveContext, line: str) -> str:
    """Cap a line with the drama the move earned — mate steals the show, a
    check gets a shout."""
    if ctx.is_checkmate:
        return _variant([
            f"CHECKMATE! {ctx.mover} plays {ctx.san} and that's the ballgame!",
            f"That's mate! {ctx.san} — {ctx.mover} finishes in style!",
            f"Game over — {ctx.san}, checkmate! What a way for {ctx.mover} to close it out!",
        ], ctx)
    if ctx.is_check:
        return line + " " + _variant([
            "And that's check!",
            "Check — the king's in trouble!",
            "Check! Pressure on the king!",
        ], ctx)
    return line


class MockCommentator(BaseCommentator):
    """Template commentary only. Zero API calls (§2.6)."""

    def __init__(self, model: str = "mock-commentator") -> None:
        super().__init__(model=model)

    async def comment_move(self, ctx: MoveContext) -> CommentaryResult:
        return CommentaryResult(template_move_commentary(ctx), "template")


class CommentatorAgent(BaseCommentator):
    """A real LLM broadcaster (§F2a). Reuses the shared client + throttle.

    On any quota/transport/empty failure it degrades to the engine-fact template
    so the voice never cuts out mid-game (§F2e).
    """

    def __init__(
        self,
        model: str,
        client: OpenRouterClient,
        *,
        temperature: float = 0.8,  # a little life in the delivery
        max_tokens: int = 120,
    ) -> None:
        super().__init__(model=model)
        self.client = client
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def comment_move(self, ctx: MoveContext) -> CommentaryResult:
        try:
            response = await self.client.complete(
                model=self.model,
                messages=[
                    {"role": "system", "content": build_commentator_system()},
                    {
                        "role": "user",
                        "content": build_commentator_user(
                            mover=ctx.mover,
                            san=ctx.san,
                            fen=ctx.fen,
                            reasoning=ctx.reasoning,
                            is_capture=ctx.is_capture,
                            is_check=ctx.is_check,
                            is_checkmate=ctx.is_checkmate,
                        ),
                    },
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                json_object=False,  # plain spoken prose, not JSON
            )
        except LLMError as exc:
            log.info("Commentary fell back to template: %s", exc)
            return CommentaryResult(template_move_commentary(ctx), "template")

        text = " ".join((response.content or "").split())[:240]
        # An empty completion is a fallback, not a dropped line.
        if text:
            return CommentaryResult(text, "model")
        return CommentaryResult(template_move_commentary(ctx), "template")

"""Move commentator (Feature 2): the never-silent template + the live agent's
fallback to it. Everything offline."""

from __future__ import annotations

import httpx
import pytest

from app.agents.base import MoveContext
from app.agents.commentator import (
    CommentatorAgent,
    MockCommentator,
    template_move_commentary,
)
from app.config import load_settings
from app.llm_client import OpenRouterClient, Throttle


def ctx(**overrides) -> MoveContext:
    base = dict(
        mover="White",
        color="white",
        san="e4",
        fen="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        reasoning="Taking the centre.",
        is_capture=False,
        is_check=False,
        is_checkmate=False,
        captured_piece=None,
    )
    base.update(overrides)
    return MoveContext(**base)


class TestTemplateCommentary:
    """Punchier, varied phrasings — assert the facts survive, not exact strings
    (the wording is chosen from a bank per position)."""

    def test_quiet_pawn_move_names_the_square(self):
        line = template_move_commentary(ctx(san="e4"))
        assert "e4" in line and "pawn" in line.lower() and line.startswith("White")

    def test_piece_move_names_piece_and_square(self):
        line = template_move_commentary(ctx(san="Nf3"))
        assert "knight" in line.lower() and "f3" in line

    def test_capture_with_check_reads_naturally(self):
        line = template_move_commentary(
            ctx(mover="Black", color="black", san="Nxf6+", is_capture=True, is_check=True,
                captured_piece="n")
        )
        assert "f6" in line and "knight" in line.lower()
        assert "check" in line.lower()

    def test_checkmate_calls_the_game(self):
        line = template_move_commentary(ctx(san="Qxh7#", is_capture=True, is_checkmate=True,
                                            captured_piece="p"))
        assert "mate" in line.lower() and "Qxh7" in line

    def test_castling_kingside_and_queenside(self):
        assert "kingside" in template_move_commentary(ctx(san="O-O"))
        assert "queenside" in template_move_commentary(ctx(san="O-O-O"))

    def test_promotion_announces_a_new_piece(self):
        line = template_move_commentary(ctx(san="e8=Q"))
        assert "promot" in line.lower() and "queen" in line and "e8" in line

    def test_capture_without_known_piece_still_reads(self):
        line = template_move_commentary(ctx(san="Bxc6", is_capture=True, captured_piece=None))
        assert "c6" in line and "bishop" in line.lower()

    def test_variety_same_move_stable_different_moves_differ(self):
        # Deterministic per position (replay-stable) but varied across moves.
        a = template_move_commentary(ctx(san="Nf3"))
        assert a == template_move_commentary(ctx(san="Nf3"))
        lines = {
            template_move_commentary(ctx(san=s, fen=f"fen-{s}"))
            for s in ("Nf3", "Nc3", "Bb5", "Bg2", "Rd1", "Qe2")
        }
        assert len(lines) >= 3  # not all identical boilerplate


@pytest.mark.asyncio
class TestMockCommentator:
    async def test_uses_the_template_and_is_labelled_template(self):
        result = await MockCommentator().comment_move(ctx(san="Nf3"))
        assert "knight" in result.text.lower() and "f3" in result.text
        assert result.source == "template"


@pytest.mark.asyncio
class TestCommentatorAgentFallback:
    @pytest.fixture
    def settings(self):
        s = load_settings()
        s.secrets.openrouter_api_key = "sk-or-test-not-a-real-key"
        return s

    def _client(self, settings, handler) -> OpenRouterClient:
        transport = httpx.MockTransport(handler)
        http = httpx.AsyncClient(transport=transport, base_url="https://openrouter.test/api/v1")
        return OpenRouterClient(settings, client=http, throttle=Throttle(0), sleep=_no_sleep)

    async def test_falls_back_to_template_when_the_model_errors(self, settings):
        # Every request 500s past the retry ladder → LLMError → template.
        client = self._client(settings, lambda _r: httpx.Response(500, text="boom"))
        agent = CommentatorAgent(model="x/commentator:free", client=client)
        result = await agent.comment_move(ctx(san="Nf3"))
        assert "knight" in result.text.lower() and "f3" in result.text
        # A rate-limited/errored fallback must be labelled template, not model —
        # the exact mislabel the first live run exposed.
        assert result.source == "template"

    async def test_uses_the_models_line_when_it_answers(self, settings):
        def handler(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "  What a move!  "}}]},
            )

        client = self._client(settings, handler)
        agent = CommentatorAgent(model="x/commentator:free", client=client)
        result = await agent.comment_move(ctx(san="Nf3"))
        assert result.text == "What a move!"
        assert result.source == "model"

    async def test_empty_model_reply_falls_back_to_template(self, settings):
        def handler(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})

        client = self._client(settings, handler)
        agent = CommentatorAgent(model="x/commentator:free", client=client)
        # Empty content raises EmptyResponseError (an LLMError) → template.
        result = await agent.comment_move(ctx(san="e4"))
        assert "e4" in result.text and result.source == "template"


async def _no_sleep(_seconds: float) -> None:
    return None

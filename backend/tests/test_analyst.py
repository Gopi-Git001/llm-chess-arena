"""Analyst tests (PLAN.md §11 Phase 4), all offline.

Two things must hold no matter what the model does:
  1. A verdict always comes back — parse failure falls back to the template.
  2. The verdict never contradicts the engine about who won (§3).
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.agents.analyst import AnalystAgent, MockAnalyst
from app.config import load_settings
from app.llm_client import OpenRouterClient, Throttle
from app.verdict import GameFacts

GOOD_VERDICT = {
    "winner": "white",
    "result_explanation": "White mated on move 2.",
    "key_moments": ["1. e4 grabbed the centre", "2. Qh5 threatened mate", "2... g6 lost"],
    "white_grade": "A",
    "black_grade": "D",
    "blunders": ["2... g6 allowed mate"],
    "best_move": "Qh5",
    "illegal_move_summary": "Both models followed the rules.",
    "verdict_paragraph": "A short, brutal game. White pounced.",
}


def move(ply=1, color="white", san="e4", attempts=1, forfeited=0, is_capture=0):
    return {
        "ply": ply,
        "move_number": (ply + 1) // 2,
        "color": color,
        "san": san,
        "uci": "e2e4",
        "fen_after": "fen-here",
        "attempts": attempts,
        "forfeited": forfeited,
        "is_capture": is_capture,
        "is_check": 0,
        "captured_piece": None,
        "reasoning": "",
    }


def facts(**kwargs) -> GameFacts:
    defaults = dict(
        game_id="g1",
        white_model="white/model:free",
        black_model="black/model:free",
        result="1-0",
        termination="checkmate",
        winner="white",
        ply_count=4,
        pgn="1. e4 e5 2. Qh5 Nc6 1-0",
        moves=[move(1), move(2, "black", "e5"), move(3, "white", "Qh5"), move(4, "black", "Nc6")],
    )
    defaults.update(kwargs)
    return GameFacts(**defaults)


@pytest.fixture
def settings():
    settings = load_settings()
    settings.secrets.openrouter_api_key = "sk-or-test-not-a-real-key"
    return settings


async def _no_sleep(_s: float) -> None:
    return None


def analyst_with_replies(settings, replies: list) -> tuple[AnalystAgent, dict]:
    state = {"n": 0, "prompts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state["prompts"].append(body["messages"][-1]["content"])
        index = min(state["n"], len(replies) - 1)
        state["n"] += 1
        reply = replies[index]
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(
            200,
            json={"model": body["model"], "choices": [{"message": {"content": reply}}]},
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.test/api/v1"
    )
    client = OpenRouterClient(settings, client=http, throttle=Throttle(0), sleep=_no_sleep)
    return AnalystAgent(model="analyst/model:free", client=client), state


class TestMockAnalyst:
    async def test_produces_a_template_verdict(self):
        """§11 Phase 4 verify: mock mode produces a template verdict."""
        verdict = await MockAnalyst().review(facts())

        assert verdict.source == "template"
        assert verdict.engine_result == "1-0"
        assert verdict.winner == "white"
        assert verdict.verdict_paragraph
        assert verdict.analyst_model == "mock-analyst"

    async def test_costs_nothing_and_needs_no_client(self):
        analyst = MockAnalyst()
        assert not hasattr(analyst, "client")
        await analyst.review(facts())

    async def test_commentary_is_canned_and_deterministic_with_a_seed(self):
        first = await MockAnalyst(seed=5).comment(facts())
        second = await MockAnalyst(seed=5).comment(facts())

        assert first and first == second


class TestAnalystHappyPath:
    async def test_valid_json_verdict_is_used(self, settings):
        analyst, state = analyst_with_replies(settings, [json.dumps(GOOD_VERDICT)])
        verdict = await analyst.review(facts())

        assert verdict.source == "analyst"
        assert verdict.best_move == "Qh5"
        assert verdict.white_grade == "A"
        assert verdict.blunders == ["2... g6 allowed mate"]
        assert len(verdict.key_moments) == 3
        assert state["n"] == 1

    async def test_verdict_survives_markdown_fences(self, settings):
        analyst, _ = analyst_with_replies(
            settings, [f"```json\n{json.dumps(GOOD_VERDICT)}\n```"]
        )
        assert (await analyst.review(facts())).source == "analyst"

    async def test_verdict_survives_prose_around_the_json(self, settings):
        analyst, _ = analyst_with_replies(
            settings, [f"Here you go!\n\n{json.dumps(GOOD_VERDICT)}\n\nGreat game."]
        )
        assert (await analyst.review(facts())).best_move == "Qh5"

    async def test_unknown_extra_fields_are_ignored(self, settings):
        payload = {**GOOD_VERDICT, "confidence": 0.8, "elo_estimate": 400}
        analyst, _ = analyst_with_replies(settings, [json.dumps(payload)])

        assert (await analyst.review(facts())).source == "analyst"

    async def test_engine_facts_are_stamped_onto_a_model_verdict(self, settings):
        analyst, _ = analyst_with_replies(settings, [json.dumps(GOOD_VERDICT)])
        verdict = await analyst.review(facts())

        assert verdict.engine_result == "1-0"
        assert verdict.engine_termination == "checkmate"
        assert verdict.analyst_model == "analyst/model:free"


class TestAnalystPrompt:
    async def test_prompt_carries_pgn_result_and_annotations(self, settings):
        analyst, state = analyst_with_replies(settings, [json.dumps(GOOD_VERDICT)])
        await analyst.review(
            facts(moves=[move(1, san="e4", attempts=3), move(2, "black", "e5", forfeited=1)])
        )

        prompt = state["prompts"][0]
        assert "1. e4 e5 2. Qh5 Nc6 1-0" in prompt
        assert "1-0" in prompt and "checkmate" in prompt
        assert "e4(retries:2)" in prompt
        assert "e5(forfeit:random)" in prompt
        assert "white/model:free" in prompt

    async def test_prompt_reports_rule_breaking_per_model(self, settings):
        analyst, state = analyst_with_replies(settings, [json.dumps(GOOD_VERDICT)])
        await analyst.review(facts(moves=[move(1, attempts=3), move(2, "black", forfeited=1)]))

        prompt = state["prompts"][0]
        assert "White: 2 illegal move(s) proposed, 0 turn(s) forfeited" in prompt
        assert "Black: 0 illegal move(s) proposed, 1 turn(s) forfeited" in prompt


class TestAnalystFallback:
    async def test_retries_once_then_falls_back(self, settings):
        """§11 Phase 4: retry once on parse failure, then template."""
        analyst, state = analyst_with_replies(settings, ["not json at all"])
        verdict = await analyst.review(facts())

        assert state["n"] == 2, "one try, one retry — not more"
        assert verdict.source == "template"
        assert verdict.engine_result == "1-0"

    async def test_retry_prompt_says_what_went_wrong(self, settings):
        analyst, state = analyst_with_replies(settings, ["garbage"])
        await analyst.review(facts())

        assert "not valid JSON" in state["prompts"][1]

    async def test_a_good_retry_is_accepted(self, settings):
        analyst, state = analyst_with_replies(settings, ["garbage", json.dumps(GOOD_VERDICT)])
        verdict = await analyst.review(facts())

        assert verdict.source == "analyst"
        assert state["n"] == 2

    async def test_unreachable_model_falls_back_without_raising(self, settings):
        analyst, _ = analyst_with_replies(settings, [httpx.Response(401)])
        verdict = await analyst.review(facts())

        assert verdict.source == "template"
        assert "unreachable" in verdict.verdict_paragraph

    async def test_empty_response_falls_back(self, settings):
        analyst, _ = analyst_with_replies(settings, [""])
        assert (await analyst.review(facts())).source == "template"

    async def test_json_that_is_not_an_object_falls_back(self, settings):
        analyst, _ = analyst_with_replies(settings, ["[1, 2, 3]"])
        assert (await analyst.review(facts())).source == "template"

    async def test_fallback_verdict_still_reports_the_real_result(self, settings):
        analyst, _ = analyst_with_replies(settings, ["garbage"])
        verdict = await analyst.review(
            facts(result="0-1", winner="black", termination="stalemate")
        )

        assert verdict.engine_result == "0-1"
        assert verdict.winner == "black"


class TestAnalystCannotRewriteHistory:
    """§3: the Analyst does not override the result."""

    async def test_a_model_awarding_the_win_to_the_loser_is_corrected(self, settings):
        lying = {**GOOD_VERDICT, "winner": "black", "verdict_paragraph": "Black clearly won."}
        analyst, _ = analyst_with_replies(settings, [json.dumps(lying)])

        verdict = await analyst.review(facts(result="1-0", winner="white"))
        assert verdict.winner == "white", "the engine decided this, not the model"

    async def test_a_model_may_pick_a_performance_winner_in_a_draw(self, settings):
        opinion = {**GOOD_VERDICT, "winner": "black"}
        analyst, _ = analyst_with_replies(settings, [json.dumps(opinion)])

        verdict = await analyst.review(
            facts(result="1/2-1/2", winner=None, termination="stalemate")
        )
        assert verdict.winner == "black"
        assert verdict.engine_winner is None
        assert verdict.engine_result == "1/2-1/2"

    async def test_model_cannot_fake_the_engine_fields(self, settings):
        forged = {
            **GOOD_VERDICT,
            "engine_result": "0-1",
            "engine_winner": "black",
            "engine_termination": "resignation",
            "source": "analyst",
        }
        analyst, _ = analyst_with_replies(settings, [json.dumps(forged)])

        verdict = await analyst.review(facts(result="1-0", winner="white"))
        assert verdict.engine_result == "1-0"
        assert verdict.engine_winner == "white"
        assert verdict.engine_termination == "checkmate"


class TestCommentary:
    async def test_commentary_returns_one_line(self, settings):
        analyst, state = analyst_with_replies(settings, ["  White is\n  pressing hard.  "])
        text = await analyst.comment(facts())

        assert text == "White is pressing hard."

    async def test_commentary_asks_for_prose_not_json(self, settings):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["body"] = body
            return httpx.Response(
                200, json={"model": body["model"], "choices": [{"message": {"content": "Hi."}}]}
            )

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://openrouter.test/api/v1"
        )
        client = OpenRouterClient(settings, client=http, throttle=Throttle(0), sleep=_no_sleep)
        await AnalystAgent("analyst/model", client).comment(facts())

        assert "response_format" not in captured["body"]

    async def test_commentary_failure_is_silent_not_fatal(self, settings):
        analyst, _ = analyst_with_replies(settings, [httpx.Response(500)])
        assert await analyst.comment(facts()) == ""

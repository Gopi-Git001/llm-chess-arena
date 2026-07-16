"""PlayerAgent tests: the illegal-move retry loop (PLAN.md §6d), offline.

A scripted transport plays the part of a misbehaving free model. No network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.agents.player import NO_MOVE, PlayerAgent
from app.config import load_settings
from app.engine import ChessEngine
from app.llm_client import OpenRouterClient, Throttle

START_FEN = ChessEngine().fen
LEGAL = ChessEngine().legal_moves_uci()


@pytest.fixture
def settings():
    settings = load_settings()
    settings.secrets.openrouter_api_key = "sk-or-test-not-a-real-key"
    return settings


def agent_with_replies(settings, replies: list, **kwargs) -> tuple[PlayerAgent, dict]:
    """`replies` are returned one per request. An entry may be:
    a str (the model's content), or an httpx.Response for transport failures.
    """
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
            json={
                "model": body["model"],
                "choices": [{"message": {"role": "assistant", "content": reply}}],
            },
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.test/api/v1"
    )
    client = OpenRouterClient(
        settings, client=http, throttle=Throttle(0), sleep=_no_sleep, **kwargs
    )
    return PlayerAgent(model="test/model:free", color="white", client=client), state


async def _no_sleep(_s: float) -> None:
    return None


async def get_move(agent: PlayerAgent, retry_budget: int = 3):
    return await agent.get_move(
        fen=START_FEN,
        legal_moves=LEGAL,
        move_history_san=[],
        move_number=1,
        retry_budget=retry_budget,
    )


class TestHappyPath:
    async def test_legal_move_first_try(self, settings):
        agent, state = agent_with_replies(
            settings, ['{"move": "e2e4", "reasoning": "Centre first."}']
        )
        proposal = await get_move(agent)

        assert proposal.uci == "e2e4"
        assert proposal.reasoning == "Centre first."
        assert proposal.attempts == 1
        assert proposal.forfeited is False
        assert proposal.illegal_attempts == []
        assert state["n"] == 1

    async def test_prompt_carries_fen_and_the_legal_move_list(self, settings):
        """§2.2: never ask a model to just play chess."""
        agent, state = agent_with_replies(settings, ['{"move": "e2e4"}'])
        await get_move(agent)

        prompt = state["prompts"][0]
        assert START_FEN in prompt
        assert "e2e4" in prompt and "g1f3" in prompt
        assert "Move 1" in prompt

    async def test_missing_reasoning_gets_a_placeholder(self, settings):
        agent, _ = agent_with_replies(settings, ['{"move": "e2e4"}'])
        assert (await get_move(agent)).reasoning == "(no reasoning given)"


class TestRetryLoop:
    async def test_illegal_move_is_retried_and_recovered(self, settings):
        agent, state = agent_with_replies(
            settings, ['{"move": "e2e5"}', '{"move": "e2e4", "reasoning": "Fine."}']
        )
        proposal = await get_move(agent)

        assert proposal.uci == "e2e4"
        assert proposal.attempts == 2
        assert proposal.illegal_attempts == ["e2e5"]
        assert proposal.forfeited is False

    async def test_retry_prompt_names_the_bad_move_and_relists_legal_moves(self, settings):
        agent, state = agent_with_replies(settings, ['{"move": "e2e5"}', '{"move": "e2e4"}'])
        await get_move(agent)

        retry_prompt = state["prompts"][1]
        assert "'e2e5' is ILLEGAL" in retry_prompt
        assert "e2e4" in retry_prompt

    async def test_unparseable_response_costs_a_retry(self, settings):
        agent, state = agent_with_replies(settings, ["I refuse to play.", '{"move": "d2d4"}'])
        proposal = await get_move(agent)

        assert proposal.uci == "d2d4"
        assert proposal.attempts == 2
        assert proposal.illegal_attempts == ["I refuse to play."]

    async def test_empty_content_costs_a_retry(self, settings):
        """§8 empty-content guard."""
        agent, _ = agent_with_replies(settings, ["", '{"move": "e2e4"}'])
        proposal = await get_move(agent)

        assert proposal.uci == "e2e4"
        assert proposal.attempts == 2

    async def test_retry_prompt_quotes_what_the_model_actually_said(self, settings):
        agent, state = agent_with_replies(settings, ["Nf3 is my move", '{"move": "g1f3"}'])
        await get_move(agent)

        assert "Nf3 is my move" in state["prompts"][1]

    async def test_budget_is_respected(self, settings):
        agent, state = agent_with_replies(settings, ['{"move": "e2e5"}'])
        proposal = await get_move(agent, retry_budget=3)

        assert state["n"] == 3, "must stop at the budget, not keep paying"
        assert proposal.attempts == 3

    async def test_budget_of_one_means_no_retry(self, settings):
        agent, state = agent_with_replies(settings, ['{"move": "e2e5"}'])
        await get_move(agent, retry_budget=1)

        assert state["n"] == 1


class TestForfeitHandoff:
    """The agent never plays a move itself — it hands a bad answer to the
    orchestrator, which substitutes a random legal move (§2.1, §2.3)."""

    async def test_exhausted_budget_returns_the_last_illegal_move(self, settings):
        agent, _ = agent_with_replies(settings, ['{"move": "e2e5"}'])
        proposal = await get_move(agent)

        assert proposal.uci == "e2e5"
        assert proposal.uci not in LEGAL, "orchestrator must see this as illegal and forfeit"
        assert proposal.forfeited is False, "forfeiting is the orchestrator's call"
        assert proposal.illegal_attempts == ["e2e5", "e2e5", "e2e5"]

    async def test_never_parseable_returns_the_no_move_sentinel(self, settings):
        agent, _ = agent_with_replies(settings, ["absolute nonsense"])
        proposal = await get_move(agent)

        assert proposal.uci == NO_MOVE
        assert proposal.uci not in LEGAL
        assert len(proposal.illegal_attempts) == 3

    async def test_agent_never_returns_a_legal_move_it_did_not_choose(self, settings):
        """Regression-in-spirit of the MockPlayer forfeit bug: an agent that
        fails must not quietly hand back a legal move, or the orchestrator's
        legality check passes and no MOVE_FORFEITED is ever emitted."""
        agent, _ = agent_with_replies(settings, ['{"move": "e2e5"}'])
        assert (await get_move(agent)).uci not in LEGAL

    async def test_transport_failure_forfeits_rather_than_hanging(self, settings):
        agent, _ = agent_with_replies(settings, [httpx.Response(401)])
        proposal = await get_move(agent)

        assert proposal.uci == NO_MOVE
        assert "unreachable" in proposal.reasoning

    async def test_rate_limit_exhaustion_forfeits(self, settings):
        agent, _ = agent_with_replies(settings, [httpx.Response(429)])
        proposal = await get_move(agent)

        assert proposal.uci == NO_MOVE

    async def test_no_legal_moves_is_a_programming_error(self, settings):
        agent, _ = agent_with_replies(settings, ['{"move": "e2e4"}'])

        with pytest.raises(ValueError, match="no legal moves"):
            await agent.get_move(
                fen=START_FEN, legal_moves=[], move_history_san=[], move_number=1
            )


class TestQuotaAccounting:
    async def test_each_retry_costs_a_request(self, settings):
        agent, _ = agent_with_replies(settings, ['{"move": "e2e5"}'])
        await get_move(agent, retry_budget=3)

        assert agent.client.requests_used == 3

    async def test_a_clean_move_costs_exactly_one(self, settings):
        agent, _ = agent_with_replies(settings, ['{"move": "e2e4"}'])
        await get_move(agent)

        assert agent.client.requests_used == 1

"""Players: MockPlayer (free, random) and PlayerAgent (a real LLM via OpenRouter).

MockPlayer exists so the entire system — orchestrator, events, store, UI — can
be built and tested without spending a single API request (PLAN.md §2.6).
"""

from __future__ import annotations

import logging
import random

from app.agents.base import BasePlayer, MoveProposal
from app.llm_client import EmptyResponseError, LLMError, OpenRouterClient
from app.parsing import ParseError, parse_move_response
from app.prompts import (
    build_player_system,
    build_player_user,
    build_retry_feedback,
    illegal_move_problem,
    unparseable_problem,
)

log = logging.getLogger(__name__)

CANNED_REASONING = [
    "Developing with tempo and hoping nobody notices the plan.",
    "This move radiates confidence, which is nearly as good as being correct.",
    "Improving the worst-placed piece, as the classics demand.",
    "Grabbing space before my opponent thinks to.",
    "A quiet move. The loud ones come later.",
    "Centralisation first, brilliance second.",
    "I calculated deeply and arrived here more or less on purpose.",
    "Prophylaxis: stopping an idea my opponent had not yet had.",
    "Applying pressure and trusting the position to reward me.",
    "Solid, flexible, and entirely defensible in the post-game interview.",
]


class MockPlayer(BasePlayer):
    """Random legal move + canned reasoning. Zero API calls.

    Pass `seed` for deterministic games in tests. `illegal_rate` fakes a model
    proposing illegal moves, so the retry/forfeit path gets exercised in mock
    mode instead of waiting for a live model to misbehave.
    """

    def __init__(
        self,
        model: str = "mock-player",
        color: str = "white",
        seed: int | None = None,
        illegal_rate: float = 0.0,
        forfeit_rate: float = 0.0,
    ) -> None:
        super().__init__(model=model, color=color)
        self._rng = random.Random(seed)
        self.illegal_rate = illegal_rate
        self.forfeit_rate = forfeit_rate

    async def get_move(
        self,
        fen: str,
        legal_moves: list[str],
        move_history_san: list[str],
        move_number: int,
        retry_budget: int = 3,
    ) -> MoveProposal:
        if not legal_moves:
            raise ValueError("get_move called with no legal moves — game is over")

        illegal_attempts: list[str] = []

        # Simulate a model that burns its whole budget. It proposes an illegal
        # move and stops there: deciding to forfeit is the orchestrator's job,
        # not the agent's (§2.1). A real model fails exactly this way — it never
        # helpfully hands back a legal move on its way out.
        if self._rng.random() < self.forfeit_rate:
            for _ in range(retry_budget - 1):
                illegal_attempts.append(self._fake_illegal_move(legal_moves))
            return MoveProposal(
                uci=self._fake_illegal_move(legal_moves),
                reasoning="(model never produced a legal move)",
                attempts=retry_budget,
                forfeited=False,
                illegal_attempts=illegal_attempts,
            )

        # Simulate a model that stumbles, then recovers within budget.
        while len(illegal_attempts) < retry_budget - 1 and self._rng.random() < self.illegal_rate:
            illegal_attempts.append(self._fake_illegal_move(legal_moves))

        return MoveProposal(
            uci=self._rng.choice(legal_moves),
            reasoning=self._rng.choice(CANNED_REASONING),
            attempts=len(illegal_attempts) + 1,
            forfeited=False,
            illegal_attempts=illegal_attempts,
        )

    def _fake_illegal_move(self, legal_moves: list[str]) -> str:
        """A well-formed UCI string that isn't in the legal list — exactly the
        failure mode real models produce most often."""
        files, ranks = "abcdefgh", "12345678"
        for _ in range(20):
            candidate = (
                self._rng.choice(files)
                + self._rng.choice(ranks)
                + self._rng.choice(files)
                + self._rng.choice(ranks)
            )
            if candidate not in legal_moves and candidate[:2] != candidate[2:]:
                return candidate
        return "e2e5"


# What we hand back when the model never produced anything move-shaped. Not a
# legal move in any position, so the orchestrator forfeits the turn (§2.3).
NO_MOVE = "0000"


class PlayerAgent(BasePlayer):
    """A real LLM playing one side, via OpenRouter.

    Owns the *retry* loop (re-prompt with the error appended, §6d) but not the
    *forfeit* decision: when the budget runs out it returns the model's last bad
    answer and lets the orchestrator substitute a random legal move. Keeping
    that authority in one place is what stops an agent from quietly playing a
    move nobody validated (§2.1).
    """

    def __init__(
        self,
        model: str,
        color: str,
        client: OpenRouterClient,
        *,
        temperature: float = 0.7,
        max_tokens: int = 300,
    ) -> None:
        super().__init__(model=model, color=color)
        self.client = client
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def get_move(
        self,
        fen: str,
        legal_moves: list[str],
        move_history_san: list[str],
        move_number: int,
        retry_budget: int = 3,
    ) -> MoveProposal:
        if not legal_moves:
            raise ValueError("get_move called with no legal moves — game is over")

        system = build_player_system(self.model, self.color)
        rejected: list[str] = []
        feedback = ""
        last_bad = NO_MOVE

        for attempt in range(1, retry_budget + 1):
            user = build_player_user(
                fen=fen,
                color=self.color,
                move_number=move_number,
                legal_moves=legal_moves,
                recent_moves=move_history_san,
                retry_feedback=feedback,
            )

            try:
                response = await self.client.complete(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                parsed = parse_move_response(response.content)

            except (ParseError, EmptyResponseError) as exc:
                # Malformed or empty: costs a retry, and the model is told why.
                raw = getattr(exc, "raw", str(exc))
                log.info("%s (%s) unparseable on attempt %s: %s", self.model, self.color, attempt, exc)
                rejected.append(_short(raw))
                last_bad = NO_MOVE
                feedback = build_retry_feedback(unparseable_problem(raw), legal_moves)
                continue

            except LLMError:
                # Transport/quota failure — the ladder in the client is already
                # spent. Forfeit rather than hang the game.
                log.exception("%s (%s) failed hard on attempt %s", self.model, self.color, attempt)
                return MoveProposal(
                    uci=NO_MOVE,
                    reasoning="(model unreachable — random move played)",
                    attempts=attempt,
                    forfeited=False,
                    illegal_attempts=rejected,
                )

            if parsed.uci in legal_moves:
                return MoveProposal(
                    uci=parsed.uci,
                    reasoning=parsed.reasoning or "(no reasoning given)",
                    attempts=attempt,
                    forfeited=False,
                    illegal_attempts=rejected,
                )

            # Well-formed but illegal — the single most common failure (§12).
            log.info("%s (%s) proposed illegal %s", self.model, self.color, parsed.uci)
            rejected.append(parsed.uci)
            last_bad = parsed.uci
            feedback = build_retry_feedback(illegal_move_problem(parsed.uci), legal_moves)

        # Budget exhausted. Hand back the last bad answer; the orchestrator
        # emits MOVE_FORFEITED and plays a random legal move on our behalf.
        return MoveProposal(
            uci=last_bad,
            reasoning="(model never produced a legal move)",
            attempts=retry_budget,
            forfeited=False,
            illegal_attempts=rejected,
        )


def _short(text: str, limit: int = 24) -> str:
    """A compact label for the UI's illegal-attempt badge."""
    cleaned = " ".join(str(text).split())
    return (cleaned[:limit] + "…") if len(cleaned) > limit else (cleaned or "(empty)")

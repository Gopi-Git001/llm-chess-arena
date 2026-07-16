"""Agent contracts. The orchestrator only ever talks to these interfaces, which
is what lets mock mode and live mode be the same code path (PLAN.md §2.6).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class MoveProposal:
    """An agent's answer for one turn.

    A proposal is only ever a *suggestion* — the orchestrator still pushes it
    through the engine, which is the sole authority on legality (§2.1).
    """

    uci: str
    reasoning: str
    attempts: int = 1
    forfeited: bool = False
    # Every rejected UCI, in order. Surfaced to the UI and the Analyst.
    illegal_attempts: list[str] = field(default_factory=list)


class BasePlayer(ABC):
    """Proposes one move per turn."""

    def __init__(self, model: str, color: str) -> None:
        self.model = model
        self.color = color  # "white" | "black"

    @abstractmethod
    async def get_move(
        self,
        fen: str,
        legal_moves: list[str],
        move_history_san: list[str],
        move_number: int,
        retry_budget: int = 3,
    ) -> MoveProposal:
        """Return a proposal. Must never raise for an ordinary bad answer from
        a model: exhaust the retry budget, then forfeit to a random legal move.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.color} model={self.model!r}>"


class BaseAnalyst(ABC):
    """Reviews a finished game and, optionally, comments during it.

    The Analyst explains and grades. It never decides the result — the engine
    already did that, objectively (PLAN.md §3).
    """

    def __init__(self, model: str) -> None:
        self.model = model

    @abstractmethod
    async def review(self, facts: "GameFacts") -> "Verdict":
        """Post-game verdict. Must always return one: on any model or parse
        failure, fall back to a verdict built from engine facts alone."""

    async def comment(self, facts: "GameFacts") -> str:
        """Optional live commentary. Default: silence."""
        return ""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} model={self.model!r}>"

"""Agent contracts. The orchestrator only ever talks to these interfaces, which
is what lets mock mode and live mode be the same code path (PLAN.md §2.6).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

# Receives each reasoning token as it streams, for the "Too Slow" live-thinking
# panel (Feature 1). None in every ordinary mode — presentation only.
TokenSink = Callable[[str], None]


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
        token_sink: TokenSink | None = None,
    ) -> MoveProposal:
        """Return a proposal. Must never raise for an ordinary bad answer from
        a model: exhaust the retry budget, then forfeit to a random legal move.

        When `token_sink` is given ("Too Slow" mode), stream the reasoning to it
        token by token as it's produced. This is presentation only — it must not
        change which move is proposed.
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


@dataclass
class MoveContext:
    """Everything the commentator needs to react to one move (Feature 2)."""

    mover: str  # "White" | "Black"
    color: str  # "white" | "black"
    san: str
    fen: str
    reasoning: str
    is_capture: bool
    is_check: bool
    is_checkmate: bool
    captured_piece: str | None


@dataclass
class CommentaryResult:
    """One move's commentary and where it actually came from.

    `source` reports what really happened for THIS line — "model" when the LLM
    authored it, "template" when it fell back to engine facts — so the label
    never claims a rate-limited fallback was model-authored.
    """

    text: str
    source: str  # "model" | "template"


class BaseCommentator(ABC):
    """Generates short, spoken-style commentary for a single move (Feature 2).

    Must ALWAYS return a line: on any model or quota failure it falls back to a
    template built from engine facts, so the broadcast voice never goes silent.
    """

    def __init__(self, model: str) -> None:
        self.model = model

    @abstractmethod
    async def comment_move(self, ctx: MoveContext) -> CommentaryResult:
        ...

    def __repr__(self) -> str:
        return f"<{type(self).__name__} model={self.model!r}>"

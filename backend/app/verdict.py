"""Verdict schema + the objective facts it's built from (PLAN.md §6 step 6, §3).

The division of labour that matters here:

* **The engine decides the result.** Checkmate, stalemate, draws and
  adjudication are settled before the Analyst is ever called.
* **The Analyst explains and grades.** It may name a *performance winner* that
  differs from the result — "draw, but Qwen played better chess" — but it
  cannot change who won.

`GameFacts` is the evidence: everything in it comes from the engine and the
database, never from a model. `template_verdict()` builds a defensible verdict
from those facts alone, which is both mock mode's output and the fallback when a
live analyst fails (§11 Phase 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_KEY_MOMENTS = 5
MIN_KEY_MOMENTS = 3


@dataclass
class GameFacts:
    """Objective, engine-derived truth about a game. No model opinions here."""

    game_id: str
    white_model: str
    black_model: str
    result: str  # 1-0 | 0-1 | 1/2-1/2 | *
    termination: str | None
    winner: str | None  # engine's winner: white | black | None
    ply_count: int
    pgn: str
    moves: list[dict[str, Any]] = field(default_factory=list)
    requests_used: int = 0

    # --- derived counts, computed from the move rows ---------------------

    def illegal_attempts(self, color: str) -> int:
        return sum(
            max(0, int(m["attempts"]) - 1) for m in self.moves if m["color"] == color
        )

    def forfeits(self, color: str) -> int:
        return sum(1 for m in self.moves if m["color"] == color and m["forfeited"])

    def move_count(self, color: str) -> int:
        return sum(1 for m in self.moves if m["color"] == color)

    def captures(self, color: str) -> int:
        return sum(1 for m in self.moves if m["color"] == color and m.get("is_capture"))

    def annotated_moves(self, limit: int | None = None) -> str:
        """Compact per-move annotations for the analyst prompt (§7)."""
        rows = self.moves if limit is None else self.moves[-limit:]
        parts = []
        for move in rows:
            note = move["san"]
            if move["forfeited"]:
                note += "(forfeit:random)"
            elif int(move["attempts"]) > 1:
                note += f"(retries:{int(move['attempts']) - 1})"
            parts.append(note)
        return " ".join(parts)

    def summary_line(self) -> str:
        return (
            f"{self.result} by {self.termination} in {self.ply_count} plies. "
            f"White ({self.white_model}): {self.illegal_attempts('white')} illegal, "
            f"{self.forfeits('white')} forfeited. "
            f"Black ({self.black_model}): {self.illegal_attempts('black')} illegal, "
            f"{self.forfeits('black')} forfeited."
        )


class Verdict(BaseModel):
    """The §6 verdict schema. Tolerant on input, strict on shape.

    `winner` is the *performance* winner and may disagree with the engine's
    result in a draw. `engine_result`/`engine_termination` are stamped in by the
    backend afterwards, never by the model, so the UI can always show the real
    outcome next to the opinion.
    """

    winner: str = "draw"  # white | black | draw
    result_explanation: str = ""
    key_moments: list[str] = Field(default_factory=list)
    white_grade: str = "?"
    black_grade: str = "?"
    blunders: list[str] = Field(default_factory=list)
    best_move: str = ""
    illegal_move_summary: str = ""
    verdict_paragraph: str = ""

    # Stamped by the backend from engine facts — authoritative.
    engine_result: str = ""
    engine_termination: str | None = None
    engine_winner: str | None = None
    analyst_model: str = ""
    source: str = "analyst"  # analyst | template

    @field_validator("winner", mode="before")
    @classmethod
    def _normalise_winner(cls, value: Any) -> str:
        """Models say "White", "1-0", "White (gpt-oss)", "nobody"… all of which
        mean one of three things."""
        if value is None:
            return "draw"
        text = str(value).strip().lower()
        if text in ("1-0", "white", "w"):
            return "white"
        if text in ("0-1", "black", "b"):
            return "black"
        if "white" in text and "black" not in text:
            return "white"
        if "black" in text and "white" not in text:
            return "black"
        return "draw"

    @field_validator("key_moments", "blunders", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> list[str]:
        """Accept ["..."], [{"move": .., "note": ..}], or a bare string."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if not isinstance(value, list):
            return [str(value)]

        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                # Flatten the shapes models reach for, in rough priority order.
                bits = [
                    str(item[key])
                    for key in ("move", "san", "ply", "move_number", "description", "note", "text", "reason")
                    if key in item and item[key] not in (None, "")
                ]
                text = " — ".join(bits) if bits else str(item)
            else:
                text = str(item)
            if text:
                out.append(text)
        return out

    @field_validator("key_moments")
    @classmethod
    def _cap_key_moments(cls, value: list[str]) -> list[str]:
        return value[:MAX_KEY_MOMENTS]

    @field_validator("white_grade", "black_grade", mode="before")
    @classmethod
    def _clean_grade(cls, value: Any) -> str:
        if value in (None, ""):
            return "?"
        return str(value).strip()[:4]


def grade_for(facts: GameFacts, color: str) -> str:
    """A defensible grade from engine facts alone.

    Only measures what we can actually observe without a chess engine
    evaluation: did the model follow the rules it was explicitly handed? Real
    chess-quality grading needs Stockfish (Phase 6) or a live analyst.
    """
    moves = facts.move_count(color)
    if moves == 0:
        return "?"

    forfeits = facts.forfeits(color)
    illegal = facts.illegal_attempts(color)
    forfeit_rate = forfeits / moves
    illegal_rate = illegal / moves

    if forfeit_rate > 0.25:
        return "F"
    if forfeit_rate > 0.1:
        return "D"
    if illegal_rate > 0.5:
        return "C"
    if illegal_rate > 0.15:
        return "B"
    if illegal > 0:
        return "A-"
    return "A"


def _performance_winner(facts: GameFacts) -> str:
    """Engine result decides a decisive game. Only a draw leaves room for an
    opinion, and then rule-following is the only evidence we have (§3)."""
    if facts.winner:
        return facts.winner

    white_bad = facts.forfeits("white") * 3 + facts.illegal_attempts("white")
    black_bad = facts.forfeits("black") * 3 + facts.illegal_attempts("black")
    if white_bad < black_bad:
        return "white"
    if black_bad < white_bad:
        return "black"
    return "draw"


def template_verdict(facts: GameFacts, reason: str = "") -> Verdict:
    """A verdict from engine facts only — no chess judgement invented.

    Used by MockAnalyst and as the fallback when a live analyst fails (§11).
    Every sentence here is something we can prove from the database.
    """
    white_illegal = facts.illegal_attempts("white")
    black_illegal = facts.illegal_attempts("black")
    white_forfeits = facts.forfeits("white")
    black_forfeits = facts.forfeits("black")
    winner = _performance_winner(facts)

    # Order matters: an adjudicated game was never *won* on the board, whatever
    # the result string says. Check the cap before the winner.
    if facts.termination == "max_moves":
        adjudication = (
            f"and was adjudicated {facts.result} on material"
            if facts.winner
            else f"and was called a draw ({facts.result}) on level material"
        )
        explanation = (
            f"Neither side finished it: the game hit the {facts.ply_count}-ply cap "
            f"{adjudication}."
        )
    elif facts.winner:
        explanation = (
            f"{facts.winner.capitalize()} won by {facts.termination} "
            f"after {facts.ply_count} plies."
        )
    else:
        explanation = f"Drawn by {facts.termination} after {facts.ply_count} plies."

    key_moments: list[str] = []
    first_capture = next((m for m in facts.moves if m.get("is_capture")), None)
    if first_capture:
        key_moments.append(
            f"Move {first_capture['move_number']}: first blood — "
            f"{first_capture['color']} played {first_capture['san']}."
        )
    first_forfeit = next((m for m in facts.moves if m["forfeited"]), None)
    if first_forfeit:
        key_moments.append(
            f"Move {first_forfeit['move_number']}: {first_forfeit['color']} ran out of "
            f"retries and had {first_forfeit['san']} played for it at random."
        )
    checks = [m for m in facts.moves if m.get("is_check")]
    if checks:
        key_moments.append(f"{len(checks)} checks were given across the game.")
    if facts.moves:
        last = facts.moves[-1]
        key_moments.append(
            f"The game ended on move {last['move_number']} with {last['san']} "
            f"({facts.termination})."
        )
    while len(key_moments) < MIN_KEY_MOMENTS:
        # Pad honestly rather than inventing drama.
        key_moments.append(
            f"{facts.ply_count} plies played; "
            f"{facts.captures('white') + facts.captures('black')} captures in total."
        )
        break

    total_illegal = white_illegal + black_illegal
    if total_illegal == 0 and white_forfeits + black_forfeits == 0:
        illegal_summary = "Both agents proposed only legal moves. No penalties."
    else:
        illegal_summary = (
            f"White proposed {white_illegal} illegal move(s) and forfeited "
            f"{white_forfeits} turn(s); Black proposed {black_illegal} and forfeited "
            f"{black_forfeits}. Forfeited turns were played as random legal moves by "
            f"the engine."
        )

    if winner == "draw":
        headline = "An honourable draw, with nothing to separate them."
    elif facts.termination == "max_moves" and facts.winner:
        headline = f"{winner.capitalize()} takes it on material, not on merit."
    elif facts.winner:
        headline = f"{winner.capitalize()} won on the board."
    else:
        headline = (
            f"The engine called it a draw, but {winner} followed the rules more "
            f"reliably and takes the performance points."
        )

    note = f" ({reason})" if reason else ""
    paragraph = (
        f"{headline} {explanation} {illegal_summary} "
        f"This verdict was assembled from engine facts alone — no model reviewed "
        f"the chess itself{note}."
    )

    return Verdict(
        winner=winner,
        result_explanation=explanation,
        key_moments=key_moments[:MAX_KEY_MOMENTS],
        white_grade=grade_for(facts, "white"),
        black_grade=grade_for(facts, "black"),
        blunders=[],  # Real blunder detection needs an evaluation (Phase 6).
        best_move="",
        illegal_move_summary=illegal_summary,
        verdict_paragraph=paragraph,
        engine_result=facts.result,
        engine_termination=facts.termination,
        engine_winner=facts.winner,
        source="template",
    )


def stamp_engine_facts(verdict: Verdict, facts: GameFacts, analyst_model: str) -> Verdict:
    """Overwrite the authoritative fields with engine truth.

    A model that claims the wrong winner gets corrected here rather than
    believed: it explains the result, it does not decide it (§3).
    """
    verdict.engine_result = facts.result
    verdict.engine_termination = facts.termination
    verdict.engine_winner = facts.winner
    verdict.analyst_model = analyst_model

    # A decisive game has exactly one performance winner: the one who won.
    if facts.winner and verdict.winner != facts.winner:
        verdict.winner = facts.winner
    return verdict

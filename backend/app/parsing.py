"""Defensive parsing of model responses (PLAN.md §7, §12).

Free models return malformed JSON *routinely*: markdown fences, prose around the
object, single quotes, SAN instead of UCI, or nothing at all. The chain is:

    strip fences → JSON extract → regex-validate UCI → (caller) legality check

Anything this module cannot turn into a well-formed UCI string raises
`ParseError`, which costs the agent one retry. This module never decides
legality — that's the engine's job (§2.1); it only guarantees *shape*.
"""

from __future__ import annotations

import json
import re

# The strict UCI grammar from §6: from-square, to-square, optional promotion.
UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")

# Same pattern, for digging a move out of prose ("I'll play e2e4!").
UCI_IN_TEXT_RE = re.compile(r"\b([a-h][1-8][a-h][1-8][qrbn]?)\b")

# ```json ... ``` or ``` ... ```
FENCE_RE = re.compile(r"```(?:[a-zA-Z]+)?\s*(.*?)```", re.DOTALL)

# First balanced-looking JSON object. Good enough: we only need the outermost
# {...} span, and json.loads validates it properly afterwards.
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

MAX_REASONING_CHARS = 240


class ParseError(ValueError):
    """The response could not be reduced to a well-formed UCI move.

    Carries `raw` — what the model actually said — so the retry prompt can quote
    it back rather than describing the failure in the abstract.
    """

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


class ParsedMove:
    __slots__ = ("uci", "reasoning")

    def __init__(self, uci: str, reasoning: str) -> None:
        self.uci = uci
        self.reasoning = reasoning

    def __repr__(self) -> str:
        return f"ParsedMove(uci={self.uci!r}, reasoning={self.reasoning!r})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ParsedMove)
            and other.uci == self.uci
            and other.reasoning == self.reasoning
        )


def strip_fences(text: str) -> str:
    """Return the contents of the first markdown code fence, else the text."""
    match = FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def normalise_uci(value: str) -> str | None:
    """Lowercase and validate. Returns None if it isn't a UCI move."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().strip('"\'').replace(" ", "").replace("-", "").lower()
    return candidate if UCI_RE.match(candidate) else None


def extract_json_object(text: str) -> dict | None:
    """First JSON object in the text, or None. Fences should be stripped first."""
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass

    match = JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _clean_reasoning(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:MAX_REASONING_CHARS]


def parse_move_response(content: str) -> ParsedMove:
    """Turn a raw completion into a ParsedMove, or raise ParseError.

    Accepts, in order of preference:
      1. strict JSON  {"move": "e2e4", "reasoning": "..."}
      2. the same wrapped in markdown fences and/or prose
      3. a bare UCI move, with or without surrounding chatter
    """
    if not isinstance(content, str) or not content.strip():
        raise ParseError("empty response", raw=content if isinstance(content, str) else "")

    body = strip_fences(content)

    payload = extract_json_object(body)
    if payload is not None:
        # Models wander between these key names; accept the obvious synonyms
        # rather than burning a retry on a right answer with a wrong label.
        for key in ("move", "uci", "move_uci", "best_move"):
            if key in payload:
                uci = normalise_uci(payload[key])
                if uci:
                    return ParsedMove(uci, _clean_reasoning(payload.get("reasoning")))
                raise ParseError(
                    f"JSON field {key!r} is not a UCI move: {payload[key]!r}", raw=content
                )
        raise ParseError(f"JSON object has no move field: {sorted(payload)!r}", raw=content)

    # No JSON at all — accept a bare move so a right answer in the wrong wrapper
    # still counts. The legality check downstream is what actually protects us.
    match = UCI_IN_TEXT_RE.search(body.lower())
    if match:
        return ParsedMove(match.group(1), "")

    raise ParseError(f"no move found in response: {body[:120]!r}", raw=content)

"""All prompt templates live here (PLAN.md §7).

Two rules drive every template:
1. Never ask a model to "just play chess" — always hand it the FEN *and* the
   explicit legal move list, and demand it pick from that list (§2.2).
2. Keep prompts short. Full PGN history in every request wastes tokens and
   confuses weaker free models; FEN + the last 10 SAN moves is enough.
"""

from __future__ import annotations

PLAYER_SYSTEM = (
    "You are {model_name} playing chess as {color} in an AI-vs-AI exhibition match.\n"
    "You will receive the position and a list of legal moves. You MUST pick your\n"
    "move ONLY from that list. Respond with STRICT JSON, nothing else:\n"
    '{{"move": "<uci from the list>", "reasoning": "<one punchy sentence, max 20 words>"}}'
)

PLAYER_USER = (
    "Position (FEN): {fen}\n"
    "You are {color}. Move {move_number}.\n"
    "Recent moves: {recent_moves}\n"
    "Legal moves (UCI) — choose EXACTLY one from this list:\n"
    "{legal_moves}"
)

# Appended verbatim after a rejected answer, so the model sees precisely what it
# got wrong and what it may choose instead (§6d).
RETRY_FEEDBACK = (
    "\n\nYour previous answer {problem} "
    "Respond with STRICT JSON only, and choose EXACTLY one move from this list:\n"
    "{legal_moves}"
)


def build_player_system(model_name: str, color: str) -> str:
    return PLAYER_SYSTEM.format(model_name=model_name, color=color)


def build_player_user(
    fen: str,
    color: str,
    move_number: int,
    legal_moves: list[str],
    recent_moves: list[str] | None = None,
    retry_feedback: str = "",
) -> str:
    prompt = PLAYER_USER.format(
        fen=fen,
        color=color,
        move_number=move_number,
        recent_moves=" ".join(recent_moves) if recent_moves else "(none — opening move)",
        legal_moves=" ".join(legal_moves),
    )
    return prompt + retry_feedback


def build_retry_feedback(problem: str, legal_moves: list[str]) -> str:
    """`problem` completes the sentence "Your previous answer ..."."""
    return RETRY_FEEDBACK.format(problem=problem, legal_moves=" ".join(legal_moves))


def illegal_move_problem(uci: str) -> str:
    return f"{uci!r} is ILLEGAL in this position."


def unparseable_problem(raw: str, limit: int = 120) -> str:
    snippet = raw.strip()[:limit] or "(empty)"
    return f"was not valid JSON with a 'move' field: {snippet!r}."


# --- analyst (§7, §6 step 6) ---------------------------------------------

ANALYST_SYSTEM = (
    "You are {model_name}, the analyst for an AI-vs-AI chess exhibition. Two LLMs "
    "played the game below. Review it and deliver a verdict.\n\n"
    "THE GAME RESULT IS FINAL AND WAS DECIDED BY A RULES ENGINE. You explain it "
    "and grade the players' performance; you do NOT change the winner.\n\n"
    "Each agent had to choose from an explicit list of legal moves. When one "
    "failed three times in a row, the engine played a random legal move for it "
    "and the turn is marked as a forfeit — that is a real failure worth judging.\n\n"
    "Respond with STRICT JSON, nothing else, exactly this shape:\n"
    "{{\n"
    '  "winner": "white" | "black" | "draw",\n'
    '  "result_explanation": "<why the game ended this way, one or two sentences>",\n'
    '  "key_moments": ["<3 to 5 short, specific moments naming real moves>"],\n'
    '  "white_grade": "<A-F, may use +/->",\n'
    '  "black_grade": "<A-F, may use +/->",\n'
    '  "blunders": ["<specific bad moves, naming them>"],\n'
    '  "best_move": "<the single best move of the game, named>",\n'
    '  "illegal_move_summary": "<how each model behaved on the rules>",\n'
    '  "verdict_paragraph": "<a punchy closing verdict, 2-4 sentences>"\n'
    "}}\n\n"
    '"winner" is the PERFORMANCE winner. In a decisive game it must match the '
    "engine result. Only in a draw may you name whoever played better chess."
)

ANALYST_USER = (
    "Game: {white_model} (White) vs {black_model} (Black)\n"
    "Engine result: {result} — {termination}\n"
    "Length: {ply_count} plies\n\n"
    "Rule-following record:\n"
    "  White: {white_illegal} illegal move(s) proposed, {white_forfeits} turn(s) forfeited\n"
    "  Black: {black_illegal} illegal move(s) proposed, {black_forfeits} turn(s) forfeited\n\n"
    "PGN:\n{pgn}\n\n"
    "Per-move annotations (retries:N = illegal proposals before a legal one; "
    "forfeit:random = engine played a random move):\n{annotations}\n\n"
    "Reference real moves from this game. Do not invent moves that were not played."
)

ANALYST_RETRY = (
    "\n\nYour previous answer was not valid JSON matching the required shape "
    "({problem}). Respond with STRICT JSON only, exactly the shape specified, "
    "and nothing else."
)

COMMENTARY_SYSTEM = (
    "You are {model_name}, providing live commentary on an AI-vs-AI chess game. "
    "Give ONE short, punchy sentence about the position — max 25 words. "
    "Plain text, no JSON, no preamble."
)

COMMENTARY_USER = (
    "Position (FEN): {fen}\n"
    "Move {move_number}. {white_model} (White) vs {black_model} (Black).\n"
    "Recent moves: {recent_moves}\n"
    "One sentence of commentary:"
)


def build_analyst_system(model_name: str) -> str:
    return ANALYST_SYSTEM.format(model_name=model_name)


def build_analyst_user(facts, retry_problem: str = "") -> str:
    prompt = ANALYST_USER.format(
        white_model=facts.white_model,
        black_model=facts.black_model,
        result=facts.result,
        termination=facts.termination,
        ply_count=facts.ply_count,
        white_illegal=facts.illegal_attempts("white"),
        white_forfeits=facts.forfeits("white"),
        black_illegal=facts.illegal_attempts("black"),
        black_forfeits=facts.forfeits("black"),
        pgn=facts.pgn,
        annotations=facts.annotated_moves(),
    )
    if retry_problem:
        prompt += ANALYST_RETRY.format(problem=retry_problem)
    return prompt


def build_commentary_system(model_name: str) -> str:
    return COMMENTARY_SYSTEM.format(model_name=model_name)


def build_commentary_user(
    fen: str, move_number: int, white_model: str, black_model: str, recent_moves: list[str]
) -> str:
    return COMMENTARY_USER.format(
        fen=fen,
        move_number=move_number,
        white_model=white_model,
        black_model=black_model,
        recent_moves=" ".join(recent_moves) if recent_moves else "(none)",
    )

"""ChessEngine — the single source of truth for game state (PLAN.md §2.1).

LLMs only ever *propose* moves. Nothing in this module trusts a model: every
move goes through `push_uci`, which validates against python-chess's legal move
generator and raises on anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO

import chess
import chess.pgn

# Standard relative piece values, used only for max-move adjudication.
PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}

# Material edge (in pawns) needed to award an adjudicated win at max_moves.
# Below this the position is called a draw. Two pawns is a real edge without
# being so strict that a clearly won game gets called a draw.
ADJUDICATION_MARGIN = 2


class IllegalMoveError(ValueError):
    """A UCI string was well-formed but not legal in the current position."""


@dataclass(frozen=True)
class MoveRecord:
    """Everything the UI and the store need about one played move."""

    ply: int
    move_number: int
    color: str  # "white" | "black"
    uci: str
    san: str
    fen_after: str
    is_check: bool
    is_checkmate: bool
    is_capture: bool
    captured_piece: str | None  # lowercase piece letter, e.g. "q"


class ChessEngine:
    """Owns the board. Wraps python-chess with the game's own rules on top.

    `max_moves` is counted in **plies** (one agent turn = one ply), matching the
    game loop in PLAN.md §6 which tests `move_count < max_moves` once per agent
    turn. At the limit the game is adjudicated on material rather than left
    unfinished (PLAN.md §12).
    """

    def __init__(self, fen: str | None = None, max_moves: int = 120) -> None:
        self._board = chess.Board(fen) if fen else chess.Board()
        self._starting_fen = self._board.fen()
        self.max_moves = max_moves
        self._san_history: list[str] = []

    # --- position ---------------------------------------------------------

    @property
    def board(self) -> chess.Board:
        return self._board

    @property
    def fen(self) -> str:
        return self._board.fen()

    @property
    def turn(self) -> str:
        return "white" if self._board.turn == chess.WHITE else "black"

    @property
    def move_number(self) -> int:
        return self._board.fullmove_number

    @property
    def ply_count(self) -> int:
        return len(self._board.move_stack)

    @property
    def is_check(self) -> bool:
        return self._board.is_check()

    def legal_moves_uci(self) -> list[str]:
        return [m.uci() for m in self._board.legal_moves]

    def san_history(self, last_n: int | None = None) -> list[str]:
        """Recent moves in SAN. Prompts send only the tail to stay small (§7)."""
        if last_n is None:
            return list(self._san_history)
        return self._san_history[-last_n:]

    # --- moves ------------------------------------------------------------

    def is_legal_uci(self, uci: str) -> bool:
        try:
            move = chess.Move.from_uci(uci)
        except (ValueError, chess.InvalidMoveError):
            return False
        return move in self._board.legal_moves

    def push_uci(self, uci: str) -> MoveRecord:
        """Validate and play a move. Raises IllegalMoveError if not legal."""
        try:
            move = chess.Move.from_uci(uci)
        except (ValueError, chess.InvalidMoveError) as exc:
            raise IllegalMoveError(f"{uci!r} is not valid UCI") from exc

        if move not in self._board.legal_moves:
            raise IllegalMoveError(f"{uci!r} is not legal in position {self.fen}")

        board = self._board
        is_capture = board.is_capture(move)
        captured: str | None = None
        if is_capture:
            if board.is_en_passant(move):
                captured = "p"
            else:
                piece = board.piece_at(move.to_square)
                captured = piece.symbol().lower() if piece else None

        # SAN and the mover must be read *before* the push.
        san = board.san(move)
        color = self.turn
        move_number = board.fullmove_number

        board.push(move)
        self._san_history.append(san)

        return MoveRecord(
            ply=len(board.move_stack),
            move_number=move_number,
            color=color,
            uci=move.uci(),
            san=san,
            fen_after=board.fen(),
            is_check=board.is_check(),
            is_checkmate=board.is_checkmate(),
            is_capture=is_capture,
            captured_piece=captured,
        )

    # --- game over --------------------------------------------------------

    @property
    def outcome(self) -> chess.Outcome | None:
        # claim_draw=True so the 50-move rule and threefold repetition actually
        # end the game. Without it python-chess only stops at the *forced*
        # 75-move / fivefold thresholds, and two shuffling models would grind on.
        # Note: this also claims at a halfmove clock of 99, since python-chess
        # counts a legal move that would reach 100.
        return self._board.outcome(claim_draw=True)

    @property
    def max_moves_reached(self) -> bool:
        return self.ply_count >= self.max_moves

    @property
    def is_game_over(self) -> bool:
        return self.outcome is not None or self.max_moves_reached

    @property
    def result(self) -> str:
        """'1-0' | '0-1' | '1/2-1/2', or '*' if still in progress."""
        outcome = self.outcome
        if outcome is not None:
            return outcome.result()
        if self.max_moves_reached:
            return self._adjudicated_result()
        return "*"

    @property
    def termination(self) -> str | None:
        """Why the game ended, e.g. 'checkmate' | 'fifty_moves' | 'max_moves'."""
        outcome = self.outcome
        if outcome is not None:
            return outcome.termination.name.lower()
        if self.max_moves_reached:
            return "max_moves"
        return None

    @property
    def winner(self) -> str | None:
        """'white' | 'black' | None for a draw or an unfinished game."""
        result = self.result
        if result == "1-0":
            return "white"
        if result == "0-1":
            return "black"
        return None

    def material_balance(self) -> int:
        """Material in pawns from White's perspective; positive = White ahead."""
        balance = 0
        for piece_type, value in PIECE_VALUES.items():
            balance += value * len(self._board.pieces(piece_type, chess.WHITE))
            balance -= value * len(self._board.pieces(piece_type, chess.BLACK))
        return balance

    def _adjudicated_result(self) -> str:
        balance = self.material_balance()
        if balance >= ADJUDICATION_MARGIN:
            return "1-0"
        if balance <= -ADJUDICATION_MARGIN:
            return "0-1"
        return "1/2-1/2"

    # --- export -----------------------------------------------------------

    def pgn(
        self,
        white_model: str = "White Agent",
        black_model: str = "Black Agent",
        event: str = "LLM Chess Arena",
        date: str | None = None,
    ) -> str:
        """Export the game as PGN. Paste-able into lichess.org/paste."""
        game = chess.pgn.Game.from_board(self._board)
        game.headers["Event"] = event
        game.headers["Site"] = "LLM Chess Arena"
        game.headers["White"] = white_model
        game.headers["Black"] = black_model
        game.headers["Result"] = self.result
        if date:
            game.headers["Date"] = date
        if self.termination:
            game.headers["Termination"] = self.termination
        # A non-standard start needs the FEN/SetUp tags or the PGN won't replay.
        if self._starting_fen != chess.STARTING_FEN:
            game.headers["FEN"] = self._starting_fen
            game.headers["SetUp"] = "1"

        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        return game.accept(exporter)

    @staticmethod
    def is_valid_pgn(pgn: str) -> bool:
        """Round-trip check: does this PGN parse back into a game with moves?"""
        game = chess.pgn.read_game(StringIO(pgn))
        if game is None or game.errors:
            return False
        board = game.board()
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                return False
            board.push(move)
        return True

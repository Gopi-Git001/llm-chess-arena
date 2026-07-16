"""Engine tests. Termination detection is the critical surface (PLAN.md §14.3):
if the engine can't tell why a game ended, the verdict is built on sand.
"""

from __future__ import annotations

import chess
import pytest

from app.engine import ChessEngine, IllegalMoveError

# Fool's mate: 1. f3 e5 2. g4 Qh4#
FOOLS_MATE_UCI = ["f2f3", "e7e5", "g2g4", "d8h4"]

# Black to move, has no legal move, and is not in check.
STALEMATE_FEN = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"

# White mates in one: Qf7#
MATE_IN_ONE_FEN = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"


class TestBasics:
    def test_starts_from_the_standard_position(self):
        engine = ChessEngine()
        assert engine.fen == chess.STARTING_FEN
        assert engine.turn == "white"
        assert engine.ply_count == 0
        assert not engine.is_game_over
        assert engine.result == "*"
        assert engine.termination is None

    def test_twenty_legal_moves_at_the_start(self):
        engine = ChessEngine()
        legal = engine.legal_moves_uci()
        assert len(legal) == 20
        assert "e2e4" in legal
        assert all(len(m) in (4, 5) for m in legal)

    def test_push_returns_move_metadata_and_advances_turn(self):
        engine = ChessEngine()
        record = engine.push_uci("e2e4")

        assert record.uci == "e2e4"
        assert record.san == "e4"
        assert record.color == "white"
        assert record.move_number == 1
        assert record.ply == 1
        assert not record.is_check
        assert not record.is_capture
        assert record.captured_piece is None
        assert engine.turn == "black"
        assert record.fen_after == engine.fen

    def test_move_number_increments_only_after_black(self):
        engine = ChessEngine()
        assert engine.push_uci("e2e4").move_number == 1
        assert engine.push_uci("e7e5").move_number == 1
        assert engine.push_uci("g1f3").move_number == 2

    def test_san_history_keeps_order_and_tail_slicing(self):
        engine = ChessEngine()
        for uci in FOOLS_MATE_UCI:
            engine.push_uci(uci)
        assert engine.san_history() == ["f3", "e5", "g4", "Qh4#"]
        assert engine.san_history(last_n=2) == ["g4", "Qh4#"]
        # Asking for more history than exists is not an error.
        assert len(engine.san_history(last_n=10)) == 4


class TestIllegalMoves:
    """The engine must reject anything a hallucinating model sends (§2.1)."""

    @pytest.mark.parametrize(
        "uci",
        [
            "e2e5",  # well-formed but not legal
            "a1a8",  # blocked by own pieces
            "e9e5",  # off-board square
            "",  # empty
            "hello",  # garbage
            "e2",  # truncated
            "O-O",  # SAN, not UCI
        ],
    )
    def test_push_rejects_illegal_or_malformed_uci(self, uci):
        engine = ChessEngine()
        with pytest.raises(IllegalMoveError):
            engine.push_uci(uci)

    def test_rejected_move_does_not_mutate_the_board(self):
        engine = ChessEngine()
        before = engine.fen
        with pytest.raises(IllegalMoveError):
            engine.push_uci("e2e5")
        assert engine.fen == before
        assert engine.ply_count == 0

    def test_is_legal_uci_agrees_with_the_legal_move_list(self):
        engine = ChessEngine()
        assert engine.is_legal_uci("e2e4")
        assert not engine.is_legal_uci("e2e5")
        assert not engine.is_legal_uci("garbage")

    def test_null_move_is_rejected(self):
        # python-chess accepts "0000" as a null move; a player must never pass.
        engine = ChessEngine()
        with pytest.raises(IllegalMoveError):
            engine.push_uci("0000")


class TestCapturesAndChecks:
    def test_capture_reports_the_captured_piece(self):
        engine = ChessEngine()
        engine.push_uci("e2e4")
        engine.push_uci("d7d5")
        record = engine.push_uci("e4d5")

        assert record.is_capture
        assert record.captured_piece == "p"
        assert record.san == "exd5"

    def test_en_passant_reports_a_captured_pawn(self):
        engine = ChessEngine()
        for uci in ["e2e4", "a7a6", "e4e5", "d7d5"]:
            engine.push_uci(uci)
        record = engine.push_uci("e5d6")  # en passant; target square is empty

        assert record.is_capture
        assert record.captured_piece == "p"

    def test_check_is_flagged(self):
        engine = ChessEngine()
        for uci in ["e2e4", "f7f6", "d1h5"]:
            engine.push_uci(uci)
        assert engine.board.is_check()
        assert engine.is_check

    def test_promotion_uci_is_accepted(self):
        engine = ChessEngine(fen="8/P6k/8/8/8/8/8/K7 w - - 0 1")
        record = engine.push_uci("a7a8q")
        assert record.san == "a8=Q"
        assert "Q" in engine.fen.split()[0]


class TestTermination:
    """The ✅ Verify fixtures from PLAN.md §11 Phase 1."""

    def test_fools_mate_is_checkmate_for_black(self):
        engine = ChessEngine()
        for uci in FOOLS_MATE_UCI:
            engine.push_uci(uci)

        assert engine.is_game_over
        assert engine.result == "0-1"
        assert engine.termination == "checkmate"
        assert engine.winner == "black"

    def test_checkmate_is_flagged_on_the_move_record(self):
        engine = ChessEngine(fen=MATE_IN_ONE_FEN)
        engine.push_uci("d8h4")
        assert engine.board.is_checkmate()

    def test_stalemate_is_a_draw_with_no_winner(self):
        engine = ChessEngine(fen=STALEMATE_FEN)

        assert engine.is_game_over
        assert engine.result == "1/2-1/2"
        assert engine.termination == "stalemate"
        assert engine.winner is None
        assert engine.legal_moves_uci() == []

    def test_fifty_move_rule_is_claimed_as_a_draw(self):
        # Halfmove clock already at 100 (= 50 full moves, no pawn move/capture).
        engine = ChessEngine(fen="7k/8/8/4Q3/8/8/8/K7 w - - 100 60")

        assert engine.is_game_over
        assert engine.result == "1/2-1/2"
        assert engine.termination == "fifty_moves"

    def test_fifty_move_rule_claimable_when_a_move_reaches_the_clock(self):
        # python-chess allows the claim at 99 too, because a legal move brings
        # the clock to 100. Documented here so the behaviour isn't a surprise.
        engine = ChessEngine(fen="7k/8/8/4Q3/8/8/8/K7 w - - 99 60")
        assert engine.termination == "fifty_moves"

    def test_fifty_move_rule_not_triggered_early(self):
        engine = ChessEngine(fen="7k/8/8/4Q3/8/8/8/K7 w - - 98 60")
        assert not engine.is_game_over
        assert engine.termination is None

    def test_insufficient_material_is_a_draw(self):
        engine = ChessEngine(fen="7k/8/8/8/8/8/8/K6B w - - 0 1")
        assert engine.is_game_over
        assert engine.termination == "insufficient_material"
        assert engine.result == "1/2-1/2"

    def test_game_in_progress_reports_no_termination(self):
        engine = ChessEngine()
        engine.push_uci("e2e4")
        assert not engine.is_game_over
        assert engine.result == "*"
        assert engine.winner is None


class TestMaxMoveAdjudication:
    def test_max_moves_ends_the_game(self):
        engine = ChessEngine(max_moves=4)
        for uci in ["g1f3", "g8f6", "f3g1", "f6g8"]:
            engine.push_uci(uci)

        assert engine.max_moves_reached
        assert engine.is_game_over
        assert engine.termination == "max_moves"

    def test_max_moves_is_counted_in_plies(self):
        engine = ChessEngine(max_moves=2)
        engine.push_uci("e2e4")
        assert not engine.is_game_over
        engine.push_uci("e7e5")
        assert engine.is_game_over

    def test_equal_material_adjudicates_to_a_draw(self):
        engine = ChessEngine(max_moves=2)
        engine.push_uci("e2e4")
        engine.push_uci("e7e5")
        assert engine.material_balance() == 0
        assert engine.result == "1/2-1/2"

    def test_material_lead_adjudicates_a_win(self):
        # White is a queen up and hits the ply cap on a quiet move. The position
        # must stay unfinished (no check/mate/stalemate) or this would test
        # checkmate detection instead of adjudication.
        engine = ChessEngine(fen="7k/p7/8/8/8/8/P7/K5Q1 w - - 0 1", max_moves=1)
        engine.push_uci("g1g3")

        assert engine.outcome is None
        assert engine.termination == "max_moves"
        assert engine.material_balance() == 9
        assert engine.result == "1-0"
        assert engine.winner == "white"

    def test_material_lead_below_the_margin_is_still_a_draw(self):
        # White is exactly one pawn up — under ADJUDICATION_MARGIN.
        engine = ChessEngine(fen="7k/8/8/8/8/8/P7/K7 w - - 0 1", max_moves=1)
        engine.push_uci("a2a3")

        assert engine.material_balance() == 1
        assert engine.result == "1/2-1/2"

    def test_black_material_lead_adjudicates_a_black_win(self):
        engine = ChessEngine(fen="6qk/p7/8/8/8/8/P7/K7 b - - 0 1", max_moves=1)
        engine.push_uci("g8g6")

        assert engine.outcome is None
        assert engine.termination == "max_moves"
        assert engine.material_balance() == -9
        assert engine.result == "0-1"
        assert engine.winner == "black"

    def test_real_checkmate_beats_the_ply_cap(self):
        # Fool's mate lands exactly on the cap; checkmate must win the tiebreak,
        # not adjudication.
        engine = ChessEngine(max_moves=4)
        for uci in FOOLS_MATE_UCI:
            engine.push_uci(uci)

        assert engine.termination == "checkmate"
        assert engine.result == "0-1"

    def test_moves_are_still_rejected_after_the_cap(self):
        engine = ChessEngine(max_moves=1)
        engine.push_uci("e2e4")
        # The cap is the orchestrator's stop signal; the board itself stays legal.
        assert engine.is_game_over
        assert engine.is_legal_uci("e7e5")


class TestPgnExport:
    def test_pgn_round_trips_and_carries_headers(self):
        engine = ChessEngine()
        for uci in FOOLS_MATE_UCI:
            engine.push_uci(uci)

        pgn = engine.pgn(white_model="gpt-oss-120b", black_model="llama-3.3-70b")

        assert ChessEngine.is_valid_pgn(pgn)
        assert '[White "gpt-oss-120b"]' in pgn
        assert '[Black "llama-3.3-70b"]' in pgn
        assert '[Result "0-1"]' in pgn
        assert '[Termination "checkmate"]' in pgn
        assert "1. f3 e5 2. g4 Qh4#" in pgn

    def test_pgn_from_a_custom_start_includes_setup_headers(self):
        engine = ChessEngine(fen=MATE_IN_ONE_FEN)
        engine.push_uci("d8h4")
        pgn = engine.pgn()

        assert '[SetUp "1"]' in pgn
        assert f'[FEN "{MATE_IN_ONE_FEN}"]' in pgn
        assert ChessEngine.is_valid_pgn(pgn)

    def test_empty_game_still_exports_valid_pgn(self):
        assert ChessEngine.is_valid_pgn(ChessEngine().pgn())

    def test_is_valid_pgn_rejects_an_unplayable_move(self):
        # The realistic corruption: a move that can't be played in the position.
        assert not ChessEngine.is_valid_pgn("1. e4 e5 2. Nf6")

    def test_is_valid_pgn_skips_unrecognised_tokens(self):
        # Documenting a python-chess limitation rather than hiding it: tokens
        # that don't match SAN grammar at all ("Qxq9") are silently skipped, so
        # this validator confirms our own exports round-trip — it is not a
        # strict grammar checker for arbitrary text.
        assert ChessEngine.is_valid_pgn("1. e4 e5 2. Qxq9 ##")

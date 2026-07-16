"""Verdict schema + template verdict tests (PLAN.md §3, §6 step 6).

The load-bearing rule: the Analyst explains the result, it never changes it.
"""

from __future__ import annotations

import pytest

from app.verdict import (
    GameFacts,
    Verdict,
    grade_for,
    stamp_engine_facts,
    template_verdict,
)


def move(
    ply=1,
    move_number=1,
    color="white",
    san="e4",
    uci="e2e4",
    attempts=1,
    forfeited=0,
    is_capture=0,
    is_check=0,
):
    return {
        "ply": ply,
        "move_number": move_number,
        "color": color,
        "san": san,
        "uci": uci,
        "fen_after": "fen",
        "attempts": attempts,
        "forfeited": forfeited,
        "is_capture": is_capture,
        "is_check": is_check,
        "captured_piece": None,
        "reasoning": "",
    }


def facts(**kwargs) -> GameFacts:
    defaults = dict(
        game_id="g1",
        white_model="white/model",
        black_model="black/model",
        result="1-0",
        termination="checkmate",
        winner="white",
        ply_count=2,
        pgn="1. e4 e5 1-0",
        moves=[move(), move(ply=2, color="black", san="e5", uci="e7e5")],
    )
    defaults.update(kwargs)
    return GameFacts(**defaults)


class TestGameFactsCounts:
    def test_illegal_attempts_counts_retries_not_moves(self):
        f = facts(
            moves=[
                move(attempts=3),  # 2 illegal proposals before a legal one
                move(ply=2, color="black", attempts=1),
                move(ply=3, attempts=2),  # 1 more
            ]
        )
        assert f.illegal_attempts("white") == 3
        assert f.illegal_attempts("black") == 0

    def test_forfeits_are_counted_per_colour(self):
        f = facts(
            moves=[
                move(forfeited=1),
                move(ply=2, color="black", forfeited=1),
                move(ply=3, forfeited=1),
            ]
        )
        assert f.forfeits("white") == 2
        assert f.forfeits("black") == 1

    def test_move_and_capture_counts(self):
        f = facts(moves=[move(is_capture=1), move(ply=2, color="black")])
        assert f.move_count("white") == 1
        assert f.captures("white") == 1
        assert f.captures("black") == 0

    def test_annotations_mark_retries_and_forfeits(self):
        f = facts(
            moves=[
                move(san="e4", attempts=3),
                move(ply=2, color="black", san="e5", forfeited=1),
                move(ply=3, san="Nf3"),
            ]
        )
        annotated = f.annotated_moves()

        assert "e4(retries:2)" in annotated
        assert "e5(forfeit:random)" in annotated
        assert "Nf3" in annotated

    def test_summary_line_reports_both_sides(self):
        line = facts(moves=[move(attempts=2), move(ply=2, color="black", forfeited=1)]).summary_line()

        assert "1-0 by checkmate" in line
        assert "1 illegal" in line


class TestGrading:
    def test_clean_play_scores_top_marks(self):
        assert grade_for(facts(moves=[move(), move(ply=2)]), "white") == "A"

    def test_a_single_illegal_among_many_costs_a_notch(self):
        moves = [move(ply=1, attempts=2)] + [move(ply=i) for i in range(2, 11)]
        assert grade_for(facts(moves=moves), "white") == "A-"

    def test_grades_are_rates_not_raw_counts(self):
        # One illegal in two moves is a 50% failure rate, not a near-clean game.
        assert grade_for(facts(moves=[move(attempts=2), move(ply=2)]), "white") == "B"

    def test_heavy_forfeiting_fails(self):
        moves = [move(ply=i, forfeited=1) for i in range(1, 5)]
        assert grade_for(facts(moves=moves), "white") == "F"

    def test_no_moves_is_unknown_not_zero(self):
        assert grade_for(facts(moves=[]), "white") == "?"

    def test_grades_are_independent_per_colour(self):
        f = facts(moves=[move(forfeited=1), move(ply=2, color="black")])
        assert grade_for(f, "white") == "F"
        assert grade_for(f, "black") == "A"


class TestWinnerNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("white", "white"),
            ("White", "white"),
            ("WHITE", "white"),
            ("1-0", "white"),
            ("black", "black"),
            ("0-1", "black"),
            ("draw", "draw"),
            ("1/2-1/2", "draw"),
            ("nobody", "draw"),
            ("White (gpt-oss-120b)", "white"),
            ("the black agent", "black"),
            (None, "draw"),
            ("", "draw"),
            ("both were bad", "draw"),
        ],
    )
    def test_models_say_all_sorts_of_things(self, raw, expected):
        assert Verdict(winner=raw).winner == expected


class TestListCoercion:
    def test_plain_strings_pass_through(self):
        assert Verdict(key_moments=["a", "b"]).key_moments == ["a", "b"]

    def test_objects_are_flattened(self):
        verdict = Verdict(key_moments=[{"move": "Qh4#", "description": "mate"}])
        assert verdict.key_moments == ["Qh4# — mate"]

    def test_bare_string_becomes_a_list(self):
        assert Verdict(blunders="Qxf7 was awful").blunders == ["Qxf7 was awful"]

    def test_none_becomes_empty(self):
        assert Verdict(key_moments=None).key_moments == []

    def test_key_moments_are_capped_at_five(self):
        assert len(Verdict(key_moments=[f"m{i}" for i in range(20)]).key_moments) == 5

    def test_empty_strings_are_dropped(self):
        assert Verdict(key_moments=["", "  ", "real"]).key_moments == ["real"]


class TestTemplateVerdict:
    def test_reports_the_engine_result_faithfully(self):
        verdict = template_verdict(facts())

        assert verdict.engine_result == "1-0"
        assert verdict.engine_winner == "white"
        assert verdict.engine_termination == "checkmate"
        assert verdict.source == "template"
        assert verdict.winner == "white"

    def test_says_out_loud_that_no_model_reviewed_it(self):
        """Never let a template verdict pass as real analysis."""
        assert "engine facts alone" in template_verdict(facts()).verdict_paragraph

    def test_invents_no_blunders_or_best_move(self):
        """Both need a real evaluation. Silence beats making something up."""
        verdict = template_verdict(facts())

        assert verdict.blunders == []
        assert verdict.best_move == ""

    def test_a_draw_awards_performance_to_the_cleaner_player(self):
        """§3: the performance winner may differ from the result in a draw."""
        verdict = template_verdict(
            facts(
                result="1/2-1/2",
                winner=None,
                termination="stalemate",
                moves=[move(forfeited=1, attempts=3), move(ply=2, color="black")],
            )
        )

        assert verdict.engine_winner is None
        assert verdict.winner == "black", "black followed the rules, white did not"
        assert "performance points" in verdict.verdict_paragraph

    def test_an_evenly_matched_draw_stays_a_draw(self):
        verdict = template_verdict(
            facts(result="1/2-1/2", winner=None, termination="stalemate")
        )
        assert verdict.winner == "draw"

    def test_key_moments_reference_real_moves(self):
        verdict = template_verdict(
            facts(
                moves=[
                    move(san="e4"),
                    move(ply=2, color="black", san="d5"),
                    move(ply=3, san="exd5", is_capture=1),
                ]
            )
        )

        assert verdict.key_moments
        assert any("exd5" in moment for moment in verdict.key_moments)

    def test_forfeits_appear_in_key_moments_and_summary(self):
        verdict = template_verdict(
            facts(moves=[move(san="a3", forfeited=1), move(ply=2, color="black")])
        )

        assert any("random" in moment for moment in verdict.key_moments)
        assert "forfeited" in verdict.illegal_move_summary

    def test_clean_game_summary_says_so(self):
        assert "only legal moves" in template_verdict(facts()).illegal_move_summary

    def test_max_moves_adjudication_is_explained_honestly(self):
        verdict = template_verdict(
            facts(result="1-0", winner="white", termination="max_moves", ply_count=120)
        )
        assert "cap" in verdict.result_explanation or "cap" in verdict.verdict_paragraph

    def test_reason_is_recorded_when_used_as_a_fallback(self):
        verdict = template_verdict(facts(), reason="analyst returned garbage")
        assert "analyst returned garbage" in verdict.verdict_paragraph

    def test_survives_a_game_with_no_moves(self):
        verdict = template_verdict(facts(moves=[], ply_count=0, result="*", termination=None))
        assert verdict.source == "template"


class TestStampEngineFacts:
    def test_engine_fields_are_authoritative(self):
        verdict = Verdict(winner="black", engine_result="wrong")
        stamped = stamp_engine_facts(verdict, facts(), "analyst/model")

        assert stamped.engine_result == "1-0"
        assert stamped.engine_winner == "white"
        assert stamped.analyst_model == "analyst/model"

    def test_a_model_cannot_award_the_win_to_the_loser(self):
        """§3: the Analyst does not override the result."""
        verdict = Verdict(winner="black", verdict_paragraph="Black was robbed.")
        stamped = stamp_engine_facts(verdict, facts(result="1-0", winner="white"), "m")

        assert stamped.winner == "white"

    def test_opinion_is_preserved_in_a_draw(self):
        verdict = Verdict(winner="black")
        stamped = stamp_engine_facts(
            verdict, facts(result="1/2-1/2", winner=None, termination="stalemate"), "m"
        )

        assert stamped.winner == "black", "a draw leaves room for an opinion"
        assert stamped.engine_winner is None

"""Move-parsing tests (PLAN.md §11 Phase 3 verify).

Every case here is a real thing free models do. The contract: return a
well-formed UCI move, or raise ParseError and cost one retry. Never return
something the engine hasn't blessed.
"""

from __future__ import annotations

import pytest

from app.parsing import ParseError, normalise_uci, parse_move_response, strip_fences


class TestCleanJson:
    def test_strict_json(self):
        parsed = parse_move_response('{"move": "e2e4", "reasoning": "King\'s pawn."}')

        assert parsed.uci == "e2e4"
        assert parsed.reasoning == "King's pawn."

    def test_promotion_move(self):
        assert parse_move_response('{"move": "a7a8q"}').uci == "a7a8q"

    def test_missing_reasoning_is_empty_not_an_error(self):
        parsed = parse_move_response('{"move": "e2e4"}')

        assert parsed.uci == "e2e4"
        assert parsed.reasoning == ""

    @pytest.mark.parametrize("key", ["move", "uci", "move_uci", "best_move"])
    def test_common_key_synonyms_are_accepted(self, key):
        # A right answer with a wrong label shouldn't burn a retry.
        assert parse_move_response(f'{{"{key}": "g1f3"}}').uci == "g1f3"

    def test_extra_fields_are_ignored(self):
        raw = '{"move": "e2e4", "reasoning": "hi", "confidence": 0.9, "eval": -0.3}'
        assert parse_move_response(raw).uci == "e2e4"

    def test_reasoning_is_collapsed_and_truncated(self):
        raw = '{"move": "e2e4", "reasoning": "' + "word " * 200 + '"}'
        reasoning = parse_move_response(raw).reasoning

        assert len(reasoning) <= 240
        assert "\n" not in reasoning


class TestMarkdownFenced:
    def test_json_fence(self):
        raw = '```json\n{"move": "e2e4", "reasoning": "Centre."}\n```'
        assert parse_move_response(raw).uci == "e2e4"

    def test_bare_fence(self):
        assert parse_move_response('```\n{"move": "d2d4"}\n```').uci == "d2d4"

    def test_prose_around_json(self):
        raw = 'Sure! Here is my move:\n\n{"move": "e2e4", "reasoning": "Best by test."}\n\nGood luck!'
        parsed = parse_move_response(raw)

        assert parsed.uci == "e2e4"
        assert parsed.reasoning == "Best by test."

    def test_fence_with_prose_around_it(self):
        raw = 'Thinking...\n```json\n{"move": "b1c3"}\n```\nHope that works.'
        assert parse_move_response(raw).uci == "b1c3"

    def test_strip_fences_returns_text_when_unfenced(self):
        assert strip_fences("no fences here") == "no fences here"


class TestBareUci:
    def test_bare_move(self):
        parsed = parse_move_response("e2e4")

        assert parsed.uci == "e2e4"
        assert parsed.reasoning == ""

    def test_move_inside_prose(self):
        assert parse_move_response("I'll play e2e4, controlling the centre.").uci == "e2e4"

    def test_uppercase_is_normalised(self):
        assert parse_move_response("E2E4").uci == "e2e4"

    def test_move_with_surrounding_whitespace_and_quotes(self):
        assert parse_move_response('  "g1f3"  ').uci == "g1f3"

    def test_first_move_wins_when_several_appear(self):
        # Better a wrong-but-legal-looking guess the engine can reject than a
        # crash: the legality check is the real gate.
        assert parse_move_response("maybe e2e4 or d2d4").uci == "e2e4"


class TestRejected:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "\n\n",
        ],
    )
    def test_empty_responses_raise(self, raw):
        with pytest.raises(ParseError):
            parse_move_response(raw)

    def test_none_raises(self):
        with pytest.raises(ParseError):
            parse_move_response(None)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "raw",
        [
            "I refuse to play chess.",
            "As an AI language model, I cannot...",
            "{}",
            '{"reasoning": "I forgot the move"}',
            "Nf3",  # SAN, not UCI
            '{"move": "Nf3"}',  # SAN inside correct JSON
            '{"move": "e2"}',  # truncated
            '{"move": "z9z9"}',  # off-board
            '{"move": 1234}',  # not a string
            '{"move": null}',
            '{"move": "e2e4e5"}',  # too long
            '{"move": "a7a8k"}',  # king is not a promotion piece
            "[1, 2, 3]",  # JSON, but not an object
        ],
    )
    def test_garbage_raises(self, raw):
        with pytest.raises(ParseError):
            parse_move_response(raw)

    def test_parse_error_carries_the_raw_text_for_the_retry_prompt(self):
        with pytest.raises(ParseError) as exc:
            parse_move_response("I refuse to play chess.")
        assert exc.value.raw == "I refuse to play chess."

    def test_san_is_rejected_rather_than_guessed(self):
        """Never translate SAN to UCI here — guessing a move the model didn't
        make is exactly the hallucination this system is built to prevent."""
        with pytest.raises(ParseError):
            parse_move_response('{"move": "Qxf7#"}')


class TestIllegalButWellFormed:
    """The §11 'illegal-but-valid-format' case: parsing must *accept* these.

    Shape and legality are different questions. Parsing guarantees shape; the
    engine's legal move list decides legality (§2.1). If parsing silently
    rejected illegal moves, the retry prompt could never tell the model which
    move it got wrong.
    """

    @pytest.mark.parametrize("uci", ["e2e5", "a1a8", "h1h8", "e1e8"])
    def test_well_formed_illegal_moves_parse_cleanly(self, uci):
        assert parse_move_response(f'{{"move": "{uci}"}}').uci == uci

    def test_parsing_does_not_know_about_positions(self):
        # No FEN is passed in — by design.
        assert parse_move_response('{"move": "d7d5"}').uci == "d7d5"


class TestNormaliseUci:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("e2e4", "e2e4"),
            ("E2E4", "e2e4"),
            ("  e2e4  ", "e2e4"),
            ("e2-e4", "e2e4"),
            ("e2 e4", "e2e4"),
            ('"e2e4"', "e2e4"),
            ("a7a8Q", "a7a8q"),
        ],
    )
    def test_accepted_forms(self, raw, expected):
        assert normalise_uci(raw) == expected

    @pytest.mark.parametrize("raw", ["Nf3", "e2", "", "z9z9", "e2e4e5", "0000", 42, None])
    def test_rejected_forms(self, raw):
        assert normalise_uci(raw) is None

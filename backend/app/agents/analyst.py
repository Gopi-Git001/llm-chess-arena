"""Analysts: MockAnalyst (free, template) and AnalystAgent (a real LLM).

The Analyst is the only agent whose output is pure opinion, which makes it the
one place a model could quietly rewrite history. It can't: `stamp_engine_facts`
overwrites the authoritative fields with engine truth after parsing, and any
failure falls back to `template_verdict`, built from the database alone. The
verdict always exists and always agrees with the engine about who won (§3).
"""

from __future__ import annotations

import logging
import random

from app.agents.base import BaseAnalyst
from app.llm_client import LLMError, OpenRouterClient
from app.parsing import extract_json_object, strip_fences
from app.prompts import (
    build_analyst_system,
    build_analyst_user,
    build_commentary_system,
    build_commentary_user,
)
from app.verdict import GameFacts, Verdict, stamp_engine_facts, template_verdict

log = logging.getLogger(__name__)

CANNED_COMMENTARY = [
    "Both engines are still pretending they meant to do that.",
    "The position is balanced, which is one word for it.",
    "Pieces are being moved. Chess, arguably, is happening.",
    "A tense moment, assuming either side noticed.",
    "The centre is contested by two models with no idea it's the centre.",
    "Somewhere, a grandmaster is quietly weeping.",
    "Material is level and so are expectations.",
    "That was almost a plan. Almost.",
]


class MockAnalyst(BaseAnalyst):
    """Template verdict + canned commentary. Zero API calls (§2.6)."""

    def __init__(self, model: str = "mock-analyst", seed: int | None = None) -> None:
        super().__init__(model=model)
        self._rng = random.Random(seed)

    async def review(self, facts: GameFacts) -> Verdict:
        verdict = template_verdict(facts)
        verdict.analyst_model = self.model
        return verdict

    async def comment(self, facts: GameFacts) -> str:
        return self._rng.choice(CANNED_COMMENTARY)


class AnalystAgent(BaseAnalyst):
    """A real LLM reviewing the finished game (§6 step 6).

    Retries once on a parse failure, then falls back to the template verdict —
    a missing verdict is not an option, and a made-up one is worse (§11).
    """

    def __init__(
        self,
        model: str,
        client: OpenRouterClient,
        *,
        temperature: float = 0.3,  # §7: consistency over variety for the analyst
        max_tokens: int = 1500,
    ) -> None:
        super().__init__(model=model)
        self.client = client
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def review(self, facts: GameFacts) -> Verdict:
        system = build_analyst_system(self.model)
        problem = ""

        for attempt in (1, 2):  # one try, one retry (§11 Phase 4)
            try:
                response = await self.client.complete(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": build_analyst_user(facts, problem)},
                    ],
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
            except LLMError as exc:
                log.warning("Analyst %s unreachable: %s", self.model, exc)
                return self._fallback(facts, f"analyst unreachable: {exc}")

            verdict = self._parse(response.content)
            if verdict is not None:
                return stamp_engine_facts(verdict, facts, self.model)

            problem = f"attempt {attempt} was not usable JSON"
            log.info("Analyst %s returned unparseable verdict on attempt %s", self.model, attempt)

        return self._fallback(facts, "analyst did not return valid JSON")

    def _parse(self, content: str) -> Verdict | None:
        payload = extract_json_object(strip_fences(content or ""))
        if payload is None:
            return None
        try:
            # Ignore unexpected keys rather than reject an otherwise fine verdict.
            known = {k: v for k, v in payload.items() if k in Verdict.model_fields}
            return Verdict(**known)
        except Exception as exc:
            log.info("Verdict failed validation: %s", exc)
            return None

    def _fallback(self, facts: GameFacts, reason: str) -> Verdict:
        verdict = template_verdict(facts, reason=reason)
        verdict.analyst_model = self.model
        return verdict

    async def comment(self, facts: GameFacts) -> str:
        """Live commentary. Best-effort: silence beats stalling the game."""
        last_moves = [m["san"] for m in facts.moves[-6:]]
        fen = facts.moves[-1]["fen_after"] if facts.moves else ""
        move_number = facts.moves[-1]["move_number"] if facts.moves else 1

        try:
            response = await self.client.complete(
                model=self.model,
                messages=[
                    {"role": "system", "content": build_commentary_system(self.model)},
                    {
                        "role": "user",
                        "content": build_commentary_user(
                            fen=fen,
                            move_number=move_number,
                            white_model=facts.white_model,
                            black_model=facts.black_model,
                            recent_moves=last_moves,
                        ),
                    },
                ],
                max_tokens=100,
                temperature=self.temperature,
                json_object=False,  # plain prose here, not JSON
            )
        except LLMError as exc:
            log.info("Commentary skipped: %s", exc)
            return ""

        return " ".join((response.content or "").split())[:240]

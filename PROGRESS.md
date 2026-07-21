# PROGRESS.md — LLM Chess Arena

Session log. Append after every phase. At the start of every session: re-read
`PLAN.md`, then this file, then resume from "Next step" at the bottom.

**Ground rules in force**
- `mode: mock` is the default for all development and testing. No live
  OpenRouter calls unless the user explicitly says "run live".
- Work strictly phase by phase (Phase 0 → 6). A phase does not start until the
  previous phase's ✅ Verify steps pass.
- Reality beats the plan: if a library API or OpenRouter differs from PLAN.md,
  follow reality, fix the code/config, and record the deviation here.

---

## POST-PHASE-5 — "Too Slow" live thinking + synced voice commentary (2026-07-18)

Two features, both built end-to-end and **verified in mock mode in-browser**
(zero API calls). Live testing deliberately NOT run — awaiting explicit approval.

### Feature 1 — "Too Slow" speed mode (10s/move, live streaming reasoning)

- **`app/thinking.py`** (new) — the pacing core. `ReasoningBuffer` turns streamed
  chunks into whole words (buffering an incomplete trailing fragment across
  deltas); `release_count()` is a pure, unit-tested function deciding how many
  words to reveal per tick (finished-early → spread the remainder across the
  leftover ticks; still-producing → trickle); `pace_window()` runs the reveal for
  exactly the window with injectable clock/sleep.
- **`app/llm_client.py`** — `complete(..., token_sink=…)` now streams (`stream:
  true`, SSE) when a sink is given, forwarding each delta and returning the *same*
  `LLMResponse`. The 429/404/5xx ladder is unchanged; only the 200 branch reads
  SSE. `sse_delta()` is a pure, tested assembler. A sink that throws never aborts
  the stream.
- **`app/agents/player.py`** — `MockPlayer` streams a canned multi-sentence
  monologue word by word (offline-testable); `PlayerAgent` streams and uses a
  reasoning-first prompt variant. **Move parse/validate/retry is byte-for-byte
  unchanged** — streaming is presentation only.
- **`app/orchestrator.py`** — in "Too Slow" mode a turn spawns `get_move` and runs
  `pace_window` concurrently; `MOVE_MADE` (carrying the full `thinking` text)
  fires only after the window closes. New `AGENT_THINKING_TOKEN` events. The plain
  `move_delay` is skipped (the window paces).
- **`app/store.py`** — additive `moves.thinking` column (with an `ALTER TABLE`
  migration for pre-existing DBs) so "click a move → see its reasoning" survives
  a refresh.
- Frontend: `ThinkingPanel` (streaming typewriter + blinking cursor + auto-scroll)
  beside each agent card; store buffers `thinkingText` per colour; a "Too Slow"
  speed option; clicking a past move shows its stored reasoning.

### Feature 2 — voice commentary synced to the board (presentation queue)

- **`app/agents/commentator.py`** (new) — `template_move_commentary()` (pure,
  engine-fact fallback, "Knight takes on f6 — and that's check!"), `MockCommentator`
  (always template, zero calls), `CommentatorAgent` (real LLM, falls back to the
  template on any `LLMError`/empty reply). `commentator_model` reuses the shared
  client/throttle/budget.
- **`app/orchestrator.py`** — after each `MOVE_MADE`, emits `MOVE_COMMENTARY
  {ply, …, source}` (gated by `commentary.enabled` / `every_n_moves`). The game
  loop may run ahead of presentation.
- Frontend: **`lib/presentationQueue.ts`** (new, framework-free) consumes items
  strictly one at a time — animate → speak → await the utterance's `end` →
  advance. The board is driven by this queue, never by raw `MOVE_MADE`.
  **`lib/tts.ts`** `TTSService` wraps `speechSynthesis`: `speak()` resolves on the
  real `end` event, a chain serialises utterances (no overlap), voice/rate/pitch,
  and a mute that keeps captions pacing (a guard timer covers browsers that drop
  `end`). `usePresentation` drives it and fast-forwards past history on rehydration.
  `AnalystPanel` shows the caption feed with the spoken line highlighted.
- The two features compose: in "Too Slow" mode each move is a 10s thinking window
  → move animates → commentary speaks → next window.

### Tests — all green

- Backend **368 passed** (was 329, network blocked): `test_thinking.py` (buffer +
  `release_count` + `pace_window` finish-early/cut-at-deadline), `test_streaming.py`
  (SSE assembler + streamed `complete` + sink-throws), `test_commentator.py`
  (template cases + agent fallback), plus orchestrator thinking-mode + commentary
  ordering tests. One pre-existing live-wiring quota test updated to disable move
  commentary so it still isolates the player-move count.
- Frontend: **`presentationQueue.test.ts`** (4 passed, `node --test`) pins the core
  guarantee — the board never advances before the speech-finished callback fires;
  late commentary is waited for; no-commentary/finish paths don't stall. `tsc -b`,
  `vite build`, `oxlint` all clean.

### ✅ Mock-mode browser verification (2026-07-18)

Backend + Vite running mock. Measured, not eyeballed:
- **F1 streaming**: the White Thinking panel grew live 260 → 295 chars over the
  window then cleared on commit; the "live" label + blinking cursor showed; moves
  landed (h4, Nf6, a4…). Clicking move 1 revealed its stored reasoning labelled
  "move 1".
- **F2 gating** (Watchable, commentary on, voice muted): board `presented` ply
  advanced 1,1,1,2,2,3,3,4,4,4 while the backend `emitted` ply raced 2,3,4,5,7,8,
  9,10,11,13 — `speaking` always equalled `presented`. **The board never got
  ahead of the voice**, and the loop ran up to 9 plies ahead. Captions rendered in
  order matching the moves ("White pushes a pawn to e4." …), the speaking line
  highlighted, mute kept captions running.
- **Refresh-proof**: a full reload rehydrated 78 moves + 78 captions from REST, in
  order. **Zero console errors** throughout.

TTS note: this browser has 22 voices but never fires the utterance `end` event
(a known automation quirk) — the `TTSService` guard timer and muted caption
pacing both cover it, and on a normal browser `end` drives the pacing. Audio
itself could not be heard in this environment, only the `speak()` calls observed.

Removed the now-superseded client-side voice hooks (`useVoice.ts`,
`useVoiceCommentary.ts`, `lib/commentary.ts`) — replaced by the TTSService +
presentation queue; nothing else imported them.

### LIVE RUN — 2026-07-18 (user approved "run live")

One small live game via `scripts/run_live_game.py` (new): `MODE=live` scoped in the
script's env, config.yaml stayed `mock`. `--max-moves 4`, 8s thinking window,
commentary on. Preflight (`verify_models.py`, now also checking `commentator_model`)
was green — all five models live+free.

**Result:** `1/2-1/2` by `max_moves`, 4 plies, **28 requests**, PGN
`1. Nf3 c6 2. Rg1 g5`. Wall-clock >10 min — the account's per-minute limit is as
brutal as the 2026-07-16 run documented.

**What the live run proved for the two new features:**
- ✅ **F1 streaming works live.** White (`gpt-oss-20b:free`) move 1 `Nf3` streamed
  its reasoning over `AGENT_THINKING_TOKEN` events (91 chars assembled: "Develops
  knight, controls center, follows opening principles"). The real streaming path —
  `stream:true` SSE → forwarded deltas → same parse/validate — ran end to end.
- ✅ **F2 never goes silent.** The commentator (`qwen3-coder:free`) was 429'd on
  *every* call, and every move still got commentary via the template fallback
  ("White brings the knight to f3." …). The voice never dropped.
- The 429 ladder, forfeit handoff, streaming, and commentary all interoperated
  under real load. Black was rate-limited to death (forfeited both turns); White
  landed move 1 then proposed 3 illegals on move 2 → forfeit. The analyst verdict
  was a template fallback (rate-limited), honestly labelled and saying so.

**Bug found by the live run and fixed:** `MOVE_COMMENTARY.source` was labelled
`"model"` even when the commentator had fallen back to the template (rate-limited)
— the label lied. `comment_move` now returns a `CommentaryResult(text, source)`
reporting the *true* source per line; the orchestrator emits that. Regression
tests pin it (`source == "template"` on a 500/empty reply, `"model"` on a real
answer). Full suite **368 passed**.

**What a live run still hasn't shown** (same blocker as before): a *model-authored*
commentator line and a model-authored verdict — the per-minute limit 429s them
before they land. The plumbing is proven; only a looser tier (or a quiet bucket)
will land a model-authored line.

### Next step

Both features are complete, mock-verified in-browser, and live-verified for
plumbing + fallbacks. Open items are tier-limited, not code: landing a
model-authored commentator/verdict line needs a looser rate limit.

---

## POST-PHASE-5 — Live voice commentary (2026-07-16)

Spoken commentary via the browser's Web Speech API (`speechSynthesis`) — free,
offline, no API cost, in keeping with mock mode. New **🎙️ Voice** toggle.

- `lib/commentary.ts` — phrase banks + `reactionForMove` / `openerLine` /
  `finaleLine`. Names the captured piece ("Black snatches a pawn!"); every win
  opens with an excited shout ("Unbelievable! … comes out on top!"), with calmer
  draw lines and an abort sign-off.
- `hooks/useVoice.ts` — TTS wrapper: async voice loading, a high-priority
  interrupt for big moments, drops backlog so speech stays in sync, stops on
  toggle-off.
- `hooks/useVoiceCommentary.ts` — drives it off the store: opener on game start,
  reactions to notable moves (capture/check/mate/forfeit — quiet moves stay
  silent), the analyst's commentary lines as they arrive, and the finale.
  Turning Voice on also enables analyst commentary so there are lines to read.
- Verified in-browser via a `speechSynthesis.speak` spy (can't hear audio in the
  test env): a full game spoke 46–47 lines — opener, piece-named captures,
  analyst lines, and a shout-led finale matching the result.

No backend changes; reuses existing COMMENTARY events + client-side reactions.

**Follow-up (same day):** the split "Commentary" (text) vs "Voice" toggles
confused the user — they turned on Commentary and expected voice. **Merged into
one 🎙️ Commentary toggle** that does both text + speech; removed the separate
Voice toggle. Reworked pacing so it's listenable at every speed: normal lines
are *skipped* when the voice is still talking (no backlog, no clipped words),
big moments (opener/mate/forfeit/finale) interrupt. Added light filler remarks
on quiet moves so slower speeds don't go silent. Verified at Slow (≈a line every
3s) and Watchable.

---

## POST-PHASE-5 — Abort fix + Reset button (2026-07-16)

- **Fixed abort.** `_finish` only emitted `GAME_OVER` for *finished* games, so
  aborting sent no terminal event: the backend stopped the game but the live UI
  froze with a stale "Abort" button until a manual refresh. `_finish` now emits
  a terminal `GAME_OVER` for every ending, tagged `status: finished | aborted |
  error`; the frontend maps that to the real status. Verified in-browser: Abort
  now shows the "Game aborted." banner, hides the button, and freezes the board
  live. Regression tests added (orchestrator + API).
- **Added a Reset button.** Clears back to the start screen from any state —
  stops the game if running, resets the store, drops `?game=` from the URL. Also
  gave Abort an "Aborting…" state and added an error banner.
- Docker images rebuilt with the fix. 332 backend tests pass.

---

## LIVE RUN — 2026-07-16 (user approved "run live")

One live game, `max_moves: 30`, commentary off, `MODE=live` scoping (config.yaml
stayed `mock`). Backend returned to mock immediately afterward.

**Preflight worked exactly as designed.** `verify_models.py` caught that
`openai/gpt-oss-120b:free` had **rotated out** of the free lineup — the precise
failure §12 predicted. Swapped White to `openai/gpt-oss-20b:free` (live, free, a
PLAN-recommended pick) in config.yaml; the other three verified live+free. No
game was started until the preflight was green.

**Result:** `1-0` by `max_moves` (adjudicated on material), 30 plies, **102
player requests**, **48 RATE_LIMITED events**. The account is on a very tight
per-minute tier — 429s throttled the whole game (each a 10/30/60s backoff),
which is why it took ~35 min of wall-clock, not the ~35 requests I estimated.

**Per-model tuning signal (§11 Phase 3) — both free models failed, differently:**
- White `gpt-oss-20b:free`: **27 illegal moves proposed, forfeited 13 of 15
  turns.** Can produce UCI but constantly proposes illegal ones. Early moves had
  real reasoning ("Develops a knight, controls center…").
- Black `llama-3.3-70b:free`: **0 illegal, but forfeited all 15 turns** — it was
  *rate-limited to death*. Every turn exhausted the 429 ladder
  (`RateLimitError after 3 backoffs`) before returning a move, so the
  orchestrator forfeited to a random legal move. Never actually got to play.

### Phase 3 — live verify ✅ NOW COMPLETE

All of §8 validated against real OpenRouter under real load: legal moves +
reasoning, illegal-move retries, forfeit handoff, the 429 backoff ladder,
`RATE_LIMITED` events reaching the stream (48 of them, rehydrated on refresh),
the request counter, and the model-rotation fallback via preflight. This was a
*richer* test than a clean game would have been — the adversity exercised every
failure path at once. The illegal-attempt tuning data is captured per model.

### Phase 4 — live verify ⚠️ PARTIAL: fallback proven, model-authored verdict NOT

A verdict WAS produced, persisted, streamed, and rendered on the web with grades
F/F and honest key moments citing real moves (Nxb8, a6, Nh6). **But it was a
`template` fallback**, because the analyst (`qwen/qwen3-coder:free`) was *also*
rate-limited out (`analyst unreachable: … rate limited after 3 backoffs`) — and
the verdict paragraph says exactly that, in plain sight. So:
- ✅ The fallback chain works under real adversity — a rate-limited analyst
  degrades to an honest template that never pretends to be analysis.
- ❌ **A model-authored verdict referencing game moments was NOT obtained.** The
  per-minute rate limit is too tight to land one analyst call right after a
  ~100-request game. This specific §11 Phase 4 goal remains unverified until
  either the account has a looser tier, or a live game is run small enough
  (e.g. `max_moves: 6`, and wait for the per-minute bucket to refill) that the
  analyst call gets through.

### Deviations / findings from the live run

1. **`white_model` changed** to `openai/gpt-oss-20b:free` in config.yaml
   (120b rotated out).
2. **`requests_used` on the game DB row undercounts by the analyst's calls.**
   `finish_game` writes the count *before* `_run_analyst` runs, so analyst
   requests (and their retries) aren't reflected in the persisted total. The
   live per-move count in events is correct; only the final DB tally is short.
   Minor, but worth fixing: move the count write to after the analyst, or add
   the analyst's requests. Logged for a later pass.
3. **Free-tier reality:** ~100 requests for a 30-move game (both models forfeit
   heavily), and the per-minute limit makes it ~35 min wall-clock. For any
   future live test, `max_moves: 6–10` is the sane size, and it's the only way
   to get a model-authored verdict through the rate limit.

---

## Phase 0 — Scaffold ✅ COMPLETE (2026-07-15)

### What was built

Repo root is the existing `LLM_Chess_Game/` directory (PLAN.md §4 calls it
`llm-chess-arena/`; no nested folder was created).

- `config.yaml` — all of PLAN.md §5, verbatim except `mode` (see deviations).
- `.env.example` — `OPENROUTER_API_KEY` + optional attribution/`MODE` vars.
- `.gitignore` — `.env` excluded from the first file written, plus venv,
  `__pycache__`, `node_modules`, `dist`, `*.db`.
- `README.md` — setup, mock vs live, getting a key, quota guidance.
- `backend/requirements.txt` + `backend/.venv` with deps installed.
- `backend/app/config.py` — pydantic-settings loader. `config.yaml` holds
  tunables, `.env` holds secrets, env `MODE` overrides `config.yaml`. Live mode
  with an empty key raises at load rather than 401-ing on every move.
- `backend/app/main.py` — FastAPI app, CORS for the Vite origin, `/health`.
- `backend/app/agents/`, `backend/tests/`, `scripts/` — package dirs staged.
- `frontend/` — Vite + React 19 + TS scaffold, Tailwind v4, `react-chessboard`,
  `chess.js`, `zustand` installed; template cruft removed; placeholder page that
  fetches `/api/health` and renders backend status; Vite proxy for `/api`+`/ws`.

### Test results

No unit tests yet — Phase 0 is scaffold only; the first tests land in Phase 1
(`test_engine.py`). `npm run build` (tsc -b + vite build) passes clean.

### ✅ Verify results — both pass

1. **`uvicorn app.main:app` serves `/health`** — PASS.
   `GET /health` → `200 {"status":"ok","version":"0.1.0","mode":"mock",
   "api_key_configured":true,"models":{...}}`. `/api/health` → `200` (same
   handler, dual-registered).
2. **`npm run dev` shows placeholder page** — PASS. Verified in-browser:
   dark-mode page renders, Tailwind styles applied, no console errors, and the
   page's fetch through the Vite proxy returned live backend status
   (`mode: mock`), so the frontend↔backend path is confirmed working.

### Deviations from PLAN.md (reality wins)

1. **`mode: mock` in `config.yaml`, not `live`.** PLAN §5 shows `mode: live`;
   the working rules (§14.2) and the user require mock as the dev default.
   `live` is opt-in via `config.yaml` or `MODE=live`.
2. **Dependency is `chess`, not `python-chess`.** On PyPI `python-chess` is now
   a stub that just depends on `chess` (installing it pulled `chess-1.11.2` +
   `python-chess-1.999`). `requirements.txt` pins `chess>=1.11`. The import is
   `import chess` either way — no code impact.
3. **Vite dev server runs on :5174, not :5173.** Port 5173 is held by an
   unrelated pre-existing process (PID 6908) that is not part of this project,
   so it was left running. Vite auto-fell back. Nothing is hardcoded to 5174.
4. **Vite binds IPv6 `localhost` only** — `127.0.0.1:5174` refuses connections
   while `localhost:5174` works. Relevant only when curl-ing the dev server.
5. **Tailwind installed as v4**, which drops `tailwind.config.js` and
   `npx tailwindcss init`. Configured the v4 way: `@tailwindcss/postcss` in
   `postcss.config.js` + `@import 'tailwindcss'` in `src/index.css`.
6. **No `/api` prefix rewrite in the Vite proxy.** PLAN §10 gives the backend
   `/api/*` paths directly, so the proxy passes them through unchanged.
   `/health` is dual-registered at `/api/health` for the frontend and bare
   `/health` for probes and the §11 verify step.
7. **Toolchain versions** are newer than PLAN assumed: Python 3.13.5, Node 24,
   React 19, Vite 8, FastAPI 0.139, pydantic 2.13. No compatibility issues so
   far; watch `react-chessboard` v5 API in Phase 2 (its props changed from v4).

### Notes for later phases

- `.env` already exists with a real `OPENROUTER_API_KEY` (`api_key_configured:
  true`). Mock mode ignores it. Do not echo it; do not run live without an
  explicit instruction.
- Phase 3 must run `scripts/verify_models.py` before any live call — the
  `:free` IDs in `config.yaml` are unverified and the lineup rotates.
- Background dev servers from this session: uvicorn on :8000, Vite on :5174.

---

## Phase 1 — Chess engine core + mock game ✅ COMPLETE (2026-07-16)

### What was built

- `app/engine.py` — `ChessEngine` wrapper over python-chess. Owns the board;
  `push_uci` validates against the legal move generator and raises
  `IllegalMoveError` on anything else, so a hallucinating model can never
  corrupt state (§2.1). Provides fen/turn/legal_moves_uci/san_history, game-over
  + termination reason, winner, material balance, max-move adjudication, PGN
  export, and `is_valid_pgn` round-trip validation.
- `app/events.py` — `Event` pydantic model + all 10 `EventType` values from §6,
  and `EventBus`: per-game async pub/sub with gap-free `seq`, full history for
  replay, and eviction of subscribers whose queue fills (a stalled browser tab
  must never stall the game loop).
- `app/store.py` — `GameStore` over sqlite3. Tables: games, moves, events,
  verdicts, with WAL, foreign keys, and `UNIQUE(game_id, ply)` /
  `UNIQUE(game_id, seq)` guards. `get_full_game` returns the §9 rehydration
  bundle. Plain sync sqlite3 is a deliberate choice: writes are sub-ms and a
  game makes only a few hundred.
- `app/agents/base.py` — `MoveProposal` + `BasePlayer` ABC. One interface means
  mock and live modes share the same orchestrator code path.
- `app/agents/player.py` — `MockPlayer`: random legal move + canned reasoning,
  seedable for deterministic tests. `illegal_rate`/`forfeit_rate` fake a
  misbehaving model so the retry and forfeit paths are exercised in mock mode
  rather than waiting for a live model to misbehave.
- `app/orchestrator.py` — the §6 game loop: thinking → propose → validate →
  retry/forfeit → push → persist → emit, with abort, request-budget
  kill-switch, and crash handling that marks a game `error` instead of leaving
  it `in_progress` forever. Legality is enforced here, never in the agent.
- `scripts/run_mock_game.py` — runs a full mock game and prints the event
  stream to stdout (the §11 Phase 1 "events printed to stdout" deliverable).
- `backend/pytest.ini` — `asyncio_mode = auto`, DeprecationWarnings as errors.

### Test results

**89 passed, 0 failed** (`pytest -q`, 3.2s warm).
- `test_engine.py` (40) — basics, illegal/malformed UCI rejection, captures,
  en passant, promotion, every termination path, adjudication, PGN round-trip.
- `test_orchestrator_mock.py` (26) — full mock game, persistence, event stream,
  illegal/forfeit handling, termination paths, abort/kill-switch/crash.
- `test_events.py` (13) — seq integrity, subscriber isolation, slow-consumer
  eviction.
- `test_store.py` (10) — round-trips, ordering, uniqueness, rehydration.

Four engine tests failed on first run. All four were **bad tests, not bad
code** — worth recording so they aren't "fixed" wrongly later:
1. Asserted `a8=Q+`; the promotion gives no check. Fixture was wrong.
2. Asserted no 50-move claim at halfmove clock 99. python-chess *does* claim at
   99, because a legal move reaches 100. Reality; test now asserts both 99
   (claimable) and 98 (not).
3. Both "material lead adjudicates" fixtures were **accidentally checkmate**, so
   they were testing checkmate detection, not adjudication. Replaced with quiet
   positions and pinned with `assert engine.outcome is None`.
4. Asserted `is_valid_pgn` rejects `"1. e4 e5 2. Qxq9 ##"`. python-chess flags
   illegal *moves* but silently skips tokens that don't match SAN grammar, so
   it parses the valid prefix. Test now asserts rejection of an unplayable move
   and documents the token-skipping limitation instead of hiding it.

### ✅ Verify results — all pass

1. **pytest green** — PASS. 89/89.
2. **Mock game start→finish in <5s** — PASS. 0.16s per game in-test (asserted
   against the 5s budget); ~1s for the whole demo script including startup.
   Zero API requests, asserted.
3. **Produces valid PGN** — PASS locally. Every stored move replays through the
   legal move generator to the exact final FEN, and exported PGN round-trips
   via `is_valid_pgn`. **Not yet pasted into lichess.org/paste** — that step
   publishes a game to an external service, so it's left for the user to
   approve. It has real value the local check can't give: lichess is an
   *independent* implementation, whereas validating python-chess output with
   python-chess is somewhat circular.
4. **Forced-fixture results correct** — PASS. Fool's mate → `0-1` /
   `checkmate` / winner black (both at engine level and through the full
   orchestrator loop). Stalemate → `1/2-1/2` / `stalemate` / no winner.
   50-move → `1/2-1/2` / `fifty_moves`. Also covered: insufficient material,
   threefold, max-move adjudication both ways.

Sample game (`--seed 42`): 120 plies, hit the ply cap, adjudicated `1-0` on
material (White up on material), 2 forfeited moves, PGN round-trips clean.

### Deviations from PLAN.md (reality wins)

1. **`max_moves` is counted in plies, not full moves.** PLAN §5 says
   "max_moves: 120" and §6's loop tests `move_count < max_moves` once per agent
   turn — i.e. per ply. Implemented per ply and documented in `ChessEngine`.
   120 plies = 60 moves each. If the intent was 120 *full* moves, change one
   constructor argument.
2. **`outcome(claim_draw=True)`.** Without it python-chess only ends games at
   the *forced* 75-move/fivefold thresholds, so the 50-move and threefold
   fixtures would never trigger and two shuffling models would grind to the ply
   cap. Side effect: claims at halfmove clock 99 (see test note above).
3. **Adjudication margin is 2 pawns.** §12 says "engine material count decides,
   else draw" without a threshold. Below a 2-pawn edge the game is called a
   draw. Single constant, `ADJUDICATION_MARGIN`.
4. **Windows console can't encode emoji** (cp1252 → `UnicodeEncodeError`). The
   demo script now reconfigures stdout to UTF-8 and falls back to ASCII icons.
5. **`tests/test_store.py` and `tests/test_events.py` added** beyond the §4 file
   list. The store and bus are load-bearing for Phase 2 rehydration and
   streaming; they warranted direct tests.
6. **The orchestrator forfeits on a push failure** rather than propagating.
   Belt-and-braces: even if a future agent slips a bad move past the pre-check,
   the game completes rather than crashing.

### Notes for later phases

- `MockPlayer(illegal_rate=…, forfeit_rate=…)` is how to exercise the illegal
  and forfeit UI states in Phase 2/5 without any live calls.
- `EventBus.history` is what a reconnecting WS client should replay from;
  `store.get_full_game()` is the REST rehydration payload. Both exist and are
  tested.
- `requests_used` is plumbed through the orchestrator, events, and DB but is
  always 0 until the Phase 3 `llm_client` increments it.
- The `ILLEGAL_ATTEMPT` event carries the exact rejected UCI, and `moves.attempts`
  records the count — the §11 Phase 3 "tuning signal" is already wired.

---

## Phase 2 — WebSocket streaming + minimal UI ✅ COMPLETE (2026-07-16)

### What was built

**Backend**
- `app/manager.py` — `GameManager` + `GameSession`: owns running games and the
  asyncio tasks driving them, so routes stay thin. Builds `MockPlayer`s in mock
  mode (carrying the requested model names as labels, so the UI looks real at
  zero cost) and raises `NotImplementedError` for live mode until Phase 3.
- `app/main.py` — the §10 REST surface (`POST /api/games`, `GET /api/games`,
  `GET /api/games/{id}`, `POST /api/games/{id}/abort`) plus `WS
  /ws/games/{game_id}?since=N` and a `GET /api/games/{id}/events` REST fallback.
  The WS subscribes *before* replaying history, so events published mid-replay
  queue instead of vanishing; finished games are served straight from SQLite so
  a backend restart doesn't break replay.

**Frontend**
- `types/events.ts` — mirrors the backend schema.
- `state/gameStore.ts` — zustand. `applyEvent` is idempotent (ignores
  `seq <= lastSeq`), `hydrate` rebuilds everything from REST including illegal/
  forfeit counts recomputed from the event log.
- `api/ws.ts` — REST client + `streamGame`: rehydrates over REST on *every*
  (re)connect, then follows the WS from that seq, with exponential backoff and
  no reconnect on deliberate closes (1000/4404).
- `components/Board.tsx` — `react-chessboard` v5 (`options` object), last-move
  highlight, animation, dragging disabled — the agents play, we watch.
- `components/MoveList.tsx` — SAN pairs, `⚠` on forfeits, `×N` retry counts.
- `components/GameControls.tsx` — New Game, Abort, speed, chaos toggle, mode
  badge, connection indicator.
- `App.tsx` — layout, player bars with thinking/illegal/forfeit badges, result
  banner, self-dismissing forfeit toasts. Game id lives in `?game=` so a
  refresh rehydrates rather than restarting.

### Test results

**120 passed, 0 failed** (`pytest -q`, ~13s). New: `test_api.py` (31) covering
health, create, validation, rehydration-replay, abort, WS stream/replay/`since`/
gap-free seq/restart-from-DB/unknown-game, and REST↔WS agreement.

### Two real bugs found — both only visible end-to-end

1. **Forfeits never reached the UI.** `MockPlayer` pre-picked a *legal* move
   when simulating a forfeit, so the orchestrator's legality check passed and
   `MOVE_FORFEITED` was never emitted — yet the move was still flagged
   `forfeited` in the DB. The DB and the event stream disagreed, the badge and
   toast never fired. Fixed in the mock, not the orchestrator: an agent that
   burns its budget now proposes an *illegal* move and lets the orchestrator
   decide to forfeit, which is how a real failing model behaves and keeps §2.1
   intact (agents propose, the orchestrator disposes). Regression test:
   `test_every_forfeited_move_has_a_matching_event`.
2. **REST and WS disagreed about event shape.** `GET /api/games/{id}` returned
   events with a `payload` key (the raw DB column) while the WS sent `data`.
   The client reads `data`, so rehydrating any game containing an
   `ILLEGAL_ATTEMPT` or `MOVE_FORFEITED` threw `TypeError: reading 'color'` and
   wedged the reconnect loop. Invisible until chaos mode: the earlier refresh
   test passed only because a clean game has no such events. `GameStore.get_events`
   now returns the canonical Event shape and the WS-from-DB path reuses it.
   Regression tests: `test_rest_events_match_the_websocket_events_exactly`,
   `test_stored_events_come_back_in_the_websocket_shape`.

Both were caught by the browser verify, not by the tests that existed at the
time — the lesson being that "the tests pass" and "the app works" are different
claims. The suite now pins both.

### ✅ Verify results — both pass

1. **New Game (mock) → full animated game live** — PASS. Clicked in-browser:
   the board animates move by move, move list fills, last-move highlight
   tracks, thinking states flicker per turn, and the game ran to completion
   (120 plies, `1/2-1/2` by `max_moves`) with the result banner rendering.
   Zero API calls. With chaos mode on, illegal badges, `×N` retry counts, `⚠`
   forfeit marks and the penalty toasts all fire.
2. **Refresh mid-game → state rehydrates** — PASS. Hard refresh at 30 plies
   restored the full move list with annotations, the exact board position, and
   the illegal/forfeit badges (recomputed from the event log), then kept
   streaming. Verified with chaos both off and on; zero console errors.

### Deviations from PLAN.md (reality wins)

1. **`app/manager.py` is new** — not in the §4 file list. Session ownership
   didn't belong in `main.py` or the orchestrator.
2. **`GameStore.get_events` returns `data`, not the DB's `payload` column.**
   Forced by bug 2 above; REST and WS must be interchangeable per §9.
3. **`@app.on_event("shutdown")` is deprecated** in FastAPI 0.139 — replaced
   with a `lifespan` context manager. Caught by `filterwarnings = error` in
   pytest.ini, which is exactly why that setting is there.
4. **`GET /api/games/{id}/events` added** beyond §10 — a REST mirror of the
   stream, cheap and useful for debugging.
5. **`uvicorn --reload` does not work in this repo.** WatchFiles never fires on
   this OneDrive-synced folder, so code changes silently don't load — a running
   server served stale code for several minutes and made a fixed bug look
   unfixed. Worse, killing the reloader parent orphans the child, which keeps
   the port bound while `tasklist` shows nothing for the parent PID. **Run
   uvicorn without `--reload`; restart it manually after backend edits.**
6. **CORS uses `allow_origin_regex`** for ports 5170-5179 + 4173, since Vite's
   port varies (5173 is taken by an unrelated process).
7. **`.env` line 2 (`base_url=`) can't be parsed by python-dotenv** — logged as
   "could not parse statement starting at line 2" on every boot. Harmless (the
   base URL comes from `config.yaml`), but that line should be removed from
   `.env` to silence it.

### Notes for later phases

- The frontend never computes chess logic: every position comes from a
  backend FEN (§2.7 holds). `chess.js` is installed but unused until the Phase 5
  scrubber.
- `GameControls` has placeholders where Phase 5's model pickers and quota meter
  go; `/api/models` is not built yet.
- Player bars are minimal — Phase 5 replaces them with real `AgentPanel`s
  (avatar, reasoning line, status). Reasoning already arrives on every
  `MOVE_MADE` and is shown as a tooltip on the move list.
- Chaos mode (`illegal_rate`/`forfeit_rate` via `POST /api/games`) is the way to
  demo penalty UI with no API calls.

---

## Phase 3 — OpenRouter integration ⚠️ CODE COMPLETE, LIVE VERIFY OUTSTANDING (2026-07-16)

**Built and verified offline at the user's instruction ("mock only for now").
No network call has been made — not even to `/models`. Phase 3 is NOT signed
off: its second ✅ Verify step requires one real game.**

### What was built

- `app/prompts.py` — the §7 templates. System prompt demands strict JSON;
  user prompt carries FEN + colour + move number + last-10 SAN + the explicit
  legal move list. `build_retry_feedback` appends the §6d correction
  ("'e2e5' is ILLEGAL…") with the legal list restated.
- `app/parsing.py` — the defensive chain from §7: strip fences → extract JSON →
  regex-validate UCI → fall back to a bare UCI in prose. Accepts key synonyms
  (`move`/`uci`/`move_uci`/`best_move`). Raises `ParseError` carrying the raw
  text so the retry prompt can quote it. **Deliberately knows nothing about
  positions**: it guarantees *shape*, the engine decides *legality*.
- `app/llm_client.py` — `OpenRouterClient` + `Throttle`.
  - `Throttle`: global minimum-interval gate behind an asyncio lock, so all
    agents share one queue (limits are per account, not per model, §8).
    `clock`/`sleep` injectable for exact timing tests.
  - Retry ladder: 429 → 10s/30s/60s backoff + `RATE_LIMITED` event → then
    `RateLimitError`; 404 → swap to `openrouter/free` once, announce it, then
    `ModelNotFoundError`; 5xx → 2 retries; 401/403 → `AuthError` with no retry
    (retrying a bad key is pointless); timeouts → `LLMError`.
  - Empty-content guard (§8) including provider errors returned inside a 200.
  - `requests_used` counts **every** request, retries included.
- `app/agents/player.py` — `PlayerAgent`: owns the retry loop, not the forfeit
  decision. On budget exhaustion it returns the model's *last bad answer* (or
  the `0000` sentinel) so the orchestrator forfeits and substitutes a random
  legal move. This is the Phase 2 lesson applied: an agent that hands back a
  legal move it didn't choose would silently skip `MOVE_FORFEITED`.
- `app/manager.py` — live mode now builds real `PlayerAgent`s sharing one
  client/throttle; the client's event hook **publishes and persists** (an event
  on the WS but not in SQLite vanishes on refresh — the Phase 2 bug in a new
  costume). Closes the httpx pool when a game ends.
- `app/orchestrator.py` — `requests_used` is now a live counter read off the
  client (`request_counter`), so the quota meter and kill-switch see the truth.
- `scripts/verify_models.py` — preflight against `/models`. **Written, not
  run.**
- `backend/tests/conftest.py` — **autouse `block_network` fixture**: real HTTP
  transports and `socket.create_connection` raise inside tests.
  `httpx.MockTransport` still works. The suite cannot spend quota by accident.

### Test results

**234 passed, 0 failed** (was 120), ~13s, **with the network blocked** — which
is what makes "no live calls were made" a checkable claim rather than a promise.
- `test_move_parsing.py` (58) — the §11 verify set.
- `test_llm_client_throttle.py` (27) — throttle timing, retry ladders, quota.
- `test_player_agent.py` (18) — retry loop, forfeit handoff, quota.
- `test_live_wiring.py` (11) — full games through the live path against a fake
  transport, plus manager wiring.
- The guard itself was verified by a temporary test that attempted a real
  request to openrouter.ai and was blocked (test removed afterwards).

### ✅ Verify results

1. **Move-parsing unit tests** — PASS. All four named cases covered:
   markdown-fenced JSON, bare UCI, garbage, and illegal-but-valid-format. The
   last one asserts parsing *accepts* well-formed illegal moves: shape and
   legality are different questions, and if parsing rejected them the retry
   prompt could never tell the model which move it got wrong.
2. **Throttle timing tests** — PASS. First request never waits; subsequent ones
   wait exactly the remaining interval; elapsed time is deducted; the gate is
   global across concurrent callers (4 callers ⇒ 12s of spacing at 4s).
3. **ONE real game** — ❌ **NOT RUN.** Requires an explicit "run live".
4. **Log and inspect illegal attempts per model** — ❌ **NOT RUN** (needs 3).
   The plumbing is ready and tested: `ILLEGAL_ATTEMPT` carries the exact
   rejected UCI and `moves.attempts` records the count per move per model.

**What offline testing cannot tell us** — worth being honest about, since these
are exactly the things Phase 3 exists to find out: whether the configured
`:free` model IDs still exist today; the real illegal-move rate per model (the
tuning signal in §11); whether models honour `response_format: json_object`;
real 429 behaviour and the account's actual daily tier. `test_live_wiring.py`
proves the plumbing, not the models.

### Deviations from PLAN.md (reality wins)

1. **`app/parsing.py` is new** — §4 implies parsing lives in the agent, but the
   §11 test file is `test_move_parsing.py`, and a pure, position-free parser is
   far easier to test exhaustively.
2. **`tests/conftest.py` with `block_network`** — beyond the plan. Given §2.4
   ("rate limits are a first-class design constraint") and the standing
   mock-only rule, making accidental live calls *impossible* rather than merely
   discouraged seemed worth 30 lines.
3. **`AuthError` on 401/403 is not in the §8 ladder** — added because retrying
   a rejected key just burns quota to fail identically.
4. **Parsing accepts key synonyms and bare UCI in prose.** §7 says strict JSON;
   in reality free models wrap it in prose or rename the field, and a right
   answer in the wrong envelope shouldn't cost a retry. Legality is still the
   gate, so this loosens nothing that matters.
5. **`verify_models.py::is_free` treats missing/unparseable pricing as NOT
   free.** A check whose purpose is preventing accidental spend has to fail
   toward caution; my first version returned True for a model with no pricing
   data.
6. **Additional test files** (`test_player_agent.py`, `test_live_wiring.py`)
   beyond §4's list.

### Notes for later phases

- Phase 4's `AnalystAgent` should reuse `OpenRouterClient` (temperature 0.3 per
  §7) and the same parse-retry-fallback discipline; `max_tokens_analysis: 1500`
  is already in config.
- `MockAnalyst` must exist for mock mode, mirroring `MockPlayer`.
- The `RATE_LIMITED` and `ERROR` (model-fallback) events now reach the frontend
  but **the UI ignores them** — `gameStore.applyEvent` has no `RATE_LIMITED`
  case, so a live game would stall with no explanation. Handle in Phase 4/5.
- `GET /api/models` (§10) is still unbuilt; `client.list_models()` is ready for
  it. It's needed for Phase 5's model picker.

---

## Phase 4 — Analyst agent + verdict ⚠️ MOCK VERIFY PASSES, LIVE VERIFY OUTSTANDING (2026-07-16)

**Built and verified in mock mode. No network call made. Like Phase 3, the
second ✅ Verify step (one live run) is still outstanding.**

### What was built

- `app/verdict.py` — `GameFacts` (objective, engine-derived evidence: results,
  per-colour illegal/forfeit counts, annotations) and the `Verdict` schema from
  §6 step 6. Tolerant on input, strict on shape: `winner` accepts "White",
  "1-0", "White (gpt-oss)", "nobody"…; `key_moments`/`blunders` accept strings,
  objects or a bare string. `template_verdict()` builds a verdict from engine
  facts alone; `stamp_engine_facts()` overwrites the authoritative fields after
  parsing so a model **cannot** rewrite who won (§3).
- `app/agents/analyst.py` — `MockAnalyst` (template verdict + canned
  commentary, zero cost) and `AnalystAgent` (real LLM, temperature 0.3, retry
  once on parse failure, then template fallback). Commentary failures are
  silent; a verdict always exists.
- `app/prompts.py` — analyst system/user prompts carrying PGN, engine result,
  termination, per-model rule-breaking record and per-move annotations, with
  the §7 instruction that the result is final.
- `app/agents/base.py` — `BaseAnalyst` ABC.
- `app/orchestrator.py` — verdict after `GAME_OVER` (emitted *and* persisted);
  optional commentary every N plies. A broken analyst cannot ruin a finished
  game — the game happened, the review is extra.
- `app/manager.py` — builds `MockAnalyst`/`AnalystAgent`; the live analyst
  shares the players' client, so its requests go through the same global
  throttle and per-game budget.
- Frontend: `VerdictCard.tsx` (engine result and analyst opinion shown as
  separate things; "template (no model review)" badge when nothing reviewed
  it), `AnalystPanel.tsx` (commentary feed + verdict + rate-limit banner),
  store handling for `VERDICT`/`COMMENTARY`/`RATE_LIMITED`, commentary toggle.
- **Closed the Phase 3 gap**: `RATE_LIMITED` is now handled in the UI — a live
  game stalling on a 429 shows "Rate limited by X — waiting Ns" instead of
  freezing silently.

### Test results

**321 passed, 0 failed** (was 234), ~20s, network blocked.
- `test_verdict.py` (45) — counts, grading, winner normalisation, list
  coercion, template honesty, engine-fact stamping.
- `test_analyst.py` (23) — mock, happy path, prompt contents, retry-then-
  fallback, and the model-cannot-rewrite-history rules.
- `test_orchestrator_mock.py` +12 — verdict emitted/persisted after GAME_OVER,
  no verdict for aborted games, broken analyst tolerated, commentary cadence.
- `test_api.py` +8 — verdict over REST/WS, survives refresh, commentary opt-in.

Two failures found along the way. One was a **real bug**: `template_verdict`
checked `facts.winner` before `termination == "max_moves"`, so an adjudicated
game printed "White won by max_moves after 120 plies" — nobody *won* that; it
was decided on material after the ply cap. Now it says "White takes it on
material, not on merit." The other was a bad fixture of mine (1 illegal in 2
moves is a 50% rate, so "B" was correct, not "A-"); the corrected expectation
is pinned by `test_grades_are_rates_not_raw_counts`.

Also removed `alert()` from the new-game error path: a modal dialog blocks the
page and would freeze a live game behind an OK button. It's a toast now.

### ✅ Verify results

1. **Mock mode produces a template verdict** — PASS, end-to-end in the browser.
   A 40-ply chaos game rendered: `1/2-1/2 — draw / by max_moves`, badge
   "template (no model review)", grades F/F with model names, and the §3
   headline case — **"Performance winner: White — drawn on the board, but the
   analyst gives it to white."** Key moments cite real moves ("Move 2: first
   blood — black played Bxa3"), the summary reports true counts, and the
   paragraph states plainly that no model reviewed the chess. Commentary feed
   rendered 20 entries when enabled; absent by default.
2. **One live run producing a real verdict referencing actual game moments** —
   ❌ **NOT RUN.** Needs an explicit "run live".

**What mock testing cannot tell us**: whether a real analyst returns valid JSON
often enough that the fallback stays rare, and whether its key moments cite real
moves rather than hallucinating games that never happened. The tolerant parser
and `stamp_engine_facts` are tested against fakes; the models are not.

### Deviations from PLAN.md (reality wins)

1. **`app/verdict.py` is new** — §4 puts everything in `analyst.py`, but the
   schema + `GameFacts` + template are used by the orchestrator and the store
   too, and keeping them model-free makes the "engine decides" rule enforceable
   in one place.
2. **`Verdict` carries `engine_result`/`engine_termination`/`engine_winner`/
   `source`** beyond §6's field list. Without them the UI cannot show the real
   outcome next to the model's opinion, and cannot admit when a verdict is a
   fallback template rather than analysis.
3. **`template_verdict` leaves `blunders` and `best_move` empty.** §6 lists
   them, but naming a blunder without an evaluation would be invention. They
   fill in when a live analyst runs, or from Stockfish in Phase 6.
4. **Grading uses rule-following only** (illegal/forfeit *rates*), because it's
   all that's observable without an engine evaluation. Documented in
   `grade_for`.
5. **Commentary cadence counts plies, not full moves**, consistent with
   `max_moves`.

### Notes for later phases

- Phase 5 owns: model pickers via `GET /api/models` (still unbuilt;
  `client.list_models()` is ready), quota meter, playback scrubber (chess.js is
  installed and still unused), sounds, mobile layout, game history page.
- The board is fixed at `boardOrientation: 'white'`.
- Tooling note: `resize_window` via the Chrome extension corrupted screenshot
  capture for that tab (`clip.scale` deserialize error, then renderer
  timeouts). The app was fine — a fresh tab reproduced nothing. Avoid
  `resize_window`; open a new tab instead. Mobile layout testing in Phase 5
  will need a different approach.

---

## Phase 5 — Full UI polish ✅ COMPLETE (2026-07-16, mock-verified in browser)

### What was built

**Backend**
- `app/models_catalog.py` + `GET /api/models` — free-model list for the pickers.
  Mock mode returns a bundled static list with **zero network** (mock stays
  offline); live mode proxies OpenRouter, filters to free, caches 1h, and falls
  back to the bundled list if OpenRouter is unreachable or returns nothing.
- `/health` now reports `max_requests_per_game` so the quota meter has a
  denominator.
- **`requests_used` undercount fixed** (the live-run finding): `set_requests_used`
  is called after `_run_analyst`, so the persisted total includes the analyst's
  calls. Confirmed in-browser — a rehydrated live game reads 102/250.

**Frontend**
- `hooks/useSounds.ts` — move/capture/check/forfeit/game-over cues synthesised
  with the Web Audio API (nothing bundled, CSP-clean), lazily unlocked on the
  first play, mutable. Only plays a *single* new live ply, never the burst of a
  rehydration.
- `components/AgentPanel.tsx` — replaces the inline PlayerBar: king-glyph
  avatar, model name, status (idle/thinking/waiting/to-move) with animated dots,
  last reasoning line, illegal/forfeit badges that go red on a forfeit.
- `components/PlaybackControls.tsx` — scrubber (⏮ ◀ slider ▶ ⏭ + live). Replays
  from the FEN stored on each move; the frontend computes no chess for it.
- `components/QuotaMeter.tsx` — requests-this-game / per-game budget bar.
- `components/GameHistory.tsx` — slide-over listing past games (result, models,
  relative time, request count); clicking loads a game via normal rehydration.
- `MoveList` — moves are now clickable to scrub; the viewed ply is highlighted.
- Model pickers (W/B/analyst) wired to `/api/models` and into `POST /api/games`.
- Responsive layout: 3-column desktop → single column on mobile, **board first**.
- Removed the last `alert()`; errors are toasts.

### Test results

**329 passed, 0 failed** (was 321), network blocked. New: `test_models_catalog.py`
(9 — mock offline, live proxy, filter-to-free, cache, fallback), plus API and
live-wiring additions for `/api/models`, the health budget field, and the
`requests_used`-includes-analyst regression.

### ✅ Verify results — pass (browser, mock mode)

PLAN's bar: "the full demo flow feels like watching a real match; a
non-technical person can understand what's happening." Verified end-to-end:
- New Game → agent panels show live status and reasoning quotes ("Applying
  pressure and trusting the position…"), board animates, move list fills, the
  scrubber shows "● live".
- **Scrubbing**: clicking move 1 froze the board at `a3` (highlighted in the
  list, "ply 1/32") while the live game kept advancing in the background;
  "live" returns to following.
- **History**: slide-over lists all past games incl. the earlier live game
  ("1-0 · White wins, max_moves, 102 req"); clicking one rehydrates it fully —
  board, move list with ⚠ marks, agent badges (40 illegal/13 forfeits), the
  verdict card (grades F/F), and the quota meter reading 102/250.
- **Model pickers** populated from `/api/models` (GPT-OSS 20B / Llama 3.3 70B /
  Qwen3 Coder). **Quota meter**, **MODE badge**, **connection light** all live.
- **Mobile** (420px, verified via JS since the extension's `resize_window`
  breaks screenshots): single-column grid, visual order board → agents →
  moves/analyst.
- **Zero console errors** throughout.

Sounds are wired and code-verified but not audibly confirmed (no audio in this
environment).

### Deviations from PLAN.md (reality wins)

1. **Scrubber replays from stored FENs, not chess.js.** Every move already
   carries its `fen_after` from the backend, so position replay needs no move
   generation. `chess.js` stays installed but unused — noted in the scrubber; a
   future feature needing legal-move generation can pick it up.
2. **`GET /api/models` is offline in mock mode** (bundled list), so the picker
   works with no key and no network, honouring the mock-only rule. §10 calls it
   "a proxy of OpenRouter's free-model list" — it is, but only in live mode.
3. **Quota meter measures requests-per-game against the kill-switch**, not a
   daily budget (§9 says "requests_used / daily budget"). There is no daily
   budget in config, and the per-game number is the one that's actually
   meaningful and available.
4. **No client-side router** — History is a slide-over, not a separate route
   (avoids adding react-router for one panel). The game id still lives in the
   URL for refresh-proof rehydration.
5. **`EvalBar.tsx` not built** — it's a Phase 6 (Stockfish) item; the §4 file
   list includes it but §11 puts it in the stretch phase.

### Cosmetic item still open

- `.env` line `base_url=` still logs a harmless dotenv parse warning on boot.
  It's the user's file; left untouched.

---

## Next step

**Phases 0–5 are complete.** Phase 3 is live-verified; Phase 4 is mock-verified
with its fallback live-verified (a model-authored live verdict is the one open
item, blocked by the account's per-minute rate limit — see "LIVE RUN" at top).

### Docker Compose added (2026-07-16) — DoD "stranger can `docker compose up`"

- `docker-compose.yml` — two services: `backend` (FastAPI, internal only) and
  `frontend` (nginx serving the built Vite bundle, proxying `/api` + `/ws` to
  the backend so the browser sees one origin, no CORS). `docker compose up
  --build` → http://localhost:8080 → New Game. Mock by default (no key). Live
  via `MODE=live` + `OPENROUTER_API_KEY` in a sibling `.env`.
- `backend/Dockerfile`, `frontend/Dockerfile` (multi-stage build→nginx),
  `frontend/nginx.conf`, `.dockerignore` (keeps `.venv`/`node_modules`/DB and,
  critically, `.env` out of the images — secrets come via compose env only).
- Backend change: `ARENA_DB_PATH` env var puts the SQLite DB on a named volume
  (`arena-data`) instead of the code tree, so history persists across restarts.
  `GameStore` now creates the DB's parent dir if missing.
- **BUILT AND VERIFIED (2026-07-16)** on the user's machine after they installed
  Docker Desktop (Docker 29.6.1, Compose v2). Both images build, backend
  healthcheck passes, app serves at localhost:8080, REST proxied (health/models/
  games), and a live mock game streamed over the nginx-proxied **WebSocket**
  (connection "open", 75 plies) — confirmed in-browser.

  Three real issues surfaced during the build, all now fixed:
  1. **`.env` blocked `docker compose` entirely.** The stray `base_url="…` line
     (long-flagged) had an unterminated quote; compose's `.env` parser is
     stricter than python-dotenv and refused to run. Replaced with a comment;
     the user's key line preserved. (The user's OpenRouter key was visible in
     `.env` during this fix — flagged to them to rotate it; it's gitignored.)
  2. **`npm ci` failed cross-platform.** The host lockfile (Windows) doesn't
     match Vite 8's Rolldown native bindings on linux-musl (`@emnapi/core`
     mismatch). Switched the frontend Dockerfile to `npm install`, which
     resolves the right platform binding at build time. Also regenerated the
     lockfile (it had drifted out of sync with package.json).
  3. **`docker-credential-desktop` not on PATH** when running the CLI outside
     Docker Desktop's own shell — needs `C:\Program Files\Docker\Docker\
     resources\bin` on PATH. Environment quirk, not a project issue.

### Definition of Done (§13) status

- [x] mock: full game + verdict, zero API calls, all tests green (329)
- [x] live: two free models complete a real game within budget (the live run;
      White forfeited heavily but the game completed and adjudicated)
- [x] every illegal attempt visible in UI + stored in DB
- [x] verdict card reflects engine result + analyst grading
- [x] refresh-proof rehydration
- [x] README covers setup, key, mock vs live, quota
- [x] `docker compose up` — built, run, and verified end-to-end at :8080

Remaining options:

**A. Phase 6 (stretch, optional)** — Stockfish eval bar (real blunder detection
+ centipawn data for the analyst), tournament/round-robin mode with a
leaderboard, PGN download / shareable replay URL, personality prompts per model.
All optional per §11.

**B. Build/verify Docker on a machine with a daemon** — `docker compose up
--build`, confirm the match plays at localhost:8080. The one validation this
environment couldn't do.

**C. Land the model-authored live verdict** — a tiny live game (`max_moves: 6`)
with a fresh per-minute bucket, so the analyst call gets through. Closes Phase
4's last gap. Needs "run live".

To re-run live later: `verify_models.py` first (lineup rotates), `MODE=live`
scopes it, keep games small (`max_moves: 6–10`) given the rate limit.

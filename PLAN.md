# PLAN.md — LLM Chess Arena

> **Multi-agent system: two LLMs play chess against each other via OpenRouter, a third LLM analyzes the game and declares a verdict, all rendered live on a web UI.**
>
> Built with Claude Code. Read this entire file before writing any code. Work phase by phase. Update `PROGRESS.md` after every session.

---

## 1. Project Summary

| | |
|---|---|
| **Name** | LLM Chess Arena |
| **What it does** | Orchestrates a full chess game between two LLM agents (White Agent, Black Agent), streams every move live to a browser with an animated board, then a third Analyst Agent reviews the finished game move-by-move and produces a verdict/commentary report |
| **LLM provider** | OpenRouter (free-tier models, OpenAI-compatible API) |
| **Backend** | Python 3.11+, FastAPI, `python-chess`, `httpx`, WebSockets |
| **Frontend** | React + TypeScript + Vite, `react-chessboard`, `chess.js`, Tailwind |
| **Persistence** | SQLite (games, moves, commentary, verdicts) — no external DB needed |

---

## 2. Core Design Principles (do not violate these)

1. **The LLM is never the source of truth for game state.** `python-chess` owns the board. LLMs only *propose* moves; the engine validates everything. LLMs hallucinate illegal moves routinely — this is the single most documented failure mode of LLM chess.
2. **Always prompt with FEN + explicit legal move list.** The proven pattern: give the model the FEN, the full list of legal moves in UCI format, and instruct it to pick ONLY from that list. Never ask an LLM to "just play chess."
3. **Every LLM call has a retry budget and a deterministic fallback.** 3 strikes on illegal/unparseable moves → engine plays a random legal move on the agent's behalf and logs a "blunder-by-forfeit" event (the Analyst gets to see this and mock the model for it — it's part of the fun).
4. **Rate limits are a first-class design constraint.** Free tier ≈ 20 req/min and 50 req/day (1,000/day if the account has ever bought $10 in credits). One game ≈ 60–120 requests. Build a global token-bucket throttle (default: 1 request per 4 seconds) and make it configurable.
5. **Model IDs live in config, never in code.** OpenRouter's free lineup rotates monthly. `openrouter/free` auto-router is the safe fallback when a configured model 404s.
6. **The whole system must run without an API key** via a `MockPlayer` (random legal moves + canned commentary). Build and test the entire UI in mock mode first; spend real API calls only at the end.
7. **Everything the frontend shows arrives over one WebSocket event stream.** The backend is the single narrator; the frontend is a dumb renderer.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        FRONTEND (React)                      │
│  ┌────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Chessboard │ │ Move list /  │ │ Agent panels:         │  │
│  │ (animated) │ │ PGN viewer   │ │ thinking status,      │  │
│  │            │ │              │ │ reasoning snippets,   │  │
│  │            │ │              │ │ retry/illegal badges  │  │
│  └────────────┘ └──────────────┘ └───────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Analyst panel: live commentary + final verdict card  │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────▲──────────────────────────────────┘
                           │ WebSocket (event stream) + REST
┌──────────────────────────┴──────────────────────────────────┐
│                     BACKEND (FastAPI)                        │
│                                                              │
│  GameOrchestrator (async loop)                               │
│   ├─ ChessEngine wrapper (python-chess: state, legality,     │
│   │   FEN/PGN, game-over detection)                          │
│   ├─ PlayerAgent (White)  ──┐                                │
│   ├─ PlayerAgent (Black)  ──┼── OpenRouterClient             │
│   ├─ AnalystAgent         ──┘   (throttle, retry, fallback)  │
│   ├─ EventBus → WebSocket broadcaster                        │
│   └─ GameStore (SQLite: games, moves, events, verdicts)      │
└──────────────────────────────────────────────────────────────┘
```

### Agent roles

| Agent | Job | When it's called |
|---|---|---|
| **White PlayerAgent** | Propose one legal UCI move + one short "trash-talk/reasoning" sentence | Every White turn |
| **Black PlayerAgent** | Same, for Black | Every Black turn |
| **AnalystAgent** | (a) Optional brief live commentary every N moves (default N=5, off by default to save quota); (b) Post-game: full review — key moments, blunders, best move, illegal-move penalties, and a final verdict with winner declaration | End of game (and optionally during) |

**Winner logic:** the chess engine decides checkmate/stalemate/draw objectively. The Analyst does NOT override the result — it *explains* it, grades both agents (accuracy, illegal-move count, decisiveness), and declares a "performance winner" which can differ from the game result in a draw (e.g., "Draw, but Qwen played better chess").

---

## 4. Repository Structure

```
llm-chess-arena/
├── PLAN.md                  # this file
├── PROGRESS.md              # session log — update every session
├── README.md
├── .env.example             # OPENROUTER_API_KEY=..., never commit .env
├── config.yaml              # models, throttle, retry budget, game settings
├── backend/
│   ├── pyproject.toml       # or requirements.txt
│   ├── app/
│   │   ├── main.py          # FastAPI app, routes, WS endpoint
│   │   ├── config.py        # pydantic-settings, loads .env + config.yaml
│   │   ├── engine.py        # ChessEngine: python-chess wrapper
│   │   ├── llm_client.py    # OpenRouterClient: httpx, throttle, retries
│   │   ├── agents/
│   │   │   ├── base.py      # Agent ABC
│   │   │   ├── player.py    # PlayerAgent (+ MockPlayer)
│   │   │   └── analyst.py   # AnalystAgent (+ MockAnalyst)
│   │   ├── orchestrator.py  # GameOrchestrator: the game loop
│   │   ├── events.py        # Event models + EventBus
│   │   ├── store.py         # SQLite via sqlite3/SQLModel
│   │   └── prompts.py       # all prompt templates in one place
│   └── tests/
│       ├── test_engine.py
│       ├── test_move_parsing.py
│       ├── test_orchestrator_mock.py
│       └── test_llm_client_throttle.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── api/ws.ts        # WebSocket client + reconnect
│       ├── state/gameStore.ts   # zustand store fed by WS events
│       ├── components/
│       │   ├── Board.tsx        # react-chessboard wrapper
│       │   ├── MoveList.tsx
│       │   ├── AgentPanel.tsx   # per-player status card
│       │   ├── AnalystPanel.tsx
│       │   ├── VerdictCard.tsx
│       │   ├── GameControls.tsx # new game, model pickers, speed
│       │   └── EvalBar.tsx      # optional (Phase 6)
│       └── types/events.ts      # mirrors backend event schema
└── scripts/
    └── verify_models.py     # hits OpenRouter /models, checks configured IDs are live+free
```

---

## 5. Configuration (`config.yaml`)

```yaml
openrouter:
  base_url: https://openrouter.ai/api/v1
  # VERIFY before first run: scripts/verify_models.py
  # Free lineup rotates. As of mid-2026 good free picks:
  #   qwen/qwen3-coder:free, openai/gpt-oss-120b:free,
  #   openai/gpt-oss-20b:free, meta-llama/llama-3.3-70b-instruct:free
  # Fallback that always works: openrouter/free (auto-router)
  white_model: openai/gpt-oss-120b:free
  black_model: meta-llama/llama-3.3-70b-instruct:free
  analyst_model: qwen/qwen3-coder:free
  fallback_model: openrouter/free
  request_timeout_s: 60
  max_tokens_move: 300
  max_tokens_analysis: 1500

throttle:
  min_seconds_between_requests: 4    # 15/min, safely under the 20/min cap
  max_requests_per_game: 250         # hard kill-switch

game:
  max_moves: 120                     # engine adjudicates draw beyond this
  illegal_move_retries: 3            # then random legal move fallback
  live_commentary_every_n_moves: 0   # 0 = off (saves quota); 5 = chatty mode
  move_delay_ui_ms: 800              # min pacing so the UI feels watchable

mode: live                           # live | mock
```

---

## 6. The Game Loop (orchestrator.py) — exact algorithm

```
1.  Create Game row in SQLite, emit GAME_STARTED event
2.  board = chess.Board()
3.  while not board.is_game_over() and move_count < max_moves
        and requests_used < max_requests_per_game:
      a. agent = white_agent if board.turn == WHITE else black_agent
      b. emit AGENT_THINKING {color, model}
      c. legal = [m.uci() for m in board.legal_moves]
      d. move, reasoning, attempts = agent.get_move(
             fen=board.fen(),
             legal_moves=legal,
             move_history_san=last_10_moves,   # keep prompt small
             retry_budget=3)
         - Each attempt: call LLM → parse UCI from response
           (strict regex ^[a-h][1-8][a-h][1-8][qrbn]?$ against JSON field,
            then membership check in legal list)
         - On failure: re-prompt with error appended
           ("'e2e5' is ILLEGAL. Choose strictly from: [...]")
         - After budget exhausted: move = random.choice(legal),
           mark forfeited=True
      e. san = board.san(move); board.push(move)
      f. Persist move row; emit MOVE_MADE {uci, san, fen, reasoning,
         attempts, forfeited, clock, captured_piece, is_check}
      g. asyncio.sleep(move_delay_ui_ms)
      h. (optional) every N moves: analyst live comment → emit COMMENTARY
4.  result = board.result()  # 1-0, 0-1, 1/2-1/2, or adjudicated
5.  emit GAME_OVER {result, termination}   # checkmate/stalemate/
                                           # insufficient material/
                                           # 50-move/repetition/max-moves
6.  Analyst post-game review:
      input: full PGN, per-move metadata (attempts, forfeits),
             final result, both model names
      output (strict JSON): {
        winner, result_explanation, key_moments[3-5],
        white_grade, black_grade, blunders[], best_move,
        illegal_move_summary, verdict_paragraph }
7.  emit VERDICT event; persist; done.
```

**WebSocket event types** (single schema, `frontend/src/types/events.ts` mirrors it):
`GAME_STARTED, AGENT_THINKING, MOVE_MADE, ILLEGAL_ATTEMPT, MOVE_FORFEITED, COMMENTARY, GAME_OVER, VERDICT, ERROR, RATE_LIMITED`

---

## 7. Prompt Design (prompts.py)

### Player move prompt (system)
```
You are {model_name} playing chess as {color} in an AI-vs-AI exhibition match.
You will receive the position and a list of legal moves. You MUST pick your
move ONLY from that list. Respond with STRICT JSON, nothing else:
{"move": "<uci from the list>", "reasoning": "<one punchy sentence, max 20 words>"}
```

### Player move prompt (user, each turn)
```
Position (FEN): {fen}
You are {color}. Move {move_number}.
Recent moves: {last_10_san}
Legal moves (UCI) — choose EXACTLY one from this list:
{legal_moves}
{retry_feedback_if_any}
```

Notes:
- Request `response_format: {"type": "json_object"}` when the model supports it; still parse defensively (strip markdown fences, regex-extract JSON, then regex-validate the UCI string).
- Keep prompts short. Full PGN history in every prompt wastes tokens and confuses weaker free models; FEN + last 10 SAN moves is enough.
- Temperature 0.7 for players (variety), 0.3 for analyst (consistency).

### Analyst post-game prompt
Provide: final PGN, result string, termination reason, per-move annotations (`attempts`, `forfeited`), model names. Demand strict JSON matching the verdict schema in §6 step 6. Instruct it: "The game result is final and decided by the rules engine — you explain it and grade performance; you do not change the winner."

---

## 8. OpenRouter Client (llm_client.py) — requirements

- `httpx.AsyncClient`, OpenAI-compatible `/chat/completions`
- Headers: `Authorization: Bearer`, plus `HTTP-Referer` and `X-Title` (OpenRouter attribution best practice)
- **Global async token-bucket**: one shared throttle across all three agents (rate limits are per account, not per model)
- Retry ladder on HTTP errors: 429 → exponential backoff (10s, 30s, 60s) + emit `RATE_LIMITED` event so the UI shows "waiting for rate limit…"; 404 model → swap to `fallback_model` and log; 5xx → 2 retries then raise
- Count every request into `requests_used`; expose it in a `GAME_STARTED`/`MOVE_MADE` payload so the UI can show a quota meter
- **Empty-content guard**: free models sometimes return empty/whitespace completions under load — treat as a failed attempt, consume one retry

---

## 9. Frontend Requirements

**Stack:** Vite + React + TS, `react-chessboard` (rendering/animation), `chess.js` (client-side move replay for the scrubber), zustand (state), Tailwind.

**Layout (desktop, 3 columns):**
- Left: White AgentPanel — model name, avatar, status (idle/thinking/moved), last reasoning line, illegal-attempt counter (badge turns red on forfeit)
- Center: animated board (smooth piece animation, last-move highlight, check highlight, capture flash), below it: playback controls (live / pause / scrub through past moves) and result banner
- Right: Black AgentPanel (mirror), MoveList (SAN, clickable to scrub), AnalystPanel (commentary feed → VerdictCard at the end with winner, grades, key moments)

**Top bar:** New Game button, model picker dropdowns (populated from `GET /api/models` which proxies OpenRouter's free-model list), speed slider, quota meter (`requests_used / daily budget`).

**Behavior:**
- WS client with auto-reconnect; on reconnect, `GET /api/games/{id}` to rehydrate full state (never rely on WS alone for state)
- Board is driven by FEN from `MOVE_MADE` events — the frontend never computes chess logic for the live game (chess.js is only for the history scrubber)
- Mobile: stack vertically, board first
- "AGENT_THINKING" shows an animated ellipsis on that player's panel; forfeited moves get a visible "⚠ illegal move penalty — random move played" toast

**Polish pass (worth it, this is the demo-wow layer):** piece move animation ≥200ms, sound effects (move/capture/check/game-over), subtle board themes, dark mode default.

---

## 10. API Surface (REST + WS)

```
POST /api/games                 # body: {white_model, black_model, analyst_model, settings} → {game_id}
GET  /api/games/{id}            # full state: moves, events, verdict (rehydration)
GET  /api/games                 # history list
POST /api/games/{id}/abort
GET  /api/models                # cached (1h) proxy of OpenRouter free models
WS   /ws/games/{id}             # event stream
```

---

## 11. Build Phases (Claude Code session plan)

Each phase ends with working, tested code and a PROGRESS.md update. Do not start a phase until the previous one's verification passes.

### Phase 0 — Scaffold (½ session)
- Repo structure above, backend venv + deps (`fastapi uvicorn python-chess httpx pydantic-settings pytest`), frontend Vite scaffold, `.env.example`, config.yaml, PROGRESS.md
- ✅ Verify: `uvicorn app.main:app` serves `/health`; `npm run dev` shows placeholder page

### Phase 1 — Chess engine core + mock game (1 session)
- `engine.py`: wrapper over python-chess (fen, legal_moves_uci, push_uci, san, game-over + termination reason, PGN export, max-move adjudication)
- `MockPlayer` (random legal move + canned reasoning), `orchestrator.py` full loop in mock mode, events printed to stdout, SQLite store
- ✅ Verify: pytest green; a mock game runs start→finish in <5s, produces valid PGN (paste it into lichess.org/paste to sanity-check), correct results for forced-checkmate test fixtures (Fool's mate, stalemate position, 50-move rule)

### Phase 2 — WebSocket streaming + minimal UI (1 session)
- EventBus → WS broadcaster; REST endpoints; React: WS client, zustand store, `react-chessboard` rendering FEN from events, basic move list
- ✅ Verify: click "New Game" (mock mode) in browser → watch a full animated game play out live; refresh mid-game → state rehydrates

### Phase 3 — OpenRouter integration (1 session)
- `llm_client.py` (throttle, retries, fallback, quota counter), `PlayerAgent` with the §7 prompts, illegal-move retry loop, `scripts/verify_models.py`
- Run `verify_models.py` FIRST — confirm configured `:free` IDs are live today; fix config if not
- ✅ Verify: unit tests for move parsing (markdown-fenced JSON, bare UCI, garbage, illegal-but-valid-format); then ONE real game with cheap settings — expect it to consume ~60–100 requests. If on the 50/day free tier, use `max_moves: 30` for this test or add $10 credits for the 1,000/day tier
- ✅ Log and inspect: how many illegal attempts per model? This is your tuning signal

### Phase 4 — Analyst agent + verdict (1 session)
- `analyst.py`: post-game review call, strict JSON verdict parsing (retry once on parse failure, then fall back to a template verdict built from engine facts), optional live commentary mode
- Frontend: AnalystPanel commentary feed + VerdictCard (winner banner, grades, key moments list)
- ✅ Verify: mock mode produces template verdict; one live run produces a real verdict that references actual game moments

### Phase 5 — Full UI polish (1–2 sessions)
- Agent panels with thinking states + illegal badges, playback scrubber (chess.js replay), model pickers via `/api/models`, quota meter, sounds, animations, dark mode, mobile layout, game history page
- ✅ Verify: full demo flow feels like watching a real match; a non-technical person can understand what's happening

### Phase 6 — Stretch (optional)
- Stockfish eval bar (local `stockfish` binary + `python-chess` engine API) — gives the Analyst objective centipawn data and makes blunder detection real instead of vibes
- Tournament mode: round-robin across N free models, leaderboard table
- Export: PGN download, shareable game replay URL
- Personality prompts per model ("aggressive gambiteer" vs "positional grinder")

---

## 12. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Free models make constant illegal moves | **Certain** | Legal-move-list prompting + 3-retry + random fallback (§6d). It's a feature: Analyst reports it |
| Daily quota exhausted mid-game | High on 50/day tier | Quota counter + kill-switch + mock mode for all dev; recommend the $10-credit 1,000/day tier before live testing |
| Configured `:free` model disappears | High (lineup rotates) | `verify_models.py` preflight + `openrouter/free` fallback in client |
| 429s stall the game | Medium | Backoff + `RATE_LIMITED` UI event; 4s min spacing keeps you under 20/min |
| Model returns malformed JSON | High | Defensive parsing chain (fence-strip → JSON extract → regex UCI → legality check); counts as retry |
| Games drag 200+ moves (weak models shuffle pieces) | Medium | `max_moves: 120` adjudication (engine material count decides, else draw) |
| WS disconnect loses UI state | Medium | REST rehydration on reconnect (§9) |
| API key leaks | — | `.env` gitignored from commit #1; backend-only (key never touches frontend) |

---

## 13. Definition of Done

- [ ] `mode: mock` — full game + verdict, zero API calls, all tests green
- [ ] `mode: live` — two free OpenRouter models complete a real game end-to-end with ≤ configured request budget
- [ ] Every illegal attempt is visible in UI and stored in DB
- [ ] Verdict card correctly reflects engine result + analyst grading
- [ ] Refresh-proof: reload mid-game restores exact state
- [ ] README covers: setup, getting an OpenRouter key, running mock vs live, quota guidance
- [ ] A stranger can `docker compose up` (or two commands) and watch a match

---

## 14. Claude Code Working Rules (for the agent building this)

1. Read PLAN.md + PROGRESS.md at session start. Work only the current phase.
2. Mock mode is the default for every dev loop. Never run live-mode tests casually — each costs real quota.
3. Write the test before or with the code for: move parsing, engine termination detection, throttle timing.
4. After each phase: run full test suite, run the phase's ✅ Verify steps manually, append results + next steps to PROGRESS.md.
5. Never commit `.env`. Never log the API key. Never put the key in frontend code.
6. If OpenRouter behavior differs from this plan (endpoints, limits, model IDs), trust reality, fix the config/docs, note it in PROGRESS.md.

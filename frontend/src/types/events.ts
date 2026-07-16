/**
 * Mirrors the backend event schema (backend/app/events.py, PLAN.md §6).
 * Keep the two in sync: the backend is the only narrator, so if a field is
 * missing here the UI simply cannot know about it.
 */

export type Color = 'white' | 'black'

export type EventType =
  | 'GAME_STARTED'
  | 'AGENT_THINKING'
  | 'MOVE_MADE'
  | 'ILLEGAL_ATTEMPT'
  | 'MOVE_FORFEITED'
  | 'COMMENTARY'
  | 'GAME_OVER'
  | 'VERDICT'
  | 'ERROR'
  | 'RATE_LIMITED'

export type GameStartedData = {
  game_id: string
  white_model: string
  black_model: string
  fen: string
  max_moves: number
  requests_used: number
}

export type AgentThinkingData = {
  color: Color
  model: string
  move_number: number
}

export type MoveMadeData = {
  ply: number
  move_number: number
  color: Color
  uci: string
  san: string
  fen: string
  reasoning: string
  attempts: number
  forfeited: boolean
  is_check: boolean
  is_checkmate: boolean
  is_capture: boolean
  captured_piece: string | null
  requests_used: number
}

export type IllegalAttemptData = {
  color: Color
  model: string
  uci: string
  move_number: number
}

export type MoveForfeitedData = {
  color: Color
  model: string
  attempted: string
  replacement: string
  attempts: number
  illegal_attempts: string[]
  move_number: number
}

export type GameOverData = {
  result: string
  termination: string
  winner: Color | null
  fen: string
  ply_count: number
  pgn: string
  requests_used: number
}

export type ErrorData = { message: string; requests_used?: number }

export type CommentaryData = {
  text: string
  model: string
  ply: number
  move_number: number
}

export type RateLimitedData = {
  model: string
  retry_in_s: number
  attempt: number
  requests_used: number
}

/**
 * The §6 verdict. `winner` is the analyst's *performance* winner and may differ
 * from `engine_winner` in a draw. The `engine_*` fields are stamped by the
 * backend from the rules engine — the analyst cannot change them (§3).
 */
export type VerdictData = {
  winner: Color | 'draw'
  result_explanation: string
  key_moments: string[]
  white_grade: string
  black_grade: string
  blunders: string[]
  best_move: string
  illegal_move_summary: string
  verdict_paragraph: string
  engine_result: string
  engine_termination: string | null
  engine_winner: Color | null
  analyst_model: string
  source: 'analyst' | 'template'
}

export type GameEvent = {
  type: EventType
  game_id: string
  seq: number
  ts: number
  data: Record<string, unknown>
}

/** A move as stored in SQLite and returned by GET /api/games/{id}. */
export type StoredMove = {
  ply: number
  move_number: number
  color: Color
  uci: string
  san: string
  fen_after: string
  reasoning: string | null
  attempts: number
  forfeited: number // SQLite has no bool; 0 | 1
  is_check: number
  is_capture: number
  captured_piece: string | null
}

export type GameStatus = 'pending' | 'in_progress' | 'finished' | 'aborted' | 'error'

/** GET /api/games/{id} — the rehydration payload (§9). */
export type FullGame = {
  id: string
  status: GameStatus
  mode: string
  white_model: string
  black_model: string
  analyst_model: string | null
  result: string | null
  termination: string | null
  final_fen: string | null
  pgn: string | null
  requests_used: number
  created_at: number
  moves: StoredMove[]
  events: GameEvent[]
  verdict: VerdictData | null
  is_running: boolean
}

export type CreateGameResponse = {
  game_id: string
  mode: string
  white_model: string
  black_model: string
}

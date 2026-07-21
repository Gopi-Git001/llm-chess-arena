/**
 * The store is fed by WebSocket events and by REST rehydration (PLAN.md §9).
 *
 * It computes no chess logic: every position it renders is a FEN the backend
 * sent (§2.7). chess.js is only for the history scrubber in Phase 5.
 */

import { create } from 'zustand'
import type {
  AgentThinkingData,
  AgentThinkingTokenData,
  Color,
  CommentaryData,
  ErrorData,
  FullGame,
  GameEvent,
  GameOverData,
  GameStartedData,
  GameStatus,
  IllegalAttemptData,
  MoveCommentaryData,
  MoveForfeitedData,
  MoveMadeData,
  RateLimitedData,
  StoredMove,
  VerdictData,
} from '../types/events'

export const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

export type { Color } from '../types/events'

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'closed' | 'reconnecting'

/** One row in the move list. Mirrors what both events and REST provide. */
export type MoveRow = {
  ply: number
  moveNumber: number
  color: Color
  uci: string
  san: string
  fen: string
  reasoning: string
  // Full streamed reasoning in "Too Slow" mode; '' otherwise. Shown when a move
  // is clicked in the move list (Feature 1).
  thinking: string
  attempts: number
  forfeited: boolean
  isCheck: boolean
  isCheckmate: boolean
  isCapture: boolean
  capturedPiece: string | null // lowercase letter, e.g. "q"
}

/** One spoken commentary caption (Feature 2). */
export type Caption = { ply: number; text: string; source: 'model' | 'template' }

export type Toast = { id: number; message: string; tone: 'warn' | 'error' }

/** One entry in the analyst's live commentary feed. */
export type Comment = { ply: number; moveNumber: number; text: string; model: string }

/** Why the game is currently paused, if it is. */
export type RateLimit = { model: string; retryInS: number; attempt: number }

type GameState = {
  gameId: string | null
  status: GameStatus | null
  connection: ConnectionState
  fen: string
  moves: MoveRow[]
  lastSeq: number
  whiteModel: string
  blackModel: string
  thinking: Color | null
  // Live streamed reasoning per colour during a "Too Slow" window (Feature 1).
  thinkingText: Record<Color, string>
  result: string | null
  termination: string | null
  winner: Color | null
  pgn: string | null
  requestsUsed: number
  illegalCounts: Record<Color, number>
  forfeitCounts: Record<Color, number>
  comments: Comment[]
  // Per-move spoken commentary, keyed by ply, plus an ordered caption feed (F2).
  commentaries: Record<number, string>
  captions: Caption[]
  verdict: VerdictData | null
  rateLimit: RateLimit | null
  toasts: Toast[]
  error: string | null

  applyEvent: (event: GameEvent) => void
  hydrate: (game: FullGame) => void
  setConnection: (state: ConnectionState) => void
  startNewGame: (gameId: string) => void
  pushToast: (message: string, tone?: Toast['tone']) => void
  dismissToast: (id: number) => void
  reset: () => void
}

const initialState = {
  gameId: null,
  status: null,
  connection: 'idle' as ConnectionState,
  fen: START_FEN,
  moves: [] as MoveRow[],
  lastSeq: 0,
  whiteModel: '',
  blackModel: '',
  thinking: null,
  thinkingText: { white: '', black: '' } as Record<Color, string>,
  result: null,
  termination: null,
  winner: null,
  pgn: null,
  requestsUsed: 0,
  illegalCounts: { white: 0, black: 0 },
  forfeitCounts: { white: 0, black: 0 },
  comments: [] as Comment[],
  commentaries: {} as Record<number, string>,
  captions: [] as Caption[],
  verdict: null as VerdictData | null,
  rateLimit: null as RateLimit | null,
  toasts: [] as Toast[],
  error: null,
}

let toastId = 0

function rowFromEvent(data: MoveMadeData): MoveRow {
  return {
    ply: data.ply,
    moveNumber: data.move_number,
    color: data.color,
    uci: data.uci,
    san: data.san,
    fen: data.fen,
    reasoning: data.reasoning,
    thinking: data.thinking ?? '',
    attempts: data.attempts,
    forfeited: data.forfeited,
    isCheck: data.is_check,
    isCheckmate: data.is_checkmate,
    isCapture: data.is_capture,
    capturedPiece: data.captured_piece,
  }
}

function rowFromStored(move: StoredMove): MoveRow {
  return {
    ply: move.ply,
    moveNumber: move.move_number,
    color: move.color,
    uci: move.uci,
    san: move.san,
    fen: move.fen_after,
    reasoning: move.reasoning ?? '',
    thinking: move.thinking ?? '',
    attempts: move.attempts,
    forfeited: Boolean(move.forfeited),
    isCheck: Boolean(move.is_check),
    // Stored rows don't carry a mate flag; the SAN's trailing # is authoritative.
    isCheckmate: move.san.includes('#'),
    isCapture: Boolean(move.is_capture),
    capturedPiece: move.captured_piece,
  }
}

export const useGameStore = create<GameState>((set, get) => ({
  ...initialState,

  applyEvent: (event) => {
    // Ignore anything we've already applied. Replay-after-reconnect sends the
    // backlog again, and events must stay idempotent.
    if (event.seq <= get().lastSeq) return
    set({ lastSeq: event.seq })

    switch (event.type) {
      case 'GAME_STARTED': {
        const data = event.data as unknown as GameStartedData
        set({
          gameId: data.game_id,
          status: 'in_progress',
          fen: data.fen,
          whiteModel: data.white_model,
          blackModel: data.black_model,
          requestsUsed: data.requests_used,
        })
        break
      }

      case 'AGENT_THINKING': {
        const color = (event.data as unknown as AgentThinkingData).color
        // A fresh turn starts a fresh live-thinking buffer for that side.
        set((state) => ({
          thinking: color,
          thinkingText: { ...state.thinkingText, [color]: '' },
        }))
        break
      }

      case 'AGENT_THINKING_TOKEN': {
        const data = event.data as unknown as AgentThinkingTokenData
        set((state) => ({
          thinkingText: {
            ...state.thinkingText,
            [data.color]: state.thinkingText[data.color] + data.text_chunk,
          },
        }))
        break
      }

      case 'MOVE_MADE': {
        const data = event.data as unknown as MoveMadeData
        const row = rowFromEvent(data)
        set((state) => ({
          // Guard against a duplicate ply from an overlapping replay.
          moves: state.moves.some((m) => m.ply === row.ply) ? state.moves : [...state.moves, row],
          fen: data.fen,
          thinking: null,
          // The live-thinking panel clears when the move commits; the full text
          // is preserved on the move row for later viewing (Feature 1).
          thinkingText: { ...state.thinkingText, [data.color]: '' },
          requestsUsed: data.requests_used,
          // A move landing means we're no longer waiting on a rate limit.
          rateLimit: null,
        }))
        break
      }

      case 'MOVE_COMMENTARY': {
        const data = event.data as unknown as MoveCommentaryData
        set((state) => ({
          commentaries: { ...state.commentaries, [data.ply]: data.text },
          captions: state.captions.some((c) => c.ply === data.ply)
            ? state.captions
            : [...state.captions, { ply: data.ply, text: data.text, source: data.source }],
        }))
        break
      }

      case 'ILLEGAL_ATTEMPT': {
        const data = event.data as unknown as IllegalAttemptData
        set((state) => ({
          illegalCounts: {
            ...state.illegalCounts,
            [data.color]: state.illegalCounts[data.color] + 1,
          },
        }))
        break
      }

      case 'MOVE_FORFEITED': {
        const data = event.data as unknown as MoveForfeitedData
        set((state) => ({
          forfeitCounts: {
            ...state.forfeitCounts,
            [data.color]: state.forfeitCounts[data.color] + 1,
          },
          toasts: [
            ...state.toasts,
            {
              id: ++toastId,
              tone: 'warn',
              message: `⚠ ${data.color} illegal move penalty — ${data.attempted} rejected, random move played`,
            },
          ],
        }))
        break
      }

      case 'COMMENTARY': {
        const data = event.data as unknown as CommentaryData
        set((state) => ({
          comments: [
            ...state.comments,
            {
              ply: data.ply,
              moveNumber: data.move_number,
              text: data.text,
              model: data.model,
            },
          ],
        }))
        break
      }

      case 'RATE_LIMITED': {
        // Without this the UI would just freeze silently mid-game (§8).
        const data = event.data as unknown as RateLimitedData
        set({
          rateLimit: { model: data.model, retryInS: data.retry_in_s, attempt: data.attempt },
          requestsUsed: data.requests_used,
        })
        break
      }

      case 'VERDICT': {
        set({ verdict: event.data as unknown as VerdictData })
        break
      }

      case 'GAME_OVER': {
        const data = event.data as unknown as GameOverData
        // Respect how the game actually ended so an aborted game doesn't render
        // as a normal finish (and vice versa).
        const finalStatus: GameStatus =
          data.status === 'aborted' ? 'aborted' : data.status === 'error' ? 'error' : 'finished'
        set({
          status: finalStatus,
          result: data.result,
          termination: data.termination,
          winner: data.winner,
          fen: data.fen,
          pgn: data.pgn,
          thinking: null,
          rateLimit: null,
          requestsUsed: data.requests_used,
        })
        break
      }

      case 'ERROR': {
        const data = event.data as unknown as ErrorData
        set((state) => ({
          error: data.message,
          thinking: null,
          toasts: [...state.toasts, { id: ++toastId, tone: 'error', message: data.message }],
        }))
        break
      }
    }
  },

  /** Rebuild state from REST. Authoritative — never trust the WS alone (§9). */
  hydrate: (game) => {
    const moves = game.moves.map(rowFromStored)
    const counts = { white: 0, black: 0 }
    const forfeits = { white: 0, black: 0 }
    const comments: Comment[] = []
    const commentaries: Record<number, string> = {}
    const captions: Caption[] = []

    for (const event of game.events) {
      if (event.type === 'ILLEGAL_ATTEMPT') {
        const color = (event.data as unknown as IllegalAttemptData).color
        counts[color] += 1
      }
      if (event.type === 'MOVE_FORFEITED') {
        const color = (event.data as unknown as MoveForfeitedData).color
        forfeits[color] += 1
      }
      if (event.type === 'COMMENTARY') {
        const data = event.data as unknown as CommentaryData
        comments.push({
          ply: data.ply,
          moveNumber: data.move_number,
          text: data.text,
          model: data.model,
        })
      }
      if (event.type === 'MOVE_COMMENTARY') {
        const data = event.data as unknown as MoveCommentaryData
        commentaries[data.ply] = data.text
        captions.push({ ply: data.ply, text: data.text, source: data.source })
      }
    }

    set({
      gameId: game.id,
      status: game.status,
      // A finished game keeps its final position; a live one resumes from the
      // last move we know about.
      fen: game.final_fen ?? moves.at(-1)?.fen ?? START_FEN,
      moves,
      lastSeq: game.events.at(-1)?.seq ?? 0,
      whiteModel: game.white_model,
      blackModel: game.black_model,
      result: game.result,
      termination: game.termination,
      winner: game.result === '1-0' ? 'white' : game.result === '0-1' ? 'black' : null,
      pgn: game.pgn,
      requestsUsed: game.requests_used,
      illegalCounts: counts,
      forfeitCounts: forfeits,
      comments,
      commentaries,
      captions,
      // REST is authoritative: the verdict survives a refresh (§9).
      verdict: game.verdict,
      rateLimit: null,
      thinking: null,
      thinkingText: { white: '', black: '' },
      toasts: [],
    })
  },

  setConnection: (connection) => set({ connection }),

  startNewGame: (gameId) => set({ ...initialState, gameId, status: 'pending' }),

  pushToast: (message, tone = 'warn') =>
    set((state) => ({ toasts: [...state.toasts, { id: ++toastId, message, tone }] })),

  dismissToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  reset: () => set({ ...initialState }),
}))

/** Squares of the most recent move, for the board highlight. */
export function lastMoveSquares(moves: MoveRow[]): { from: string; to: string } | null {
  const last = moves.at(-1)
  if (!last) return null
  return { from: last.uci.slice(0, 2), to: last.uci.slice(2, 4) }
}

/**
 * REST client + WebSocket stream with auto-reconnect (PLAN.md §9).
 *
 * Rule: the WS is a *delivery* mechanism, never the source of truth. Every
 * (re)connection rehydrates from REST first, then follows the stream from the
 * sequence number that rehydration established.
 */

import type { CreateGameResponse, FullGame, GameEvent } from '../types/events'

export type NewGameOptions = {
  whiteModel?: string
  blackModel?: string
  analystModel?: string
  maxMoves?: number
  moveDelayMs?: number
  seed?: number
  commentaryEveryNMoves?: number
  // "Too Slow" live-thinking window per move (Feature 1). 0 = off.
  thinkingWindowMs?: number
  // Voice/move commentary synced to the board (Feature 2).
  moveCommentaryEnabled?: boolean
  illegalRate?: number
  forfeitRate?: number
}

async function asJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`${response.status} ${response.statusText}: ${body.slice(0, 200)}`)
  }
  return response.json() as Promise<T>
}

export async function createGame(options: NewGameOptions = {}): Promise<CreateGameResponse> {
  return asJson<CreateGameResponse>(
    await fetch('/api/games', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        white_model: options.whiteModel,
        black_model: options.blackModel,
        analyst_model: options.analystModel,
        settings: {
          max_moves: options.maxMoves,
          move_delay_ms: options.moveDelayMs,
          seed: options.seed,
          commentary_every_n_moves: options.commentaryEveryNMoves,
          thinking_window_ms: options.thinkingWindowMs,
          move_commentary_enabled: options.moveCommentaryEnabled,
          illegal_rate: options.illegalRate ?? 0,
          forfeit_rate: options.forfeitRate ?? 0,
        },
      }),
    }),
  )
}

export async function fetchGame(gameId: string): Promise<FullGame> {
  return asJson<FullGame>(await fetch(`/api/games/${gameId}`))
}

export type FreeModel = { id: string; name: string; context_length: number | null }

export async function fetchModels(): Promise<{ models: FreeModel[]; source: string }> {
  return asJson<{ models: FreeModel[]; source: string }>(await fetch('/api/models'))
}

export type GameSummary = {
  id: string
  created_at: number
  white_model: string
  black_model: string
  status: string
  result: string | null
  termination: string | null
  requests_used: number
}

export async function fetchGames(limit = 30): Promise<GameSummary[]> {
  const body = await asJson<{ games: GameSummary[] }>(await fetch(`/api/games?limit=${limit}`))
  return body.games
}

export async function abortGame(gameId: string): Promise<void> {
  await fetch(`/api/games/${gameId}/abort`, { method: 'POST' })
}

export type StreamHandlers = {
  onEvent: (event: GameEvent) => void
  onHydrate: (game: FullGame) => void
  onStatus: (status: 'connecting' | 'open' | 'closed' | 'reconnecting') => void
  /** Where to resume from. Read at connect time, not captured once. */
  getSince: () => number
}

const MAX_BACKOFF_MS = 10_000

/**
 * Follow a game's events until `close()` is called.
 * Returns a disposer; safe to call more than once.
 */
export function streamGame(gameId: string, handlers: StreamHandlers): () => void {
  let socket: WebSocket | null = null
  let retryTimer: number | undefined
  let attempt = 0
  let disposed = false

  const connect = async () => {
    if (disposed) return
    handlers.onStatus(attempt === 0 ? 'connecting' : 'reconnecting')

    // REST first: the stream only fills in what happens next.
    try {
      handlers.onHydrate(await fetchGame(gameId))
    } catch (error) {
      if (disposed) return
      console.warn('[ws] rehydrate failed, retrying', error)
      scheduleRetry()
      return
    }
    if (disposed) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/ws/games/${gameId}?since=${handlers.getSince()}`
    socket = new WebSocket(url)

    socket.onopen = () => {
      attempt = 0
      handlers.onStatus('open')
    }

    socket.onmessage = (message) => {
      try {
        handlers.onEvent(JSON.parse(message.data) as GameEvent)
      } catch (error) {
        console.error('[ws] bad event payload', error)
      }
    }

    socket.onclose = (event) => {
      socket = null
      if (disposed) return
      handlers.onStatus('closed')
      // 1000 = the backend closed us deliberately: the game is over, so there
      // is nothing left to stream and reconnecting would loop forever.
      if (event.code === 1000 || event.code === 4404) return
      scheduleRetry()
    }

    socket.onerror = () => socket?.close()
  }

  const scheduleRetry = () => {
    if (disposed) return
    const delay = Math.min(500 * 2 ** attempt, MAX_BACKOFF_MS)
    attempt += 1
    handlers.onStatus('reconnecting')
    retryTimer = window.setTimeout(connect, delay)
  }

  void connect()

  return () => {
    disposed = true
    window.clearTimeout(retryTimer)
    if (socket) {
      socket.onclose = null
      socket.close()
      socket = null
    }
  }
}

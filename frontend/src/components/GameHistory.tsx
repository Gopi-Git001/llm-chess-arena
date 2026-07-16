/**
 * Past games (PLAN.md §9 game history). A slide-over panel listing finished and
 * in-progress games; clicking one loads it (and its stored verdict) via the
 * normal rehydration path.
 */

import { useEffect, useState } from 'react'
import { fetchGames, type GameSummary } from '../api/ws'

type GameHistoryProps = {
  open: boolean
  onClose: () => void
  onOpenGame: (gameId: string) => void
  currentGameId: string | null
}

const RESULT_LABEL: Record<string, string> = {
  '1-0': 'White',
  '0-1': 'Black',
  '1/2-1/2': 'Draw',
}

function relativeTime(epochSeconds: number): string {
  const diff = Date.now() / 1000 - epochSeconds
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function shortModel(id: string): string {
  // "meta-llama/llama-3.3-70b-instruct:free" → "llama-3.3-70b-instruct"
  return id.split('/').pop()?.replace(':free', '') ?? id
}

export default function GameHistory({ open, onClose, onOpenGame, currentGameId }: GameHistoryProps) {
  const [games, setGames] = useState<GameSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setGames(null)
    setError(null)
    fetchGames(40)
      .then(setGames)
      .catch((e: Error) => setError(e.message))
  }, [open])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-20 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-md flex-col border-l border-zinc-800 bg-zinc-950 shadow-xl">
        <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <h2 className="text-sm font-semibold text-zinc-100">Game history</h2>
          <button onClick={onClose} className="rounded p-1 text-zinc-400 hover:bg-zinc-800" aria-label="Close">
            ✕
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-2">
          {error && <p className="p-4 text-sm text-red-400">Could not load history: {error}</p>}
          {!error && games === null && <p className="p-4 text-sm text-zinc-500">Loading…</p>}
          {games?.length === 0 && <p className="p-4 text-sm text-zinc-500">No games yet.</p>}

          <ul className="space-y-1">
            {games?.map((game) => (
              <li key={game.id}>
                <button
                  onClick={() => {
                    onOpenGame(game.id)
                    onClose()
                  }}
                  className={`w-full rounded-md border px-3 py-2 text-left transition-colors hover:bg-zinc-900 ${
                    game.id === currentGameId ? 'border-emerald-800 bg-emerald-950/30' : 'border-zinc-800'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-xs text-zinc-300">
                      {shortModel(game.white_model)} vs {shortModel(game.black_model)}
                    </span>
                    <span className="shrink-0 text-[10px] text-zinc-600">{relativeTime(game.created_at)}</span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[11px]">
                    <StatusPill status={game.status} result={game.result} />
                    {game.termination && <span className="text-zinc-600">{game.termination}</span>}
                    {game.requests_used > 0 && (
                      <span className="ml-auto text-zinc-600">{game.requests_used} req</span>
                    )}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  )
}

function StatusPill({ status, result }: { status: string; result: string | null }) {
  if (status === 'in_progress') {
    return <span className="rounded bg-sky-950 px-1.5 py-0.5 text-sky-300">playing</span>
  }
  if (status === 'finished' && result) {
    const label = RESULT_LABEL[result] ?? result
    return (
      <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-zinc-300">
        {result} · {label === 'Draw' ? 'draw' : `${label} wins`}
      </span>
    )
  }
  return <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-zinc-500">{status}</span>
}

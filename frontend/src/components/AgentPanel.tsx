/**
 * Per-player status card (PLAN.md §9): avatar, model name, status
 * (idle/thinking/moved), last reasoning line, and the illegal/forfeit badges
 * that turn red on a forfeit.
 */

import type { Color, MoveRow } from '../state/gameStore'

type AgentPanelProps = {
  color: Color
  model: string
  thinking: boolean
  illegal: number
  forfeits: number
  lastMove: MoveRow | null
  active: boolean
  toMove: boolean
}

function statusLabel(thinking: boolean, toMove: boolean, active: boolean): string {
  if (!active) return 'idle'
  if (thinking) return 'thinking…'
  if (toMove) return 'to move'
  return 'waiting'
}

export default function AgentPanel({
  color,
  model,
  thinking,
  illegal,
  forfeits,
  lastMove,
  active,
  toMove,
}: AgentPanelProps) {
  const status = statusLabel(thinking, toMove, active)

  return (
    <div
      className={`rounded-lg border bg-zinc-900/70 p-3 transition-colors ${
        thinking ? 'border-sky-700 ring-1 ring-sky-800/50' : 'border-zinc-800'
      }`}
    >
      <div className="flex items-center gap-3">
        {/* Avatar: a chess king glyph on the player's colour. */}
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-lg ${
            color === 'white'
              ? 'border-zinc-300 bg-zinc-100 text-zinc-900'
              : 'border-zinc-600 bg-zinc-800 text-zinc-100'
          }`}
          aria-hidden
        >
          {color === 'white' ? '♔' : '♚'}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate font-mono text-xs text-zinc-200" title={model}>
              {model || `${color} agent`}
            </span>
            <span
              className={`shrink-0 text-[11px] ${
                thinking ? 'text-sky-400' : toMove && active ? 'text-zinc-300' : 'text-zinc-600'
              }`}
            >
              {status}
              {thinking && <ThinkingDots />}
            </span>
          </div>

          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] text-zinc-600 uppercase">{color}</span>
            {illegal > 0 && (
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  forfeits > 0 ? 'bg-red-950 text-red-400' : 'bg-amber-950 text-amber-400'
                }`}
                title="Illegal moves proposed"
              >
                {illegal} illegal
              </span>
            )}
            {forfeits > 0 && (
              <span
                className="rounded bg-red-950 px-1.5 py-0.5 text-[10px] text-red-400"
                title="Turns forfeited to a random legal move"
              >
                {forfeits} forfeit{forfeits > 1 ? 's' : ''}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Last reasoning line — the "trash-talk" the model gave with its move. */}
      <p className="mt-2 min-h-[2.5rem] text-xs leading-snug text-zinc-400 italic">
        {lastMove?.reasoning
          ? `“${lastMove.reasoning}”`
          : active
            ? 'No reasoning yet.'
            : ''}
      </p>
    </div>
  )
}

function ThinkingDots() {
  return (
    <span className="ml-0.5 inline-flex">
      <span className="animate-pulse">.</span>
      <span className="animate-pulse [animation-delay:150ms]">.</span>
      <span className="animate-pulse [animation-delay:300ms]">.</span>
    </span>
  )
}

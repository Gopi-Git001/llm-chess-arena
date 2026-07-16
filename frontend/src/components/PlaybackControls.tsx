/**
 * Playback scrubber (PLAN.md §9). Live games follow the latest move; scrubbing
 * freezes the board at a past ply until "Live" is pressed again.
 *
 * The board replays from the FEN stored on each move — the backend already sent
 * it, so the frontend never recomputes chess for the scrubber. (chess.js is
 * available if a future feature needs move generation, but position replay
 * doesn't.)
 */

type PlaybackControlsProps = {
  totalPlies: number
  /** null = following live; a number = frozen at that ply index (1-based). */
  viewPly: number | null
  onSelect: (ply: number | null) => void
  gameOver: boolean
}

export default function PlaybackControls({
  totalPlies,
  viewPly,
  onSelect,
  gameOver,
}: PlaybackControlsProps) {
  if (totalPlies === 0) return null

  const current = viewPly ?? totalPlies
  const isLive = viewPly === null
  const atStart = current <= 0
  const atEnd = current >= totalPlies

  const go = (ply: number) => {
    const clamped = Math.max(0, Math.min(totalPlies, ply))
    // Landing on the last ply of a live game means "resume following".
    onSelect(clamped >= totalPlies && !gameOver ? null : clamped)
  }

  return (
    <div className="mt-3 flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2">
      <IconButton label="Start" disabled={atStart} onClick={() => go(0)}>
        ⏮
      </IconButton>
      <IconButton label="Previous" disabled={atStart} onClick={() => go(current - 1)}>
        ◀
      </IconButton>

      <input
        type="range"
        min={0}
        max={totalPlies}
        value={current}
        onChange={(e) => go(Number(e.target.value))}
        className="h-1 flex-1 cursor-pointer accent-emerald-500"
        aria-label="Scrub through moves"
      />

      <IconButton label="Next" disabled={atEnd} onClick={() => go(current + 1)}>
        ▶
      </IconButton>
      <IconButton label="End" disabled={atEnd} onClick={() => go(totalPlies)}>
        ⏭
      </IconButton>

      <span className="ml-1 w-24 shrink-0 text-right text-[11px] text-zinc-500">
        {isLive && !gameOver ? (
          <span className="font-medium text-emerald-400">● live</span>
        ) : (
          <>
            ply {current}/{totalPlies}
            {!gameOver && (
              <button
                onClick={() => onSelect(null)}
                className="ml-2 text-emerald-500 hover:underline"
              >
                live
              </button>
            )}
          </>
        )}
      </span>
    </div>
  )
}

function IconButton({
  children,
  label,
  disabled,
  onClick,
}: {
  children: React.ReactNode
  label: string
  disabled: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="rounded px-1.5 py-0.5 text-xs text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-30"
    >
      {children}
    </button>
  )
}

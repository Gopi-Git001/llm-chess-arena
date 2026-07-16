/**
 * SAN move list, paired by move number. Forfeited moves are badged — an illegal
 * move must be visible in the UI, not just counted (PLAN.md §13).
 * Clickable scrubbing lands in Phase 5.
 */

import { useEffect, useRef } from 'react'
import type { MoveRow } from '../state/gameStore'

type MoveListProps = {
  moves: MoveRow[]
  /** null = following live; otherwise the ply currently shown on the board. */
  viewPly: number | null
  onSelectPly: (ply: number | null) => void
}

type Pair = { moveNumber: number; white?: MoveRow; black?: MoveRow }

function pairMoves(moves: MoveRow[]): Pair[] {
  const pairs: Pair[] = []
  for (const move of moves) {
    let pair = pairs.at(-1)
    if (!pair || pair.moveNumber !== move.moveNumber || move.color === 'white') {
      pair = { moveNumber: move.moveNumber }
      pairs.push(pair)
    }
    pair[move.color] = move
  }
  return pairs
}

function Cell({
  move,
  selected,
  onClick,
}: {
  move?: MoveRow
  selected: boolean
  onClick: () => void
}) {
  if (!move) return <span className="px-1 text-zinc-600">…</span>
  return (
    <button
      onClick={onClick}
      className={`rounded px-1 text-left hover:bg-zinc-800 ${
        selected ? 'bg-emerald-900/60 text-emerald-200' : move.forfeited ? 'text-amber-400' : 'text-zinc-200'
      }`}
      title={move.reasoning || 'Click to view this position'}
    >
      {move.san}
      {move.forfeited && <span title="Illegal move penalty — random move played"> ⚠</span>}
      {move.attempts > 1 && !move.forfeited && (
        <span className="text-amber-500" title={`${move.attempts} attempts`}>
          {' '}
          ×{move.attempts}
        </span>
      )}
    </button>
  )
}

export default function MoveList({ moves, viewPly, onSelectPly }: MoveListProps) {
  const endRef = useRef<HTMLDivElement>(null)
  const pairs = pairMoves(moves)

  // Follow the game as it plays — but not while the user is scrubbing history.
  useEffect(() => {
    if (viewPly === null) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [moves.length, viewPly])

  return (
    <div className="flex h-full flex-col">
      <h2 className="mb-2 text-xs font-medium tracking-wide text-zinc-400 uppercase">
        Moves {moves.length > 0 && <span className="text-zinc-600">({moves.length} plies)</span>}
      </h2>

      <div className="flex-1 overflow-y-auto rounded-md border border-zinc-800 bg-zinc-900/60 p-2">
        {pairs.length === 0 ? (
          <p className="p-2 text-sm text-zinc-600">No moves yet.</p>
        ) : (
          <ol className="font-mono text-sm">
            {pairs.map((pair) => (
              <li
                key={pair.moveNumber}
                className="grid grid-cols-[2.5rem_1fr_1fr] gap-1 rounded px-1 py-0.5 odd:bg-zinc-900"
              >
                <span className="py-0.5 text-zinc-500">{pair.moveNumber}.</span>
                <Cell
                  move={pair.white}
                  selected={viewPly === pair.white?.ply}
                  onClick={() => pair.white && onSelectPly(pair.white.ply)}
                />
                <Cell
                  move={pair.black}
                  selected={viewPly === pair.black?.ply}
                  onClick={() => pair.black && onSelectPly(pair.black.ply)}
                />
              </li>
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </div>
    </div>
  )
}

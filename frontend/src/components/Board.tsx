/**
 * react-chessboard wrapper. v5 takes a single `options` object (v4's flat props
 * are gone) — verified against the installed dist/Chessboard.d.ts.
 *
 * The board is a pure renderer: it draws the FEN the backend sent and never
 * decides anything about chess itself (PLAN.md §9).
 */

import { Chessboard } from 'react-chessboard'
import type { CSSProperties } from 'react'

type BoardProps = {
  fen: string
  lastMove: { from: string; to: string } | null
  animationMs?: number
}

const HIGHLIGHT: CSSProperties = { background: 'rgba(250, 204, 21, 0.35)' }

export default function Board({ fen, lastMove, animationMs = 250 }: BoardProps) {
  const squareStyles: Record<string, CSSProperties> = lastMove
    ? { [lastMove.from]: HIGHLIGHT, [lastMove.to]: HIGHLIGHT }
    : {}

  return (
    <div className="aspect-square w-full">
      <Chessboard
        options={{
          id: 'arena-board',
          position: fen,
          // Nobody drags pieces here — the agents play, we watch.
          allowDragging: false,
          animationDurationInMs: animationMs,
          boardOrientation: 'white',
          showNotation: true,
          squareStyles,
          darkSquareStyle: { backgroundColor: '#3f4a5a' },
          lightSquareStyle: { backgroundColor: '#cbd5e1' },
          boardStyle: { borderRadius: '0.5rem', overflow: 'hidden' },
        }}
      />
    </div>
  )
}

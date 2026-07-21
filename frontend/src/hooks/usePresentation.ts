/**
 * Drives the presentation queue off the store (Feature 2).
 *
 * When commentary is enabled, the board the user sees is driven by THIS hook —
 * one move at a time, each spoken to completion before the next animates — not
 * by raw MOVE_MADE events. When disabled, the hook reports `enabled: false` and
 * the caller falls back to the live store FEN.
 *
 * On (re)hydration it fast-forwards to the latest known move without re-speaking
 * history: commentary is for live play, so only moves that arrive *after* the
 * queue exists get the animate-then-speak treatment.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { PresentationQueue } from '../lib/presentationQueue'
import { START_FEN, useGameStore } from '../state/gameStore'
import type { TTSService } from '../lib/tts'

export function usePresentation(opts: {
  enabled: boolean
  gameId: string | null
  tts: TTSService
}) {
  const { enabled, gameId, tts } = opts

  const [presentedPly, setPresentedPly] = useState(0)
  const [displayFen, setDisplayFen] = useState(START_FEN)
  const [speakingPly, setSpeakingPly] = useState<number | null>(null)

  const queueRef = useRef<PresentationQueue | null>(null)
  const fedMoveRef = useRef(0)
  const fedCommentsRef = useRef<Set<number>>(new Set())

  const moves = useGameStore((s) => s.moves)
  const commentaries = useGameStore((s) => s.commentaries)
  const status = useGameStore((s) => s.status)

  // (Re)create the queue whenever the game changes or presentation turns on.
  useEffect(() => {
    if (!enabled || !gameId) {
      queueRef.current = null
      return
    }

    const existing = useGameStore.getState().moves
    const last = existing.at(-1)
    const startPly = (last?.ply ?? 0) + 1

    const queue = new PresentationQueue(
      {
        animate: (ply, fen) => {
          setDisplayFen(fen)
          setPresentedPly(ply)
        },
        // The board does not advance until this resolves on the utterance's end.
        speak: (_ply, text) => tts.speak(text),
        onSpeakStart: (ply) => setSpeakingPly(ply),
        onAdvance: () => setSpeakingPly(null),
      },
      startPly,
    )
    queueRef.current = queue

    // Fast-forward past anything already on the board (rehydration): show the
    // latest position and mark that history as already fed.
    fedMoveRef.current = last?.ply ?? 0
    fedCommentsRef.current = new Set(
      Object.keys(useGameStore.getState().commentaries).map(Number),
    )
    setPresentedPly(last?.ply ?? 0)
    setDisplayFen(last?.fen ?? START_FEN)
    setSpeakingPly(null)

    return () => {
      tts.cancel()
      queueRef.current = null
    }
  }, [enabled, gameId, tts])

  // Feed newly-arrived moves, in order.
  useEffect(() => {
    const queue = queueRef.current
    if (!queue) return
    for (const move of moves) {
      if (move.ply > fedMoveRef.current) {
        queue.addMove(move.ply, move.fen)
        fedMoveRef.current = move.ply
      }
    }
  }, [moves])

  // Feed newly-arrived commentary.
  useEffect(() => {
    const queue = queueRef.current
    if (!queue) return
    for (const key of Object.keys(commentaries)) {
      const ply = Number(key)
      if (!fedCommentsRef.current.has(ply)) {
        queue.addCommentary(ply, commentaries[ply])
        fedCommentsRef.current.add(ply)
      }
    }
  }, [commentaries])

  // Once the game ends, stop the queue waiting on commentary that won't come.
  useEffect(() => {
    const queue = queueRef.current
    if (!queue) return
    if (status && status !== 'in_progress' && status !== 'pending') {
      queue.finish()
    }
  }, [status])

  const displayLastMove = useMemo(() => {
    const move = moves.find((m) => m.ply === presentedPly)
    return move ? { from: move.uci.slice(0, 2), to: move.uci.slice(2, 4) } : null
  }, [moves, presentedPly])

  return {
    active: enabled && Boolean(gameId),
    displayFen,
    displayLastMove,
    presentedPly,
    speakingPly,
    supported: tts.supported,
  }
}

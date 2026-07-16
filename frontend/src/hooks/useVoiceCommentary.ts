/**
 * Drives the voice commentator off the live game state.
 *
 * Speaks: an opener when a game begins, short reactions to notable moves
 * (captures, checks, mate, forfeits), the analyst's own commentary lines as
 * they arrive, and an excited finale at the end. Quiet moves stay silent so the
 * commentary doesn't drown the match.
 */

import { useEffect, useRef } from 'react'
import { useGameStore } from '../state/gameStore'
import { fillerLine, finaleLine, openerLine, reactionForMove } from '../lib/commentary'
import { useVoice } from './useVoice'

export function useVoiceCommentary(enabled: boolean) {
  const { speak, stop, supported } = useVoice(enabled)

  const moves = useGameStore((s) => s.moves)
  const comments = useGameStore((s) => s.comments)
  const status = useGameStore((s) => s.status)
  const gameId = useGameStore((s) => s.gameId)
  const winner = useGameStore((s) => s.winner)
  const termination = useGameStore((s) => s.termination)

  const lastPlyRef = useRef(0)
  const lastCommentRef = useRef(0)
  const openedGameRef = useRef<string | null>(null)
  const finishedGameRef = useRef<string | null>(null)

  // A new (or cleared) game resets every tracker and silences leftover speech.
  useEffect(() => {
    lastPlyRef.current = 0
    lastCommentRef.current = 0
    openedGameRef.current = null
    finishedGameRef.current = null
    stop()
  }, [gameId, stop])

  // Opener — greet a game we're watching from the start (not a mid-game join).
  useEffect(() => {
    if (!enabled || !gameId) return
    if (status !== 'in_progress' && status !== 'pending') return
    if (openedGameRef.current === gameId) return
    openedGameRef.current = gameId
    if (moves.length <= 2) speak(openerLine(), { priority: 'high' })
  }, [enabled, gameId, status, moves.length, speak])

  // Reactions to new live moves. The +1 guard means a rehydration backlog
  // (many plies at once) is skipped, and only genuinely new moves react.
  useEffect(() => {
    if (!enabled) return
    const last = moves.at(-1)
    if (!last) {
      lastPlyRef.current = 0
      return
    }
    if (last.ply === lastPlyRef.current + 1) {
      const reaction = reactionForMove(last)
      if (reaction) {
        speak(reaction.text, { priority: reaction.priority })
      } else if (Math.random() < 0.4) {
        // Quiet move: sometimes drop in a filler remark so the commentary keeps
        // flowing. Normal priority means it's skipped if the voice is still busy.
        speak(fillerLine(last))
      }
    }
    lastPlyRef.current = last.ply
  }, [moves, enabled, speak])

  // Analyst commentary lines — speak the newest so we don't dump a backlog.
  useEffect(() => {
    if (!enabled) return
    if (comments.length > lastCommentRef.current) {
      const latest = comments[comments.length - 1]
      if (latest?.text) speak(latest.text)
    }
    lastCommentRef.current = comments.length
  }, [comments, enabled, speak])

  // The finish — an excited shout on a win, a calmer line on a draw. Aborted
  // games get a brief sign-off.
  useEffect(() => {
    if (!enabled || !gameId) return
    if (status !== 'finished' && status !== 'aborted') return
    if (finishedGameRef.current === gameId) return
    finishedGameRef.current = gameId

    if (status === 'aborted') {
      speak('And the game is stopped there.', { priority: 'high' })
      return
    }
    const adjudicated = termination === 'max_moves'
    speak(finaleLine(winner, termination, adjudicated), {
      priority: 'high',
      pitch: winner ? 1.15 : 1,
      rate: winner ? 1.1 : 1,
    })
  }, [status, enabled, gameId, winner, termination, speak])

  return { supported }
}

/**
 * Phrase generation for the voice commentator.
 *
 * The analyst's own commentary (when enabled) is spoken verbatim; these lines
 * are the *reactions* the commentator adds on top — openers, captures, checks,
 * the finish. Kept punchy because they're spoken aloud between moves.
 */

import type { Color, MoveRow } from '../state/gameStore'

const PIECE_NAMES: Record<string, string> = {
  p: 'pawn',
  n: 'knight',
  b: 'bishop',
  r: 'rook',
  q: 'queen',
  k: 'king',
}

const OPENERS = [
  "And we're underway! Two language models, one chessboard.",
  'Here we go — let the silicon showdown begin!',
  "Game on! Let's see which model blunders less.",
  'The pieces are set. Let battle commence!',
  'Welcome, everyone! Our two contenders are ready to move.',
]

const CAPTURES = [
  'Ooh, a capture!',
  '{color} snatches a {piece}!',
  'Down comes the {piece}!',
  'And the {piece} is gone!',
  '{color} grabs material!',
]

const CHECKS = ['Check!', 'The king is under fire!', 'Pressure on the king!', "Check — the king's in a spot!"]

const CHECKMATES = [
  "Checkmate! And that's the game!",
  'Mate! What a way to finish!',
  "Checkmate — the king has nowhere to run!",
]

const FORFEITS = [
  'Oh no! {color} plays an illegal move and the engine steps in!',
  'Yikes — {color} fumbles, and a random move is forced!',
  'A blunder by forfeit from {color}! The engine takes over.',
  "Ouch! {color} couldn't find a legal move!",
]

const PROMOTIONS = ['Promotion! A new queen joins the fight!', '{color} promotes — say hello to a fresh piece!']

const WIN_SHOUTS = ['Ohhhh!', 'Wow!', 'Incredible!', 'What a game!', 'Unbelievable!', 'Yes!']

const DRAWS = [
  "It's a draw! Honours even.",
  'All square — a hard-fought draw!',
  'Neither side could break through. A draw it is!',
  'They shake hands — a draw!',
]

const ADJUDICATED = [
  '{winner} takes it on material as the clock runs out!',
  "time's up, and {winner} is ahead on the board!",
  '{winner} grinds it out to the move limit and comes out on top!',
]

function pick(list: string[]): string {
  return list[Math.floor(Math.random() * list.length)]
}

function fill(template: string, vars: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '')
}

function pieceName(letter: string | null): string {
  return (letter && PIECE_NAMES[letter.toLowerCase()]) || 'piece'
}

export type Reaction = { text: string; priority: 'high' | 'normal' }

export function openerLine(): string {
  return pick(OPENERS)
}

/**
 * A spoken reaction for a just-played move, or null when the move isn't
 * noteworthy (most quiet moves stay silent so the voice doesn't over-talk).
 */
export function reactionForMove(move: MoveRow): Reaction | null {
  const color = move.color === 'white' ? 'White' : 'Black'

  if (move.forfeited) {
    return { text: fill(pick(FORFEITS), { color }), priority: 'high' }
  }
  if (move.isCheckmate || move.san.includes('#')) {
    return { text: pick(CHECKMATES), priority: 'high' }
  }
  if (move.san.includes('=')) {
    return { text: fill(pick(PROMOTIONS), { color }), priority: 'normal' }
  }
  if (move.isCheck) {
    return { text: pick(CHECKS), priority: 'normal' }
  }
  if (move.isCapture) {
    return {
      text: fill(pick(CAPTURES), { color, piece: pieceName(move.capturedPiece) }),
      priority: 'normal',
    }
  }
  return null
}

/** The big finish — an excited shout on a win, a calmer line on a draw. */
export function finaleLine(
  winner: Color | null,
  termination: string | null,
  adjudicated: boolean,
): string {
  const term = humanTermination(termination)
  if (winner) {
    const who = winner === 'white' ? 'White' : 'Black'
    // Every win opens with an excited shout — a checkmate names the mate, an
    // adjudicated win describes the grind, but both celebrate.
    const shout = pick(WIN_SHOUTS)
    if (adjudicated) return `${shout} ${fill(pick(ADJUDICATED), { winner: who })}`
    return `${shout} ${who} wins by ${term}!`
  }
  return pick(DRAWS)
}

function humanTermination(t: string | null): string {
  switch (t) {
    case 'checkmate':
      return 'checkmate'
    case 'stalemate':
      return 'stalemate'
    case 'insufficient_material':
      return 'insufficient material'
    case 'fifty_moves':
      return 'the fifty-move rule'
    case 'threefold_repetition':
    case 'fivefold_repetition':
      return 'repetition'
    case 'max_moves':
      return 'the move limit'
    default:
      return t ?? 'the rules'
  }
}

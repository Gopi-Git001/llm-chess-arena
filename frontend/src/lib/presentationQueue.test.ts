/**
 * Presentation-queue ordering tests (Feature 2, required by the spec).
 *
 * Run with:  node --test src/lib/presentationQueue.test.ts
 * (Node strips the TS types; no build step or test framework needed.)
 *
 * The central guarantee under test: the board never advances to the next move
 * before the current move's speech-finished callback has fired.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PresentationQueue } from './presentationQueue.ts'

type Deferred = { promise: Promise<void>; resolve: () => void }
function deferred(): Deferred {
  let resolve!: () => void
  const promise = new Promise<void>((r) => (resolve = r))
  return { promise, resolve }
}

// Flush pending micro/macrotasks so the queue's async loop settles.
const flush = () => new Promise((r) => setTimeout(r, 0))

// A wait that never resolves — used when commentary is always present, so no
// real timers are scheduled and the test exits promptly.
const neverWait = () => new Promise<void>(() => {})

test('board never advances before the speech-finished callback fires', async () => {
  const events: string[] = []
  const speaks: Deferred[] = []

  const q = new PresentationQueue({
    animate: (ply) => events.push(`animate:${ply}`),
    speak: (ply) => {
      events.push(`speak:${ply}`)
      const d = deferred()
      speaks.push(d)
      return d.promise
    },
    onAdvance: (ply) => events.push(`advance:${ply}`),
    wait: neverWait,
  })

  // The game loop runs ahead: both moves + commentary are pushed up front.
  q.addMove(1, 'fen1')
  q.addCommentary(1, 'c1')
  q.addMove(2, 'fen2')
  q.addCommentary(2, 'c2')
  await flush()

  // Move 1 animated and speaking — but move 2 must NOT have animated yet.
  assert.deepEqual(events, ['animate:1', 'speak:1'])

  speaks[0].resolve() // speech for move 1 ends
  await flush()

  assert.deepEqual(events, ['animate:1', 'speak:1', 'advance:1', 'animate:2', 'speak:2'])

  speaks[1].resolve()
  await flush()
  assert.deepEqual(events, [
    'animate:1',
    'speak:1',
    'advance:1',
    'animate:2',
    'speak:2',
    'advance:2',
  ])
})

test('waits for late-arriving commentary before speaking', async () => {
  const events: string[] = []
  const speaks: Deferred[] = []

  const q = new PresentationQueue({
    animate: (ply) => events.push(`animate:${ply}`),
    speak: (ply, text) => {
      events.push(`speak:${ply}:${text}`)
      const d = deferred()
      speaks.push(d)
      return d.promise
    },
    onAdvance: (ply) => events.push(`advance:${ply}`),
    wait: neverWait,
  })

  q.addMove(1, 'fen1') // move arrives, commentary hasn't yet
  await flush()
  assert.deepEqual(events, ['animate:1'], 'animates, then waits for commentary')

  q.addCommentary(1, 'hello') // commentary lands late
  await flush()
  assert.deepEqual(events, ['animate:1', 'speak:1:hello'])
})

test('a move with no commentary is presented silently after the timeout', async () => {
  const events: string[] = []
  let releaseTimeout!: () => void

  const q = new PresentationQueue({
    animate: (ply) => events.push(`animate:${ply}`),
    speak: (ply) => {
      events.push(`speak:${ply}`)
      return Promise.resolve()
    },
    onAdvance: (ply) => events.push(`advance:${ply}`),
    // Controllable timeout: fires only when we release it.
    wait: () => new Promise<void>((r) => (releaseTimeout = r)),
  })

  q.addMove(1, 'fen1') // no commentary will ever arrive for this ply
  await flush()
  assert.deepEqual(events, ['animate:1'], 'waiting for commentary or timeout')

  releaseTimeout() // commentary never came — timeout elapses
  await flush()
  // Presented without speech, and it advanced so the next move can play.
  assert.deepEqual(events, ['animate:1', 'advance:1'])
})

test('finish() stops the queue waiting for commentary that will never come', async () => {
  const events: string[] = []

  const q = new PresentationQueue({
    animate: (ply) => events.push(`animate:${ply}`),
    speak: (ply) => {
      events.push(`speak:${ply}`)
      return Promise.resolve()
    },
    onAdvance: (ply) => events.push(`advance:${ply}`),
    wait: neverWait,
  })

  q.addMove(1, 'fen1')
  await flush()
  assert.deepEqual(events, ['animate:1'])

  q.finish() // game over, no commentary coming
  await flush()
  assert.deepEqual(events, ['animate:1', 'advance:1'])
})

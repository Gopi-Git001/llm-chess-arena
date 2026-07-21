/**
 * Presentation queue (Feature 2, mandatory architecture).
 *
 * The backend game loop may run ahead of what the viewer has seen. This queue
 * consumes items strictly one at a time and in ply order:
 *
 *     animate the move → speak its commentary → await speech-finished → advance
 *
 * The board the user sees is driven by THIS queue, never by raw MOVE_MADE
 * events, so it can never get ahead of the voice. No timers or sleeps are used
 * to guess when speech ends — `speak` returns a Promise that resolves on the
 * utterance's real 'end' event.
 *
 * Deliberately framework-free so it can be unit-tested without React or a DOM.
 */

export type PresentationCallbacks = {
  /** Show the move on the board (set the displayed FEN). */
  animate: (ply: number, fen: string) => void
  /** Speak the commentary; MUST resolve only when the utterance actually ends. */
  speak: (ply: number, text: string) => Promise<void>
  /** Fired as speech begins, for caption highlighting. */
  onSpeakStart?: (ply: number, text: string) => void
  /** Fired after an item is fully presented (spoken) and before the next. */
  onAdvance?: (ply: number) => void
  /**
   * How long to wait for a move's commentary to arrive before presenting it
   * without speech (some plies legitimately have none). Prevents a stall.
   */
  commentaryTimeoutMs?: number
  /** Injectable timer so the timeout is testable. Default: setTimeout. */
  wait?: (ms: number) => Promise<void>
}

const DEFAULT_COMMENTARY_TIMEOUT_MS = 6000

function defaultWait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export class PresentationQueue {
  private cb: PresentationCallbacks
  private fens = new Map<number, string>()
  private texts = new Map<number, string>()
  private nextPly = 1
  private pumping = false
  private finished = false
  private waiters: Array<() => void> = []

  constructor(cb: PresentationCallbacks, startPly = 1) {
    this.cb = cb
    this.nextPly = startPly
  }

  /** A move is ready to be presented (from a MOVE_MADE event). */
  addMove(ply: number, fen: string): void {
    if (ply < this.nextPly) return // already presented
    this.fens.set(ply, fen)
    this.signal()
    void this.pump()
  }

  /** Commentary for a move (from a MOVE_COMMENTARY event). May arrive late. */
  addCommentary(ply: number, text: string): void {
    if (ply < this.nextPly) return
    this.texts.set(ply, text)
    this.signal()
  }

  /** No more items are coming (game over) — stop waiting on absent commentary. */
  finish(): void {
    this.finished = true
    this.signal()
  }

  /** The ply currently being (or about to be) presented. */
  get presentedPly(): number {
    return this.nextPly - 1
  }

  private signal(): void {
    const waiters = this.waiters
    this.waiters = []
    for (const resolve of waiters) resolve()
  }

  private nextChange(): Promise<void> {
    return new Promise((resolve) => this.waiters.push(resolve))
  }

  private async pump(): Promise<void> {
    if (this.pumping) return
    this.pumping = true
    try {
      // Present every consecutive move that's available, one fully at a time.
      while (true) {
        const fen = this.fens.get(this.nextPly)
        if (fen === undefined) break // this move hasn't arrived yet

        this.cb.animate(this.nextPly, fen)

        const text = await this.awaitCommentary(this.nextPly)
        if (text) {
          this.cb.onSpeakStart?.(this.nextPly, text)
          // The board does not advance until this resolves — the whole point.
          await this.cb.speak(this.nextPly, text)
        }

        this.cb.onAdvance?.(this.nextPly)
        this.nextPly += 1
      }
    } finally {
      this.pumping = false
    }
  }

  /**
   * Wait for this ply's commentary. Resolves with the text once it arrives, or
   * null if the game finishes or we wait past the timeout (a move with no
   * commentary still gets presented — just silently).
   */
  private async awaitCommentary(ply: number): Promise<string | null> {
    const wait = this.cb.wait ?? defaultWait
    const timeoutMs = this.cb.commentaryTimeoutMs ?? DEFAULT_COMMENTARY_TIMEOUT_MS
    let timedOut = false
    const timer = wait(timeoutMs).then(() => {
      timedOut = true
    })

    while (true) {
      const text = this.texts.get(ply)
      if (text !== undefined) return text
      if (this.finished) return null
      if (timedOut) return null
      // Wake on the next addMove/addCommentary/finish, or when the timer fires.
      await Promise.race([this.nextChange(), timer])
    }
  }
}

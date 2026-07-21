/**
 * TTSService — text-to-speech for the synced voice commentary (Feature 2d).
 *
 * Default engine is the browser Web Speech API (`speechSynthesis`): no key, no
 * cost, always available — the same reasoning as mock mode. The one method the
 * presentation queue depends on is `speak(text)`, which returns a Promise that
 * resolves on the utterance's real 'end' event (never a timer), so the board
 * only advances once the voice has actually finished.
 *
 * The class is deliberately engine-agnostic behind that Promise contract: a
 * backend edge-tts implementation could be swapped in later by implementing the
 * same `speak` shape (audio 'ended' event → resolve). That is NOT built now.
 */

export type TTSVoice = { id: string; name: string; lang: string }

const SUPPORTED =
  typeof window !== 'undefined' && 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window

export class TTSService {
  private synth: SpeechSynthesis | null = SUPPORTED ? window.speechSynthesis : null
  private voice: SpeechSynthesisVoice | null = null
  private muted = false
  private rate = 1.05
  private pitch = 1
  // Serialises utterances so they never overlap (the queue guard).
  private chain: Promise<void> = Promise.resolve()

  constructor() {
    if (this.synth) {
      this.loadVoice()
      // Voices populate asynchronously on most browsers.
      this.synth.addEventListener?.('voiceschanged', () => this.loadVoice())
    }
  }

  get supported(): boolean {
    return SUPPORTED
  }

  private loadVoice(): void {
    if (!this.synth) return
    const voices = this.synth.getVoices()
    if (voices.length === 0) return
    this.voice =
      voices.find((v) => /en[-_]?(US|GB)/i.test(v.lang) && /google|samantha|zira|female/i.test(v.name)) ||
      voices.find((v) => /^en/i.test(v.lang)) ||
      voices[0]
  }

  listVoices(): TTSVoice[] {
    if (!this.synth) return []
    return this.synth
      .getVoices()
      .filter((v) => /^en/i.test(v.lang))
      .map((v) => ({ id: v.voiceURI, name: v.name, lang: v.lang }))
  }

  setVoice(id: string): void {
    if (!this.synth) return
    const match = this.synth.getVoices().find((v) => v.voiceURI === id)
    if (match) this.voice = match
  }

  setRate(rate: number): void {
    this.rate = rate
  }

  setPitch(pitch: number): void {
    this.pitch = pitch
  }

  /** Mute silences audio but callers still get resolved Promises, so captions
   * (and the presentation queue) keep pacing (Feature 2f: "mute keeps captions
   * running"). */
  setMuted(muted: boolean): void {
    this.muted = muted
    if (muted && this.synth) this.synth.cancel()
  }

  get isMuted(): boolean {
    return this.muted
  }

  /**
   * Speak `text`, resolving when the utterance ends. Serialised: a new call
   * waits for the previous utterance to finish, so they never overlap. When
   * muted (or unsupported), it resolves after a short caption-reading delay so
   * the queue still advances at a listenable pace.
   */
  speak(text: string, opts: { rate?: number; pitch?: number } = {}): Promise<void> {
    const run = () => this.speakNow(text, opts)
    // Append to the chain; swallow errors so one failure can't wedge the queue.
    const next = this.chain.then(run, run)
    this.chain = next.catch(() => undefined)
    return next
  }

  /** Stop all speech immediately and clear the queue. */
  cancel(): void {
    if (this.synth) this.synth.cancel()
    this.chain = Promise.resolve()
  }

  private speakNow(text: string, opts: { rate?: number; pitch?: number }): Promise<void> {
    const clean = text.trim()
    if (!clean) return Promise.resolve()

    if (!this.synth || this.muted) {
      // No audio, but pace the captions by roughly how long it'd take to read.
      return this.captionDelay(clean)
    }

    return new Promise<void>((resolve) => {
      try {
        const utterance = new SpeechSynthesisUtterance(clean)
        if (this.voice) utterance.voice = this.voice
        utterance.rate = opts.rate ?? this.rate
        utterance.pitch = opts.pitch ?? this.pitch
        utterance.volume = 1
        let done = false
        const finish = () => {
          if (done) return
          done = true
          resolve()
        }
        utterance.onend = finish
        utterance.onerror = finish
        // Safety net: if the browser drops the 'end' event (a known quirk),
        // don't hang the queue forever.
        const guardMs = Math.min(15000, 1200 + clean.length * 80)
        setTimeout(finish, guardMs)
        this.synth!.speak(utterance)
      } catch {
        resolve()
      }
    })
  }

  private captionDelay(text: string): Promise<void> {
    const ms = Math.min(6000, 700 + text.length * 45)
    return new Promise((resolve) => setTimeout(resolve, ms))
  }
}

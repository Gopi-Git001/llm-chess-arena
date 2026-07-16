/**
 * Sound effects via the Web Audio API — synthesised, so nothing is bundled and
 * the CSP stays clean (PLAN.md §9 polish pass).
 *
 * The AudioContext is created lazily on the first play, because browsers block
 * audio until a user gesture. "New Game" is that gesture, so the first sound of
 * a match is what unlocks it.
 */

import { useCallback, useRef } from 'react'

export type SoundName = 'move' | 'capture' | 'check' | 'gameover' | 'forfeit'

type Tone = { freq: number; duration: number; type: OscillatorType; gain?: number }

// Each cue is a tiny sequence of tones. Kept short and soft — this plays on
// every move, so it must never become annoying.
const CUES: Record<SoundName, Tone[]> = {
  move: [{ freq: 440, duration: 0.06, type: 'sine' }],
  capture: [
    { freq: 320, duration: 0.05, type: 'triangle' },
    { freq: 180, duration: 0.09, type: 'triangle' },
  ],
  check: [{ freq: 880, duration: 0.12, type: 'square', gain: 0.05 }],
  forfeit: [
    { freq: 200, duration: 0.1, type: 'sawtooth', gain: 0.05 },
    { freq: 150, duration: 0.14, type: 'sawtooth', gain: 0.05 },
  ],
  gameover: [
    { freq: 523, duration: 0.14, type: 'sine' },
    { freq: 659, duration: 0.14, type: 'sine' },
    { freq: 784, duration: 0.22, type: 'sine' },
  ],
}

export function useSounds(enabled: boolean) {
  const ctxRef = useRef<AudioContext | null>(null)

  const play = useCallback(
    (name: SoundName) => {
      if (!enabled) return
      try {
        let ctx = ctxRef.current
        if (!ctx) {
          const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
          ctx = new AC()
          ctxRef.current = ctx
        }
        if (ctx.state === 'suspended') void ctx.resume()

        let when = ctx.currentTime
        for (const tone of CUES[name]) {
          const osc = ctx.createOscillator()
          const gain = ctx.createGain()
          osc.type = tone.type
          osc.frequency.value = tone.freq
          const peak = tone.gain ?? 0.08
          // Quick attack, exponential release — a soft blip, not a beep.
          gain.gain.setValueAtTime(0.0001, when)
          gain.gain.exponentialRampToValueAtTime(peak, when + 0.005)
          gain.gain.exponentialRampToValueAtTime(0.0001, when + tone.duration)
          osc.connect(gain).connect(ctx.destination)
          osc.start(when)
          osc.stop(when + tone.duration)
          when += tone.duration
        }
      } catch {
        // Audio is a nicety; never let it break the game.
      }
    },
    [enabled],
  )

  return play
}

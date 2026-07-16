/**
 * Text-to-speech via the browser's Web Speech API (PLAN.md §9 polish).
 *
 * Free, offline, no API cost — the same reason mock mode exists. `speechSynthesis`
 * is unlocked by a user gesture (the New Game click), so the opener spoken right
 * after that click is what enables the voice for the rest of the match.
 */

import { useCallback, useEffect, useRef } from 'react'

export type SpeakOptions = {
  /** 'high' cancels whatever's talking and jumps the queue — for big moments. */
  priority?: 'high' | 'normal'
  rate?: number
  pitch?: number
}

const SUPPORTED = typeof window !== 'undefined' && 'speechSynthesis' in window

function pickVoice(): SpeechSynthesisVoice | null {
  if (!SUPPORTED) return null
  const voices = window.speechSynthesis.getVoices()
  if (voices.length === 0) return null
  // Prefer an English voice; a lively one if the platform offers it.
  return (
    voices.find((v) => /en[-_]?(US|GB)/i.test(v.lang) && /female|zira|samantha|google/i.test(v.name)) ||
    voices.find((v) => /^en/i.test(v.lang)) ||
    voices[0]
  )
}

export function useVoice(enabled: boolean) {
  const voiceRef = useRef<SpeechSynthesisVoice | null>(null)

  // Voices load asynchronously on most browsers.
  useEffect(() => {
    if (!SUPPORTED) return
    const load = () => {
      voiceRef.current = pickVoice()
    }
    load()
    window.speechSynthesis.addEventListener('voiceschanged', load)
    return () => window.speechSynthesis.removeEventListener('voiceschanged', load)
  }, [])

  // Stop talking the moment voice is switched off.
  useEffect(() => {
    if (!enabled && SUPPORTED) window.speechSynthesis.cancel()
  }, [enabled])

  const speak = useCallback(
    (text: string, opts: SpeakOptions = {}) => {
      if (!enabled || !SUPPORTED || !text.trim()) return
      try {
        const synth = window.speechSynthesis
        if (opts.priority === 'high') synth.cancel()
        // If a long backlog has built up (slow speech vs fast moves), drop it so
        // the commentary stays roughly in sync with the board.
        if (synth.speaking && synth.pending) synth.cancel()

        const u = new SpeechSynthesisUtterance(text)
        if (voiceRef.current) u.voice = voiceRef.current
        u.rate = opts.rate ?? 1.05
        u.pitch = opts.pitch ?? 1
        u.volume = 1
        synth.speak(u)
      } catch {
        // Speech is a nicety; never let it break the game.
      }
    },
    [enabled],
  )

  const stop = useCallback(() => {
    if (SUPPORTED) window.speechSynthesis.cancel()
  }, [])

  return { speak, stop, supported: SUPPORTED }
}

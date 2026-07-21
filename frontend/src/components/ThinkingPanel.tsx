/**
 * The live-thinking side panel for "Too Slow" mode (Feature 1).
 *
 * Reasoning tokens arrive over the WebSocket and are appended to the store, so
 * the text grows word by word here — a natural typewriter effect. A blinking
 * cursor marks live streaming, and the panel auto-scrolls to follow along. When
 * a past move is selected in the move list, this shows that move's stored
 * reasoning instead (click a move → see how it thought).
 */

import { useEffect, useRef } from 'react'
import type { Color } from '../state/gameStore'

type ThinkingPanelProps = {
  color: Color
  text: string
  streaming: boolean
  // A label so a stored (scrubbed) view reads differently from a live one.
  label: string
}

export default function ThinkingPanel({ color, text, streaming, label }: ThinkingPanelProps) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (streaming) endRef.current?.scrollIntoView({ block: 'end' })
  }, [text, streaming])

  return (
    <div
      className={`rounded-lg border bg-zinc-950/60 p-2.5 transition-colors ${
        streaming ? 'border-sky-700/70' : 'border-zinc-800'
      }`}
    >
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] font-medium tracking-wide text-zinc-500 uppercase">
          {color} thinking
        </span>
        <span className="text-[10px] text-zinc-600">{label}</span>
      </div>
      <div className="max-h-28 overflow-y-auto">
        <p className="text-[11px] leading-relaxed whitespace-pre-wrap text-zinc-300">
          {text || <span className="text-zinc-600 italic">Waiting to think…</span>}
          {streaming && (
            <span className="ml-0.5 inline-block h-3 w-1.5 translate-y-0.5 animate-pulse bg-sky-400" />
          )}
        </p>
        <div ref={endRef} />
      </div>
    </div>
  )
}

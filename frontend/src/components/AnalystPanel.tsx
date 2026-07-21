/**
 * The analyst's column: a live commentary feed that becomes a verdict card when
 * the game ends (PLAN.md §9).
 *
 * Commentary is off by default (config `live_commentary_every_n_moves: 0`)
 * because each line costs a request in live mode, so the empty state explains
 * itself rather than looking broken.
 */

import { useEffect, useRef } from 'react'
import type { Caption, Comment, RateLimit } from '../state/gameStore'
import type { VerdictData } from '../types/events'
import VerdictCard from './VerdictCard'

type AnalystPanelProps = {
  comments: Comment[]
  captions: Caption[]
  speakingPly: number | null
  verdict: VerdictData | null
  rateLimit: RateLimit | null
  status: string | null
  whiteModel: string
  blackModel: string
  analystModel?: string
}

/** Live spoken-commentary captions. The line currently being spoken is
 * highlighted; captions keep running even when the voice is muted (F2f). */
function CaptionFeed({ captions, speakingPly }: { captions: Caption[]; speakingPly: number | null }) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [captions.length, speakingPly])

  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-medium tracking-wide text-zinc-400 uppercase">
        🎙️ Commentary
        {speakingPly !== null && (
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
        )}
      </h2>
      <ul className="max-h-48 space-y-1.5 overflow-y-auto">
        {captions.map((caption) => {
          const speaking = caption.ply === speakingPly
          return (
            <li
              key={caption.ply}
              className={`rounded px-2 py-1 text-xs transition-colors ${
                speaking ? 'bg-emerald-950/60 text-emerald-200' : 'text-zinc-400'
              }`}
            >
              <span className="mr-1 font-mono text-zinc-600">{Math.ceil(caption.ply / 2)}.</span>
              {caption.text}
            </li>
          )
        })}
        <div ref={endRef} />
      </ul>
    </section>
  )
}

function Waiting({ status }: { status: string | null }) {
  if (status === 'finished') {
    return (
      <p className="flex items-center gap-2 text-sm text-zinc-500">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-violet-500" />
        Reviewing the game…
      </p>
    )
  }
  return (
    <p className="text-sm text-zinc-600">
      The analyst reviews the game once it finishes.
    </p>
  )
}

export default function AnalystPanel({
  comments,
  captions,
  speakingPly,
  verdict,
  rateLimit,
  status,
  whiteModel,
  blackModel,
}: AnalystPanelProps) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [comments.length, verdict])

  const gameOver = status === 'finished'

  return (
    <div className="flex flex-col gap-3">
      {/* A live game that stalls on a 429 must say so, not just freeze (§8). */}
      {rateLimit && (
        <div className="flex items-center gap-2 rounded-md border border-amber-800 bg-amber-950/50 px-3 py-2 text-xs text-amber-200">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400" />
          <span>
            Rate limited by <span className="font-mono">{rateLimit.model}</span> — waiting{' '}
            {rateLimit.retryInS}s before retry {rateLimit.attempt}.
          </span>
        </div>
      )}

      {captions.length > 0 && <CaptionFeed captions={captions} speakingPly={speakingPly} />}

      {verdict ? (
        <VerdictCard verdict={verdict} whiteModel={whiteModel} blackModel={blackModel} />
      ) : (
        <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
          <h2 className="mb-2 text-xs font-medium tracking-wide text-zinc-400 uppercase">
            Analyst
          </h2>
          <Waiting status={gameOver ? 'finished' : status} />
        </section>
      )}

      {comments.length > 0 && (
        <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
          <h2 className="mb-2 text-xs font-medium tracking-wide text-zinc-400 uppercase">
            Commentary
          </h2>
          <ul className="max-h-48 space-y-2 overflow-y-auto">
            {comments.map((comment) => (
              <li key={comment.ply} className="text-xs">
                <span className="mr-1 font-mono text-zinc-600">{comment.moveNumber}.</span>
                <span className="text-zinc-300">{comment.text}</span>
              </li>
            ))}
            <div ref={endRef} />
          </ul>
        </section>
      )}
    </div>
  )
}

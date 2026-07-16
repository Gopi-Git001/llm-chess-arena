/**
 * The final verdict (PLAN.md §9, §3).
 *
 * The engine's result and the analyst's opinion are shown as *separate* things,
 * because they are: the rules engine decided who won, the analyst only grades
 * how they played. When a verdict is a fallback template, the card says so
 * rather than dressing up bookkeeping as insight.
 */

import type { VerdictData } from '../types/events'

type VerdictCardProps = {
  verdict: VerdictData
  whiteModel: string
  blackModel: string
}

const GRADE_TONE: Record<string, string> = {
  A: 'text-emerald-400',
  B: 'text-lime-400',
  C: 'text-amber-400',
  D: 'text-orange-400',
  F: 'text-red-400',
  '?': 'text-zinc-500',
}

function gradeTone(grade: string): string {
  return GRADE_TONE[grade.charAt(0).toUpperCase()] ?? 'text-zinc-300'
}

function Grade({ label, grade, model }: { label: string; grade: string; model: string }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900 p-2 text-center">
      <div className="text-[10px] tracking-wide text-zinc-500 uppercase">{label}</div>
      <div className={`text-2xl font-semibold ${gradeTone(grade)}`}>{grade}</div>
      <div className="truncate font-mono text-[10px] text-zinc-600" title={model}>
        {model}
      </div>
    </div>
  )
}

export default function VerdictCard({ verdict, whiteModel, blackModel }: VerdictCardProps) {
  const engineDecisive = verdict.engine_winner !== null
  // The interesting case from §3: "Draw, but Qwen played better chess."
  const opinionDiffers = !engineDecisive && verdict.winner !== 'draw'

  return (
    <section className="rounded-lg border border-violet-900 bg-violet-950/30">
      <header className="border-b border-violet-900/60 px-4 py-3">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-xs font-medium tracking-wide text-violet-300 uppercase">
            Verdict
          </h2>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] ${
              verdict.source === 'analyst'
                ? 'bg-violet-900/60 text-violet-200'
                : 'bg-zinc-800 text-zinc-400'
            }`}
            title={
              verdict.source === 'analyst'
                ? `Reviewed by ${verdict.analyst_model}`
                : 'No model reviewed this game — built from engine facts only'
            }
          >
            {verdict.source === 'analyst' ? verdict.analyst_model : 'template (no model review)'}
          </span>
        </div>

        {/* The result, straight from the engine — never the model's opinion. */}
        <p className="mt-2 text-lg font-semibold text-zinc-100">
          {verdict.engine_result}
          {engineDecisive ? ` — ${verdict.engine_winner} wins` : ' — draw'}
        </p>
        <p className="text-xs text-zinc-400">by {verdict.engine_termination}</p>

        {opinionDiffers && (
          <p className="mt-2 rounded bg-violet-900/40 px-2 py-1 text-xs text-violet-200">
            Performance winner: <strong className="capitalize">{verdict.winner}</strong> — drawn on
            the board, but the analyst gives it to {verdict.winner}.
          </p>
        )}
      </header>

      <div className="space-y-3 px-4 py-3">
        <div className="grid grid-cols-2 gap-2">
          <Grade label="White" grade={verdict.white_grade} model={whiteModel} />
          <Grade label="Black" grade={verdict.black_grade} model={blackModel} />
        </div>

        {verdict.verdict_paragraph && (
          <p className="text-sm leading-relaxed text-zinc-300">{verdict.verdict_paragraph}</p>
        )}

        {verdict.key_moments.length > 0 && (
          <div>
            <h3 className="mb-1 text-[10px] tracking-wide text-zinc-500 uppercase">Key moments</h3>
            <ol className="list-inside list-decimal space-y-1 text-xs text-zinc-400">
              {verdict.key_moments.map((moment, i) => (
                <li key={i}>{moment}</li>
              ))}
            </ol>
          </div>
        )}

        {verdict.blunders.length > 0 && (
          <div>
            <h3 className="mb-1 text-[10px] tracking-wide text-zinc-500 uppercase">Blunders</h3>
            <ul className="list-inside list-disc space-y-1 text-xs text-red-300/80">
              {verdict.blunders.map((blunder, i) => (
                <li key={i}>{blunder}</li>
              ))}
            </ul>
          </div>
        )}

        {verdict.best_move && (
          <p className="text-xs text-zinc-400">
            <span className="text-zinc-500">Best move: </span>
            <span className="font-mono text-emerald-400">{verdict.best_move}</span>
          </p>
        )}

        {verdict.illegal_move_summary && (
          <p className="border-t border-zinc-800 pt-2 text-xs text-zinc-500">
            {verdict.illegal_move_summary}
          </p>
        )}
      </div>
    </section>
  )
}

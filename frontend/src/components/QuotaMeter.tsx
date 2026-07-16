/**
 * Requests-this-game against the per-game kill-switch (PLAN.md §5, §9).
 *
 * Mock games never spend requests, so the meter reads 0/budget and stays calm —
 * an honest signal that nothing is being spent.
 */

type QuotaMeterProps = {
  used: number
  budget: number
  mode: string | null
}

export default function QuotaMeter({ used, budget, mode }: QuotaMeterProps) {
  const pct = budget > 0 ? Math.min(100, (used / budget) * 100) : 0
  const hot = pct > 80
  const warm = pct > 50

  return (
    <div className="flex items-center gap-2 text-xs text-zinc-500" title="Requests used this game / per-game budget">
      <span className="hidden sm:inline">requests</span>
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={`h-full rounded-full transition-all ${
            hot ? 'bg-red-500' : warm ? 'bg-amber-500' : mode === 'mock' ? 'bg-sky-600' : 'bg-emerald-500'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="tabular-nums">
        {used}
        <span className="text-zinc-600">/{budget}</span>
      </span>
    </div>
  )
}

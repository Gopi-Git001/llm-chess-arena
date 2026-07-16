/**
 * Top bar (PLAN.md §9): New Game, model pickers, speed, plus the quota meter,
 * sound/history toggles, mode badge and connection light.
 *
 * The chaos toggle is a mock-mode affordance — it makes the players propose
 * illegal moves on demand so the retry/forfeit UI can be seen without a live
 * model misbehaving. It has no effect in live mode.
 */

import type { FreeModel } from '../api/ws'
import QuotaMeter from './QuotaMeter'

type GameControlsProps = {
  onNewGame: () => void
  onAbort: () => void
  busy: boolean
  running: boolean
  speedMs: number
  onSpeedChange: (ms: number) => void
  chaos: boolean
  onChaosChange: (chaos: boolean) => void
  commentary: boolean
  onCommentaryChange: (on: boolean) => void
  soundOn: boolean
  onSoundChange: (on: boolean) => void
  onOpenHistory: () => void
  models: FreeModel[]
  whiteModel: string
  blackModel: string
  analystModel: string
  onWhiteModel: (id: string) => void
  onBlackModel: (id: string) => void
  onAnalystModel: (id: string) => void
  requestsUsed: number
  requestBudget: number
  connection: string
  mode: string | null
}

const SPEEDS = [
  { label: 'Fast', ms: 150 },
  { label: 'Watchable', ms: 800 },
  { label: 'Slow', ms: 2000 },
]

const CONNECTION_TONE: Record<string, string> = {
  open: 'bg-emerald-500',
  connecting: 'bg-amber-500 animate-pulse',
  reconnecting: 'bg-amber-500 animate-pulse',
  closed: 'bg-zinc-600',
  idle: 'bg-zinc-700',
}

function ModelSelect({
  label,
  value,
  models,
  onChange,
  disabled,
}: {
  label: string
  value: string
  models: FreeModel[]
  onChange: (id: string) => void
  disabled: boolean
}) {
  // Keep the configured model selectable even if it isn't in the free list.
  const hasValue = models.some((m) => m.id === value)
  return (
    <label className="flex items-center gap-1 text-[11px] text-zinc-500">
      <span className="w-3 shrink-0">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="max-w-[9rem] truncate rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 font-mono text-[11px] text-zinc-200 disabled:opacity-50"
      >
        {!hasValue && value && <option value={value}>{value}</option>}
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </select>
    </label>
  )
}

export default function GameControls(props: GameControlsProps) {
  const {
    onNewGame,
    onAbort,
    busy,
    running,
    speedMs,
    onSpeedChange,
    chaos,
    onChaosChange,
    commentary,
    onCommentaryChange,
    soundOn,
    onSoundChange,
    onOpenHistory,
    models,
    whiteModel,
    blackModel,
    analystModel,
    onWhiteModel,
    onBlackModel,
    onAnalystModel,
    requestsUsed,
    requestBudget,
    connection,
    mode,
  } = props

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onNewGame}
          disabled={busy}
          className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? 'Starting…' : 'New Game'}
        </button>

        {running && (
          <button
            type="button"
            onClick={onAbort}
            className="rounded-md border border-zinc-700 px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
          >
            Abort
          </button>
        )}

        <ModelSelect label="W" value={whiteModel} models={models} onChange={onWhiteModel} disabled={busy} />
        <ModelSelect label="B" value={blackModel} models={models} onChange={onBlackModel} disabled={busy} />
        <ModelSelect label="A" value={analystModel} models={models} onChange={onAnalystModel} disabled={busy} />

        <label className="flex items-center gap-1.5 text-xs text-zinc-400">
          Speed
          <select
            value={speedMs}
            onChange={(e) => onSpeedChange(Number(e.target.value))}
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200"
          >
            {SPEEDS.map((speed) => (
              <option key={speed.ms} value={speed.ms}>
                {speed.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-zinc-400">
        <label
          className="flex items-center gap-1.5"
          title="Make the mock players propose illegal moves, to demo the penalty UI"
        >
          <input type="checkbox" checked={chaos} onChange={(e) => onChaosChange(e.target.checked)} className="accent-amber-500" />
          <span className="text-xs">Chaos</span>
        </label>

        <label
          className="flex items-center gap-1.5"
          title="Live commentary every 6 plies. One extra request per comment in live mode."
        >
          <input type="checkbox" checked={commentary} onChange={(e) => onCommentaryChange(e.target.checked)} className="accent-violet-500" />
          <span className="text-xs">Commentary</span>
        </label>

        <button
          onClick={() => onSoundChange(!soundOn)}
          className="flex items-center gap-1 text-xs hover:text-zinc-200"
          title={soundOn ? 'Mute sound effects' : 'Enable sound effects'}
        >
          <span>{soundOn ? '🔊' : '🔇'}</span>
          <span>Sound</span>
        </button>

        <button onClick={onOpenHistory} className="text-xs hover:text-zinc-200" title="Browse past games">
          ⏱ History
        </button>

        <div className="ml-auto flex items-center gap-3">
          <QuotaMeter used={requestsUsed} budget={requestBudget} mode={mode} />
          {mode && (
            <span
              className={`rounded px-2 py-0.5 text-xs font-medium ${
                mode === 'mock' ? 'bg-sky-950 text-sky-300' : 'bg-rose-950 text-rose-300'
              }`}
              title={mode === 'mock' ? 'No API calls are being made' : 'Live models — spending quota'}
            >
              {mode.toUpperCase()}
            </span>
          )}
          <span className="flex items-center gap-1.5 text-xs text-zinc-500">
            <span className={`inline-block h-2 w-2 rounded-full ${CONNECTION_TONE[connection] ?? 'bg-zinc-700'}`} />
            <span className="hidden sm:inline">{connection}</span>
          </span>
        </div>
      </div>
    </div>
  )
}

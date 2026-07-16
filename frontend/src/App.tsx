/**
 * The arena. Layout (PLAN.md §9): board + scrubber centre, agent panels
 * flanking, move list and analyst on the right. Stacks vertically on mobile
 * with the board first.
 *
 * Live position comes from the backend's FEN (§2.7). The scrubber replays past
 * positions from the FEN stored on each move — the frontend never computes
 * chess for the live game.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AgentPanel from './components/AgentPanel'
import AnalystPanel from './components/AnalystPanel'
import Board from './components/Board'
import GameControls from './components/GameControls'
import GameHistory from './components/GameHistory'
import MoveList from './components/MoveList'
import PlaybackControls from './components/PlaybackControls'
import { abortGame, createGame, fetchModels, streamGame, type FreeModel } from './api/ws'
import { START_FEN, lastMoveSquares, useGameStore, type Color, type MoveRow } from './state/gameStore'
import { useSounds } from './hooks/useSounds'

function gameIdFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get('game')
}

function putGameIdInUrl(gameId: string) {
  const url = new URL(window.location.href)
  url.searchParams.set('game', gameId)
  window.history.replaceState({}, '', url)
}

function fenAtPly(moves: MoveRow[], ply: number): string {
  if (ply <= 0) return START_FEN
  return moves.find((m) => m.ply === ply)?.fen ?? START_FEN
}

function squaresAtPly(moves: MoveRow[], ply: number): { from: string; to: string } | null {
  const move = moves.find((m) => m.ply === ply)
  if (!move) return null
  return { from: move.uci.slice(0, 2), to: move.uci.slice(2, 4) }
}

export default function App() {
  const [gameId, setGameId] = useState<string | null>(gameIdFromUrl)
  const [busy, setBusy] = useState(false)
  const [speedMs, setSpeedMs] = useState(800)
  const [chaos, setChaos] = useState(false)
  const [commentary, setCommentary] = useState(false)
  const [soundOn, setSoundOn] = useState(true)
  const [mode, setMode] = useState<string | null>(null)
  const [requestBudget, setRequestBudget] = useState(250)
  const [historyOpen, setHistoryOpen] = useState(false)

  // Scrubber: null = follow live; a ply number = frozen at that position.
  const [viewPly, setViewPly] = useState<number | null>(null)

  const [models, setModels] = useState<FreeModel[]>([])
  const [whiteModel, setWhiteModel] = useState('')
  const [blackModel, setBlackModel] = useState('')
  const [analystModel, setAnalystModel] = useState('')

  const store = useGameStore()
  const playSound = useSounds(soundOn)

  // Config + model list on load.
  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then((h) => {
        setMode(h.mode)
        if (h.max_requests_per_game) setRequestBudget(h.max_requests_per_game)
        setWhiteModel((v) => v || h.models.white)
        setBlackModel((v) => v || h.models.black)
        setAnalystModel((v) => v || h.models.analyst)
      })
      .catch(() => setMode(null))

    fetchModels()
      .then((r) => setModels(r.models))
      .catch(() => setModels([]))
  }, [])

  // One stream per game id. Rehydrates over REST on every (re)connect.
  useEffect(() => {
    if (!gameId) return
    setViewPly(null) // a fresh game follows live
    const dispose = streamGame(gameId, {
      onEvent: (event) => useGameStore.getState().applyEvent(event),
      onHydrate: (game) => useGameStore.getState().hydrate(game),
      onStatus: (status) => useGameStore.getState().setConnection(status),
      getSince: () => useGameStore.getState().lastSeq,
    })
    return dispose
  }, [gameId])

  // Sound effects, driven off the growing move list. A single new ply is a live
  // move (play it); a jump of many plies is a rehydration (stay silent).
  const soundedPlyRef = useRef(0)
  const gameOverSoundedRef = useRef<string | null>(null)
  useEffect(() => {
    const last = store.moves.at(-1)
    if (!last) {
      soundedPlyRef.current = 0
      return
    }
    if (last.ply === soundedPlyRef.current + 1) {
      if (last.forfeited) playSound('forfeit')
      else if (last.isCheck) playSound('check')
      else if (last.isCapture) playSound('capture')
      else playSound('move')
    }
    soundedPlyRef.current = last.ply
  }, [store.moves, playSound])

  useEffect(() => {
    if (store.status === 'finished' && gameId && gameOverSoundedRef.current !== gameId) {
      gameOverSoundedRef.current = gameId
      playSound('gameover')
    }
  }, [store.status, gameId, playSound])

  const openGame = useCallback((id: string) => {
    useGameStore.getState().reset()
    putGameIdInUrl(id)
    setGameId(id)
  }, [])

  const handleNewGame = useCallback(async () => {
    setBusy(true)
    try {
      const created = await createGame({
        whiteModel,
        blackModel,
        analystModel,
        moveDelayMs: speedMs,
        illegalRate: chaos ? 0.6 : 0,
        forfeitRate: chaos ? 0.15 : 0,
        commentaryEveryNMoves: commentary ? 6 : 0,
      })
      useGameStore.getState().startNewGame(created.game_id)
      putGameIdInUrl(created.game_id)
      setGameId(created.game_id)
    } catch (error) {
      console.error('failed to start game', error)
      useGameStore.getState().pushToast(`Could not start a game: ${(error as Error).message}`, 'error')
    } finally {
      setBusy(false)
    }
  }, [whiteModel, blackModel, analystModel, speedMs, chaos, commentary])

  const handleAbort = useCallback(async () => {
    if (gameId) await abortGame(gameId)
  }, [gameId])

  const running = store.status === 'in_progress' || store.status === 'pending'
  const gameOver = store.status === 'finished' || store.status === 'aborted'

  // What the board shows: live latest, or a scrubbed-to position.
  const displayFen = viewPly === null ? store.fen : fenAtPly(store.moves, viewPly)
  const displayLastMove =
    viewPly === null ? lastMoveSquares(store.moves) : squaresAtPly(store.moves, viewPly)

  const nextToMove: Color = store.moves.length % 2 === 0 ? 'white' : 'black'
  const whiteLast = useMemo(() => store.moves.filter((m) => m.color === 'white').at(-1) ?? null, [store.moves])
  const blackLast = useMemo(() => store.moves.filter((m) => m.color === 'black').at(-1) ?? null, [store.moves])

  return (
    <div className="min-h-full bg-zinc-950 text-zinc-100">
      <header className="border-b border-zinc-800 px-4 py-3 sm:px-6">
        <div className="mb-3 flex items-baseline gap-3">
          <h1 className="text-lg font-semibold tracking-tight">♟ LLM Chess Arena</h1>
          <p className="hidden text-sm text-zinc-500 sm:block">Two LLMs play. A third one judges.</p>
        </div>
        <GameControls
          onNewGame={handleNewGame}
          onAbort={handleAbort}
          busy={busy}
          running={running}
          speedMs={speedMs}
          onSpeedChange={setSpeedMs}
          chaos={chaos}
          onChaosChange={setChaos}
          commentary={commentary}
          onCommentaryChange={setCommentary}
          soundOn={soundOn}
          onSoundChange={setSoundOn}
          onOpenHistory={() => setHistoryOpen(true)}
          models={models}
          whiteModel={whiteModel}
          blackModel={blackModel}
          analystModel={analystModel}
          onWhiteModel={setWhiteModel}
          onBlackModel={setBlackModel}
          onAnalystModel={setAnalystModel}
          requestsUsed={store.requestsUsed}
          requestBudget={requestBudget}
          connection={store.connection}
          mode={mode}
        />
      </header>

      <main className="mx-auto grid max-w-6xl items-start gap-4 p-4 lg:grid-cols-[16rem_minmax(0,1fr)_18rem] lg:gap-6 lg:p-6">
        {/* Left column: agent panels. On mobile they move below the board. */}
        <div className="order-2 flex flex-col gap-3 lg:order-1">
          <AgentPanel
            color="white"
            model={store.whiteModel || whiteModel}
            thinking={store.thinking === 'white'}
            illegal={store.illegalCounts.white}
            forfeits={store.forfeitCounts.white}
            lastMove={whiteLast}
            active={Boolean(gameId)}
            toMove={running && nextToMove === 'white'}
          />
          <AgentPanel
            color="black"
            model={store.blackModel || blackModel}
            thinking={store.thinking === 'black'}
            illegal={store.illegalCounts.black}
            forfeits={store.forfeitCounts.black}
            lastMove={blackLast}
            active={Boolean(gameId)}
            toMove={running && nextToMove === 'black'}
          />
        </div>

        {/* Centre: board + scrubber + result banner. Board first on mobile. */}
        <section className="order-1 mx-auto w-full max-w-[34rem] lg:order-2">
          <Board fen={displayFen} lastMove={displayLastMove} animationMs={Math.min(speedMs / 2, 300)} />

          <PlaybackControls
            totalPlies={store.moves.length}
            viewPly={viewPly}
            onSelect={setViewPly}
            gameOver={gameOver}
          />

          {store.status === 'finished' && (
            <div className="mt-3 rounded-lg border border-emerald-800 bg-emerald-950/40 p-3 text-center">
              <p className="text-lg font-semibold">
                {store.result}
                {store.winner ? ` — ${store.winner} wins` : ' — draw'}
              </p>
              <p className="text-sm text-zinc-400">by {store.termination}</p>
            </div>
          )}

          {store.status === 'aborted' && (
            <div className="mt-3 rounded-lg border border-zinc-700 bg-zinc-900 p-3 text-center text-sm text-zinc-400">
              Game aborted.
            </div>
          )}
        </section>

        {/* Right column: move list + analyst. */}
        <aside className="order-3 flex flex-col gap-4">
          <div className="h-64 lg:h-72">
            <MoveList moves={store.moves} viewPly={viewPly} onSelectPly={setViewPly} />
          </div>

          {gameId ? (
            <AnalystPanel
              comments={store.comments}
              verdict={store.verdict}
              rateLimit={store.rateLimit}
              status={store.status}
              whiteModel={store.whiteModel || whiteModel}
              blackModel={store.blackModel || blackModel}
            />
          ) : (
            <p className="rounded-md border border-dashed border-zinc-800 p-4 text-sm text-zinc-500">
              Press <strong className="text-zinc-300">New Game</strong> to watch two agents play.
              {mode === 'mock' && ' No API calls are made in mock mode.'}
            </p>
          )}
        </aside>
      </main>

      <GameHistory
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onOpenGame={openGame}
        currentGameId={gameId}
      />

      <Toasts />
    </div>
  )
}

function Toasts() {
  const toasts = useGameStore((s) => s.toasts)
  const dismiss = useGameStore((s) => s.dismissToast)

  useEffect(() => {
    if (toasts.length === 0) return
    const timer = window.setTimeout(() => dismiss(toasts[0].id), 4000)
    return () => window.clearTimeout(timer)
  }, [toasts, dismiss])

  return (
    <div className="fixed right-4 bottom-4 z-30 flex flex-col gap-2">
      {toasts.slice(-3).map((toast) => (
        <button
          key={toast.id}
          onClick={() => dismiss(toast.id)}
          className={`max-w-sm rounded-md border px-3 py-2 text-left text-sm ${
            toast.tone === 'error'
              ? 'border-red-800 bg-red-950 text-red-200'
              : 'border-amber-800 bg-amber-950 text-amber-200'
          }`}
        >
          {toast.message}
        </button>
      ))}
    </div>
  )
}

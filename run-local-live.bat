@echo off
REM Launches the LLM Chess Arena locally in LIVE mode (real OpenRouter calls).
REM Requires OPENROUTER_API_KEY in backend\.env. Your account's per-minute rate
REM limit will throttle hard and the commentator will mostly fall back to the
REM (now punchy) template lines. Keep games short.
REM
REM TIP: before a live game, lower game.max_moves in config.yaml (e.g. 8) so a
REM full 120-ply game doesn't run for hours / exhaust your daily quota.

start "LLM Chess - Backend (LIVE)" cmd /k "cd /d %~dp0backend && set MODE=live&& .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
start "LLM Chess - Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo LIVE mode. Two windows are opening. The top-right badge in the app will read
echo LIVE. Expect "rate limited - waiting Ns" pauses. Open the Vite URL, tick
echo Commentary, and click New Game. Use Abort to stop.
echo.
pause

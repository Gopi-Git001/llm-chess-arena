@echo off
REM Launches the LLM Chess Arena locally in MOCK mode (no API calls, free).
REM Opens two persistent windows: backend (FastAPI) and frontend (Vite).
REM Double-click this file, then open the http://localhost:<port>/ the Vite
REM window prints (usually 5173).

start "LLM Chess - Backend (mock)" cmd /k "cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
start "LLM Chess - Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo Two windows are opening: Backend and Frontend.
echo When the Frontend window says "Local: http://localhost:5173/" (or similar),
echo open that URL in your browser, tick the Commentary box, and click New Game.
echo.
pause

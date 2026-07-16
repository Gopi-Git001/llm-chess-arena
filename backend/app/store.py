"""GameStore — SQLite persistence for games, moves, events and verdicts.

Deliberately plain `sqlite3`: writes here are sub-millisecond and a game makes
a few hundred of them, so the simplicity is worth more than async plumbing.
Every illegal attempt is persisted, not just counted — the Analyst reads them
and the UI shows them (PLAN.md §13).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine import MoveRecord
from app.events import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id            TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    mode          TEXT NOT NULL,
    white_model   TEXT NOT NULL,
    black_model   TEXT NOT NULL,
    analyst_model TEXT,
    status        TEXT NOT NULL,      -- pending | in_progress | finished | aborted | error
    result        TEXT,               -- 1-0 | 0-1 | 1/2-1/2
    termination   TEXT,
    final_fen     TEXT,
    pgn           TEXT,
    requests_used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS moves (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply            INTEGER NOT NULL,
    move_number    INTEGER NOT NULL,
    color          TEXT NOT NULL,
    uci            TEXT NOT NULL,
    san            TEXT NOT NULL,
    fen_after      TEXT NOT NULL,
    reasoning      TEXT,
    attempts       INTEGER NOT NULL DEFAULT 1,
    forfeited      INTEGER NOT NULL DEFAULT 0,
    is_check       INTEGER NOT NULL DEFAULT 0,
    is_capture     INTEGER NOT NULL DEFAULT 0,
    captured_piece TEXT,
    created_at     REAL NOT NULL,
    UNIQUE (game_id, ply)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    seq     INTEGER NOT NULL,
    type    TEXT NOT NULL,
    ts      REAL NOT NULL,
    payload TEXT NOT NULL,           -- JSON
    UNIQUE (game_id, seq)
);

CREATE TABLE IF NOT EXISTS verdicts (
    game_id    TEXT PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    payload    TEXT NOT NULL         -- JSON
);

CREATE INDEX IF NOT EXISTS idx_moves_game  ON moves (game_id, ply);
CREATE INDEX IF NOT EXISTS idx_events_game ON events (game_id, seq);
CREATE INDEX IF NOT EXISTS idx_games_created ON games (created_at DESC);
"""

# ARENA_DB_PATH lets the DB live on a mounted volume (Docker) without shadowing
# the code directory; defaults to backend/arena.db for local dev.
DEFAULT_DB_PATH = Path(
    os.environ.get("ARENA_DB_PATH", Path(__file__).resolve().parents[1] / "arena.db")
)


@dataclass
class GameRow:
    id: str
    white_model: str
    black_model: str
    analyst_model: str | None
    mode: str
    status: str


class GameStore:
    """Owns the SQLite connection. Pass `:memory:` in tests."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        # Create the parent directory for a file-backed DB (e.g. a Docker volume
        # path that doesn't exist yet). sqlite won't do this itself.
        if self.db_path != ":memory:":
            parent = Path(self.db_path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI may touch this from a worker thread.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- games ------------------------------------------------------------

    def create_game(
        self,
        white_model: str,
        black_model: str,
        analyst_model: str | None,
        mode: str,
        game_id: str | None = None,
    ) -> str:
        game_id = game_id or uuid.uuid4().hex[:12]
        now = time.time()
        self._conn.execute(
            """INSERT INTO games (id, created_at, updated_at, mode, white_model,
                                  black_model, analyst_model, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (game_id, now, now, mode, white_model, black_model, analyst_model),
        )
        self._conn.commit()
        return game_id

    def set_status(self, game_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE games SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), game_id),
        )
        self._conn.commit()

    def finish_game(
        self,
        game_id: str,
        result: str,
        termination: str | None,
        final_fen: str,
        pgn: str,
        requests_used: int = 0,
        status: str = "finished",
    ) -> None:
        self._conn.execute(
            """UPDATE games
                  SET status = ?, result = ?, termination = ?, final_fen = ?,
                      pgn = ?, requests_used = ?, updated_at = ?
                WHERE id = ?""",
            (status, result, termination, final_fen, pgn, requests_used, time.time(), game_id),
        )
        self._conn.commit()

    def set_requests_used(self, game_id: str, requests_used: int) -> None:
        """Update the request tally. The analyst runs after finish_game, so its
        calls need to be folded in afterward or the total under-reports."""
        self._conn.execute(
            "UPDATE games SET requests_used = ?, updated_at = ? WHERE id = ?",
            (requests_used, time.time(), game_id),
        )
        self._conn.commit()

    def get_game(self, game_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        return dict(row) if row else None

    def list_games(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM games ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- moves ------------------------------------------------------------

    def add_move(
        self,
        game_id: str,
        record: MoveRecord,
        reasoning: str | None = None,
        attempts: int = 1,
        forfeited: bool = False,
    ) -> None:
        self._conn.execute(
            """INSERT INTO moves (game_id, ply, move_number, color, uci, san,
                                  fen_after, reasoning, attempts, forfeited,
                                  is_check, is_capture, captured_piece, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                game_id,
                record.ply,
                record.move_number,
                record.color,
                record.uci,
                record.san,
                record.fen_after,
                reasoning,
                attempts,
                int(forfeited),
                int(record.is_check),
                int(record.is_capture),
                record.captured_piece,
                time.time(),
            ),
        )
        self._conn.commit()

    def get_moves(self, game_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM moves WHERE game_id = ? ORDER BY ply", (game_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- events -----------------------------------------------------------

    def add_event(self, event: Event) -> None:
        self._conn.execute(
            "INSERT INTO events (game_id, seq, type, ts, payload) VALUES (?,?,?,?,?)",
            (event.game_id, event.seq, event.type.value, event.ts, json.dumps(event.data)),
        )
        self._conn.commit()

    def get_events(self, game_id: str) -> list[dict[str, Any]]:
        """Events in the same shape the WebSocket sends them.

        The DB column is `payload`, but callers get `data` — REST rehydration
        and the WS stream must be byte-for-byte interchangeable, or the client
        needs two parsers for one event and one of them will rot.
        """
        rows = self._conn.execute(
            "SELECT game_id, seq, type, ts, payload FROM events WHERE game_id = ? ORDER BY seq",
            (game_id,),
        ).fetchall()
        return [
            {
                "type": r["type"],
                "game_id": r["game_id"],
                "seq": r["seq"],
                "ts": r["ts"],
                "data": json.loads(r["payload"]),
            }
            for r in rows
        ]

    # --- verdicts ---------------------------------------------------------

    def save_verdict(self, game_id: str, verdict: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO verdicts (game_id, created_at, payload) VALUES (?,?,?)
               ON CONFLICT(game_id) DO UPDATE SET payload = excluded.payload,
                                                  created_at = excluded.created_at""",
            (game_id, time.time(), json.dumps(verdict)),
        )
        self._conn.commit()

    def get_verdict(self, game_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT payload FROM verdicts WHERE game_id = ?", (game_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    # --- rehydration ------------------------------------------------------

    def get_full_game(self, game_id: str) -> dict[str, Any] | None:
        """Everything the frontend needs to restore state after a reload (§9)."""
        game = self.get_game(game_id)
        if game is None:
            return None
        return {
            **game,
            "moves": self.get_moves(game_id),
            "events": self.get_events(game_id),
            "verdict": self.get_verdict(game_id),
        }

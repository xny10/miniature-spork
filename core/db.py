from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# If a Railway Volume is attached, Railway exposes its absolute mount path.
# DATA_DIR can still override this explicitly. Locally we keep ./data.
RAILWAY_VOLUME_MOUNT_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
DEFAULT_DATA_DIR = (
    Path(RAILWAY_VOLUME_MOUNT_PATH)
    if RAILWAY_VOLUME_MOUNT_PATH
    else BASE_DIR / "data"
)
DATA_DIR = Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
DB_PATH = Path(os.getenv("DATABASE_PATH", str(DATA_DIR / "app.db"))).expanduser()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def conn():
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init_db():
    with conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS kv (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        c.commit()


def db_health() -> dict:
    with conn() as c:
        row = c.execute("SELECT 1 AS ok").fetchone()
    return {
        "ok": bool(row and row["ok"] == 1),
        "path": str(DB_PATH),
        "persistentVolume": bool(RAILWAY_VOLUME_MOUNT_PATH),
    }


def set_json(key: str, value):
    payload = json.dumps(value, ensure_ascii=False)
    with conn() as c:
        c.execute(
            """
            INSERT INTO kv(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET
              value=excluded.value,
              updated_at=excluded.updated_at
            """,
            (key, payload, utcnow_iso()),
        )
        c.commit()


def get_json(key: str, default=None):
    with conn() as c:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return default


def delete_key(key: str):
    with conn() as c:
        c.execute("DELETE FROM kv WHERE key=?", (key,))
        c.commit()

import sqlite3
from contextlib import contextmanager
from typing import Generator

from app.shelfa.config import DB_PATH

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname TEXT NOT NULL DEFAULT 'anonymous',
                device_id TEXT NOT NULL DEFAULT 'anonymous',
                client_name TEXT NOT NULL DEFAULT 'anonymous',
                kind TEXT NOT NULL,
                text_content TEXT,
                stored_name TEXT,
                original_name TEXT,
                mime_type TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL UNIQUE,
                token TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_unread (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                peer_nickname TEXT NOT NULL,
                unread_count INTEGER NOT NULL DEFAULT 0,
                UNIQUE(device_id, peer_nickname)
            )
            """
        )
        conn.commit()

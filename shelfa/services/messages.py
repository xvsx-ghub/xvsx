from typing import Optional

import sqlite3

from shelfa.database import get_db
from shelfa.services.storage import utc_now_iso


def normalize_device_id(raw: Optional[str]) -> str:
    if raw is None:
        return "anonymous"
    return raw.strip()[:64] or "anonymous"


def row_to_message(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "nickname": row["nickname"],
        "group_flag": row["group_flag"],
        "client_name": row["client_name"],
        "device_id": row["device_id"],
        "kind": row["kind"],
        "text": row["text_content"],
        "file_url": f"/files/{row['stored_name']}" if row["stored_name"] else None,
        "original_name": row["original_name"],
        "mime_type": row["mime_type"],
        "created_at": row["created_at"],
    }


def message_exists(message_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
    return row is not None


def list_thread_messages(
    nickname: str,
    client_name: str,
    after_id: Optional[int],
    limit: int,
) -> list[sqlite3.Row]:
    query = """
        SELECT *
        FROM messages
        WHERE (
            (nickname = ? AND client_name = ?)
            OR
            (nickname = ? AND client_name = ?)
        )
    """
    params: list = [nickname, client_name, client_name, nickname]

    if after_id is not None:
        query += " AND id > ?"
        params.append(after_id)

    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        return conn.execute(query, params).fetchall()


def list_user_messages(
    nickname: str,
    after_id: Optional[int],
    limit: int,
) -> list[sqlite3.Row]:
    query = """
        SELECT *
        FROM messages
        WHERE (nickname = ? OR client_name = ?)
    """
    params: list = [nickname, nickname]

    if after_id is not None:
        query += " AND id > ?"
        params.append(after_id)

    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        return conn.execute(query, params).fetchall()


def insert_text_message(
    nickname: str,
    group_flag: str,
    device_id: str,
    client_name: str,
    text: str,
) -> sqlite3.Row:
    created = utc_now_iso()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages (
                nickname, group_flag, device_id, client_name, kind, text_content,
                stored_name, original_name, mime_type, created_at
            )
            VALUES (?, ?, ?, ?, 'text', ?, NULL, NULL, NULL, ?)
            """,
            (nickname, group_flag, device_id, client_name, text, created),
        )
        conn.commit()
        new_id = cur.lastrowid
    with get_db() as conn:
        return conn.execute("SELECT * FROM messages WHERE id = ?", (new_id,)).fetchone()


def insert_file_message(
    nickname: str,
    group_flag: str,    
    device_id: str,
    client_name: str,
    kind: str,
    stored_name: str,
    original_name: str,
    mime_type: str,
) -> sqlite3.Row:
    created = utc_now_iso()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages (
                nickname, group_flag, device_id, client_name, kind, text_content,
                stored_name, original_name, mime_type, created_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                nickname,
                group_flag, 
                device_id,
                client_name,
                kind,
                stored_name,
                original_name,
                mime_type,
                created,
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
    with get_db() as conn:
        return conn.execute("SELECT * FROM messages WHERE id = ?", (new_id,)).fetchone()


def upsert_device_token(device_id: str, token: str) -> None:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM device_tokens WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE device_tokens SET token = ? WHERE id = ?",
                (token, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO device_tokens (device_id, token) VALUES (?, ?)",
                (device_id, token),
            )
        conn.commit()


def get_token_by_nickname(nickname: str) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT token FROM device_tokens
            WHERE device_id IN (
                SELECT device_id
                FROM messages
                WHERE nickname = ?
                ORDER BY id DESC
                LIMIT 1
            )
            """,
            (nickname,),
        ).fetchone()

    return row["token"] if row else None


def get_device_id_by_nickname(nickname: str) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT device_id FROM messages
            WHERE nickname = ?
            ORDER BY id DESC LIMIT 1
            """,
            (nickname,),
        ).fetchone()
    return row["device_id"] if row else None


def increment_unread_for_device(device_id: str, peer_nickname: str) -> None:
    if device_id == "anonymous":
        return
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, unread_count FROM device_unread WHERE device_id = ? AND peer_nickname = ?",
            (device_id, peer_nickname),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE device_unread SET unread_count = ? WHERE id = ?",
                (int(existing["unread_count"]) + 1, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO device_unread (device_id, peer_nickname, unread_count) VALUES (?, ?, 1)",
                (device_id, peer_nickname),
            )
        conn.commit()


def clear_unread_for_thread(device_id: str, peer_nickname: Optional[str] = None) -> None:
    with get_db() as conn:
        if peer_nickname is not None:
            conn.execute(
                "UPDATE device_unread SET unread_count = 0 WHERE device_id = ? AND peer_nickname = ?",
                (device_id, peer_nickname),
            )
        else:
            conn.execute(
                "UPDATE device_unread SET unread_count = 0 WHERE device_id = ?",
                (device_id,),
            )
        conn.commit()


def total_unread_for_device(device_id: str) -> int:
    if device_id == "anonymous":
        return 0
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(unread_count), 0) AS unread_total FROM device_unread WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    if not row:
        return 0
    return int(row["unread_total"] or 0)

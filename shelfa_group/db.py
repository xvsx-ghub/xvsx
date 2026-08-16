import sqlite3
from contextlib import contextmanager
from typing import Generator
from typing import Optional

from shelfa_group.config import DB_PATH

UNKNOWN_USER_TYPE = 0
PRIVATE_USER_TYPE = 1
GROUP_USER_TYPE = 2



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


def init() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_type INTEGER NOT NULL DEFAULT 0,
                message_content TEXT NOT NULL,
                sender_nickname TEXT NOT NULL,
                recipient_nickname TEXT NOT NULL,
                timestamp_unix INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_nickname TEXT NOT NULL UNIQUE,
                user_type INTEGER NOT NULL DEFAULT 0,
                device_id TEXT NOT NULL,
                fcm_token TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_nickname TEXT NOT NULL,
                recipient_nickname TEXT NOT NULL,
                unread_messages_count INTEGER NOT NULL DEFAULT 0,
                UNIQUE(sender_nickname, recipient_nickname)
            )
            """
        )
        conn.commit()


##########################################################################################       
# message table functions


def set_message(
    sender_nickname: str,
    recipient_nickname: str,
    message_content: str,
    message_type: int,
    timestamp_unix: int
) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO message (message_type, message_content, sender_nickname, recipient_nickname, timestamp_unix)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_type, message_content, sender_nickname, recipient_nickname, timestamp_unix),
        )
        conn.commit()
        message_id = cursor.lastrowid
        return {
            "id": message_id,
            "message_type": message_type,
            "message_content": message_content,
            "sender_nickname": sender_nickname,
            "recipient_nickname": recipient_nickname,
            "timestamp_unix": timestamp_unix,
        }
             
        
def get_message_list(
    sender_nickname: Optional[str] = None,
    recipient_nickname: Optional[str] = None,
    after_id: Optional[int] = None,
    limit: Optional[int] = None
) -> list[dict]:
    with get_db() as conn:
        query = "SELECT * FROM message WHERE 1=1"
        params = []
        
        if sender_nickname is None and recipient_nickname is not None:
            query += " AND (sender_nickname = ? OR recipient_nickname = ?)"
            params.extend([recipient_nickname, recipient_nickname])
        
        if sender_nickname is not None and recipient_nickname is not None:
            query += " AND ((sender_nickname = ? AND recipient_nickname = ?) OR (sender_nickname = ? AND recipient_nickname = ?))"
            params.extend([sender_nickname, recipient_nickname, recipient_nickname, sender_nickname])
            
        if after_id is not None:
            query += " AND id > ?"
            params.append(after_id)
            
        query += " ORDER BY id ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    
def get_sender_nickname_list(recipient_nickname: str) -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT sender_nickname FROM message
            WHERE recipient_nickname = ?
            """,
            (recipient_nickname,),
        ).fetchall()
        return [row["sender_nickname"] for row in rows]


def row_to_message(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "message_type": row["message_type"],
        "message_content": row["message_content"],
        "sender_nickname": row["sender_nickname"],
        "recipient_nickname": row["recipient_nickname"],
        "timestamp_unix": row["timestamp_unix"],
    }


##########################################################################################
# user table functions

def set_user(
    user_nickname: str,
    user_type: int,
    device_id: str,
    fcm_token: str,
) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO user (user_nickname, user_type, device_id, fcm_token)
            VALUES (?, ?, ?, ?)
            """,
            (user_nickname, user_type, device_id, fcm_token),
        )
        conn.commit()
        user_id = cursor.lastrowid
        return {
            "id": user_id,
            "user_nickname": user_nickname,
            "user_type": user_type,
            "device_id": device_id,
            "fcm_token": fcm_token,
        }

def get_user_type(user_nickname: str) -> Optional[int]:
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT user_type FROM user WHERE user_nickname = ?",
            (user_nickname,),
        )
        row = cursor.fetchone()
        if row:
            return row["user_type"]
        return None


def get_user_by_nickname(user_nickname: str) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM user WHERE user_nickname = ?",
            (user_nickname,),
        ).fetchone()
        if row:
            return row
        return None


def get_fcm_token_by_nickname(nickname: str) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT fcm_token FROM user
            WHERE user_nickname = ?
            """,
            (nickname,),
        ).fetchone()
        if row:
            return row["fcm_token"]
        return None
        

##########################################################################################       
# contact_registry table functions


def get_unread_messages_count(sender_nickname: str, recipient_nickname: str) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT unread_messages_count FROM contact_registry WHERE sender_nickname = ? AND recipient_nickname = ?",
            (sender_nickname, recipient_nickname),
        )
        row = cursor.fetchone()
        if row:
            return row["unread_messages_count"]
        return 0

def increment_unread_messages_count(sender_nickname: str, recipient_nickname: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO contact_registry (
                sender_nickname,
                recipient_nickname,
                unread_messages_count
            )
            VALUES (?, ?, 1)
            ON CONFLICT(sender_nickname, recipient_nickname)
            DO UPDATE SET
                unread_messages_count = unread_messages_count + 1
            """,
            (sender_nickname, recipient_nickname),
        )
        conn.commit()
        
def clear_unread_messages_count(sender_nickname: str, recipient_nickname: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE contact_registry
            SET unread_messages_count = 0
            WHERE sender_nickname = ? AND recipient_nickname = ?
            """,
            (sender_nickname, recipient_nickname),
        )
        conn.commit()
  
  
def get_contact_list(user_nickname: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM contact_registry
            WHERE sender_nickname = ?
               OR recipient_nickname = ?
            ORDER BY id
            """,
            (user_nickname, user_nickname),
        ).fetchall()

    return [dict(row) for row in rows]  
  
  
def get_contact_nickname_list(user_nickname: str) -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT recipient_nickname AS nickname
            FROM contact_registry
            WHERE sender_nickname = ?

            UNION

            SELECT sender_nickname AS nickname
            FROM contact_registry
            WHERE recipient_nickname = ?

            ORDER BY nickname
            """,
            (user_nickname, user_nickname),
        ).fetchall()

    return [row["nickname"] for row in rows]


def create_contact(
    sender_nickname: str,
    recipient_nickname: str,
) -> dict:
    with get_db() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO contact_registry (
                    sender_nickname,
                    recipient_nickname
                )
                VALUES (?, ?)
                """,
                (sender_nickname, recipient_nickname),
            )
            conn.commit()

        except sqlite3.IntegrityError:
            raise ValueError("Contact already exists")

        row = conn.execute(
            """
            SELECT *
            FROM contact_registry
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()

    return dict(row)


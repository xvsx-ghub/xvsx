import json
import sqlite3
from contextlib import contextmanager
from math import ceil
import time
from typing import Generator
from typing import Optional

from shelfa_group.config import DB_PATH

UNKNOWN_USER_TYPE = 0
PRIVATE_USER_TYPE = 1
GROUP_USER_TYPE = 2

FREE_ACCOUNT_MODE = "free"
LIMITED_ACCOUNT_MODE = "limited"
PREMIUM_ACCOUNT_MODE = "premium"
ACCOUNT_MODES = (
    FREE_ACCOUNT_MODE,
    LIMITED_ACCOUNT_MODE,
    PREMIUM_ACCOUNT_MODE,
)
DEFAULT_ACCOUNT_MODE_LIMITS = {
    FREE_ACCOUNT_MODE: {
        "max_contacts": 5,
        "max_message_history_per_contact": 50,
    },
    LIMITED_ACCOUNT_MODE: {
        "max_contacts": 50,
        "max_message_history_per_contact": 500,
    },
}


class ContactLimitExceeded(ValueError):
    pass



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
                fcm_token TEXT NOT NULL,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                account_mode TEXT NOT NULL DEFAULT 'free'
                    CHECK (account_mode IN ('free', 'limited', 'premium'))
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS call_session (
                call_id TEXT PRIMARY KEY,
                room_name TEXT NOT NULL UNIQUE,
                caller_nickname TEXT NOT NULL,
                callee_nickname TEXT NOT NULL,
                call_type TEXT NOT NULL CHECK (call_type IN ('audio', 'video')),
                status TEXT NOT NULL CHECK (
                    status IN (
                        'ringing', 'active', 'ended', 'declined',
                        'cancelled', 'busy', 'expired', 'failed'
                    )
                ),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS call_session_participants_status
            ON call_session (caller_nickname, callee_nickname, status)
            """
        )
        user_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(user)")
        }
        if "is_blocked" not in user_columns:
            conn.execute(
                "ALTER TABLE user ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0"
            )
        if "account_mode" not in user_columns:
            conn.execute(
                """
                ALTER TABLE user
                ADD COLUMN account_mode TEXT NOT NULL DEFAULT 'free'
                    CHECK (account_mode IN ('free', 'limited', 'premium'))
                """
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_mode_limits (
                account_mode TEXT PRIMARY KEY
                    CHECK (account_mode IN ('free', 'limited')),
                max_contacts INTEGER NOT NULL CHECK (max_contacts >= 0),
                max_message_history_per_contact INTEGER NOT NULL
                    CHECK (max_message_history_per_contact >= 0)
            )
            """
        )
        for account_mode, limits in DEFAULT_ACCOUNT_MODE_LIMITS.items():
            conn.execute(
                """
                INSERT OR IGNORE INTO account_mode_limits (
                    account_mode, max_contacts, max_message_history_per_contact
                )
                VALUES (?, ?, ?)
                """,
                (
                    account_mode,
                    limits["max_contacts"],
                    limits["max_message_history_per_contact"],
                ),
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                entity TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_login_attempt (
                client_key TEXT PRIMARY KEY,
                attempt_count INTEGER NOT NULL,
                window_started_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_session (
                session_id TEXT PRIMARY KEY,
                actor TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                revoked_at INTEGER
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
    timestamp_unix: int,
    contact_pairs: Optional[list[tuple[str, str]]] = None,
) -> dict:
    with get_db() as conn:
        if contact_pairs is not None:
            conn.execute("BEGIN IMMEDIATE")
            for contact_sender, contact_recipient in contact_pairs:
                _check_contact_capacity(
                    conn,
                    contact_sender,
                    contact_recipient,
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO contact_registry (
                        sender_nickname, recipient_nickname, unread_messages_count
                    )
                    VALUES (?, ?, 0)
                    """,
                    (contact_sender, contact_recipient),
                )
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
    limit: Optional[int] = None,
    history_limit: Optional[int] = None,
) -> list[dict]:
    with get_db() as conn:
        query = "SELECT * FROM message WHERE 1=1"
        params = []
        
        if sender_nickname is None and recipient_nickname is None:
            pass         
                    
        elif sender_nickname is None and recipient_nickname is not None:
            query += " AND (sender_nickname = ? OR recipient_nickname = ?)"
            params.extend([recipient_nickname, recipient_nickname])
        
        elif sender_nickname is not None and recipient_nickname is None:
            query += " AND (sender_nickname = ? OR recipient_nickname = ?)"
            params.extend([sender_nickname, sender_nickname])
        
        elif sender_nickname is not None and recipient_nickname is not None:
            query += " AND ((sender_nickname = ? AND recipient_nickname = ?) OR (sender_nickname = ? AND recipient_nickname = ?))"
            params.extend([sender_nickname, recipient_nickname, recipient_nickname, sender_nickname])
            
        if history_limit is not None:
            partition_user = sender_nickname or recipient_nickname
            query = (
                "SELECT * FROM ("
                "SELECT message.*, ROW_NUMBER() OVER ("
                "PARTITION BY CASE WHEN sender_nickname = ? "
                "THEN recipient_nickname ELSE sender_nickname END "
                "ORDER BY id DESC) AS history_rank "
                f"{query[query.index('FROM message'):]}"
                ") WHERE history_rank <= ?"
            )
            params.insert(0, partition_user)
            params.append(history_limit)

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
            SELECT DISTINCT m.sender_nickname
            FROM message m
            LEFT JOIN user u ON u.user_nickname = m.sender_nickname
            WHERE m.recipient_nickname = ?
              AND COALESCE(u.is_blocked, 0) = 0
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
            "account_mode": FREE_ACCOUNT_MODE,
        }


def claim_admin_created_user(
    user_nickname: str,
    device_id: str,
    fcm_token: str,
) -> Optional[dict]:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE user
            SET device_id = ?, fcm_token = ?
            WHERE user_nickname = ? AND device_id = '' AND is_blocked = 0
            """,
            (device_id, fcm_token, user_nickname),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM user WHERE user_nickname = ?",
            (user_nickname,),
        ).fetchone()
        return dict(row) if row else None


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


def get_account_mode(user_nickname: str) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT account_mode FROM user WHERE user_nickname = ?",
            (user_nickname,),
        ).fetchone()
        return row["account_mode"] if row else FREE_ACCOUNT_MODE


def get_message_history_limit(user_nickname: str) -> Optional[int]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT u.account_mode, l.max_message_history_per_contact
            FROM user u
            LEFT JOIN account_mode_limits l ON l.account_mode = u.account_mode
            WHERE u.user_nickname = ?
            """,
            (user_nickname,),
        ).fetchone()
        if row is None:
            return DEFAULT_ACCOUNT_MODE_LIMITS[FREE_ACCOUNT_MODE][
                "max_message_history_per_contact"
            ]
        if row["account_mode"] == PREMIUM_ACCOUNT_MODE:
            return None
        return row["max_message_history_per_contact"]


def get_contact_limit(user_nickname: str) -> Optional[int]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT u.account_mode, l.max_contacts
            FROM user u
            LEFT JOIN account_mode_limits l ON l.account_mode = u.account_mode
            WHERE u.user_nickname = ?
            """,
            (user_nickname,),
        ).fetchone()
        if row is None:
            return DEFAULT_ACCOUNT_MODE_LIMITS[FREE_ACCOUNT_MODE]["max_contacts"]
        if row["account_mode"] == PREMIUM_ACCOUNT_MODE:
            return None
        return row["max_contacts"]


def create_call_session(
    call_id: str,
    room_name: str,
    caller_nickname: str,
    callee_nickname: str,
    call_type: str,
    now: int,
) -> bool:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE call_session
            SET status = 'expired', updated_at = ?
            WHERE (status = 'ringing' AND created_at <= ?)
               OR (status = 'active' AND updated_at <= ?)
            """,
            (now, now - 45, now - 4 * 60 * 60),
        )
        existing = conn.execute(
            """
            SELECT 1 FROM call_session
            WHERE status IN ('ringing', 'active')
              AND (
                  caller_nickname IN (?, ?)
                  OR callee_nickname IN (?, ?)
              )
            LIMIT 1
            """,
            (
                caller_nickname,
                callee_nickname,
                caller_nickname,
                callee_nickname,
            ),
        ).fetchone()
        if existing:
            conn.commit()
            return False
        conn.execute(
            """
            INSERT INTO call_session (
                call_id, room_name, caller_nickname, callee_nickname,
                call_type, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'ringing', ?, ?)
            """,
            (
                call_id,
                room_name,
                caller_nickname,
                callee_nickname,
                call_type,
                now,
                now,
            ),
        )
        conn.commit()
        return True


def transition_call_session(
    call_id: str,
    actor_nickname: str,
    other_nickname: str,
    action: str,
    call_type: str,
    now: int,
) -> tuple[dict | None, bool]:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE call_session
            SET status = 'expired', updated_at = ?
            WHERE (status = 'ringing' AND created_at <= ?)
               OR (status = 'active' AND updated_at <= ?)
            """,
            (now, now - 45, now - 4 * 60 * 60),
        )
        row = conn.execute(
            "SELECT * FROM call_session WHERE call_id = ?",
            (call_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None, False
        if row["call_type"] != call_type:
            conn.commit()
            raise ValueError("Call type does not match")

        actor_is_caller = (
            actor_nickname == row["caller_nickname"]
            and other_nickname == row["callee_nickname"]
        )
        actor_is_callee = (
            actor_nickname == row["callee_nickname"]
            and other_nickname == row["caller_nickname"]
        )
        if not actor_is_caller and not actor_is_callee:
            conn.commit()
            raise PermissionError("User is not a participant in this call")
        if action in {"join", "decline", "busy"} and not actor_is_callee:
            conn.commit()
            raise PermissionError("Only the invited user can perform this action")
        if action == "cancel" and not actor_is_caller:
            conn.commit()
            raise PermissionError("Only the caller can cancel this call")

        status = row["status"]
        if status not in {"ringing", "active"}:
            conn.commit()
            return dict(row), False

        if action == "join":
            if status != "ringing":
                conn.commit()
                raise ValueError("Call is no longer waiting for an answer")
            next_status = "active"
        elif action == "decline":
            next_status = "declined"
        elif action == "cancel":
            next_status = "cancelled"
        elif action == "busy":
            next_status = "busy"
        else:
            next_status = "ended"

        conn.execute(
            """
            UPDATE call_session SET status = ?, updated_at = ?
            WHERE call_id = ?
            """,
            (next_status, now, call_id),
        )
        updated = conn.execute(
            "SELECT * FROM call_session WHERE call_id = ?",
            (call_id,),
        ).fetchone()
        conn.commit()
        return dict(updated), True


def get_account_mode_limits() -> dict[str, dict[str, int]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT account_mode, max_contacts, max_message_history_per_contact
            FROM account_mode_limits
            ORDER BY account_mode
            """
        ).fetchall()
    return {
        row["account_mode"]: {
            "max_contacts": row["max_contacts"],
            "max_message_history_per_contact": row[
                "max_message_history_per_contact"
            ],
        }
        for row in rows
    }


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
    
    
def set_fcm_token(user_nickname: str, fcm_token: str) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE user
            SET fcm_token = ?
            WHERE user_nickname = ?
            """,
            (fcm_token, user_nickname),
        )
        conn.commit()
        
        row = conn.execute(
            "SELECT * FROM user WHERE user_nickname = ?",
            (user_nickname,),
        ).fetchone()
        
        return dict(row) if row else None


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
            INSERT INTO contact_registry (
                sender_nickname,
                recipient_nickname,
                unread_messages_count
            )
            VALUES (?, ?, 0)
            ON CONFLICT(sender_nickname, recipient_nickname)
            DO UPDATE SET
                unread_messages_count = 0
            """,
            (sender_nickname, recipient_nickname),
        )
        conn.commit()
  
  
def get_contact_list(
    user_nickname: str,
    max_contacts: Optional[int] = None,
) -> list[dict]:
    with get_db() as conn:
        query = """
            SELECT *
            FROM contact_registry
            WHERE sender_nickname = ?
               OR recipient_nickname = ?
            ORDER BY id
            """
        if max_contacts is None:
            rows = conn.execute(query, (user_nickname, user_nickname)).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT c.*
                FROM contact_registry c
                WHERE (c.sender_nickname = ? OR c.recipient_nickname = ?)
                  AND CASE WHEN c.sender_nickname = ? THEN c.recipient_nickname
                           ELSE c.sender_nickname END IN (
                    SELECT counterpart
                    FROM (
                        SELECT CASE WHEN sender_nickname = ? THEN recipient_nickname
                                    ELSE sender_nickname END AS counterpart,
                               MAX(id) AS latest_id
                        FROM contact_registry
                        WHERE sender_nickname = ? OR recipient_nickname = ?
                        GROUP BY counterpart
                        ORDER BY latest_id DESC
                        LIMIT ?
                    )
                  )
                ORDER BY c.id
                """,
                (
                    user_nickname,
                    user_nickname,
                    user_nickname,
                    user_nickname,
                    user_nickname,
                    user_nickname,
                    max_contacts,
                ),
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


def are_contacts(first_nickname: str, second_nickname: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM contact_registry
            WHERE (sender_nickname = ? AND recipient_nickname = ?)
               OR (sender_nickname = ? AND recipient_nickname = ?)
            LIMIT 1
            """,
            (
                first_nickname,
                second_nickname,
                second_nickname,
                first_nickname,
            ),
        ).fetchone()
    return row is not None


def _check_contact_capacity(
    conn: sqlite3.Connection,
    sender_nickname: str,
    recipient_nickname: str,
) -> None:
    existing = conn.execute(
        """
        SELECT 1 FROM contact_registry
        WHERE (sender_nickname = ? AND recipient_nickname = ?)
           OR (sender_nickname = ? AND recipient_nickname = ?)
        """,
        (
            sender_nickname,
            recipient_nickname,
            recipient_nickname,
            sender_nickname,
        ),
    ).fetchone()
    if existing:
        return

    for nickname in (sender_nickname, recipient_nickname):
        user = conn.execute(
            "SELECT account_mode FROM user WHERE user_nickname = ?",
            (nickname,),
        ).fetchone()
        account_mode = user["account_mode"] if user else FREE_ACCOUNT_MODE
        if account_mode == PREMIUM_ACCOUNT_MODE:
            continue
        limits = conn.execute(
            """
            SELECT max_contacts FROM account_mode_limits
            WHERE account_mode = ?
            """,
            (account_mode,),
        ).fetchone()
        if limits is None:
            raise RuntimeError(f"Missing usage limits for account mode: {account_mode}")
        contact_count = conn.execute(
            """
            SELECT COUNT(DISTINCT CASE
                WHEN sender_nickname = ? THEN recipient_nickname
                ELSE sender_nickname
            END)
            FROM contact_registry
            WHERE sender_nickname = ? OR recipient_nickname = ?
            """,
            (nickname, nickname, nickname),
        ).fetchone()[0]
        if contact_count >= limits["max_contacts"]:
            raise ContactLimitExceeded(
                f"Contact limit reached for user '{nickname}'"
            )


def create_contact(
    sender_nickname: str,
    recipient_nickname: str,
) -> dict:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT 1 FROM contact_registry
            WHERE sender_nickname = ? AND recipient_nickname = ?
            """,
            (sender_nickname, recipient_nickname),
        ).fetchone()
        if existing:
            raise ValueError("Contact already exists")

        _check_contact_capacity(conn, sender_nickname, recipient_nickname)

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


def _admin_audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    entity: str,
    entity_id: str,
    details: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO admin_audit (
            actor, action, entity, entity_id, details_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            actor,
            action,
            entity,
            entity_id,
            json.dumps(details or {}, separators=(",", ":"), sort_keys=True),
            int(time.time()),
        ),
    )


def _admin_page(
    conn: sqlite3.Connection,
    count_sql: str,
    rows_sql: str,
    params: list,
    page: int,
    page_size: int,
) -> dict:
    total = conn.execute(count_sql, params).fetchone()[0]
    rows = conn.execute(
        rows_sql,
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": ceil(total / page_size) if total else 0,
    }


def _admin_like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def admin_dashboard_stats() -> dict:
    with get_db() as conn:
        counts = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM user WHERE user_type = ?) AS total_users,
                (SELECT COUNT(*) FROM user WHERE user_type = ?) AS groups,
                (SELECT COUNT(*) FROM user WHERE is_blocked = 1) AS blocked_users,
                (SELECT COUNT(*) FROM message) AS total_messages,
                (SELECT COUNT(*) FROM contact_registry) AS total_contacts,
                (SELECT COALESCE(SUM(unread_messages_count), 0)
                 FROM contact_registry) AS unread_messages,
                (SELECT COUNT(*) FROM message
                 WHERE timestamp_unix >= ?) AS messages_today
            """,
            (
                PRIVATE_USER_TYPE,
                GROUP_USER_TYPE,
                int(time.time()) - 86400,
            ),
        ).fetchone()
        return {
            **dict(counts),
            "database_size_bytes": (
                conn.execute("PRAGMA page_count").fetchone()[0]
                * conn.execute("PRAGMA page_size").fetchone()[0]
            ),
            "sqlite_version": sqlite3.sqlite_version,
        }


def admin_database_info() -> dict:
    with get_db() as conn:
        tables = []
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ):
            table_name = row["name"]
            quoted_table_name = table_name.replace('"', '""')
            columns = [
                column["name"]
                for column in conn.execute(
                    f'PRAGMA table_info("{quoted_table_name}")'
                )
            ]
            row_count = conn.execute(
                f'SELECT COUNT(*) FROM "{quoted_table_name}"'
            ).fetchone()[0]
            tables.append(
                {"name": table_name, "columns": columns, "rows": row_count}
            )
        return {
            "path": str(DB_PATH.name),
            "size_bytes": (
                conn.execute("PRAGMA page_count").fetchone()[0]
                * conn.execute("PRAGMA page_size").fetchone()[0]
            ),
            "sqlite_version": sqlite3.sqlite_version,
            "tables": tables,
        }


def admin_list_users(
    page: int,
    page_size: int,
    search: str | None = None,
    user_type: int | None = None,
    status: str | None = None,
) -> dict:
    conditions = ["1 = 1"]
    params: list = []
    if search:
        term = _admin_like(search)
        conditions.append("(u.user_nickname LIKE ? ESCAPE '\\')")
        params.append(term)
    if user_type is not None:
        conditions.append("u.user_type = ?")
        params.append(user_type)
    if status == "blocked":
        conditions.append("u.is_blocked = 1")
    elif status == "active":
        conditions.append("u.is_blocked = 0")
    where = " AND ".join(conditions)
    with get_db() as conn:
        return _admin_page(
            conn,
            f"SELECT COUNT(*) FROM user u WHERE {where}",
            f"""
            SELECT u.id, u.user_nickname, u.user_type, u.is_blocked,
                   u.account_mode,
                   (SELECT COUNT(*) FROM message m
                    WHERE m.sender_nickname = u.user_nickname
                       OR m.recipient_nickname = u.user_nickname) AS message_count,
                   (SELECT MAX(timestamp_unix) FROM message m
                    WHERE m.sender_nickname = u.user_nickname
                       OR m.recipient_nickname = u.user_nickname) AS last_active
            FROM user u WHERE {where}
            ORDER BY u.id DESC LIMIT ? OFFSET ?
            """,
            params,
            page,
            page_size,
        )


def admin_create_user(
    actor: str,
    nickname: str,
    user_type: int,
    account_mode: str = FREE_ACCOUNT_MODE,
) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO user (
                user_nickname, user_type, device_id, fcm_token, account_mode
            )
            VALUES (?, ?, '', '', ?)
            """,
            (nickname, user_type, account_mode),
        )
        user_id = cursor.lastrowid
        _admin_audit(
            conn,
            actor,
            "user.created",
            "user",
            str(user_id),
            {
                "nickname": nickname,
                "user_type": user_type,
                "account_mode": account_mode,
            },
        )
        conn.commit()
        return {
            "id": user_id,
            "user_nickname": nickname,
            "user_type": user_type,
            "is_blocked": 0,
            "account_mode": account_mode,
            "message_count": 0,
            "last_active": None,
        }


def admin_update_user(
    actor: str,
    user_id: int,
    user_type: int | None,
    is_blocked: bool | None,
    account_mode: str | None = None,
) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, user_nickname, user_type, is_blocked, account_mode
            FROM user WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        updated_type = user_type if user_type is not None else row["user_type"]
        updated_blocked = (
            int(is_blocked) if is_blocked is not None else row["is_blocked"]
        )
        updated_mode = account_mode if account_mode is not None else row["account_mode"]
        conn.execute(
            """
            UPDATE user
            SET user_type = ?, is_blocked = ?, account_mode = ?
            WHERE id = ?
            """,
            (updated_type, updated_blocked, updated_mode, user_id),
        )
        _admin_audit(
            conn,
            actor,
            "user.updated",
            "user",
            str(user_id),
            {
                "nickname": row["user_nickname"],
                "user_type": updated_type,
                "is_blocked": bool(updated_blocked),
                "account_mode": updated_mode,
            },
        )
        conn.commit()
        return {
            "id": user_id,
            "user_nickname": row["user_nickname"],
            "user_type": updated_type,
            "is_blocked": updated_blocked,
            "account_mode": updated_mode,
        }


def admin_update_account_mode_limits(actor: str, limits: dict) -> dict:
    with get_db() as conn:
        for account_mode in (FREE_ACCOUNT_MODE, LIMITED_ACCOUNT_MODE):
            mode_limits = limits[account_mode]
            conn.execute(
                """
                UPDATE account_mode_limits
                SET max_contacts = ?, max_message_history_per_contact = ?
                WHERE account_mode = ?
                """,
                (
                    mode_limits["max_contacts"],
                    mode_limits["max_message_history_per_contact"],
                    account_mode,
                ),
            )
            _admin_audit(
                conn,
                actor,
                "usage_limits.updated",
                "account_mode",
                account_mode,
                mode_limits,
            )
        conn.commit()
    return get_account_mode_limits()


def admin_delete_user(actor: str, user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_nickname FROM user WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        nickname = row["user_nickname"]
        message_count = conn.execute(
            """
            SELECT COUNT(*) FROM message
            WHERE sender_nickname = ? OR recipient_nickname = ?
            """,
            (nickname, nickname),
        ).fetchone()[0]
        contact_count = conn.execute(
            """
            SELECT COUNT(*) FROM contact_registry
            WHERE sender_nickname = ? OR recipient_nickname = ?
            """,
            (nickname, nickname),
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM message WHERE sender_nickname = ? OR recipient_nickname = ?",
            (nickname, nickname),
        )
        conn.execute(
            """
            DELETE FROM contact_registry
            WHERE sender_nickname = ? OR recipient_nickname = ?
            """,
            (nickname, nickname),
        )
        conn.execute("DELETE FROM user WHERE id = ?", (user_id,))
        _admin_audit(
            conn,
            actor,
            "user.deleted",
            "user",
            str(user_id),
            {
                "nickname": nickname,
                "messages_deleted": message_count,
                "contacts_deleted": contact_count,
            },
        )
        conn.commit()
        return {
            "id": user_id,
            "user_nickname": nickname,
            "messages_deleted": message_count,
            "contacts_deleted": contact_count,
        }


def admin_list_messages(
    page: int,
    page_size: int,
    search: str | None = None,
    message_type: int | None = None,
) -> dict:
    conditions = ["1 = 1"]
    params: list = []
    if search:
        term = _admin_like(search)
        conditions.append(
            "(message_content LIKE ? ESCAPE '\\' "
            "OR sender_nickname LIKE ? ESCAPE '\\' "
            "OR recipient_nickname LIKE ? ESCAPE '\\')"
        )
        params.extend([term, term, term])
    if message_type is not None:
        conditions.append("message_type = ?")
        params.append(message_type)
    where = " AND ".join(conditions)
    with get_db() as conn:
        return _admin_page(
            conn,
            f"SELECT COUNT(*) FROM message WHERE {where}",
            f"""
            SELECT id, message_type, message_content, sender_nickname,
                   recipient_nickname, timestamp_unix
            FROM message WHERE {where}
            ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            params,
            page,
            page_size,
        )


def admin_delete_message(actor: str, message_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, sender_nickname, recipient_nickname
            FROM message WHERE id = ?
            """,
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM message WHERE id = ?", (message_id,))
        _admin_audit(
            conn,
            actor,
            "message.deleted",
            "message",
            str(message_id),
            {
                "sender_nickname": row["sender_nickname"],
                "recipient_nickname": row["recipient_nickname"],
            },
        )
        conn.commit()
        return dict(row)


def admin_list_contacts(
    page: int,
    page_size: int,
    search: str | None = None,
) -> dict:
    conditions = ["1 = 1"]
    params: list = []
    if search:
        term = _admin_like(search)
        conditions.append(
            "(sender_nickname LIKE ? ESCAPE '\\' "
            "OR recipient_nickname LIKE ? ESCAPE '\\')"
        )
        params.extend([term, term])
    where = " AND ".join(conditions)
    with get_db() as conn:
        return _admin_page(
            conn,
            f"SELECT COUNT(*) FROM contact_registry WHERE {where}",
            f"""
            SELECT id, sender_nickname, recipient_nickname,
                   unread_messages_count
            FROM contact_registry WHERE {where}
            ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            params,
            page,
            page_size,
        )


def admin_delete_contact(actor: str, contact_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, sender_nickname, recipient_nickname
            FROM contact_registry WHERE id = ?
            """,
            (contact_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM contact_registry WHERE id = ?", (contact_id,))
        _admin_audit(
            conn,
            actor,
            "contact.deleted",
            "contact",
            str(contact_id),
            {
                "sender_nickname": row["sender_nickname"],
                "recipient_nickname": row["recipient_nickname"],
            },
        )
        conn.commit()
        return dict(row)


def admin_list_audit(page: int, page_size: int) -> dict:
    with get_db() as conn:
        result = _admin_page(
            conn,
            "SELECT COUNT(*) FROM admin_audit",
            """
            SELECT id, actor, action, entity, entity_id, details_json, created_at
            FROM admin_audit ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            [],
            page,
            page_size,
        )
        for item in result["items"]:
            item["details"] = json.loads(item.pop("details_json"))
        return result


def admin_auth_is_limited(client_key: str, now: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT attempt_count, window_started_at
            FROM admin_login_attempt WHERE client_key = ?
            """,
            (client_key,),
        ).fetchone()
        return bool(
            row
            and now - row["window_started_at"] < 900
            and row["attempt_count"] >= 5
        )


def admin_record_failed_login(client_key: str, now: int) -> None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT attempt_count, window_started_at
            FROM admin_login_attempt WHERE client_key = ?
            """,
            (client_key,),
        ).fetchone()
        if row and now - row["window_started_at"] < 900:
            conn.execute(
                """
                UPDATE admin_login_attempt
                SET attempt_count = attempt_count + 1
                WHERE client_key = ?
                """,
                (client_key,),
            )
        else:
            conn.execute(
                """
                INSERT INTO admin_login_attempt (
                    client_key, attempt_count, window_started_at
                )
                VALUES (?, 1, ?)
                ON CONFLICT(client_key) DO UPDATE SET
                    attempt_count = excluded.attempt_count,
                    window_started_at = excluded.window_started_at
                """,
                (client_key, now),
            )
        _admin_audit(
            conn,
            "unknown",
            "admin.login_failed",
            "admin",
            client_key[:12],
        )
        conn.commit()


def admin_create_session(
    actor: str,
    client_key: str,
    session_id: str,
    expires_at: int,
) -> None:
    with get_db() as conn:
        now = int(time.time())
        conn.execute("DELETE FROM admin_session WHERE expires_at <= ?", (now,))
        conn.execute(
            "DELETE FROM admin_login_attempt WHERE client_key = ?",
            (client_key,),
        )
        conn.execute(
            """
            INSERT INTO admin_session (session_id, actor, expires_at)
            VALUES (?, ?, ?)
            """,
            (session_id, actor, expires_at),
        )
        _admin_audit(conn, actor, "admin.login", "admin", actor)
        conn.commit()


def admin_session_is_active(session_id: str, now: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM admin_session
            WHERE session_id = ? AND expires_at > ? AND revoked_at IS NULL
            """,
            (session_id, now),
        ).fetchone()
        return row is not None


def admin_record_logout(actor: str, session_id: str) -> None:
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE admin_session
            SET revoked_at = ?
            WHERE session_id = ? AND revoked_at IS NULL
            """,
            (int(time.time()), session_id),
        )
        if cursor.rowcount:
            _admin_audit(conn, actor, "admin.logout", "admin", actor)
        conn.commit()

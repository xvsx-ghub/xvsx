import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.shelfa.config import DATA_DIR, DB_PATH, UPLOAD_DIR, MAX_DATA_BYTES
from app.shelfa.database import get_db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def total_data_bytes() -> int:
    total = 0
    if DB_PATH.is_file():
        total += DB_PATH.stat().st_size
    if UPLOAD_DIR.is_dir():
        for path in UPLOAD_DIR.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    return total


def _unlink_stored_file(stored_name: Optional[str]) -> None:
    if not stored_name:
        return
    path = UPLOAD_DIR / Path(stored_name).name
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def _delete_oldest_message_row() -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, stored_name FROM messages ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if not row:
            return False
        _unlink_stored_file(row["stored_name"])
        conn.execute("DELETE FROM messages WHERE id = ?", (row["id"],))
        conn.commit()
    return True


def _delete_oldest_orphan_file() -> bool:
    if not UPLOAD_DIR.is_dir():
        return False
    files = [
        path
        for path in UPLOAD_DIR.iterdir()
        if path.is_file() and path.name != ".gitkeep"
    ]
    if not files:
        return False
    oldest = min(files, key=lambda file: file.stat().st_mtime_ns)
    try:
        oldest.unlink()
    except OSError:
        return False
    return True


def _vacuum_db() -> None:
    with get_db() as conn:
        conn.execute("VACUUM")
        conn.commit()


def enforce_data_limit() -> None:
    if not DATA_DIR.is_dir():
        return
    pruned = False
    for _ in range(1_000_000):
        if total_data_bytes() <= MAX_DATA_BYTES:
            break
        if _delete_oldest_message_row():
            pruned = True
        elif _delete_oldest_orphan_file():
            pruned = True
        else:
            break
    if pruned:
        _vacuum_db()


def save_upload(content: bytes, original_name: str, ext: str) -> str:
    stored = f"{uuid.uuid4().hex}.{ext}"
    path = UPLOAD_DIR / stored
    path.write_bytes(content)
    return stored

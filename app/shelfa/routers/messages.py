import sys
from pathlib import Path
from typing import Optional
import logging

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.shelfa.config import ALLOWED_EXTENSIONS, EXTENSION_TO_KIND, MAX_UPLOAD_BYTES
from app.shelfa.schemas import MessageResponse, MessagesListResponse, PostMessageRequest
from app.shelfa.services.messages import (
    clear_unread_for_thread,
    get_device_id_by_nickname,
    get_token_by_nickname,
    increment_unread_for_device,
    insert_file_message,
    insert_text_message,
    list_thread_messages,
    list_user_messages,
    message_exists,
    normalize_device_id,
    row_to_message,
    total_unread_for_device,
)
from app.shelfa.services.notifications import is_firebase_ready, send_alert_notification
from app.shelfa.services.storage import enforce_data_limit, save_upload

router = APIRouter(prefix="/api", tags=["messages"])
logger = logging.getLogger(__name__)


def _notify_recipient(
    sender_nickname: str,
    sender_device_id: str,
    client_name: str,
    body: str,
) -> None:
    badge_count = 1
    target_device_id = get_device_id_by_nickname(nickname=client_name)
    if target_device_id and target_device_id != sender_device_id:
        increment_unread_for_device(
            device_id=target_device_id,
            peer_nickname=sender_nickname,
        )
        badge_count = total_unread_for_device(device_id=target_device_id)

    token = get_token_by_nickname(nickname=client_name)
    if token is not None and is_firebase_ready():
        send_alert_notification(
            token=token,
            title=sender_nickname,
            body=body,
            data={"type": "chat_message"},
            badge=badge_count,
        )
        logger.info(
            f"Notification sent. Token: {token}, Firebase ready: {is_firebase_ready()}"
        )
    else:
        logger.warning(
            f"Notification not sent. Token: {token}, Firebase ready: {is_firebase_ready()}"
        )


@router.get("/messages", response_model=MessagesListResponse)
def list_messages(
    after_id: Optional[int] = Query(None),
    nickname: Optional[str] = Query(None),
    client_name: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=200),
):
    normalized_device_id = normalize_device_id(device_id)

    if after_id is not None and not message_exists(after_id):
        after_id = None

    if nickname is not None and client_name is not None:
        rows = list_thread_messages(nickname, client_name, after_id, limit)
        clear_unread_for_thread(
            device_id=normalized_device_id,
            peer_nickname=client_name,
        )
        return {
            "messages": [row_to_message(row) for row in rows],
            "unread_count": total_unread_for_device(normalized_device_id),
        }

    if nickname is not None and client_name is None:
        rows = list_user_messages(nickname, after_id, limit)
        clear_unread_for_thread(device_id=normalized_device_id, peer_nickname=None)
        return {
            "messages": [row_to_message(row) for row in rows],
            "unread_count": total_unread_for_device(normalized_device_id),
        }

    return {
        "messages": [],
        "unread_count": total_unread_for_device(normalized_device_id),
    }


@router.post("/messages", response_model=MessageResponse, status_code=201)
def post_message(body: PostMessageRequest):
    nickname = (body.nickname or "anonymous").strip()[:64] or "anonymous"
    device_id = (body.device_id or "anonymous").strip()[:64] or "anonymous"
    client_name = (body.client_name or "anonymous").strip()[:64] or "anonymous"
    text = (body.text or "").strip()

    if not text:
        raise HTTPException(status_code=400, detail="text required")

    row = insert_text_message(
        nickname=nickname,
        device_id=device_id,
        client_name=client_name,
        text=text,
    )
    enforce_data_limit()
    _notify_recipient(nickname, device_id, client_name, text[:100])
    return row_to_message(row)


@router.post("/upload", response_model=MessageResponse, status_code=201)
async def upload_file(
    nickname: str = Form("guest"),
    device_id: str = Form("anonymous"),
    client_name: str = Form("anonymous"),
    file: UploadFile = File(...),
):
    nickname = (nickname or "guest").strip()[:64] or "guest"
    device_id = (device_id or "anonymous").strip()[:64] or "anonymous"
    client_name = (client_name or "anonymous").strip()[:64] or "anonymous"

    if not file.filename:
        raise HTTPException(status_code=400, detail="file required")

    original = file.filename
    ext = Path(original).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="file type not allowed")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large")

    kind = EXTENSION_TO_KIND[ext]
    stored = save_upload(content, original, ext)
    mime = file.content_type or "application/octet-stream"

    row = insert_file_message(
        nickname=nickname,
        device_id=device_id,
        client_name=client_name,
        kind=kind,
        stored_name=stored,
        original_name=original,
        mime_type=mime,
    )
    enforce_data_limit()
    _notify_recipient(nickname, device_id, client_name, "Attachment")
    return row_to_message(row)

import logging
import time
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Query
from pydantic import BaseModel

import shelfa_group.db as db
import shelfa_group.fcm as fcm

api_router = APIRouter(prefix="/shelfa", tags=["api_router"])

logger = logging.getLogger("shelfa")


class MessageResponse(BaseModel):
    id: int
    message_type: int
    message_content: str
    sender_nickname: str
    recipient_nickname: str
    timestamp_unix: int


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    unread_count: int


class UserResponse(BaseModel):
    id: int
    user_nickname: str
    user_type: int
    device_id: str
    fcm_token: str
    
    
class PostUserRequest(BaseModel):
    user_nickname: str = "",
    user_type: str = "",
    device_id: str = "",
    fcm_token: str = ""
    
    
class PostMessageRequest(BaseModel):
    sender_nickname: str = "",
    recipient_nickname: str = "",
    message_content: str = "",
    message_type: str = ""
    
    
class StatusResponse(BaseModel):
    status: bool = True


##########################################################################################
# message

@api_router.get("/message_list", response_model=MessageListResponse)
def get_message_list(
    sender_nickname: str = Query(...),
    recipient_nickname: str = Query(...),
    after_id: str | None = Query(None),
):
    logger.info(
        "Listing messages. sender=%s recipient=%s after_id=%s",
        sender_nickname,
        recipient_nickname,
        after_id,
    )

    recipient_type = db.get_user_type(recipient_nickname)

    match recipient_type:
        case db.GROUP_USER_TYPE:
            sender_filter = None
        case db.PRIVATE_USER_TYPE:
            sender_filter = sender_nickname
        case _:
            raise HTTPException(status_code=404, detail="Recipient not found")

    rows = db.get_message_list(
        sender_filter,
        recipient_nickname,
        to_int(after_id),
    )

    unread_count = db.get_unread_messages_count(
        sender_nickname,
        recipient_nickname,
    )
    
    db.clear_unread_messages_count(
        sender_nickname,
        recipient_nickname,
    )

    return MessageListResponse(
        messages=[db.row_to_message(row) for row in rows],
        unread_count=unread_count,
    )

@api_router.post("/message", response_model=MessageResponse)
def post_message(body: PostMessageRequest):
    logger.info(
        "Posting message. sender=%s recipient=%s type=%s",
        body.sender_nickname,
        body.recipient_nickname,
        body.message_type,
    )

    recipient_type = db.get_user_type(body.recipient_nickname)
    if recipient_type is None:
        raise HTTPException(status_code=404, detail="Recipient not found")

    row = db.set_message(
        body.sender_nickname,
        body.recipient_nickname,
        body.message_content,
        body.message_type,
        int(time.time()),
    )

    if recipient_type == db.GROUP_USER_TYPE:
        for nickname in db.get_sender_nickname_list(body.recipient_nickname):
            if nickname == body.sender_nickname:
                continue

            db.increment_unread_messages_count(
                nickname,
                body.recipient_nickname,
            )
            
            fcm.send_alert_notification(
                token=db.get_fcm_token_by_nickname(nickname),
                title=f"{body.sender_nickname} in group {body.recipient_nickname}",
                body=body.message_content,
                badge=db.get_unread_messages_count(
                    nickname,
                    body.recipient_nickname,
                ),
            )

    elif recipient_type == db.PRIVATE_USER_TYPE:
        db.increment_unread_messages_count(
            body.sender_nickname,
            body.recipient_nickname,
        )
        
        fcm.send_alert_notification(
            token=db.get_fcm_token_by_nickname(body.recipient_nickname),
            title=body.sender_nickname,
            body=body.message_content,
            badge=db.get_unread_messages_count(
                body.sender_nickname,
                body.recipient_nickname,
            ),
        )   
    else:
        raise HTTPException(status_code=404, detail="Unknown recipient type")

    return MessageResponse(
        id=row["id"],
        message_type=row["message_type"],
        message_content=row["message_content"],
        sender_nickname=row["sender_nickname"],
        recipient_nickname=row["recipient_nickname"],
        timestamp_unix=row["timestamp_unix"],
    )


##########################################################################################
# user


@api_router.get("/user_existence", response_model=StatusResponse)
def get_user_existence(
    user_nickname: str = Query(...),
):
    logger.info("Checking user existence. nickname=%s", user_nickname)

    row = db.get_user_by_nickname(user_nickname)

    if row is None:
        return StatusResponse(status=False)
    return StatusResponse(status=True)


@api_router.post("/user", response_model=UserResponse)
def post_user(body: PostUserRequest):
    logger.info(
        "Creating/updating user. nickname=%s type=%s device_id=%s",
        body.user_nickname,
        body.user_type,
        body.device_id,
    )
    
    row = db.get_user_by_nickname(body.user_nickname)
    
    if row:
        if row["device_id"] == body.device_id:
            logger.info("User valid.")
            return UserResponse(
                id=row["id"],
                user_nickname=row["user_nickname"],
                user_type=row["user_type"],
                device_id=row["device_id"],
                fcm_token=row["fcm_token"],
            )
        logger.info("User invalid.")
        raise HTTPException(
            status_code=409,
            detail="User is already registered on another device.",
        )
    logger.info("User creating.")
    row = db.set_user(
        body.user_nickname,
        body.user_type,
        body.device_id,
        body.fcm_token,
    )
    
    return UserResponse(
        id=row["id"],
        user_nickname=row["user_nickname"],
        user_type=row["user_type"],
        device_id=row["device_id"],
        fcm_token=row["fcm_token"],
    )


def to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


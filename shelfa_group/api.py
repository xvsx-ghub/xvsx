import logging
from pathlib import Path
import time
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from shelfa_group.config import MAX_UPLOAD_BYTES, UPLOAD_DIR
import shelfa_group.db as db
import shelfa_group.fcm as fcm
from shelfa_group.storage import upload

api_router = APIRouter(prefix="/shelfa", tags=["api_router"])

logger = logging.getLogger("shelfa")


class MessageResponse(BaseModel):
    id: int
    message_type: int
    message_content: str
    sender_nickname: str
    sender_user_type: int = 0
    recipient_nickname: str
    recipient_user_type: int = 0
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
    user_nickname: str = ""
    user_type: int = 0
    device_id: str = ""
    fcm_token: str = ""
    
    
class PostMessageRequest(BaseModel):
    sender_nickname: str = ""
    recipient_nickname: str = ""
    message_content: str = ""
    message_type: int = 0
    
    
class FilePathResponse(BaseModel):
    file_path: str = ""
    
    
class StatusResponse(BaseModel):
    status: bool = True
    

class ContactResponse(BaseModel):
    id: int
    sender_nickname: str
    sender_user_type: int = 0
    recipient_nickname: str
    recipient_user_type: int = 0
    unread_messages_count: int
    

class ContactListResponse(BaseModel):
    contact_list: list[ContactResponse]
    
  
class GetContactRequest(BaseModel):
    user_nickname: str
    

class PostContactRequest(BaseModel):
    sender_nickname: str
    recipient_nickname: str
    

##########################################################################################
# message

@api_router.get("/message_list", response_model=MessageListResponse)
def get_message_list(
    sender_nickname: str = Query(None),
    recipient_nickname: str = Query(None),
    reset_unread_count_status: str = Query(None),
    after_id: str | None = Query(None),
):
    logger.info(
        "Listing messages. sender=%s recipient=%s reset_unread_count_status=%s after_id=%s",
        sender_nickname,
        recipient_nickname,
        reset_unread_count_status,
        after_id,
    )

    recipient_type = db.get_user_type(sender_nickname)

    match recipient_type:
        case db.PRIVATE_USER_TYPE:            
            m_sender_nickname = sender_nickname
            m_recipient_nickname = recipient_nickname
            
            rows = db.get_message_list(
                m_sender_nickname,
                m_recipient_nickname,
                to_int(after_id),
            )
        
            unread_count = db.get_unread_messages_count(
                sender_nickname,
                recipient_nickname,
            )
            
            if reset_unread_count_status == "1":
                db.clear_unread_messages_count(
                    sender_nickname,
                    recipient_nickname,
                )
            messages = []
            for row in rows:
                msg = db.row_to_message(row)
                sender_type = db.get_user_type(msg.get("sender_nickname"))
                recipient_type = db.get_user_type(msg.get("recipient_nickname"))
                msg["sender_user_type"] = sender_type if sender_type is not None else 0
                msg["recipient_user_type"] = recipient_type if recipient_type is not None else 0
                messages.append(msg)
        
            return MessageListResponse(
                messages=messages,
                unread_count=unread_count,
            )
            
        case db.GROUP_USER_TYPE:
            m_sender_nickname = None
            m_recipient_nickname = sender_nickname
            
            rows = db.get_message_list(
                m_sender_nickname,
                m_recipient_nickname,
                to_int(after_id),
            )
        
            unread_count = db.get_unread_messages_count(
                sender_nickname,
                recipient_nickname,
            )
            
            if reset_unread_count_status == "1":
                db.clear_unread_messages_count(
                    sender_nickname,
                    recipient_nickname,
                )
            messages = []
            for row in rows:
                msg = db.row_to_message(row)
                sender_type = db.get_user_type(msg.get("sender_nickname"))
                recipient_type = db.get_user_type(msg.get("recipient_nickname"))
                msg["sender_user_type"] = sender_type if sender_type is not None else 0
                msg["recipient_user_type"] = recipient_type if recipient_type is not None else 0
                messages.append(msg)
        
            return MessageListResponse(
                messages=messages,
                unread_count=unread_count,
            )
        case _:
            raise HTTPException(status_code=404, detail="Recipient not found")


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
        sender_user_type=(db.get_user_type(row["sender_nickname"]) or 0),
        recipient_nickname=row["recipient_nickname"],
        recipient_user_type=(db.get_user_type(row["recipient_nickname"]) or 0),
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



##########################################################################################
# contact


@api_router.post("/contact", response_model=ContactResponse)
def create_contact(body: PostContactRequest):
    logger.info(
        "Creating contact. sender=%s recipient=%s",
        body.sender_nickname,
        body.recipient_nickname,
    )

    if body.sender_nickname == body.recipient_nickname:
        raise HTTPException(
            status_code=400,
            detail="Sender and recipient must be different",
        )

    if db.get_user_by_nickname(body.recipient_nickname) is None:
        raise HTTPException(
            status_code=404,
            detail=f"User '{body.recipient_nickname}' doesn't exist",
        )

    try:
        row = db.create_contact(
            body.sender_nickname,
            body.recipient_nickname,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
        )

    data = dict(row)
    sender_type = db.get_user_type(data.get("sender_nickname"))
    recipient_type = db.get_user_type(data.get("recipient_nickname"))
    data["sender_user_type"] = sender_type if sender_type is not None else 0
    data["recipient_user_type"] = recipient_type if recipient_type is not None else 0

    return ContactResponse(**data)


@api_router.get("/contact_list", response_model=ContactListResponse)
def get_contact_list(
    user_nickname: str = Query(...),
):
    logger.info("Contact list for %s", user_nickname)
    rows = db.get_contact_list(user_nickname)

    contact_list = []
    for row in rows:
        data = dict(row)
        # enrich with user types (default to 0 if unknown)
        sender_type = db.get_user_type(data.get("sender_nickname"))
        recipient_type = db.get_user_type(data.get("recipient_nickname"))
        data["sender_user_type"] = sender_type if sender_type is not None else 0
        data["recipient_user_type"] = recipient_type if recipient_type is not None else 0
        contact_list.append(ContactResponse(**data))

    return ContactListResponse(
        contact_list=contact_list,
    )

    
##########################################################################################
# storage


@api_router.post("/file", response_model=FilePathResponse)
async def post_file(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large")

    file_path = upload(content, file.filename)
    
    get_file_request_name = "shelfa/file"

    return FilePathResponse(
        file_path=f"{get_file_request_name}/{file_path}"
    )
    

@api_router.get("/file/{name}")
def get_file(name: str):    
    safe = Path(name).name
    if safe != name or ".." in name:
        raise HTTPException(status_code=400, detail="bad path")

    full = UPLOAD_DIR / safe    
    if not full.is_file():
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(full)


def to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


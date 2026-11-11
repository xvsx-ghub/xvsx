import hmac
import logging
from pathlib import Path
import time
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from shelfa_group.config import MAX_UPLOAD_BYTES, UPLOAD_DIR
from shelfa_group.calls import (
    LiveKitConfigurationError,
    create_participant_token,
    livekit_configuration,
)
import shelfa_group.db as db
import shelfa_group.fcm as fcm
from shelfa_group.storage import upload

api_router = APIRouter(prefix="/shelfa", tags=["api_router"])

logger = logging.getLogger("shelfa")


def ensure_user_is_not_blocked(user_nickname: str | None) -> None:
    if not user_nickname:
        return
    user = db.get_user_by_nickname(user_nickname)
    if user is not None and user["is_blocked"]:
        raise HTTPException(status_code=403, detail="User account is blocked")


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
    account_mode: str = db.FREE_ACCOUNT_MODE
    
    
class PostUserRequest(BaseModel):
    user_nickname: str = ""
    user_type: int = 0
    device_id: str = ""
    fcm_token: str = ""
    
    
class PostFcmTokenRequest(BaseModel):
    user_nickname: str = ""
    fcm_token: str = ""
    
    
class PostMessageRequest(BaseModel):
    sender_nickname: str = ""
    recipient_nickname: str = ""
    message_content: str = ""
    message_type: int = 0


class PostCallRequest(BaseModel):
    sender_nickname: str = Field(min_length=1, max_length=80)
    device_id: str = Field(min_length=1, max_length=256)
    recipient_nickname: str = Field(min_length=1, max_length=80)
    call_type: Literal["audio", "video"]
    action: Literal["start", "join", "end", "decline", "cancel", "busy"]
    call_id: str = Field(default="", max_length=64)

    @field_validator("sender_nickname", "device_id", "recipient_nickname")
    @classmethod
    def normalize_nickname(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Nickname cannot be empty")
        return value

    @field_validator("call_id")
    @classmethod
    def normalize_call_id(cls, value: str) -> str:
        return value.strip()


class CallResponse(BaseModel):
    call_id: str = ""
    room_name: str = ""
    livekit_url: str = ""
    url: str = ""
    token: str = ""
    call_type: str = ""
    status: str = ""
    
    
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
        "api: get_message_list(). sender=%s recipient=%s reset_unread_count_status=%s after_id=%s",
        sender_nickname,
        recipient_nickname,
        reset_unread_count_status,
        after_id,
    )

    ensure_user_is_not_blocked(sender_nickname)
    ensure_user_is_not_blocked(recipient_nickname)
    recipient_type = db.get_user_type(sender_nickname)
    history_limit = db.get_message_history_limit(sender_nickname)

    match recipient_type:
        case db.PRIVATE_USER_TYPE:            
            m_sender_nickname = sender_nickname
            m_recipient_nickname = recipient_nickname
            
            rows = db.get_message_list(
                m_sender_nickname,
                m_recipient_nickname,
                to_int(after_id),
                history_limit=history_limit,
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
                history_limit=history_limit,
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
        "api: post_message(). sender=%s recipient=%s message_type=%s message_content=%s",
        body.sender_nickname,
        body.recipient_nickname,
        body.message_type,
        body.message_content,
    )

    ensure_user_is_not_blocked(body.sender_nickname)
    ensure_user_is_not_blocked(body.recipient_nickname)
    recipient_type = db.get_user_type(body.recipient_nickname)
    
    if recipient_type is None:
        raise HTTPException(status_code=404, detail="Recipient not found")

    if recipient_type == db.GROUP_USER_TYPE:
        group_members = set(db.get_sender_nickname_list(body.recipient_nickname))
        group_members.add(body.sender_nickname)
        contact_pairs = [
            (body.recipient_nickname, nickname)
            for nickname in group_members
        ]
    else:
        contact_pairs = [(body.sender_nickname, body.recipient_nickname)]

    try:
        row = db.set_message(
            body.sender_nickname,
            body.recipient_nickname,
            body.message_content,
            body.message_type,
            int(time.time()),
            contact_pairs=contact_pairs,
        )
    except db.ContactLimitExceeded as e:
        raise HTTPException(
            status_code=403,
            detail=str(e),
        ) from e

    if recipient_type == db.GROUP_USER_TYPE:
        for nickname in db.get_sender_nickname_list(body.recipient_nickname):
                       
            db.increment_unread_messages_count(
                body.recipient_nickname,
                nickname,
            )
            
            try:
                fcm.send_alert_notification(
                    token=db.get_fcm_token_by_nickname(nickname),
                    title=f"{body.sender_nickname} in group {body.recipient_nickname}",
                    body=body.message_content,
                    data={
                        "sender_nickname": body.sender_nickname,
                        "recipient_nickname": body.recipient_nickname,
                        "message_content": body.message_content,
                        "shelfa_message_type": str(body.message_type),
                    },
                    badge=db.get_unread_messages_count(
                        body.recipient_nickname,
                        nickname,
                    ),
                )
            except Exception as e:
                logger.info(
                    "Failed to send FCM alert notification to nickname=%s: %s",
                    nickname,
                    e,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to send FCM alert notification to nickname={nickname}: {e}",
                ) from e

    elif recipient_type == db.PRIVATE_USER_TYPE:
        
        db.increment_unread_messages_count(
            body.sender_nickname,
            body.recipient_nickname,
        )
        
        try:
            fcm.send_alert_notification(
                token=db.get_fcm_token_by_nickname(body.recipient_nickname),
                title=body.sender_nickname,
                body=body.message_content,
                data={
                    "sender_nickname": body.sender_nickname,
                    "recipient_nickname": body.recipient_nickname,
                    "message_content": body.message_content,
                    "shelfa_message_type": str(body.message_type),
                },
                badge=db.get_unread_messages_count(
                    body.sender_nickname,
                    body.recipient_nickname,
                ),
            )
        except Exception as e:
            logger.info(
                "Failed to send FCM alert notification to nickname=%s: %s",
                body.recipient_nickname,
                e,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to send FCM alert notification to nickname={body.recipient_nickname}: {e}",
            ) from e        

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


@api_router.post("/call", response_model=CallResponse)
def post_call(body: PostCallRequest) -> CallResponse:
    if body.sender_nickname == body.recipient_nickname:
        raise HTTPException(
            status_code=400,
            detail="Sender and recipient must be different",
        )

    sender = db.get_user_by_nickname(body.sender_nickname)
    recipient = db.get_user_by_nickname(body.recipient_nickname)
    if sender is None or recipient is None:
        raise HTTPException(status_code=404, detail="Call participant not found")
    if not sender["device_id"] or not hmac.compare_digest(
        str(sender["device_id"]),
        body.device_id,
    ):
        raise HTTPException(status_code=403, detail="Device identity does not match")
    ensure_user_is_not_blocked(body.sender_nickname)
    ensure_user_is_not_blocked(body.recipient_nickname)

    now = int(time.time())
    if body.action == "start":
        if not db.are_contacts(body.sender_nickname, body.recipient_nickname):
            raise HTTPException(
                status_code=403,
                detail="Calls are only available between contacts",
            )
        recipient_token = str(recipient["fcm_token"]).strip()
        if not recipient_token:
            raise HTTPException(
                status_code=409,
                detail="Recipient has not registered for call notifications",
            )
        try:
            livekit_url, api_key, api_secret = livekit_configuration()
        except LiveKitConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        call_id = uuid.uuid4().hex
        room_name = f"shelfa-{call_id}"
        if not db.create_call_session(
            call_id,
            room_name,
            body.sender_nickname,
            body.recipient_nickname,
            body.call_type,
            now,
        ):
            return CallResponse(call_type=body.call_type, status="busy")

        try:
            fcm.send_alert_notification(
                token=recipient_token,
                title=f"Incoming {body.call_type} call",
                body=f"{body.sender_nickname} is calling you",
                data={
                    "type": "call",
                    "action": "invite",
                    "call_id": call_id,
                    "room_name": room_name,
                    "call_type": body.call_type,
                    "sender_nickname": body.sender_nickname,
                    "recipient_nickname": body.recipient_nickname,
                },
            )
        except Exception as error:
            logger.exception(
                "Failed to send call invite from %s to %s",
                body.sender_nickname,
                body.recipient_nickname,
            )
            db.transition_call_session(
                call_id,
                body.sender_nickname,
                body.recipient_nickname,
                "cancel",
                body.call_type,
                now,
            )
            raise HTTPException(
                status_code=502,
                detail="Unable to notify the recipient about this call",
            ) from error

        return CallResponse(
            call_id=call_id,
            room_name=room_name,
            livekit_url=livekit_url,
            token=create_participant_token(
                api_key,
                api_secret,
                room_name,
                call_id,
                body.sender_nickname,
                body.call_type,
                now,
            ),
            call_type=body.call_type,
            status="ringing",
        )

    if not body.call_id:
        raise HTTPException(status_code=422, detail="call_id is required")
    try:
        call, changed = db.transition_call_session(
            body.call_id,
            body.sender_nickname,
            body.recipient_nickname,
            body.action,
            body.call_type,
            now,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    if body.action == "join":
        try:
            livekit_url, api_key, api_secret = livekit_configuration()
        except LiveKitConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if call["status"] != "active":
            raise HTTPException(status_code=409, detail="Call is no longer available")
        return CallResponse(
            call_id=call["call_id"],
            room_name=call["room_name"],
            livekit_url=livekit_url,
            token=create_participant_token(
                api_key,
                api_secret,
                call["room_name"],
                call["call_id"],
                body.sender_nickname,
                call["call_type"],
                now,
            ),
            call_type=call["call_type"],
            status=call["status"],
        )

    if changed:
        other_nickname = (
            call["callee_nickname"]
            if body.sender_nickname == call["caller_nickname"]
            else call["caller_nickname"]
        )
        other = db.get_user_by_nickname(other_nickname)
        if other is not None:
            try:
                fcm.send_background_notification(
                    token=other["fcm_token"],
                    data={
                        "type": "call",
                        "action": body.action,
                        "call_id": call["call_id"],
                        "room_name": call["room_name"],
                        "call_type": call["call_type"],
                        "sender_nickname": body.sender_nickname,
                        "recipient_nickname": other_nickname,
                    },
                )
            except Exception as error:
                logger.exception(
                    "Failed to send call %s signal to %s",
                    body.action,
                    other_nickname,
                )
                raise HTTPException(
                    status_code=502,
                    detail="Call state was updated but the other participant "
                    "could not be notified",
                ) from error

    return CallResponse(
        call_id=call["call_id"],
        room_name=call["room_name"],
        call_type=call["call_type"],
        status=call["status"],
    )


##########################################################################################
# user


@api_router.get("/user_existence", response_model=StatusResponse)
def get_user_existence(
    user_nickname: str = Query(...),
):
    logger.info("api: get_user_existence(). nickname=%s", user_nickname)

    row = db.get_user_by_nickname(user_nickname)

    if row is None:
        return StatusResponse(status=False)
    if row["is_blocked"]:
        raise HTTPException(status_code=403, detail="User account is blocked")
    return StatusResponse(status=True)


@api_router.post("/user", response_model=UserResponse)
def post_user(body: PostUserRequest):
    logger.info("api: post_user(). nickname=%s type=%s device_id=%s", body.user_nickname, body.user_type, body.device_id)
    
    row = db.get_user_by_nickname(body.user_nickname)
    
    if row:
        if row["is_blocked"]:
            raise HTTPException(status_code=403, detail="User account is blocked")
        if not row["device_id"] and body.device_id:
            row = db.claim_admin_created_user(
                body.user_nickname,
                body.device_id,
                body.fcm_token,
            )
            if row and row["is_blocked"]:
                raise HTTPException(
                    status_code=403,
                    detail="User account is blocked",
                )
            if row and row["device_id"] == body.device_id:
                return UserResponse(
                    id=row["id"],
                    user_nickname=row["user_nickname"],
                    user_type=row["user_type"],
                    device_id=row["device_id"],
                    fcm_token=row["fcm_token"],
                    account_mode=row["account_mode"],
                )
            raise HTTPException(
                status_code=409,
                detail="User is already registered on another device.",
            )
        if row["device_id"] == body.device_id:
            if row["fcm_token"] != body.fcm_token:
                logger.info("api: post_user(). User exists with the same device_id but different fcm_token. Raising HTTPException.")
                raise HTTPException(
                    status_code=422,
                    detail="Fcm token update required.",
                )

            logger.info("api: post_user(). User already exists with the same device_id. Returning existing user.")
            return UserResponse(
                id=row["id"],
                user_nickname=row["user_nickname"],
                user_type=row["user_type"],
                device_id=row["device_id"],
                fcm_token=row["fcm_token"],
                account_mode=row["account_mode"],
            )
        else:
            logger.info("api: post_user(). User already exists with a different device_id. Raising HTTPException.")
            raise HTTPException(
                status_code=409,
                detail="User is already registered on another device.",
            )
    logger.info("api: post_user(). Creating new user with nickname=%s, type=%s, device_id=%s", body.user_nickname, body.user_type, body.device_id)
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
        account_mode=row["account_mode"],
    )


@api_router.post("/fcm_token", response_model=UserResponse)
def post_fcm_token(body: PostFcmTokenRequest):
    logger.info("api: post_fcm_token(). user_nickname=%s fcm_token=%s", body.user_nickname, body.fcm_token) 
    
    row = db.get_user_by_nickname(body.user_nickname)
    if row is None:
        logger.info("api: post_fcm_token(). User not found. nickname=%s", body.user_nickname)
        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )
    ensure_user_is_not_blocked(body.user_nickname)
    
    row = db.set_fcm_token(
        body.user_nickname,
        body.fcm_token,
    )
    if row is None:
        logger.info("api: post_fcm_token(). Failed to set FCM token for user_nickname=%s", body.user_nickname)
        raise HTTPException(
            status_code=404,
            detail="User not found.")

    return UserResponse(
        id=row["id"],
        user_nickname=row["user_nickname"],
        user_type=row["user_type"],
        device_id=row["device_id"],
        fcm_token=row["fcm_token"],
        account_mode=row["account_mode"],
    )
    
    

##########################################################################################
# contact


@api_router.post("/contact", response_model=ContactResponse)
def create_contact(body: PostContactRequest):
    logger.info("api: create_contact(). sender=%s recipient=%s", body.sender_nickname, body.recipient_nickname)

    ensure_user_is_not_blocked(body.sender_nickname)
    ensure_user_is_not_blocked(body.recipient_nickname)
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
    except db.ContactLimitExceeded as e:
        raise HTTPException(
            status_code=403,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
        )
        
    try:
        fcm.send_alert_notification(
            token=db.get_fcm_token_by_nickname(body.recipient_nickname),
            title=f"{body.sender_nickname} wants to connect with you",
            body="New contact request",
            data={
                "sender_nickname": body.sender_nickname,
                "recipient_nickname": body.recipient_nickname,
            },
            badge=db.get_unread_messages_count(
                body.sender_nickname,
                body.recipient_nickname,
            ),
        )
    except Exception as e:
        logger.exception(
            "Failed to send FCM alert notification to nickname=%s: %s",
            body.recipient_nickname,
            e,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send FCM alert notification to nickname={body.recipient_nickname}: {e}",
        ) from e
        

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
    logger.info("api: get_contact_list(). user_nickname=%s", user_nickname)
    ensure_user_is_not_blocked(user_nickname)
    rows = db.get_contact_list(
        user_nickname,
        max_contacts=db.get_contact_limit(user_nickname),
    )

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
    logger.info("api: post_file(). filename=%s content_type=%s", file.filename, file.content_type)
    
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
    logger.info("api: get_file(). name=%s", name)
    
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

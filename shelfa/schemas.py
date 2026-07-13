from typing import Optional
from pydantic import BaseModel

class FcmRegisterRequest(BaseModel):
    token: str
    device_id: Optional[str] = None

class PostMessageRequest(BaseModel):
    nickname: str = "anonymous"
    device_id: str = "anonymous"
    client_name: str = "anonymous"
    text: str

class MessageResponse(BaseModel):
    id: int
    nickname: str
    client_name: str
    device_id: str
    kind: str
    text: Optional[str] = None
    file_url: Optional[str] = None
    original_name: Optional[str] = None
    mime_type: Optional[str] = None
    created_at: str

class MessagesListResponse(BaseModel):
    messages: list[MessageResponse]
    unread_count: int

class IdentityValidateResponse(BaseModel):
    valid: bool

class IdentityCheckResponse(BaseModel):
    exists: bool

class OkResponse(BaseModel):
    ok: bool = True

class ErrorResponse(BaseModel):
    error: str

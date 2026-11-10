import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from typing import Any
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

from shelfa_group import db


class NoStoreJSONResponse(JSONResponse):
    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
    ) -> None:
        response_headers = dict(headers or {})
        response_headers.setdefault("Cache-Control", "no-store")
        super().__init__(
            content,
            status_code,
            response_headers,
            media_type,
            background,
        )


router = APIRouter(tags=["admin"], default_response_class=NoStoreJSONResponse)
SESSION_COOKIE = "shelfa_admin_session"
SESSION_MAX_AGE = 8 * 60 * 60


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class CreateUserRequest(BaseModel):
    user_nickname: str = Field(min_length=1, max_length=80)
    user_type: int = Field(default=db.PRIVATE_USER_TYPE, ge=1, le=2)
    account_mode: Literal["free", "limited", "premium"] = db.FREE_ACCOUNT_MODE

    @field_validator("user_nickname")
    @classmethod
    def normalize_nickname(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Nickname cannot be empty")
        return value


class UpdateUserRequest(BaseModel):
    user_type: int | None = Field(default=None, ge=1, le=2)
    is_blocked: bool | None = None
    account_mode: Literal["free", "limited", "premium"] | None = None


class AccountModeLimits(BaseModel):
    max_contacts: int = Field(ge=0, le=1_000_000)
    max_message_history_per_contact: int = Field(ge=0, le=1_000_000)


class UpdateAccountModeLimitsRequest(BaseModel):
    free: AccountModeLimits
    limited: AccountModeLimits


class AdminSession(BaseModel):
    username: str
    csrf_token: str
    session_id: str


def _credentials() -> tuple[str, str, str] | None:
    username = os.environ.get("SHELFA_ADMIN_USERNAME", "")
    password = os.environ.get("SHELFA_ADMIN_PASSWORD", "")
    secret_key = os.environ.get("SHELFA_ADMIN_SECRET_KEY", "")
    if (
        not username
        or len(password) < 12
        or len(secret_key.encode("utf-8")) < 32
    ):
        return None
    return username, password, secret_key


def _cookie_secure() -> bool:
    return os.environ.get("SHELFA_ADMIN_COOKIE_SECURE", "true").lower() not in {
        "0",
        "false",
        "no",
    }


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _new_session(
    username: str,
    secret_key: str,
    session_id: str,
    expires_at: int,
) -> tuple[str, str]:
    csrf_token = secrets.token_urlsafe(32)
    payload = _b64encode(
        json.dumps(
            {
                "username": username,
                "expires_at": expires_at,
                "csrf_token": csrf_token,
                "session_id": session_id,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signed_value = f"v1.{payload}"
    signature = hmac.new(
        secret_key.encode("utf-8"),
        signed_value.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signed_value}.{_b64encode(signature)}", csrf_token


def _read_session(token: str | None) -> AdminSession | None:
    credentials = _credentials()
    if credentials is None or token is None:
        return None
    try:
        version, payload, signature = token.split(".", 2)
        if version != "v1":
            return None
        signed_value = f"{version}.{payload}"
        expected = hmac.new(
            credentials[2].encode("utf-8"),
            signed_value.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(signature)):
            return None
        claims = json.loads(_b64decode(payload))
        if (
            claims["username"] != credentials[0]
            or not isinstance(claims["expires_at"], int)
            or claims["expires_at"] <= int(time.time())
            or not isinstance(claims["csrf_token"], str)
            or not isinstance(claims["session_id"], str)
            or not db.admin_session_is_active(
                claims["session_id"],
                int(time.time()),
            )
        ):
            return None
        return AdminSession(
            username=claims["username"],
            csrf_token=claims["csrf_token"],
            session_id=claims["session_id"],
        )
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError):
        return None


def require_admin(request: Request) -> AdminSession:
    session = _read_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="Administrator sign-in required")
    return session


def require_csrf(
    x_csrf_token: Annotated[str | None, Header()] = None,
    session: AdminSession = Depends(require_admin),
) -> AdminSession:
    if x_csrf_token is None or not hmac.compare_digest(
        session.csrf_token, x_csrf_token
    ):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return session


def _login_client_key(request: Request, secret_key: str) -> str:
    peer = request.client.host if request.client else "unknown"
    return hmac.new(
        secret_key.encode("utf-8"),
        peer.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@router.get("/admin")
def admin_page() -> FileResponse:
    file_path = Path(__file__).resolve().parent / "web" / "admin.html"
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Admin interface not found")
    return FileResponse(
        file_path,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'self'; script-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
                "frame-ancestors 'none'; base-uri 'self'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


@router.get("/admin/api/session")
def admin_session(request: Request) -> dict:
    session = _read_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        return {"authenticated": False, "configured": _credentials() is not None}
    return {
        "authenticated": True,
        "username": session.username,
        "csrf_token": session.csrf_token,
    }


@router.post("/admin/api/login")
def admin_login(body: LoginRequest, request: Request) -> JSONResponse:
    credentials = _credentials()
    if credentials is None:
        raise HTTPException(
            status_code=503,
            detail="Administrator credentials are not configured",
        )
    client_key = _login_client_key(request, credentials[2])
    now = int(time.time())
    if db.admin_auth_is_limited(client_key, now):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Try again in 15 minutes.",
        )

    username_matches = hmac.compare_digest(
        body.username.encode("utf-8"), credentials[0].encode("utf-8")
    )
    password_matches = hmac.compare_digest(
        body.password.encode("utf-8"), credentials[1].encode("utf-8")
    )
    if not username_matches or not password_matches:
        db.admin_record_failed_login(client_key, now)
        raise HTTPException(status_code=401, detail="Invalid administrator credentials")

    expires_at = now + SESSION_MAX_AGE
    session_id = secrets.token_urlsafe(24)
    db.admin_create_session(
        credentials[0],
        client_key,
        session_id,
        expires_at,
    )
    token, csrf_token = _new_session(
        credentials[0],
        credentials[2],
        session_id,
        expires_at,
    )
    response = JSONResponse(
        {"authenticated": True, "username": credentials[0], "csrf_token": csrf_token}
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        path="/admin",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/admin/api/logout")
def admin_logout(
    session: AdminSession = Depends(require_csrf),
) -> JSONResponse:
    db.admin_record_logout(session.username, session.session_id)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(
        SESSION_COOKIE,
        path="/admin",
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/admin/api/stats")
def dashboard_stats(_: AdminSession = Depends(require_admin)) -> dict:
    return db.admin_dashboard_stats()


@router.get("/admin/api/database")
def database_info(_: AdminSession = Depends(require_admin)) -> dict:
    return db.admin_database_info()


@router.get("/admin/api/users")
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    user_type: int | None = Query(default=None, ge=1, le=2),
    status: str | None = Query(default=None, pattern="^(active|blocked)$"),
    _: AdminSession = Depends(require_admin),
) -> dict:
    return db.admin_list_users(page, page_size, search, user_type, status)


@router.post("/admin/api/users")
def create_user(
    body: CreateUserRequest,
    session: AdminSession = Depends(require_csrf),
) -> dict:
    try:
        return db.admin_create_user(
            session.username,
            body.user_nickname,
            body.user_type,
            body.account_mode,
        )
    except sqlite3.IntegrityError as error:
        if "UNIQUE constraint failed: user.user_nickname" in str(error):
            raise HTTPException(
                status_code=409,
                detail="A user with that nickname already exists",
            ) from error
        raise


@router.patch("/admin/api/users/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUserRequest,
    session: AdminSession = Depends(require_csrf),
) -> dict:
    if (
        body.user_type is None
        and body.is_blocked is None
        and body.account_mode is None
    ):
        raise HTTPException(status_code=422, detail="No user changes were provided")
    result = db.admin_update_user(
        session.username,
        user_id,
        body.user_type,
        body.is_blocked,
        body.account_mode,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="User not found")
    return result


@router.get("/admin/api/usage-limits")
def get_usage_limits(_: AdminSession = Depends(require_admin)) -> dict:
    return db.get_account_mode_limits()


@router.put("/admin/api/usage-limits")
def update_usage_limits(
    body: UpdateAccountModeLimitsRequest,
    session: AdminSession = Depends(require_csrf),
) -> dict:
    return db.admin_update_account_mode_limits(
        session.username,
        body.model_dump(),
    )


@router.delete("/admin/api/users/{user_id}")
def delete_user(
    user_id: int,
    session: AdminSession = Depends(require_csrf),
) -> dict:
    result = db.admin_delete_user(session.username, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="User not found")
    return result


@router.get("/admin/api/messages")
def list_messages(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=200),
    message_type: int | None = Query(default=None, ge=0),
    _: AdminSession = Depends(require_admin),
) -> dict:
    return db.admin_list_messages(page, page_size, search, message_type)


@router.delete("/admin/api/messages/{message_id}")
def delete_message(
    message_id: int,
    session: AdminSession = Depends(require_csrf),
) -> dict:
    result = db.admin_delete_message(session.username, message_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return result


@router.get("/admin/api/contacts")
def list_contacts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    _: AdminSession = Depends(require_admin),
) -> dict:
    return db.admin_list_contacts(page, page_size, search)


@router.delete("/admin/api/contacts/{contact_id}")
def delete_contact(
    contact_id: int,
    session: AdminSession = Depends(require_csrf),
) -> dict:
    result = db.admin_delete_contact(session.username, contact_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return result


@router.get("/admin/api/audit")
def list_audit(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    _: AdminSession = Depends(require_admin),
) -> dict:
    return db.admin_list_audit(page, page_size)

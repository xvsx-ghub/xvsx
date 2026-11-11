import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlsplit


class LiveKitConfigurationError(RuntimeError):
    pass


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def livekit_configuration() -> tuple[str, str, str]:
    url = os.environ.get("LIVEKIT_URL", "").strip()
    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "").strip()
    parsed_url = urlsplit(url)
    if parsed_url.scheme not in {"ws", "wss"} or not parsed_url.netloc:
        raise LiveKitConfigurationError(
            "LiveKit is not configured. Set LIVEKIT_URL to a ws:// or wss:// URL."
        )
    if not api_key or len(api_secret.encode("utf-8")) < 32:
        raise LiveKitConfigurationError(
            "LiveKit is not configured. Set LIVEKIT_API_KEY and a "
            "LIVEKIT_API_SECRET of at least 32 bytes."
        )
    return url, api_key, api_secret


def create_participant_token(
    api_key: str,
    api_secret: str,
    room_name: str,
    call_id: str,
    nickname: str,
    call_type: str,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    identity = hmac.new(
        api_secret.encode("utf-8"),
        f"{call_id}:{nickname}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    header = _base64url(
        json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()
    )
    video_grant = {
        "room": room_name,
        "roomJoin": True,
        "canPublish": True,
        "canPublishData": True,
        "canSubscribe": True,
        "canUpdateOwnMetadata": True,
        "canPublishSources": (
            ["microphone", "camera"] if call_type == "video" else ["microphone"]
        ),
    }
    claims = {
        "iss": api_key,
        "sub": identity,
        "nbf": issued_at - 5,
        "exp": issued_at + 4 * 60 * 60,
        "video": video_grant,
    }
    payload = _base64url(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(
        api_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    return f"{header}.{payload}.{_base64url(signature)}"

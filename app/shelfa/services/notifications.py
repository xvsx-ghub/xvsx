from typing import Optional

from app.shelfa.config import FIREBASE_AUTH_PATH

_firebase_admin = None
_firebase_credentials = None
_firebase_messaging = None


def _ensure_firebase_modules() -> None:
    global _firebase_admin, _firebase_credentials, _firebase_messaging
    if _firebase_admin is not None or _firebase_messaging is not None:
        return

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
    except ModuleNotFoundError:
        _firebase_admin = None
        _firebase_credentials = None
        _firebase_messaging = None
        return

    _firebase_admin = firebase_admin
    _firebase_credentials = credentials
    _firebase_messaging = messaging


def init_firebase() -> bool:
    _ensure_firebase_modules()
    if _firebase_admin is None:
        return False
    if _firebase_admin._apps:
        return False
    if not FIREBASE_AUTH_PATH.is_file():
        return False
    cred = _firebase_credentials.Certificate(FIREBASE_AUTH_PATH)
    _firebase_admin.initialize_app(cred)
    return True


def is_firebase_ready() -> bool:
    _ensure_firebase_modules()
    return bool(_firebase_admin and _firebase_admin._apps)


def _fcm_data_payload(data: Optional[dict], *, notification_kind: str) -> dict[str, str]:
    out: dict[str, str] = {"notification_kind": notification_kind}
    for key, value in (data or {}).items():
        if value is None:
            continue
        out[str(key)] = value if isinstance(value, str) else str(value)
    return out


def _get_messaging():
    _ensure_firebase_modules()
    if _firebase_messaging is None:
        raise RuntimeError("firebase_admin is not available")
    return _firebase_messaging


def send_alert_notification(
    token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    badge: Optional[int] = None,
) -> str:
    messaging = _get_messaging()
    payload = _fcm_data_payload(data, notification_kind="alert")
    if badge is not None:
        payload["badge"] = str(int(badge))

    aps_kwargs: dict = dict(
        alert=messaging.ApsAlert(title=title, body=body),
        sound="default",
    )
    if badge is not None:
        aps_kwargs["badge"] = int(badge)

    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data=payload,
        token=token,
        android=messaging.AndroidConfig(priority="high"),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(aps=messaging.Aps(**aps_kwargs)),
        ),
    )
    return messaging.send(message)


def send_background_notification(
    token: str,
    data: dict,
    badge: Optional[int] = None,
) -> str:
    messaging = _get_messaging()
    payload = _fcm_data_payload(data, notification_kind="background")
    if badge is not None:
        payload["badge"] = str(int(badge))

    if badge is None:
        apns = messaging.APNSConfig(
            headers={
                "apns-push-type": "background",
                "apns-priority": "5",
            },
            payload=messaging.APNSPayload(
                aps=messaging.Aps(content_available=True),
            ),
        )
    else:
        apns = messaging.APNSConfig(
            headers={
                "apns-push-type": "alert",
                "apns-priority": "10",
            },
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    content_available=True,
                    badge=int(badge),
                ),
            ),
        )

    message = messaging.Message(
        data=payload,
        token=token,
        android=messaging.AndroidConfig(priority="high"),
        apns=apns,
    )
    return messaging.send(message)

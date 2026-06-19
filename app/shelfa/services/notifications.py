from typing import Optional

import firebase_admin
from firebase_admin import credentials, messaging

from app.shelfa.config import FIREBASE_AUTH_PATH


def init_firebase() -> bool:
    if firebase_admin._apps:
        return False
    if not FIREBASE_AUTH_PATH.is_file():
        return False
    cred = credentials.Certificate(FIREBASE_AUTH_PATH)
    firebase_admin.initialize_app(cred)
    return True


def is_firebase_ready() -> bool:
    return bool(firebase_admin._apps)


def _fcm_data_payload(data: Optional[dict], *, notification_kind: str) -> dict[str, str]:
    out: dict[str, str] = {"notification_kind": notification_kind}
    for key, value in (data or {}).items():
        if value is None:
            continue
        out[str(key)] = value if isinstance(value, str) else str(value)
    return out


def send_alert_notification(
    token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    badge: Optional[int] = None,
) -> str:
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

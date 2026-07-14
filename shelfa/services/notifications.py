import logging
from collections.abc import Mapping
from typing import Any

import firebase_admin
from firebase_admin import credentials, get_app, initialize_app, messaging

from shelfa.config import FIREBASE_AUTH_PATH

logger = logging.getLogger("xvsx")


def init_firebase() -> bool:
    """Initialize Firebase once."""
    try:
        get_app()
        logger.info("Firebase already initialized.")
    except ValueError:
        logger.info("Initializing Firebase...")
        initialize_app(credentials.Certificate(FIREBASE_AUTH_PATH))
        logger.info("Firebase initialized successfully.")

    return True


def _fcm_data_payload(
    data: Mapping[str, Any] | None,
    *,
    notification_kind: str,
) -> dict[str, str]:
    payload = {"notification_kind": notification_kind}

    for key, value in (data or {}).items():
        if value is not None:
            payload[str(key)] = str(value)

    return payload


def send_alert_notification(
    token: str,
    title: str,
    body: str,
    data: Mapping[str, Any] | None = None,
    badge: int | None = None,
) -> str:
    logger.info(
        "Preparing to send FCM alert notification to token=%s...",
        token[:12],
    )

    payload = _fcm_data_payload(data, notification_kind="alert")

    if badge is not None:
        badge = int(badge)
        payload["badge"] = str(badge)

    aps_kwargs = {
        "alert": messaging.ApsAlert(title=title, body=body),
        "sound": "default",
    }

    if badge is not None:
        aps_kwargs["badge"] = badge

    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data=payload,
        token=token,
        android=messaging.AndroidConfig(priority="high"),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(**aps_kwargs),
            ),
        ),
    )

    try:
        logger.info("Sending FCM alert notification...")
        return messaging.send(message)
    except Exception:
        logger.exception("FCM alert send failed")
        raise


def send_background_notification(
    token: str,
    data: Mapping[str, Any],
    badge: int | None = None,
) -> str:
    logger.info(
        "Preparing to send FCM background notification to token=%s...",
        token[:12],
    )

    payload = _fcm_data_payload(data, notification_kind="background")

    if badge is not None:
        badge = int(badge)
        payload["badge"] = str(badge)

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
                    badge=badge,
                ),
            ),
        )

    message = messaging.Message(
        data=payload,
        token=token,
        android=messaging.AndroidConfig(priority="high"),
        apns=apns,
    )

    try:
        logger.info("Sending FCM background notification...")
        return messaging.send(message)
    except Exception:
        logger.exception("FCM background send failed")
        raise
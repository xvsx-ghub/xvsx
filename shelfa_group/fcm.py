import logging
from collections.abc import Mapping
from typing import Any

from firebase_admin import credentials, get_app, initialize_app, messaging

from shelfa_group.config import FIREBASE_AUTH_PATH


logger = logging.getLogger("shelfa")


def init(app_name: str = "shelfa_group") -> bool:
    logger.info("fcm: init(). Initializing Firebase app %s...", app_name)
    try:
        get_app(app_name)
    except ValueError:
        try:
            with open(FIREBASE_AUTH_PATH, "r") as f:
                f.read()
        except FileNotFoundError:
            logger.error(
                "fcm: init(). Firebase auth file not found at %s. FCM will not work.",
                FIREBASE_AUTH_PATH,
            )
            return False
        try:
            initialize_app(credentials.Certificate(FIREBASE_AUTH_PATH), name=app_name)  
        except Exception as e:
            logger.exception(
                "fcm: init(). Failed to initialize Firebase app %s: %s", app_name, e
            )
            return False
                    
    logger.info("fcm: init(). Firebase app %s initialized.", app_name)
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
    data: Mapping[str, Any],
    badge: int,
) -> str:
    logger.info(
        "fcm: send_alert_notification(). token=%s: title=%s, body=%s, data=%s, badge=%s",
        token,
        title,
        body,
        data,
        badge
    )

    payload = _fcm_data_payload(data, notification_kind="alert")
    payload["badge"] = str(badge)

    aps_kwargs = {
        "alert": messaging.ApsAlert(title=title, body=body),
        "sound": "default",
    }
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
        app = get_app("shelfa_group")
        return messaging.send(message, app=app)
    except Exception as e:
        logger.exception("fcm: send_alert_notification(). FCM alert send failed: %s", e)
        raise RuntimeError(f"FCM alert send failed: {e}") from e

def send_background_notification(
    token: str | None,
    data: Mapping[str, Any],
    badge: int | None = None,
) -> str:
    token_value = str(token).strip() if token is not None else ""
    if not token_value:
        logger.warning("Skipping FCM background notification because token is missing.")
        return ""
    if len(token_value) < 10 or any(ch.isspace() for ch in token_value):
        logger.warning(
            "Skipping FCM background notification because token format looks invalid: %s",
            token_value[:12],
        )
        return ""

    logger.info(
        "Preparing to send FCM background notification to token=%s...",
        token_value[:12],
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
        token=token_value,
        android=messaging.AndroidConfig(priority="high"),
        apns=apns,
    )

    try:
        logger.info("Sending FCM background notification...")
        app = get_app("shelfa_group")
        return messaging.send(message, app=app)
    except Exception:
        logger.exception("FCM background send failed")
        raise
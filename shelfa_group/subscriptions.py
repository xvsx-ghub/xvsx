"""Verify store purchases and apply the authoritative entitlement."""

import logging

from shelfa_group import fcm, subscription_db
from shelfa_group.db import get_user_by_nickname
from shelfa_group.store_clients import (
    AppleAppStoreClient,
    GooglePlayClient,
    StoreNotConfigured,
    StoreVerificationError,
    VerifiedPurchase,
)
from shelfa_group.subscriptions_catalog import plan_for_product

logger = logging.getLogger("shelfa")

_clients: dict[str, object | None] = {"apple": None, "google": None}

GOOGLE_NOTIFICATION_TYPES = {
    1: "RECOVERED",
    2: "RENEWED",
    3: "CANCELED",
    4: "PURCHASED",
    5: "ON_HOLD",
    6: "IN_GRACE_PERIOD",
    7: "RESTARTED",
    8: "PRICE_CHANGE_CONFIRMED",
    9: "DEFERRED",
    10: "PAUSED",
    11: "PAUSE_SCHEDULE_CHANGED",
    12: "REVOKED",
    13: "EXPIRED",
    19: "PRICE_CHANGE_UPDATED",
    20: "PENDING_PURCHASE_CANCELED",
}


def set_store_clients(apple=None, google=None) -> None:
    _clients["apple"] = apple
    _clients["google"] = google


def get_apple_client():
    if _clients["apple"] is not None:
        return _clients["apple"]
    return AppleAppStoreClient.from_environment()


def get_google_client():
    if _clients["google"] is not None:
        return _clients["google"]
    return GooglePlayClient.from_environment()


def _check_product(requested_product_id: str, verified: VerifiedPurchase) -> None:
    if requested_product_id != verified.product_id:
        raise StoreVerificationError("Incorrect product identifier")
    if plan_for_product(verified.product_id) != verified.account_plan:
        raise StoreVerificationError("Incorrect product identifier")


def verify_for_user(user_id: int, platform: str, product_id: str, purchase_token: str | None, transaction_id: str | None, signed_transaction: str | None) -> dict:
    platform = platform.upper()
    if platform == "GOOGLE":
        verified = get_google_client().get_subscription(purchase_token or "", product_id)
        _check_product(product_id, verified)
        if verified.status == "PENDING":
            result = subscription_db.apply_store_subscription(
                user_id,
                verified.as_dict(),
                event_type="purchase_pending",
                authoritative=True,
            )
            result["success"] = True
            return result
        result = subscription_db.apply_store_subscription(
            user_id,
            verified.as_dict(),
            event_type="purchase_verified",
            authoritative=True,
        )
        _acknowledge_google_if_needed(verified)
        _notify_account(user_id, result)
        result["success"] = True
        return result
    if platform == "APPLE":
        client = get_apple_client()
        verified = client.subscription_status(
            transaction_id or "",
            signed_transaction or "",
            product_id,
        )
        _check_product(product_id, verified)
        if transaction_id and verified.transaction_id and transaction_id != verified.transaction_id:
            raise StoreVerificationError("Incorrect transaction identifier")
        result = subscription_db.apply_store_subscription(
            user_id,
            verified.as_dict(),
            event_type="purchase_verified",
            authoritative=True,
        )
        _notify_account(user_id, result)
        result["success"] = True
        return result
    raise StoreVerificationError("Incorrect subscription platform")


def apply_verified_notification(user_id: int, verified: VerifiedPurchase, event_type: str, notification_id: str | None) -> dict:
    result = subscription_db.apply_store_subscription(
        user_id,
        verified.as_dict(),
        event_type=event_type,
        notification_id=notification_id,
        authoritative=True,
    )
    if result.get("applied"):
        _notify_account(user_id, result)
    return result


def _acknowledge_google_if_needed(verified: VerifiedPurchase) -> None:
    if verified.status in {"PENDING", "EXPIRED", "REVOKED"}:
        return
    state = verified.acknowledgement_state or ""
    if "ACKNOWLEDGED" in state and "PENDING" not in state:
        return
    try:
        get_google_client().acknowledge(verified.product_id, verified.purchase_token or "")
    except Exception:
        logger.exception(
            "subscription: Google acknowledgement failed for product=%s",
            verified.product_id,
        )


def _notify_account(user_id: int, status: dict) -> None:
    try:
        from shelfa_group.db import get_db

        with get_db() as conn:
            row = conn.execute(
                "SELECT fcm_token, account_mode FROM user WHERE id = ?",
                (user_id,),
            ).fetchone()
        if row is None or not row["fcm_token"]:
            return
        fcm.send_background_notification(
            row["fcm_token"],
            {
                "type": "subscription",
                "silent": "1",
                "account_mode": row["account_mode"],
                "entitlement_source": status.get("entitlementSource", ""),
            },
        )
    except Exception:
        logger.info("subscription: account notification was not delivered user_id=%s", user_id)


def status_for_nickname(nickname: str) -> dict | None:
    user = get_user_by_nickname(nickname)
    if user is None:
        return None
    return subscription_db.get_status(int(user["id"]))

"""Subscription HTTP API. The backend is the source of paid entitlements."""

import hmac
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from shelfa_group import subscription_db, subscriptions
from shelfa_group.db import get_user_by_nickname
from shelfa_group.store_clients import (
    StoreAuthenticationError,
    StoreNotConfigured,
    StoreVerificationError,
    assert_pubsub_authorized,
    decode_pubsub_message,
)
from shelfa_group.subscriptions import GOOGLE_NOTIFICATION_TYPES
from shelfa_group.subscriptions_catalog import catalog_plans

logger = logging.getLogger("shelfa")

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


class VerifySubscriptionRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=20)
    productId: str = Field(min_length=1, max_length=255)
    user_nickname: str = Field(min_length=1, max_length=80)
    device_id: str = Field(min_length=1, max_length=256)
    purchaseToken: str | None = Field(default=None, max_length=8192)
    transactionId: str | None = Field(default=None, max_length=255)
    signedTransactionInfo: str | None = Field(default=None, max_length=20000)


def authorized_user(user_nickname: str, device_id: str):
    nickname = user_nickname.strip()
    device = device_id.strip()
    if not nickname or not device:
        raise HTTPException(status_code=401, detail="Authentication is required")
    user = get_user_by_nickname(nickname)
    if user is None or not user["device_id"]:
        raise HTTPException(status_code=401, detail="User is not registered")
    if user["is_blocked"]:
        raise HTTPException(status_code=403, detail="User account is blocked")
    registered = str(user["device_id"])
    if len(registered) != len(device) or not hmac.compare_digest(registered, device):
        raise HTTPException(
            status_code=401,
            detail="Device is not authorized for this account",
        )
    return user


def _verification_error(error: Exception) -> HTTPException:
    if isinstance(error, StoreNotConfigured):
        return HTTPException(status_code=503, detail="Store verification is not configured")
    if isinstance(error, subscription_db.SubscriptionOwnershipConflict):
        return HTTPException(
            status_code=409,
            detail="This purchase is already associated with another account",
        )
    if isinstance(error, StoreAuthenticationError):
        return HTTPException(status_code=401, detail="Store notification authentication failed")
    if isinstance(error, StoreVerificationError):
        return HTTPException(status_code=400, detail=str(error) or "Purchase verification failed")
    logger.exception("subscription: unexpected verification error")
    return HTTPException(status_code=400, detail="Purchase verification failed")


def _verify_response(result: dict) -> dict:
    subscription = result.get("subscription")
    body = {
        "success": bool(result.get("success", True)),
        "effectivePlan": result.get("effectivePlan"),
        "entitlementSource": result.get("entitlementSource"),
    }
    if subscription is not None:
        body["subscription"] = {
            "platform": subscription["platform"],
            "plan": subscription["plan"],
            "status": subscription["status"],
            "expiresAt": subscription.get("expiresAt"),
            "autoRenew": subscription.get("autoRenew", False),
        }
    else:
        body["subscription"] = None
    return body


@router.get("/products")
def get_products() -> dict:
    return {"plans": catalog_plans()}


@router.get("/status")
def get_status(
    user_nickname: str = Query(min_length=1, max_length=80),
    device_id: str = Query(min_length=1, max_length=256),
) -> dict:
    user = authorized_user(user_nickname, device_id)
    return subscription_db.get_status(int(user["id"]))


@router.post("/verify")
def verify_purchase(body: VerifySubscriptionRequest) -> dict:
    user = authorized_user(body.user_nickname, body.device_id)
    platform = body.platform.strip().upper()
    logger.info(
        "subscription: verify user_id=%s platform=%s product=%s",
        user["id"],
        platform,
        body.productId,
    )
    try:
        result = subscriptions.verify_for_user(
            int(user["id"]),
            platform,
            body.productId,
            body.purchaseToken,
            body.transactionId,
            body.signedTransactionInfo,
        )
    except Exception as error:
        raise _verification_error(error) from error
    logger.info(
        "subscription: verified user_id=%s plan=%s source=%s",
        user["id"],
        result.get("effectivePlan"),
        result.get("entitlementSource"),
    )
    return _verify_response(result)


@router.post("/apple/notifications")
def apple_notification(body: dict) -> dict:
    signed_payload = body.get("signedPayload") if isinstance(body, dict) else None
    if not isinstance(signed_payload, str) or not signed_payload:
        raise HTTPException(status_code=400, detail="Missing signed notification")
    try:
        client = subscriptions.get_apple_client()
        decoded = client.decode_notification(signed_payload)
        notification_id = decoded.get("notification_id") or None
        if notification_id and subscription_db.notification_already_processed("APPLE", notification_id):
            return {"success": True, "duplicate": True}
        verified = client.subscription_status(
            "",
            decoded["signed_transaction"],
            None,
        )
        owner_id = subscription_db.find_owner_id(
            platform="APPLE",
            environment=verified.environment,
            purchase_token=None,
            original_transaction_id=verified.original_transaction_id,
            linked_purchase_token=None,
        )
        event_type = decoded.get("notification_type") or "APPLE_NOTIFICATION"
        if owner_id is None:
            subscription_db.record_notification(
                platform="APPLE",
                event_type=event_type,
                notification_id=None,
                summary={
                    "product_id": verified.product_id,
                    "status": verified.status,
                    "notification_id": notification_id,
                    "assigned": False,
                },
            )
            return {"success": True, "assigned": False}
        subscriptions.apply_verified_notification(
            owner_id,
            verified,
            event_type,
            notification_id,
        )
    except Exception as error:
        raise _verification_error(error) from error
    return {"success": True}


@router.post("/google/notifications")
async def google_notification(
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    try:
        assert_pubsub_authorized(authorization, token)
        message_id, payload = decode_pubsub_message(await request.json())
    except Exception as error:
        raise _verification_error(error) from error

    if payload.get("testNotification") is not None:
        return {"success": True, "test": True}
    note = payload.get("subscriptionNotification")
    if not isinstance(note, dict):
        return {"success": True, "ignored": True}
    package_name = str(payload.get("packageName") or "")
    configured = os.environ.get("SHELFA_GOOGLE_PACKAGE_NAME", "com.shelfa")
    if package_name and package_name != configured:
        raise HTTPException(status_code=400, detail="Incorrect application identifier")
    purchase_token = str(note.get("purchaseToken") or "")
    if not purchase_token:
        raise HTTPException(status_code=400, detail="Missing purchase token")
    if subscription_db.notification_already_processed("GOOGLE", message_id):
        return {"success": True, "duplicate": True}
    event_code = int(note.get("notificationType") or 0)
    event_type = GOOGLE_NOTIFICATION_TYPES.get(event_code, f"GOOGLE_{event_code}")
    try:
        verified = subscriptions.get_google_client().get_subscription(purchase_token, None)
        if verified.bundle_or_package != configured:
            raise StoreVerificationError("Incorrect application identifier")
        owner_id = subscription_db.find_owner_id(
            platform="GOOGLE",
            environment=verified.environment,
            purchase_token=purchase_token,
            original_transaction_id=None,
            linked_purchase_token=verified.linked_purchase_token,
        )
        if owner_id is None:
            subscription_db.record_notification(
                platform="GOOGLE",
                event_type=event_type,
                notification_id=None,
                summary={
                    "product_id": verified.product_id,
                    "status": verified.status,
                    "notification_id": message_id,
                    "assigned": False,
                },
            )
            return {"success": True, "assigned": False}
        subscriptions.apply_verified_notification(owner_id, verified, event_type, message_id)
    except Exception as error:
        raise _verification_error(error) from error
    return {"success": True}

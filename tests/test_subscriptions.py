import base64
import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shelfa_group import api, db, subscription_db, subscriptions, subscriptions_api
from shelfa_group.store_clients import StoreVerificationError, VerifiedPurchase


class FakeGoogle:
    def __init__(self):
        self.purchases = {}
        self.acknowledged = []

    def get_subscription(self, purchase_token, expected_product_id=None):
        purchase = self.purchases.get(purchase_token)
        if purchase is None:
            raise StoreVerificationError("Invalid Google purchase")
        if expected_product_id and expected_product_id != purchase.product_id:
            raise StoreVerificationError("Incorrect product identifier")
        return purchase

    def acknowledge(self, product_id, purchase_token):
        self.acknowledged.append((product_id, purchase_token))


class FakeApple:
    def __init__(self):
        self.transactions = {}
        self.notifications = {}

    def subscription_status(self, transaction_id, signed_transaction, expected_product_id=None):
        purchase = self.transactions.get(signed_transaction)
        if purchase is None:
            raise StoreVerificationError("Invalid Apple transaction")
        if expected_product_id and expected_product_id != purchase.product_id:
            raise StoreVerificationError("Incorrect product identifier")
        if transaction_id and purchase.transaction_id and transaction_id != purchase.transaction_id:
            raise StoreVerificationError("Incorrect transaction identifier")
        return purchase

    def decode_notification(self, signed_payload):
        decoded = self.notifications.get(signed_payload)
        if decoded is None:
            raise StoreVerificationError("Invalid Apple notification signature")
        return decoded


def purchase(**overrides) -> VerifiedPurchase:
    values = dict(
        platform="GOOGLE",
        environment="Production",
        product_id="shelfa_premium",
        account_plan="premium",
        status="ACTIVE",
        purchase_token="token-premium",
        transaction_id="GPA.1",
        original_transaction_id=None,
        start_date=1_700_000_000,
        expiration_date=1_800_000_000,
        auto_renew=True,
        acknowledgement_state="ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
        linked_purchase_token=None,
        bundle_or_package="com.shelfa",
        event_time=1_700_000_000,
    )
    values.update(overrides)
    return VerifiedPurchase(**values)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "subscriptions.db")
    monkeypatch.setattr(subscriptions.fcm, "send_background_notification", lambda *args, **kwargs: "")
    monkeypatch.setattr(api.fcm, "send_alert_notification", lambda **kwargs: None)
    db.init()
    google = FakeGoogle()
    apple = FakeApple()
    subscriptions.set_store_clients(apple=apple, google=google)
    app = FastAPI()
    app.include_router(api.api_router)
    app.include_router(subscriptions_api.router)
    with TestClient(app) as test_client:
        yield test_client, google, apple
    subscriptions.set_store_clients(apple=None, google=None)


def register(client, nickname="payer", device_id="device-1"):
    response = client.post(
        "/shelfa/user",
        json={
            "user_nickname": nickname,
            "user_type": 1,
            "device_id": device_id,
            "fcm_token": "fcm-token-123456",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_products_use_configured_limits_and_do_not_include_prices(client):
    test_client, _, _ = client
    response = test_client.get("/api/subscriptions/products")
    assert response.status_code == 200
    plans = {item["plan"]: item for item in response.json()["plans"]}
    assert plans["LIMITED"]["contactsLimit"] == 50
    assert plans["LIMITED"]["messagesLimit"] == 500
    assert plans["LIMITED"]["androidProductId"] == "shelfa_limited"
    assert plans["LIMITED"]["iosProductId"] == "com.shelfa.limited.monthly"
    assert plans["PREMIUM"]["contactsLimit"] is None
    assert plans["PREMIUM"]["messagesLimit"] is None
    assert "price" not in response.text


def test_rejects_unauthenticated_and_unconfigured_verification(client, monkeypatch):
    test_client, _, _ = client
    register(test_client)
    missing_device = test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "GOOGLE",
            "productId": "shelfa_premium",
            "user_nickname": "payer",
            "device_id": "other-device",
            "purchaseToken": "token-premium",
        },
    )
    assert missing_device.status_code == 401
    subscriptions.set_store_clients(apple=None, google=None)
    unconfigured = test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "GOOGLE",
            "productId": "shelfa_premium",
            "user_nickname": "payer",
            "device_id": "device-1",
            "purchaseToken": "token-premium",
        },
    )
    assert unconfigured.status_code == 503


def test_google_purchase_duplicate_renewal_and_cross_platform_status(client):
    test_client, google, _ = client
    register(test_client)
    google.purchases["token-premium"] = purchase()
    body = {
        "platform": "GOOGLE",
        "productId": "shelfa_premium",
        "user_nickname": "payer",
        "device_id": "device-1",
        "purchaseToken": "token-premium",
    }
    first = test_client.post("/api/subscriptions/verify", json=body)
    assert first.status_code == 200
    assert first.json()["success"] is True
    assert first.json()["subscription"]["plan"] == "PREMIUM"
    assert first.json()["subscription"]["status"] == "ACTIVE"
    assert first.json()["effectivePlan"] == "PREMIUM"

    second = test_client.post("/api/subscriptions/verify", json=body)
    assert second.status_code == 200
    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
    assert count == 1

    google.purchases["token-premium"] = purchase(expiration_date=1_900_000_000, event_time=1_700_000_100)
    renewed = test_client.post("/api/subscriptions/verify", json=body)
    assert renewed.json()["subscription"]["expiresAt"] == "2030-03-17T17:46:40Z"

    status = test_client.get(
        "/api/subscriptions/status",
        params={"user_nickname": "payer", "device_id": "device-1"},
    )
    assert status.status_code == 200
    payload = status.json()
    assert payload["effectivePlan"] == "PREMIUM"
    assert payload["entitlementSource"] == "GOOGLE_SUBSCRIPTION"
    assert payload["limits"]["contacts"] is None
    assert payload["subscription"]["platform"] == "GOOGLE"


def test_invalid_google_purchase_and_product_mismatch(client):
    test_client, google, _ = client
    register(test_client)
    google.purchases["token-premium"] = purchase()
    invalid = test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "GOOGLE",
            "productId": "shelfa_premium",
            "user_nickname": "payer",
            "device_id": "device-1",
            "purchaseToken": "missing-token",
        },
    )
    assert invalid.status_code == 400
    mismatch = test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "GOOGLE",
            "productId": "shelfa_limited",
            "user_nickname": "payer",
            "device_id": "device-1",
            "purchaseToken": "token-premium",
        },
    )
    assert mismatch.status_code == 400
    assert db.get_account_mode("payer") == "free"


def test_pending_purchase_does_not_grant_entitlement(client):
    test_client, google, _ = client
    register(test_client)
    google.purchases["token-pending"] = purchase(
        purchase_token="token-pending",
        status="PENDING",
        product_id="shelfa_limited",
        account_plan="limited",
    )
    response = test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "GOOGLE",
            "productId": "shelfa_limited",
            "user_nickname": "payer",
            "device_id": "device-1",
            "purchaseToken": "token-pending",
        },
    )
    assert response.status_code == 200
    assert response.json()["effectivePlan"] == "FREE"
    assert response.json()["subscription"]["status"] == "PENDING"


def test_apple_purchase_and_invalid_transaction(client):
    test_client, _, apple = client
    register(test_client)
    apple.transactions["signed-premium"] = purchase(
        platform="APPLE",
        product_id="com.shelfa.premium.monthly",
        purchase_token=None,
        transaction_id="2000001",
        original_transaction_id="2000001",
    )
    response = test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "APPLE",
            "productId": "com.shelfa.premium.monthly",
            "user_nickname": "payer",
            "device_id": "device-1",
            "transactionId": "2000001",
            "signedTransactionInfo": "signed-premium",
        },
    )
    assert response.status_code == 200
    assert response.json()["subscription"]["platform"] == "APPLE"
    assert response.json()["entitlementSource"] == "APPLE_SUBSCRIPTION"
    invalid = test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "APPLE",
            "productId": "com.shelfa.premium.monthly",
            "user_nickname": "payer",
            "device_id": "device-1",
            "transactionId": "2000001",
            "signedTransactionInfo": "forged",
        },
    )
    assert invalid.status_code == 400


def test_purchase_cannot_be_claimed_by_another_account(client):
    test_client, google, _ = client
    register(test_client, "payer", "device-1")
    register(test_client, "other", "device-2")
    google.purchases["token-premium"] = purchase()
    body = {
        "platform": "GOOGLE",
        "productId": "shelfa_premium",
        "purchaseToken": "token-premium",
    }
    assert test_client.post(
        "/api/subscriptions/verify",
        json={**body, "user_nickname": "payer", "device_id": "device-1"},
    ).status_code == 200
    conflict = test_client.post(
        "/api/subscriptions/verify",
        json={**body, "user_nickname": "other", "device_id": "device-2"},
    )
    assert conflict.status_code == 409
    assert db.get_account_mode("other") == "free"
    assert db.get_account_mode("payer") == "premium"


def test_expiration_downgrades_without_deleting_contacts(client):
    test_client, google, _ = client
    register(test_client, "payer", "device-1")
    register(test_client, "friend", "device-2")
    google.purchases["token-premium"] = purchase(expiration_date=int(time.time()) + 3600)
    assert test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "GOOGLE",
            "productId": "shelfa_premium",
            "user_nickname": "payer",
            "device_id": "device-1",
            "purchaseToken": "token-premium",
        },
    ).status_code == 200
    created = test_client.post(
        "/shelfa/contact",
        json={"sender_nickname": "payer", "recipient_nickname": "friend"},
    )
    assert created.status_code == 200
    with db.get_db() as conn:
        conn.execute(
            "UPDATE subscriptions SET expiration_date = ? WHERE purchase_token = ?",
            (int(time.time()) - 10, "token-premium"),
        )
        conn.commit()
    status = test_client.get(
        "/api/subscriptions/status",
        params={"user_nickname": "payer", "device_id": "device-1"},
    )
    assert status.json()["effectivePlan"] == "FREE"
    assert status.json()["subscription"]["status"] == "EXPIRED"
    with db.get_db() as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM contact_registry").fetchone()[0]
    assert remaining == 1
    extra_friend = register(test_client, "third", "device-3")
    assert extra_friend["user_nickname"] == "third"
    for index in range(5):
        nickname = f"more-{index}"
        register(test_client, nickname, f"device-more-{index}")
        response = test_client.post(
            "/shelfa/contact",
            json={"sender_nickname": "payer", "recipient_nickname": nickname},
        )
        if index == 4:
            assert response.status_code == 403
        else:
            assert response.status_code == 200


def test_admin_override_beats_subscription_until_it_expires(client, monkeypatch):
    test_client, google, _ = client
    monkeypatch.setenv("SHELFA_ADMIN_USERNAME", "workspace-admin")
    monkeypatch.setenv("SHELFA_ADMIN_PASSWORD", "long-enough-password")
    monkeypatch.setenv("SHELFA_ADMIN_SECRET_KEY", "a" * 48)
    monkeypatch.setenv("SHELFA_ADMIN_COOKIE_SECURE", "false")
    from shelfa_group import admin

    app = test_client.app
    app.include_router(admin.router)
    user = register(test_client)
    google.purchases["token-limited"] = purchase(
        purchase_token="token-limited",
        product_id="shelfa_limited",
        account_plan="limited",
    )
    assert test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "GOOGLE",
            "productId": "shelfa_limited",
            "user_nickname": "payer",
            "device_id": "device-1",
            "purchaseToken": "token-limited",
        },
    ).status_code == 200
    sign_in = test_client.post(
        "/admin/api/login",
        json={"username": "workspace-admin", "password": "long-enough-password"},
    )
    headers = {"X-CSRF-Token": sign_in.json()["csrf_token"]}
    updated = test_client.patch(
        f"/admin/api/users/{user['id']}",
        json={"account_mode": "premium"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["account_mode"] == "premium"
    status = test_client.get(
        "/api/subscriptions/status",
        params={"user_nickname": "payer", "device_id": "device-1"},
    ).json()
    assert status["effectivePlan"] == "PREMIUM"
    assert status["entitlementSource"] == "ADMIN_OVERRIDE"
    assert status["subscription"]["plan"] == "LIMITED"

    expires = int(time.time()) + 5
    assert test_client.patch(
        f"/admin/api/users/{user['id']}",
        json={"account_mode": "premium", "admin_override_expires_at": expires},
        headers=headers,
    ).status_code == 200
    monkeypatch.setattr(subscription_db, "now", lambda: expires + 1)
    restored = test_client.get(
        "/api/subscriptions/status",
        params={"user_nickname": "payer", "device_id": "device-1"},
    ).json()
    assert restored["effectivePlan"] == "LIMITED"
    assert restored["entitlementSource"] == "GOOGLE_SUBSCRIPTION"


def test_google_notification_is_idempotent_and_authoritative(client, monkeypatch):
    test_client, google, _ = client
    register(test_client)
    google.purchases["token-premium"] = purchase(expiration_date=1_800_000_000)
    assert test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "GOOGLE",
            "productId": "shelfa_premium",
            "user_nickname": "payer",
            "device_id": "device-1",
            "purchaseToken": "token-premium",
        },
    ).status_code == 200
    google.purchases["token-premium"] = purchase(expiration_date=1_900_000_000, event_time=1_700_000_500)
    monkeypatch.setenv("SHELFA_GOOGLE_PUBSUB_VERIFICATION_TOKEN", "pubsub-secret")
    payload = {
        "packageName": "com.shelfa",
        "subscriptionNotification": {
            "notificationType": 2,
            "purchaseToken": "token-premium",
        },
    }
    body = {
        "message": {
            "messageId": "message-1",
            "data": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii"),
        }
    }
    rejected = test_client.post("/api/subscriptions/google/notifications", json=body)
    assert rejected.status_code == 401
    accepted = test_client.post(
        "/api/subscriptions/google/notifications?token=pubsub-secret",
        json=body,
    )
    assert accepted.status_code == 200
    duplicate = test_client.post(
        "/api/subscriptions/google/notifications?token=pubsub-secret",
        json=body,
    )
    assert duplicate.json()["duplicate"] is True
    status = test_client.get(
        "/api/subscriptions/status",
        params={"user_nickname": "payer", "device_id": "device-1"},
    ).json()
    assert status["subscription"]["expiresAt"] == "2030-03-17T17:46:40Z"


def test_apple_notification_and_duplicate(client):
    test_client, _, apple = client
    register(test_client)
    original = purchase(
        platform="APPLE",
        product_id="com.shelfa.limited.monthly",
        account_plan="limited",
        purchase_token=None,
        transaction_id="300",
        original_transaction_id="300",
    )
    apple.transactions["signed-limited"] = original
    assert test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "APPLE",
            "productId": "com.shelfa.limited.monthly",
            "user_nickname": "payer",
            "device_id": "device-1",
            "transactionId": "300",
            "signedTransactionInfo": "signed-limited",
        },
    ).status_code == 200
    renewed = purchase(
        platform="APPLE",
        product_id="com.shelfa.limited.monthly",
        account_plan="limited",
        purchase_token=None,
        transaction_id="301",
        original_transaction_id="300",
        expiration_date=1_900_000_000,
    )
    apple.transactions["signed-renewed"] = renewed
    apple.notifications["signed-note"] = {
        "notification_id": "apple-note-1",
        "notification_type": "DID_RENEW",
        "signed_transaction": "signed-renewed",
    }
    first = test_client.post(
        "/api/subscriptions/apple/notifications",
        json={"signedPayload": "signed-note"},
    )
    assert first.status_code == 200
    second = test_client.post(
        "/api/subscriptions/apple/notifications",
        json={"signedPayload": "signed-note"},
    )
    assert second.json()["duplicate"] is True
    forged = test_client.post(
        "/api/subscriptions/apple/notifications",
        json={"signedPayload": "forged"},
    )
    assert forged.status_code == 400


def test_out_of_order_store_event_does_not_roll_back_a_newer_state(client):
    test_client, _, _ = client
    user = register(test_client)
    newer = purchase(expiration_date=1_900_000_000, event_time=500).as_dict()
    older = purchase(expiration_date=1_800_000_000, event_time=100).as_dict()
    subscription_db.apply_store_subscription(
        user["id"],
        newer,
        event_type="renewed",
        notification_id="event-new",
        authoritative=False,
    )
    stale = subscription_db.apply_store_subscription(
        user["id"],
        older,
        event_type="purchased",
        notification_id="event-old",
        authoritative=False,
    )
    assert stale["reason"] == "stale"
    status = subscription_db.get_status(user["id"])
    assert status["subscription"]["expiresAt"] == "2030-03-17T17:46:40Z"


def test_concurrent_verification_keeps_a_single_owner(client):
    test_client, google, _ = client
    register(test_client, "payer", "device-1")
    register(test_client, "other", "device-2")
    google.purchases["token-premium"] = purchase()
    results = []
    barrier = threading.Barrier(2)

    def claim(nickname, device_id):
        barrier.wait()
        response = test_client.post(
            "/api/subscriptions/verify",
            json={
                "platform": "GOOGLE",
                "productId": "shelfa_premium",
                "user_nickname": nickname,
                "device_id": device_id,
                "purchaseToken": "token-premium",
            },
        )
        results.append(response.status_code)

    threads = [
        threading.Thread(target=claim, args=("payer", "device-1")),
        threading.Thread(target=claim, args=("other", "device-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 409]
    with db.get_db() as conn:
        owners = conn.execute("SELECT COUNT(DISTINCT user_id) FROM subscriptions").fetchone()[0]
    assert owners == 1


def test_replacement_token_follows_the_linked_purchase(client, monkeypatch):
    test_client, google, _ = client
    register(test_client)
    google.purchases["token-old"] = purchase(purchase_token="token-old", product_id="shelfa_limited", account_plan="limited")
    assert test_client.post(
        "/api/subscriptions/verify",
        json={
            "platform": "GOOGLE",
            "productId": "shelfa_limited",
            "user_nickname": "payer",
            "device_id": "device-1",
            "purchaseToken": "token-old",
        },
    ).status_code == 200
    google.purchases["token-new"] = purchase(
        purchase_token="token-new",
        linked_purchase_token="token-old",
        product_id="shelfa_premium",
        account_plan="premium",
    )
    monkeypatch.setenv("SHELFA_GOOGLE_PUBSUB_VERIFICATION_TOKEN", "pubsub-secret")
    payload = {
        "packageName": "com.shelfa",
        "subscriptionNotification": {"notificationType": 4, "purchaseToken": "token-new"},
    }
    response = test_client.post(
        "/api/subscriptions/google/notifications?token=pubsub-secret",
        json={
            "message": {
                "messageId": "upgrade-1",
                "data": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii"),
            }
        },
    )
    assert response.status_code == 200
    status = test_client.get(
        "/api/subscriptions/status",
        params={"user_nickname": "payer", "device_id": "device-1"},
    ).json()
    assert status["effectivePlan"] == "PREMIUM"
    assert status["subscription"]["productId"] == "shelfa_premium"

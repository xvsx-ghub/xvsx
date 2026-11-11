import base64
import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shelfa_group import api, db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "calls-test.db")
    db.init()

    app = FastAPI()
    app.include_router(api.api_router)
    with TestClient(app) as test_client:
        yield test_client


def decode_and_verify_token(token: str, api_secret: str) -> dict:
    header, payload, signature = token.split(".")
    signing_input = f"{header}.{payload}".encode("ascii")
    expected_signature = hmac.new(
        api_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    actual_signature = base64.urlsafe_b64decode(
        signature + "=" * (-len(signature) % 4)
    )
    assert hmac.compare_digest(actual_signature, expected_signature)
    return json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )


def prepare_users():
    db.set_user("caller", 1, "caller-device", "caller-fcm-token")
    db.set_user("callee", 1, "callee-device", "callee-fcm-token")
    db.create_contact("caller", "callee")


def configure_livekit(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-api-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s" * 32)


def test_start_join_and_end_call(client, monkeypatch):
    configure_livekit(monkeypatch)
    prepare_users()
    invites = []
    signals = []
    monkeypatch.setattr(
        api.fcm,
        "send_alert_notification",
        lambda **kwargs: invites.append(kwargs),
    )
    monkeypatch.setattr(
        api.fcm,
        "send_background_notification",
        lambda **kwargs: signals.append(kwargs),
    )

    started = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "caller",
            "device_id": "caller-device",
            "recipient_nickname": "callee",
            "call_type": "video",
            "action": "start",
        },
    )
    assert started.status_code == 200
    outgoing = started.json()
    assert outgoing["status"] == "ringing"
    assert outgoing["livekit_url"] == "wss://livekit.example.test"
    assert outgoing["room_name"].startswith("shelfa-")
    outgoing_claims = decode_and_verify_token(
        outgoing["token"],
        "s" * 32,
    )
    assert outgoing_claims["iss"] == "test-api-key"
    assert outgoing_claims["video"]["room"] == outgoing["room_name"]
    assert outgoing_claims["video"]["roomJoin"] is True
    assert outgoing_claims["video"]["canPublishSources"] == ["microphone", "camera"]
    assert outgoing_claims["sub"] != "caller"

    assert invites[0]["token"] == "callee-fcm-token"
    assert invites[0]["data"] == {
        "type": "call",
        "action": "invite",
        "call_id": outgoing["call_id"],
        "room_name": outgoing["room_name"],
        "call_type": "video",
        "sender_nickname": "caller",
        "recipient_nickname": "callee",
    }

    joined = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "callee",
            "device_id": "callee-device",
            "recipient_nickname": "caller",
            "call_type": "video",
            "action": "join",
            "call_id": outgoing["call_id"],
        },
    )
    assert joined.status_code == 200
    incoming = joined.json()
    assert incoming["status"] == "active"
    assert incoming["room_name"] == outgoing["room_name"]
    incoming_claims = decode_and_verify_token(incoming["token"], "s" * 32)
    assert incoming_claims["sub"] != outgoing_claims["sub"]

    ended = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "callee",
            "device_id": "callee-device",
            "recipient_nickname": "caller",
            "call_type": "video",
            "action": "end",
            "call_id": outgoing["call_id"],
        },
    )
    assert ended.status_code == 200
    assert ended.json()["status"] == "ended"
    assert signals[0]["token"] == "caller-fcm-token"
    assert signals[0]["data"]["action"] == "end"
    assert signals[0]["data"]["sender_nickname"] == "callee"


def test_call_requires_registered_contact_and_livekit_configuration(
    client,
    monkeypatch,
):
    db.set_user("caller", 1, "caller-device", "caller-fcm-token")
    db.set_user("stranger", 1, "stranger-device", "stranger-fcm-token")
    request = {
        "sender_nickname": "caller",
        "device_id": "caller-device",
        "recipient_nickname": "stranger",
        "call_type": "audio",
        "action": "start",
    }
    assert client.post("/shelfa/call", json=request).status_code == 403

    db.create_contact("caller", "stranger")
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    unavailable = client.post("/shelfa/call", json=request)
    assert unavailable.status_code == 503
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM call_session").fetchone()[0] == 0


def test_busy_call_and_invalid_participant_action(client, monkeypatch):
    configure_livekit(monkeypatch)
    prepare_users()
    db.set_user("third", 1, "third-device", "third-fcm-token")
    db.create_contact("caller", "third")
    monkeypatch.setattr(api.fcm, "send_alert_notification", lambda **kwargs: "message-id")

    spoofed = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "caller",
            "device_id": "attacker-device",
            "recipient_nickname": "callee",
            "call_type": "audio",
            "action": "start",
        },
    )
    assert spoofed.status_code == 403

    active = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "caller",
            "device_id": "caller-device",
            "recipient_nickname": "callee",
            "call_type": "audio",
            "action": "start",
        },
    )
    assert active.status_code == 200
    busy = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "caller",
            "device_id": "caller-device",
            "recipient_nickname": "third",
            "call_type": "audio",
            "action": "start",
        },
    )
    assert busy.status_code == 200
    assert busy.json()["status"] == "busy"

    forged = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "third",
            "device_id": "third-device",
            "recipient_nickname": "caller",
            "call_type": "audio",
            "action": "join",
            "call_id": active.json()["call_id"],
        },
    )
    assert forged.status_code == 403


def test_call_validation_and_decline_transition(client, monkeypatch):
    configure_livekit(monkeypatch)
    prepare_users()
    signals = []
    monkeypatch.setattr(api.fcm, "send_alert_notification", lambda **kwargs: "invite-id")
    monkeypatch.setattr(
        api.fcm,
        "send_background_notification",
        lambda **kwargs: signals.append(kwargs),
    )
    assert client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "caller",
            "device_id": "caller-device",
            "recipient_nickname": "callee",
            "call_type": "group",
            "action": "start",
        },
    ).status_code == 422

    started = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "caller",
            "device_id": "caller-device",
            "recipient_nickname": "callee",
            "call_type": "audio",
            "action": "start",
        },
    )
    mismatched_join = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "callee",
            "device_id": "callee-device",
            "recipient_nickname": "caller",
            "call_type": "video",
            "action": "join",
            "call_id": started.json()["call_id"],
        },
    )
    assert mismatched_join.status_code == 409

    declined = client.post(
        "/shelfa/call",
        json={
            "sender_nickname": "callee",
            "device_id": "callee-device",
            "recipient_nickname": "caller",
            "call_type": "audio",
            "action": "decline",
            "call_id": started.json()["call_id"],
        },
    )
    assert declined.status_code == 200
    assert declined.json()["status"] == "declined"
    assert signals[0]["data"]["action"] == "decline"

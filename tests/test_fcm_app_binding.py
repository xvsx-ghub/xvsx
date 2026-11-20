import shelfa_group.fcm as fcm


def test_send_alert_notification_uses_initialized_app(monkeypatch):
    seen = {}

    def fake_send(message, app=None):
        seen["app"] = app
        return "message-id"

    monkeypatch.setattr(fcm.messaging, "send", fake_send)
    monkeypatch.setattr(fcm, "get_app", lambda app_name: object())

    result = fcm.send_alert_notification(
        token="abc1234567890",
        title="Hello",
        body="World",
    )

    assert result == "message-id"
    assert seen["app"] is not None


def test_call_invite_uses_ios_ringtone(monkeypatch):
    seen = {}

    def fake_send(message, app=None):
        seen["message"] = message
        return "message-id"

    monkeypatch.setattr(fcm.messaging, "send", fake_send)
    monkeypatch.setattr(fcm, "get_app", lambda app_name: object())

    fcm.send_alert_notification(
        token="abc1234567890",
        title="Incoming video call",
        body="Ada is calling you",
        data={
            "type": "call",
            "action": "invite",
            "call_id": "room-1",
            "call_type": "video",
            "sender_nickname": "Ada",
        },
    )

    aps = seen["message"].apns.payload.aps
    assert aps.alert is None
    assert aps.sound is None
    assert aps.content_available is True
    assert seen["message"].notification is None
    assert seen["message"].android.notification is None
    assert seen["message"].apns.headers["apns-push-type"] == "background"
    assert seen["message"].data["type"] == "call"
    assert "title" not in seen["message"].data
    assert "body" not in seen["message"].data

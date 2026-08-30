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

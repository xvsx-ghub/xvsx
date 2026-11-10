import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shelfa_group import admin, api, db
from shelfa_group import shelfa as shelfa_group_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "admin-test.db")
    monkeypatch.setenv("SHELFA_ADMIN_USERNAME", "workspace-admin")
    monkeypatch.setenv("SHELFA_ADMIN_PASSWORD", "long-enough-password")
    monkeypatch.setenv("SHELFA_ADMIN_SECRET_KEY", "a" * 48)
    monkeypatch.setenv("SHELFA_ADMIN_COOKIE_SECURE", "false")
    db.init()

    app = FastAPI()
    app.include_router(api.api_router)
    app.include_router(admin.router)
    with TestClient(app) as test_client:
        yield test_client


def sign_in(client: TestClient) -> str:
    response = client.post(
        "/admin/api/login",
        json={"username": "workspace-admin", "password": "long-enough-password"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    return response.json()["csrf_token"]


def test_admin_requires_login_and_uses_protected_session(client):
    assert client.get("/admin/api/stats").status_code == 401
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Content-Security-Policy" in page.headers

    csrf_token = sign_in(client)
    session = client.get("/admin/api/session")
    assert session.json() == {
        "authenticated": True,
        "username": "workspace-admin",
        "csrf_token": csrf_token,
    }
    stats = client.get("/admin/api/stats")
    assert stats.status_code == 200
    assert stats.headers["cache-control"] == "no-store"
    assert client.cookies.get(admin.SESSION_COOKIE)

    without_csrf = client.post(
        "/admin/api/users",
        json={"user_nickname": "no-csrf"},
    )
    assert without_csrf.status_code == 403


def test_user_management_blocking_claim_and_audit(client):
    csrf_token = sign_in(client)
    headers = {"X-CSRF-Token": csrf_token}

    created = client.post(
        "/admin/api/users",
        json={"user_nickname": "reserved-member", "user_type": 1},
        headers=headers,
    )
    assert created.status_code == 200
    account_id = created.json()["id"]
    assert created.json()["is_blocked"] == 0

    registered = client.post(
        "/shelfa/user",
        json={
            "user_nickname": "reserved-member",
            "user_type": 1,
            "device_id": "member-device",
            "fcm_token": "member-token",
        },
    )
    assert registered.status_code == 200

    blocked = client.patch(
        f"/admin/api/users/{account_id}",
        json={"is_blocked": True},
        headers=headers,
    )
    assert blocked.status_code == 200
    assert client.post(
        "/shelfa/user",
        json={
            "user_nickname": "reserved-member",
            "user_type": 1,
            "device_id": "member-device",
            "fcm_token": "member-token",
        },
    ).status_code == 403
    assert client.get(
        "/admin/api/users?status=blocked&search=reserved"
    ).json()["total"] == 1
    assert "device_id" not in client.get(
        "/admin/api/users?search=reserved-member"
    ).json()["items"][0]

    unblocked = client.patch(
        f"/admin/api/users/{account_id}",
        json={"is_blocked": False, "user_type": 2},
        headers=headers,
    )
    assert unblocked.json()["user_type"] == 2
    audit = client.get("/admin/api/audit").json()
    actions = [item["action"] for item in audit["items"]]
    assert "admin.login" in actions
    assert "user.created" in actions
    assert "user.updated" in actions

    session_cookie = client.cookies.get(admin.SESSION_COOKIE)
    logout = client.post("/admin/api/logout", headers=headers)
    assert logout.status_code == 200
    client.cookies.set(admin.SESSION_COOKIE, session_cookie, path="/admin")
    assert client.get("/admin/api/stats").status_code == 401
    assert client.get("/admin/api/session").json()["authenticated"] is False


def test_admin_manages_account_modes_and_usage_limits(client):
    csrf_token = sign_in(client)
    headers = {"X-CSRF-Token": csrf_token}

    created = client.post(
        "/admin/api/users",
        json={
            "user_nickname": "limited-member",
            "user_type": 1,
            "account_mode": "limited",
        },
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["account_mode"] == "limited"

    updated = client.patch(
        f"/admin/api/users/{created.json()['id']}",
        json={"account_mode": "premium"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["account_mode"] == "premium"
    assert client.get("/admin/api/users").json()["items"][0]["account_mode"] == "premium"
    assert client.patch(
        f"/admin/api/users/{created.json()['id']}",
        json={"account_mode": "invalid"},
        headers=headers,
    ).status_code == 422

    default_limits = client.get("/admin/api/usage-limits")
    assert default_limits.status_code == 200
    assert default_limits.json() == {
        "free": {
            "max_contacts": 5,
            "max_message_history_per_contact": 50,
        },
        "limited": {
            "max_contacts": 50,
            "max_message_history_per_contact": 500,
        },
    }

    limits = {
        "free": {
            "max_contacts": 3,
            "max_message_history_per_contact": 25,
        },
        "limited": {
            "max_contacts": 30,
            "max_message_history_per_contact": 250,
        },
    }
    saved = client.put(
        "/admin/api/usage-limits",
        json=limits,
        headers=headers,
    )
    assert saved.status_code == 200
    assert saved.json() == limits
    assert client.put(
        "/admin/api/usage-limits",
        json=limits,
    ).status_code == 403
    assert "usage_limits.updated" in [
        item["action"] for item in client.get("/admin/api/audit").json()["items"]
    ]


def test_contact_limits_and_premium_access(client, monkeypatch):
    monkeypatch.setattr(api.fcm, "send_alert_notification", lambda **kwargs: None)
    db.set_user("free-member", 1, "free-device", "free-token")
    db.set_user("new-contact", 1, "contact-device", "contact-token")
    for index in range(5):
        db.create_contact("free-member", f"existing-{index}")

    blocked = client.post(
        "/shelfa/contact",
        json={
            "sender_nickname": "free-member",
            "recipient_nickname": "new-contact",
        },
    )
    assert blocked.status_code == 403
    assert "Contact limit reached" in blocked.json()["detail"]
    blocked_message = client.post(
        "/shelfa/message",
        json={
            "sender_nickname": "free-member",
            "recipient_nickname": "new-contact",
            "message_content": "over the contact limit",
        },
    )
    assert blocked_message.status_code == 403
    assert db.get_message_list("free-member", "new-contact") == []
    contacts = client.get(
        "/shelfa/contact_list?user_nickname=free-member"
    ).json()["contact_list"]
    assert len(contacts) == 5

    db.set_user("premium-member", 1, "premium-device", "premium-token")
    with db.get_db() as connection:
        connection.execute(
            "UPDATE user SET account_mode = 'premium' WHERE user_nickname = ?",
            ("premium-member",),
        )
        connection.commit()

    for index in range(6):
        db.set_user(f"premium-contact-{index}", 1, f"device-{index}", "token")
        response = client.post(
            "/shelfa/contact",
            json={
                "sender_nickname": "premium-member",
                "recipient_nickname": f"premium-contact-{index}",
            },
        )
        assert response.status_code == 200

    message = client.post(
        "/shelfa/message",
        json={
            "sender_nickname": "premium-member",
            "recipient_nickname": "premium-contact-0",
            "message_content": "premium conversation",
            "message_type": "0",
        },
    )
    assert message.status_code == 200
    assert message.json()["message_type"] == 0


def test_group_message_cannot_exceed_contact_limit(client, monkeypatch):
    monkeypatch.setattr(api.fcm, "send_alert_notification", lambda **kwargs: None)
    db.set_user("group-owner", 2, "group-device", "group-token")
    for index in range(5):
        nickname = f"group-member-{index}"
        db.set_user(nickname, 1, f"device-{index}", "token")
        db.set_message(nickname, "group-owner", "joined", 0, index)
    db.set_user("new-group-member", 1, "new-device", "new-token")

    response = client.post(
        "/shelfa/message",
        json={
            "sender_nickname": "new-group-member",
            "recipient_nickname": "group-owner",
            "message_content": "over the group contact limit",
        },
    )
    assert response.status_code == 403
    assert db.get_contact_list("group-owner") == []
    assert db.get_message_list("new-group-member", "group-owner") == []


def test_message_history_limit_and_premium_access(client):
    db.set_user("history-member", 1, "history-device", "history-token")
    db.set_user("history-contact", 1, "contact-device", "contact-token")
    for index in range(51):
        db.set_message(
            "history-member",
            "history-contact",
            f"message {index}",
            0,
            index,
        )

    limited_history = client.get(
        "/shelfa/message_list?sender_nickname=history-member"
        "&recipient_nickname=history-contact"
    ).json()["messages"]
    assert len(limited_history) == 50
    assert limited_history[0]["message_content"] == "message 1"
    assert limited_history[-1]["message_content"] == "message 50"

    with db.get_db() as connection:
        connection.execute(
            "UPDATE user SET account_mode = 'premium' WHERE user_nickname = ?",
            ("history-member",),
        )
        connection.commit()
    premium_history = client.get(
        "/shelfa/message_list?sender_nickname=history-member"
        "&recipient_nickname=history-contact"
    ).json()["messages"]
    assert len(premium_history) == 51


def test_database_browsing_filters_and_user_delete_cascade(client):
    with db.get_db() as connection:
        connection.execute(
            """
            INSERT INTO user (user_nickname, user_type, device_id, fcm_token)
            VALUES ('alice', 1, 'device', 'token')
            """
        )
        connection.execute(
            """
            INSERT INTO user (user_nickname, user_type, device_id, fcm_token)
            VALUES ('garden-club', 2, '', '')
            """
        )
        connection.execute(
            """
            INSERT INTO message (
                message_type, message_content, sender_nickname,
                recipient_nickname, timestamp_unix
            )
            VALUES (0, 'hello garden club', 'alice', 'garden-club', 1700000000)
            """
        )
        connection.execute(
            """
            INSERT INTO contact_registry (
                sender_nickname, recipient_nickname, unread_messages_count
            )
            VALUES ('alice', 'garden-club', 2)
            """
        )
        connection.commit()

    sign_in(client)
    users = client.get("/admin/api/users?search=garden-club&user_type=2").json()
    assert users["total"] == 1
    assert users["pages"] == 1
    assert users["items"][0]["message_count"] == 1
    page_one = client.get("/admin/api/users?page=1&page_size=1").json()
    page_two = client.get("/admin/api/users?page=2&page_size=1").json()
    assert page_one["pages"] == page_two["pages"] == 2
    assert page_one["items"][0]["id"] != page_two["items"][0]["id"]

    messages = client.get("/admin/api/messages?search=garden%20club&page_size=1").json()
    assert messages["total"] == 1
    assert messages["items"][0]["message_content"] == "hello garden club"
    contacts = client.get("/admin/api/contacts?search=alice").json()
    assert contacts["total"] == 1
    assert contacts["items"][0]["unread_messages_count"] == 2

    database = client.get("/admin/api/database").json()
    tables = {table["name"]: table for table in database["tables"]}
    assert {"user", "message", "contact_registry", "admin_audit"} <= tables.keys()
    assert "fcm_token" in tables["user"]["columns"]
    stats = client.get("/admin/api/stats").json()
    assert stats["total_users"] == 1
    assert stats["groups"] == 1

    account_id = users["items"][0]["id"]
    deleted = client.delete(
        f"/admin/api/users/{account_id}",
        headers={"X-CSRF-Token": client.get("/admin/api/session").json()["csrf_token"]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["messages_deleted"] == 1
    assert deleted.json()["contacts_deleted"] == 1
    assert db.admin_list_messages(1, 20)["total"] == 0
    assert db.admin_list_contacts(1, 20)["total"] == 0


def test_blocked_accounts_are_rejected_by_nickname_based_app_routes(client):
    with db.get_db() as connection:
        connection.execute(
            """
            INSERT INTO user (
                user_nickname, user_type, device_id, fcm_token, is_blocked
            )
            VALUES ('blocked-member', 1, 'device', 'token', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO user (user_nickname, user_type, device_id, fcm_token)
            VALUES ('open-member', 1, 'open-device', 'open-token')
            """
        )
        connection.execute(
            """
            INSERT INTO message (
                message_type, message_content, sender_nickname,
                recipient_nickname, timestamp_unix
            )
            VALUES (0, 'join group', 'blocked-member', 'community', 1),
                   (0, 'join group', 'open-member', 'community', 2)
            """
        )
        connection.commit()

    assert db.get_sender_nickname_list("community") == ["open-member"]
    assert client.post(
        "/shelfa/message",
        json={
            "sender_nickname": "blocked-member",
            "recipient_nickname": "someone",
            "message_content": "hello",
        },
    ).status_code == 403
    assert client.post(
        "/shelfa/fcm_token",
        json={"user_nickname": "blocked-member", "fcm_token": "new-token"},
    ).status_code == 403
    assert client.post(
        "/shelfa/contact",
        json={
            "sender_nickname": "blocked-member",
            "recipient_nickname": "someone",
        },
    ).status_code == 403
    assert client.get(
        "/shelfa/contact_list?user_nickname=blocked-member"
    ).status_code == 403


def test_message_and_contact_deletion_are_audited(client):
    with db.get_db() as connection:
        connection.execute(
            """
            INSERT INTO message (
                message_type, message_content, sender_nickname,
                recipient_nickname, timestamp_unix
            )
            VALUES (0, 'remove this', 'alice', 'bob', 1700000000)
            """
        )
        connection.execute(
            """
            INSERT INTO contact_registry (
                sender_nickname, recipient_nickname, unread_messages_count
            )
            VALUES ('alice', 'bob', 0)
            """
        )
        connection.commit()

    csrf_token = sign_in(client)
    message_id = db.admin_list_messages(1, 20)["items"][0]["id"]
    contact_id = db.admin_list_contacts(1, 20)["items"][0]["id"]
    assert client.delete(
        f"/admin/api/messages/{message_id}",
        headers={"X-CSRF-Token": csrf_token},
    ).status_code == 200
    assert client.delete(
        f"/admin/api/contacts/{contact_id}",
        headers={"X-CSRF-Token": csrf_token},
    ).status_code == 200
    actions = [item["action"] for item in client.get("/admin/api/audit").json()["items"]]
    assert "message.deleted" in actions
    assert "contact.deleted" in actions


def test_login_rate_limit_and_missing_configuration(client, monkeypatch):
    for _ in range(5):
        response = client.post(
            "/admin/api/login",
            json={"username": "workspace-admin", "password": "wrong-password"},
        )
        assert response.status_code == 401
    limited = client.post(
        "/admin/api/login",
        json={"username": "workspace-admin", "password": "long-enough-password"},
    )
    assert limited.status_code == 429

    monkeypatch.delenv("SHELFA_ADMIN_PASSWORD")
    unavailable = client.post(
        "/admin/api/login",
        json={"username": "workspace-admin", "password": "long-enough-password"},
    )
    assert unavailable.status_code == 503


def test_database_migration_adds_block_status_to_existing_user_table(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_nickname TEXT NOT NULL UNIQUE,
            user_type INTEGER NOT NULL DEFAULT 0,
            device_id TEXT NOT NULL,
            fcm_token TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.init()
    with db.get_db() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(user)")
        }
    assert "is_blocked" in columns
    assert "account_mode" in columns


def test_shelfa_group_startup_registers_admin_router(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "startup.db")
    monkeypatch.setattr(shelfa_group_app.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(shelfa_group_app.config, "UPLOAD_DIR", tmp_path / "upload")
    monkeypatch.setattr(shelfa_group_app.config, "PRIVATE_DIR", tmp_path / "private")
    monkeypatch.setattr(shelfa_group_app.fcm, "init", lambda app_name: True)
    app = FastAPI()

    shelfa_group_app.init_shelfa(app)

    with TestClient(app) as client:
        assert client.get("/admin").status_code == 200
        assert client.get("/admin/api/session").status_code == 200
        assert client.get("/admin/api/stats").status_code == 401

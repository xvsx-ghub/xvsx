import shelfa_group.db as db
from shelfa_group.api import PostFcmTokenRequest, post_fcm_token


def test_post_fcm_token_updates_existing_user_token():
    db.init()

    with db.get_db() as conn:
        conn.execute("DELETE FROM user")
        conn.commit()

    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO user (user_nickname, user_type, device_id, fcm_token) VALUES (?, ?, ?, ?)",
            ("alice", 1, "device-123", "old-token"),
        )
        conn.commit()

    try:
        result = post_fcm_token(
            PostFcmTokenRequest(
                user_nickname="alice",
                fcm_token="new-token",
            )
        )

        assert result.fcm_token == "new-token"
        row = db.get_user_by_nickname("alice")
        assert row is not None
        assert row["fcm_token"] == "new-token"
    finally:
        with db.get_db() as conn:
            conn.execute("DELETE FROM user")
            conn.commit()

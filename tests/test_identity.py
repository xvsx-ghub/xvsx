import shelfa.database as database
from shelfa.services.identity import register_identity
from shelfa.services.messages import insert_text_message, row_to_message


def test_register_identity_inserts_message_successfully():
    database.init_db()

    with database.get_db() as conn:
        conn.execute("DELETE FROM messages")
        conn.commit()

    try:
        assert register_identity("pytest-user","0", "device-123") is True
    finally:
        with database.get_db() as conn:
            conn.execute("DELETE FROM messages")
            conn.commit()


def test_row_to_message_normalizes_empty_group_flag_to_default():
    database.init_db()

    with database.get_db() as conn:
        conn.execute("DELETE FROM messages")
        conn.commit()

    try:
        row = insert_text_message(
            nickname="pytest-user",
            group_flag="",
            device_id="device-456",
            client_name="client",
            text="hello",
        )
        message = row_to_message(row)
        assert message["group_flag"] == "0"
    finally:
        with database.get_db() as conn:
            conn.execute("DELETE FROM messages")
            conn.commit()

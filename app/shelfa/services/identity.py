from app.shelfa.database import get_db
from app.shelfa.services.messages import get_device_id_by_nickname, insert_text_message


def check_nickname_exists(nickname: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE nickname = ? LIMIT 1",
            (nickname,),
        ).fetchone()
    return row is not None


def declare_user(nickname: str, device_id: str) -> None:
    message = f"My nickname is {nickname}"
    insert_text_message(
        nickname=nickname,
        device_id=device_id,
        client_name="Chat",
        text=message,
    )


def validate_identity(nickname: str, device_id: str) -> bool:
    if check_nickname_exists(nickname):
        return get_device_id_by_nickname(nickname) == device_id
    return True


def register_identity(nickname: str, device_id: str) -> bool:
    if not validate_identity(nickname, device_id):
        return False
    declare_user(nickname=nickname, device_id=device_id)
    return True

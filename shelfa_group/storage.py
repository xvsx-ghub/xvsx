import uuid

from shelfa_group.config import UPLOAD_DIR

def upload(content: bytes, filename: str) -> str:
    file_name = f"{uuid.uuid4().hex}_{filename}"
    file_path = UPLOAD_DIR / file_name
    file_path.write_bytes(content)
    return file_name

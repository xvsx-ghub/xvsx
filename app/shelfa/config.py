import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "chat.db"
FIREBASE_CREDENTIALS_FILENAME = "shelf-a953d-firebase-adminsdk-fbsvc-ec291cc8bb.json"
DEFAULT_FIREBASE_AUTH_PATH = Path("/home/xvsx/xassistancex") / FIREBASE_CREDENTIALS_FILENAME
FIREBASE_AUTH_PATH = Path(os.environ.get("FIREBASE_CREDENTIALS_PATH", DEFAULT_FIREBASE_AUTH_PATH))

if not FIREBASE_AUTH_PATH.is_file():
    home_auth_path = Path.home() / FIREBASE_CREDENTIALS_FILENAME
    if home_auth_path.is_file():
        FIREBASE_AUTH_PATH = home_auth_path

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_DATA_BYTES = 1024 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    "pdf",
    "jpg",
    "jpeg",
    "png",
    "gif",
    "webp",
    "mp4",
    "webm",
    "mov",
    "mp3",
    "wav",
    "ogg",
    "m4a",
    "aac",
    "flac",
}

EXTENSION_TO_KIND = {
    "pdf": "document",
    "jpg": "image",
    "jpeg": "image",
    "png": "image",
    "gif": "image",
    "webp": "image",
    "mp4": "video",
    "webm": "video",
    "mov": "video",
    "mp3": "audio",
    "wav": "audio",
    "ogg": "audio",
    "m4a": "audio",
    "aac": "audio",
    "flac": "audio",
}

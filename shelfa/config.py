import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "upload"
PRIVATE_DIR = DATA_DIR / "private"

DB_PATH = DATA_DIR / "shelfa.db"

FIREBASE_CREDENTIALS_FILENAME = "shelf-a953d-firebase-adminsdk-fbsvc-ec291cc8bb.json"
FIREBASE_AUTH_PATH = PRIVATE_DIR / FIREBASE_CREDENTIALS_FILENAME

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

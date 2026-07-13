from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from shelfa.config import UPLOAD_DIR

router = APIRouter(tags=["files"])

@router.get("/files/{name}")
def serve_file(name: str):
    safe = Path(name).name
    if safe != name or ".." in name:
        raise HTTPException(status_code=400, detail="bad path")

    full = UPLOAD_DIR / safe
    if not full.is_file():
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(full)

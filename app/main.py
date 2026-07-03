import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.shelfa.config import MAX_UPLOAD_BYTES
from app.shelfa.database import init_db
from app.shelfa.routers import fcm, files, identity, messages
from app.shelfa.services.notifications import init_firebase
from app.shelfa.services.storage import enforce_data_limit

SHELFA_DIR = Path(__file__).resolve().parent / "shelfa"
templates = Jinja2Templates(directory=str(SHELFA_DIR / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    init_firebase()
    enforce_data_limit()
    yield


app = FastAPI(title="Shelfa", version="1.0.0", lifespan=lifespan)
app.state.max_upload_bytes = MAX_UPLOAD_BYTES

app.include_router(fcm.router)
app.include_router(identity.router)
app.include_router(messages.router)
app.include_router(files.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    if isinstance(exc.detail, str):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "chat.html")

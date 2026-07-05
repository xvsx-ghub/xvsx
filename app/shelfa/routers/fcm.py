from fastapi import APIRouter, HTTPException

from app.shelfa.schemas import FcmRegisterRequest, OkResponse
from app.shelfa.services.messages import normalize_device_id, upsert_device_token

router = APIRouter(prefix="/api/fcm", tags=["fcm"])


@router.post("/register", response_model=OkResponse)
def register_fcm_token(body: FcmRegisterRequest):
    token = body.token.strip()
    device_id = normalize_device_id(body.device_id)

    if not token:
        raise HTTPException(status_code=400, detail="token required")

    upsert_device_token(device_id=device_id, token=token)
    return {"ok": True}

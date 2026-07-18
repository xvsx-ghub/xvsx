from fastapi import APIRouter, Query
from shelfa.schemas import IdentityCheckResponse, IdentityValidateResponse
from shelfa.services.identity import check_nickname_exists, register_identity

router = APIRouter(prefix="/api/identity", tags=["identity"])

@router.get("/validate", response_model=IdentityValidateResponse)
def identity_validate(
    device_id: str = Query(...),
    nickname: str = Query(...),
    group_flag: str = Query("0")
):
    if register_identity(nickname=nickname, group_flag=group_flag, device_id=device_id):
        return {"valid": True}
    return {"valid": False}

@router.get("/check", response_model=IdentityCheckResponse)
def identity_check(nickname: str = Query(...)):
    return {"exists": check_nickname_exists(nickname)}

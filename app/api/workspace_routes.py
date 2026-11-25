from datetime import datetime,timezone
from typing import Annotated
from fastapi import APIRouter,Depends,Response
from sqlalchemy.orm import Session
from app.config import Settings,get_settings
from app.db import get_db
from app.entitlements import account_entitlements
from app.models import PlatformStatistic
from app.user_auth import UserContext,get_current_user

router=APIRouter(tags=["workspace"])


@router.get("/v1/account/entitlements")
def entitlements(context:Annotated[UserContext,Depends(get_current_user)],session:Annotated[Session,Depends(get_db)],settings:Annotated[Settings,Depends(get_settings)],response:Response):
    response.headers["Cache-Control"]="no-store"
    return account_entitlements(session,context.user,settings)


@router.get("/v1/public/statistics")
def public_statistics(session:Annotated[Session,Depends(get_db)],response:Response):
    response.headers["Cache-Control"]="public, max-age=300"
    snapshot=session.get(PlatformStatistic,"public_product_count")
    return {"product_count":snapshot.value if snapshot and snapshot.method=="exact_count" else None,
        "verified_at":snapshot.verified_at if snapshot and snapshot.method=="exact_count" else None,
        "method":"exact_count" if snapshot and snapshot.method=="exact_count" else "unavailable"}
